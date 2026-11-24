"""Reciprocal rank fusion and the deterministic rerank (ranking.py) plus the offline search built on them."""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from smart_campus_assistant import local_search, ranking


def doc(chunk_id: str, text: str, section: str | None = None) -> Document:
    meta = {"chunk_id": chunk_id, "source": "x.md", "page": 1}
    if section:
        meta["section"] = section
    return Document(id=chunk_id, page_content=f"{section}\n{text}" if section else text, metadata=meta)


A, B, C, D = (doc(i, f"text {i}") for i in "abcd")


def ids(docs) -> list[str]:
    return [d.metadata["chunk_id"] for d in docs]


# ---------- fusion ----------

def test_rrf_rewards_chunks_both_retrievers_found():
    fused = ranking.reciprocal_rank_fusion([[A, B, C], [C, A, D]])
    assert ids(d for d, _ in fused) == ["a", "c", "b", "d"]
    scores = dict((d.metadata["chunk_id"], s) for d, s in fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["d"] == pytest.approx(1 / 63)


def test_rrf_keeps_the_first_lists_copy_and_counts_a_chunk_once_per_list():
    fresh = doc("a", "fresh local text")
    fused = ranking.reciprocal_rank_fusion([[fresh, fresh], [A]])
    assert len(fused) == 1 and fused[0][0] is fresh
    assert fused[0][1] == pytest.approx(2 / 61)
    assert ranking.reciprocal_rank_fusion([[], []]) == []


def test_doc_key_prefers_chunk_id_then_id_then_text():
    assert ranking.doc_key(A) == "a"
    assert ranking.doc_key(Document(id="z", page_content="t")) == "z"
    one, two = Document(page_content="same"), Document(page_content="same")
    assert ranking.doc_key(one) == ranking.doc_key(two)
    assert ranking.doc_key(one) != ranking.doc_key(Document(page_content="other"))


def test_candidate_count_is_bounded():
    assert ranking.candidate_count(1) == ranking.MIN_CANDIDATES
    assert ranking.candidate_count(5) == 20
    assert ranking.candidate_count(50) == ranking.MAX_CANDIDATES


# ---------- rerank ----------

def test_rerank_prefers_a_chunk_that_covers_every_topic_word():
    one_word = doc("one", "Hostel hostel hostel hostel blocks and rooms.")
    every_word = doc("all", "The hostel mess charge is Rs 3,500 per month.")
    out = ranking.rerank("hostel mess charge", [(one_word, 1.0), (every_word, 0.8)], k=2)
    assert ids(out) == ["all", "one"]


def test_rerank_uses_the_heading_path():
    plain = doc("plain", "Rs 5 per day per book.", section="Library > Rules")
    titled = doc("titled", "Rs 5 per day per book.", section="Library > Overdue fine")
    out = ranking.rerank("overdue fine", [(plain, 1.0), (titled, 1.0)], k=2)
    assert ids(out)[0] == "titled"


def test_rerank_branch_hint_and_named_branch():
    cse = doc("cse", "CSE end-sem timetable for Semester 5: CS501 on 2026-12-01.")
    ece = doc("ece", "ECE end-sem timetable for Semester 5: EC501 on 2026-12-01.")
    common = doc("common", "End-sem exams run from 2026-12-01 to 2026-12-10 for Semester 5.")
    hinted = ranking.rerank("Semester 5 timetable (CSE Computer Science and Engineering)",
                            [(ece, 1.0), (cse, 1.0), (common, 1.0)], k=3)
    assert ids(hinted) == ["cse", "common", "ece"]  # own branch up, other branch down, neutral in between
    named = ranking.rerank("ece semester 5 timetable", [(cse, 1.0), (ece, 1.0)], k=2)
    assert ids(named) == ["ece", "cse"]
    both = ranking.rerank("compare CSE and ECE timetable", [(common, 1.0), (ece, 1.0), (cse, 1.0)], k=3)
    assert set(ids(both[:2])) == {"cse", "ece"}


def test_rerank_drops_near_duplicates_but_keeps_distinct_facts():
    full = doc("full", "Late fee is Rs 100 per day after the due date, up to Rs 5,000.")
    stale = doc("stale", "Late fee is Rs 100 per day after the due date.")
    me = doc("me", "Mechanical Engineering (ME) has an intake of 60 students.")
    ce = doc("ce", "Civil Engineering (CE) has an intake of 60 students.")
    out = ranking.rerank("late fee intake", [(full, 1.0), (stale, 0.9), (me, 0.8), (ce, 0.8)], k=4)
    assert ids(out) == ["full", "me", "ce"]


def test_rerank_edges():
    assert ranking.rerank("anything", [], k=3) == []
    assert ranking.rerank("anything", [(A, 1.0)], k=0) == []
    # Only stopwords: the first-stage order is kept.
    assert ids(ranking.rerank("what is the", [(A, 0.2), (B, 0.9)], k=2)) == ["b", "a"]
    assert ids(ranking.rerank("text", [(A, 0.0), (B, 0.0)], k=2)) == ["a", "b"]


def test_split_scope_and_branch_words():
    assert ranking.split_scope("Fees? (CSE Computer Science and Engineering)") == ("Fees?", "CSE")
    assert ranking.split_scope("Fees (IT Information Technology)")[1] is None
    assert ranking.query_branches("mechanical vs civil") == {"ME", "CE"}
    assert ranking.query_branches("mujhe me ka fees batao") == set()  # Hinglish "me" is not the branch
    assert ranking.query_branches("ME and CE placements") == {"ME", "CE"}


def test_section_of():
    assert ranking.section_of(doc("s", "body", section="Fees > Late fee")) == "Fees > Late fee"
    assert ranking.section_of(Document(page_content="Heading line\nbody")) == "Heading line"
    assert ranking.section_of(Document(page_content="one line only")) is None


# ---------- offline search uses the same rerank ----------

def test_offline_search_puts_the_students_branch_first():
    hits = local_search.search("Semester 5 end-sem timetable (ECE Electronics and Communication Engineering)", k=3)
    assert hits and "ECE end-sem timetable, Semester 5" in hits[0].page_content


def test_offline_search_returns_whole_rule_sections():
    hits = local_search.search("minimum attendance condonation", k=2)
    top = hits[0]
    assert top.metadata["section"] == "Academic Rules for B.Tech Students > Attendance"
    assert top.metadata["chunk_id"].endswith(":c0") and "condonation" in top.page_content.lower()


def test_keyword_search_is_the_raw_bm25_ranking():
    raw = local_search.keyword_search("library fine per day", 5)
    assert raw and all(score > 0 for _, score in raw)
    assert [s for _, s in raw] == sorted((s for _, s in raw), reverse=True)
    assert local_search.keyword_search("library fine", 0) == []
