"""Edge cases: offline search, blank / oversized questions, pronoun handling, concurrency, cheap lookups."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from smart_campus_assistant import (
    auth,
    deep_research,
    fakes,
    local_search,
    notices,
    personal,
    retriever,
    router,
    student_data,
)
from smart_campus_assistant import graph as graph_module

SID = "XYZ24CSE001"


@pytest.fixture
def fake_mode():
    fakes.enable_fake_mode()
    yield fakes
    fakes.disable_fake_mode()


@pytest.fixture
def notices_file(tmp_path, monkeypatch):
    path = tmp_path / "notices.json"
    path.write_text(json.dumps({"notices": []}), encoding="utf-8")
    monkeypatch.setenv("SCA_NOTICES_FILE", str(path))
    local_search.reset_cache()
    yield path
    local_search.reset_cache()


# ---------------------------------------------------------------- offline search


def test_local_search_finds_real_document_facts():
    docs = local_search.search("library fine per day")
    assert docs and docs[0].metadata["source"] == "library_rules.md"
    assert any("Rs 5 per day" in d.page_content for d in docs)


def test_local_search_understands_hinglish_and_plurals():
    hinglish = local_search.search("Hostel mein mess ka charge kitna hai?")
    assert hinglish and any("Mess" in d.page_content for d in hinglish)
    assert local_search.tokenize("Fees of the books") == ["fee", "book"]
    assert local_search.tokenize("kitab ki pariksha") == ["book", "exam"]


@pytest.mark.parametrize("query", ["", "   ", "the of and kya hai", "zzqqxx unknownword"])
def test_local_search_returns_nothing_without_a_topic_match(query):
    assert local_search.search(query) == []


def test_local_search_respects_k_and_college_filter():
    assert len(local_search.search("fee", k=2)) == 2
    assert local_search.search("fee", college="another-college") == []


def test_branch_hint_only_breaks_ties():
    """The "(CSE ...)" hint added for signed-in students must not replace the real question."""
    hinted = local_search.search("What is the hostel caution deposit? (CSE Computer Science and Engineering)")
    assert hinted and "caution deposit" in hinted[0].page_content.lower()


def test_new_notice_is_searchable_at_once_and_gone_after_delete(fake_mode, notices_file):
    assert not any("Seminar Hall 2" in d.page_content for d in retriever.search("robotics club orientation"))
    notice, indexed, _ = notices.publish_notice(
        "Robotics club orientation", "The Robotics club orientation is on 2026-11-20 at 17:00 in Seminar Hall 2.",
        "general", ["ALL"], "Admin",
    )
    assert indexed  # no AWS needed in fake mode
    answer = graph_module.run_question("When is the robotics club orientation?")["answer"]
    assert "2026-11-20" in answer
    deleted, unindexed, _ = notices.remove_notice(notice["id"])
    assert deleted and unindexed
    assert not any("Seminar Hall 2" in d.page_content for d in retriever.search("robotics club orientation"))


def test_fake_search_falls_back_to_builtin_docs(monkeypatch):
    monkeypatch.setattr(local_search, "chunk_count", lambda: 0)
    docs = fakes.fake_search("library fine")
    assert docs and docs[0] in fakes.FAKE_DOCS
    assert fakes.fake_search("zzqqxx") == []  # unrelated questions no longer get a random document


# ---------------------------------------------------------------- extractive fake answers


def test_table_rows_become_readable_facts():
    body = "Fees > Academic fee\n| Component | Open | SC / ST |\n|---|---|---|\n| Tuition fee | 80,000 | 20,000 |\n"
    heading, statements = fakes._block_statements(body)
    assert heading == "Fees > Academic fee"
    assert statements == ["Tuition fee - Open: 80,000; SC / ST: 20,000"]


def test_table_without_header_and_notice_metadata_lines():
    _, rows = fakes._block_statements("Fees > Bill\n| SC / ST | 35,000 | 89,000 |\n")
    assert rows == ["SC / ST | 35,000 | 89,000"]
    _, lines = fakes._block_statements("Notice N-1 > Title\nCategory: Exams. For: CSE, ECE.\nLab exams are on Friday.\n")
    assert lines == ["Lab exams are on Friday."]


def test_context_facts_pick_the_matching_statement():
    context = (
        "[1] (XYZ | library | library_rules.md p.2)\nLibrary > Timings\nThe library has 400 seats. "
        "It is open on Sunday from 09:00 to 17:00.\n\n"
        "[2] (XYZ | fees | fee_structure.md p.1)\nFees > Tuition\nTuition is Rs 80,000 per semester.\n"
    )
    facts = fakes._context_facts(context, question="Is the library open on Sunday?")
    assert facts == [(1, "It is open on Sunday from 09:00 to 17:00")]
    assert fakes._context_facts(context, question="zzqqxx", fallback=False) == []
    assert fakes._context_facts("", question="anything") == []


def test_unknown_topic_says_documents_do_not_contain_it(fake_mode):
    result = graph_module.run_question("What is zzqqxx?")
    assert "do not contain" in result["answer"]
    assert result["citations"] == []


def test_fake_load_students_accepts_db_path(fake_mode, tmp_path):
    """Regression: the demo wrapper dropped db_path, so registration with an explicit database crashed."""
    assert student_data.find_student_by_email("nobody@example.com", db_path=tmp_path / "app.db") is None


# ---------------------------------------------------------------- blank and oversized questions


@pytest.mark.parametrize("question", ["", "   ", "\n\t ", None])
def test_blank_question_is_answered_without_any_model_call(fake_mode, question):
    result = graph_module.run_question(question, student_id=SID)
    assert result["answer"] == router.EMPTY_QUESTION_MESSAGE
    assert fakes.CALL_LOG == [] and result["summary"]["llm_calls"] == 0
    assert result["citations"] == []


def test_blank_question_with_forced_route_still_stops(fake_mode):
    result = graph_module.run_question("  ", force_route="deep")
    assert result["answer"] == router.EMPTY_QUESTION_MESSAGE and fakes.CALL_LOG == []


def test_blank_question_stream_only_runs_the_router(fake_mode):
    nodes = [n for chunk in graph_module.graph.stream({"question": " "}, stream_mode="updates") for n in chunk]
    assert nodes == ["router"]


def test_long_question_is_cut_and_whitespace_collapsed(fake_mode):
    long_question = "What is  the library\nfine? " + "x" * 5000
    result = graph_module.run_question(long_question)
    assert len(result["question"]) == router.MAX_QUESTION_CHARS
    assert result["question"].startswith("What is the library fine? x")
    assert router.normalize_question("  a \n b  ") == "a b"


# ---------------------------------------------------------------- pronouns: things vs other people


@pytest.mark.parametrize("question", [
    "What are the hostel rules and their fees?",
    "Supplementary exam kab hai aur uski fees kitni hai?",
    "Tell me about the library and their timings and fine",
    "Which scholarships are there and unki eligibility kya hai?",
])
def test_pronoun_for_a_thing_is_not_another_student(fake_mode, question):
    own = student_data.get_student(SID)
    assert not personal.asks_about_other_student(question, own)
    assert router.heuristic_route(question)[0] != "personal"
    assert graph_module.run_question(question, student_id=SID)["answer"] != personal.REFUSAL_MESSAGE


@pytest.mark.parametrize("question", [
    "What is her attendance?",
    "Uski fees kitni due hai?",
    "What are their exam results?",
    "Mere dost ki hostel fees kitni hai?",
    "Show me another student's CGPA",
])
def test_other_people_are_still_refused(fake_mode, question):
    own = student_data.get_student(SID)
    assert personal.asks_about_other_student(question, own)
    result = graph_module.run_question(question, student_id=SID, force_route="personal")
    assert result["answer"] == personal.REFUSAL_MESSAGE
    assert ("answer", "personal") not in fakes.CALL_LOG


@pytest.mark.parametrize("question", [
    "Make a checklist of every deadline before the end-sem exams",
    "Which scholarships can a student combine?",
    "How do backlogs affect placements?",
    "SC student ke liye hostel ke saath ek saal ka total kharcha kitna hoga?",
])
def test_multi_step_questions_route_deep(question):
    assert router.heuristic_route(question)[0] == "deep"


# ---------------------------------------------------------------- concurrent research


def test_research_runs_sub_questions_concurrently_and_keeps_plan_order(monkeypatch):
    plan = [f"sub question {i}" for i in range(4)]
    seen_threads: set[str] = set()

    def slow_one(question, sub_q, own_record):
        seen_threads.add(threading.current_thread().name)
        time.sleep(0.05 * (4 - plan.index(sub_q)))  # the first sub-question finishes last
        doc = Document(page_content=sub_q, metadata={"source": "x.md", "page": 1})
        usage = {"node": "researcher", "model_id": "m", "input_tokens": 1, "output_tokens": 1, "latency_ms": 1.0}
        return {"sub_question": sub_q, "summary": sub_q, "citations": []}, [doc], usage

    monkeypatch.setattr(deep_research, "_research_one", slow_one)
    monkeypatch.setattr(deep_research, "get_settings",
                        lambda: SimpleNamespace(max_research_loops=0, research_workers=4))
    out = deep_research.research({"question": "q", "plan": plan})
    assert [n["sub_question"] for n in out["notes"]] == plan
    assert [d.page_content for d in out["docs"]] == plan
    assert len(out["usage"]) == 4 and len(seen_threads) > 1


@pytest.mark.parametrize("workers", [0, 1, -3])
def test_research_workers_below_two_run_sequentially(monkeypatch, fake_mode, workers):
    monkeypatch.setattr(deep_research, "get_settings",
                        lambda: SimpleNamespace(max_research_loops=0, research_workers=workers))
    result = graph_module.run_question("Compare CSE vs ECE placements", force_route="deep")
    assert len(result["notes"]) == len(result["plan"]) >= 3


def test_research_with_nothing_pending_makes_no_calls(monkeypatch):
    monkeypatch.setattr(deep_research, "get_settings",
                        lambda: SimpleNamespace(max_research_loops=0, research_workers=4))
    out = deep_research.research({"question": "q", "plan": []})
    assert out["notes"] == [] and out["usage"] == []


def test_parallel_and_sequential_deep_answers_match(monkeypatch, fake_mode):
    answers = []
    for workers in (1, 4):
        monkeypatch.setattr(deep_research, "get_settings",
                            lambda w=workers: SimpleNamespace(max_research_loops=0, research_workers=w))
        answers.append(graph_module.run_question("Compare CSE vs ECE placements", force_route="deep")["answer"])
    assert answers[0] == answers[1]


# ---------------------------------------------------------------- cheap lookups


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)
    return tmp_path / "app.db"


def test_count_users_and_account_email_lookup(db_file):
    assert auth.count_users(db_file) == 0
    assert auth.account_email_for_student("XYZ24CSE001", db_file) is None
    auth.create_user("Aarav@XYZ.edu.example", "password1", "student", "Aarav", "xyz24cse001", db_file)
    auth.create_user("admin@xyz.edu.example", "password1", "admin", "Admin", path=db_file)
    assert auth.count_users(db_file) == 2
    assert auth.account_email_for_student(" xyz24cse001 ", db_file) == "aarav@xyz.edu.example"
    assert auth.account_email_for_student("XYZ99CSE999", db_file) is None
    assert auth.account_email_for_student(None, db_file) is None


def test_all_records_matches_per_student_lookups():
    records = student_data.all_records()
    ids = [r["student_id"] for r in records]
    assert ids == sorted(ids) == [s["student_id"] for s in student_data.list_students()]
    assert records[0] == student_data.get_student(ids[0])
    records[0]["name"] = "changed"  # callers get copies, never the cached record
    assert student_data.get_student(ids[0])["name"] != "changed"
