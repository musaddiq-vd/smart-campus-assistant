"""Offline tests for ingest.py and retriever.py (no AWS: fake embeddings, memory backend)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from smart_campus_assistant import ingest, local_search, models, retriever
from smart_campus_assistant.config import COLLEGE_KEY, COLLEGE_NAME, get_settings

REQUIRED_KEYS = {"college", "doc_type", "source", "page", "chunk_id", "section"}
REAL_DATA_DIR = get_settings().data_dir

LONG = "Tuition fee for B.Tech is Rs 80,000 per semester payable online. " * 30

FEE_MD = f"""# Fee Structure 2026-27

Fees are charged per semester.

## Academic fee per semester

| Component | Open |
|---|---|
| Tuition fee | 80,000 |

## Late fee

A late fee of Rs 100 per day is charged after the due date.

## Long section

{LONG}
"""

RULES_MD = """# Academic Rules

## Attendance

Minimum attendance is 75% in every course. Condonation from 65% to 75%.

## Empty

## Grading

O grade is 10 points.
"""

NOTES_MD = """Some text before any heading, long enough to keep.

# Misc notes

The canteen is open from 08:00 to 20:00.
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "college"
    root.mkdir()
    (root / "fee_structure.md").write_text(FEE_MD, encoding="utf-8")
    (root / "academic_rules.md").write_text(RULES_MD, encoding="utf-8")
    (root / "misc_notes.md").write_text(NOTES_MD, encoding="utf-8")
    (root / "ignored.txt").write_text("not markdown", encoding="utf-8")
    sub = root / "nested"
    sub.mkdir()
    (sub / "hostel_rules.md").write_text("# Hostel\n\nNot loaded: non-recursive.", encoding="utf-8")
    return root


@pytest.fixture
def memory_backend(monkeypatch, data_dir, tmp_path):
    """Memory vector store with fake embeddings and settings (hybrid search) pointing at the temp data."""
    settings = dataclasses.replace(get_settings(), vector_backend="memory", data_dir=data_dir, retriever_k=3,
                                   search_mode="hybrid")
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(models, "get_embeddings", lambda: DeterministicFakeEmbedding(size=64))
    monkeypatch.setenv("SCA_UPLOADS_DIR", str(tmp_path / "uploads"))
    notices_path = tmp_path / "notices.json"
    notices_path.write_text(json.dumps({"notices": []}), encoding="utf-8")
    monkeypatch.setenv("SCA_NOTICES_FILE", str(notices_path))
    clear_store = retriever.get_vector_store.cache_clear  # tests may replace get_vector_store itself
    clear_store()
    local_search.reset_cache()
    yield settings
    clear_store()
    local_search.reset_cache()


# ---------- doc_type map ----------

@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("fee_structure.md", "fees"),
        ("exam_notifications.md", "exams"),
        ("academic_rules.md", "rules"),
        ("hostel_rules.md", "hostel"),
        ("library_rules.md", "library"),
        ("branches_and_programmes.md", "branches"),
        ("scholarships.md", "scholarships"),
        ("placements_2026.md", "placements"),
        ("about_and_admissions.md", "admissions"),
        ("important_dates_and_contacts.md", "calendar"),
        ("fee_structure", "fees"),
        ("misc_notes.md", "other"),
    ],
)
def test_doc_type_mapping(filename, expected):
    assert ingest.doc_type_for(filename) == expected


# ---------- loading and splitting (temp data) ----------

def test_load_markdown_sections(data_dir):
    docs = ingest.load_documents(data_dir)
    sources = {d.metadata["source"] for d in docs}
    # Only top-level *.md files are loaded.
    assert sources == {"fee_structure.md", "academic_rules.md", "misc_notes.md"}
    rules = [d for d in docs if d.metadata["source"] == "academic_rules.md"]
    # The heading-only "Empty" section is dropped; section numbers stay 1-based and contiguous.
    assert [d.metadata["page"] for d in rules] == [1, 2]
    assert rules[0].metadata["section"] == "Academic Rules > Attendance"
    assert rules[1].metadata["section"] == "Academic Rules > Grading"
    notes = [d for d in docs if d.metadata["source"] == "misc_notes.md"]
    assert notes[0].metadata["section"] == "misc_notes"  # text before the first heading


def test_chunk_metadata_contract(data_dir):
    chunks = ingest.split_documents(ingest.load_documents(data_dir))
    assert chunks
    for c in chunks:
        m = c.metadata
        assert set(m) == REQUIRED_KEYS
        assert m["college"] == COLLEGE_KEY
        assert isinstance(m["page"], int) and m["page"] >= 1
        assert "/" not in m["source"] and "\\" not in m["source"] and m["source"].endswith(".md")
        assert m["chunk_id"].startswith(f"{m['source']}:s{m['page']}:c")
        assert c.id == m["chunk_id"]
        assert len(c.page_content) <= ingest.CHUNK_SIZE
        assert "  " not in c.page_content
        # The heading path is both the first line of the text and the "section" metadata.
        assert c.page_content.split("\n", 1)[0] == m["section"]
    assert {c.metadata["doc_type"] for c in chunks} == {"fees", "rules", "other"}


def test_chunks_start_with_heading_path(data_dir):
    chunks = ingest.split_documents(ingest.load_documents(data_dir))
    late = next(c for c in chunks if "Rs 100 per day" in c.page_content)
    assert late.page_content.startswith("Fee Structure 2026-27 > Late fee\n")
    assert late.metadata["doc_type"] == "fees"
    table = next(c for c in chunks if "| Tuition fee | 80,000 |" in c.page_content)
    assert table.page_content.startswith("Fee Structure 2026-27 > Academic fee per semester\n")
    # Every piece of a long section repeats the heading path.
    long_parts = [c for c in chunks if c.page_content.startswith("Fee Structure 2026-27 > Long section")]
    assert len(long_parts) > 1


def test_chunk_ids_unique_and_stable(data_dir):
    first = ingest.split_documents(ingest.load_documents(data_dir))
    second = ingest.split_documents(ingest.load_documents(data_dir))
    ids = [c.metadata["chunk_id"] for c in first]
    assert len(ids) == len(set(ids))
    assert ids == [c.metadata["chunk_id"] for c in second]
    # Section 4 of the fee file ("Long section") splits into c0, c1, ...
    assert "fee_structure.md:s4:c0" in ids and "fee_structure.md:s4:c1" in ids


# ---------- real college data ----------

def test_real_college_docs_split_cleanly():
    files = sorted(p.name for p in REAL_DATA_DIR.glob("*.md"))
    assert 9 <= len(files) <= 11
    chunks = ingest.split_documents(ingest.load_documents(REAL_DATA_DIR))
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))
    assert {c.metadata["source"] for c in chunks} == set(files)
    # Every real file has a known doc_type.
    assert "other" not in {c.metadata["doc_type"] for c in chunks}
    assert all(len(c.page_content) <= ingest.CHUNK_SIZE for c in chunks)
    assert all(c.metadata["college"] == COLLEGE_KEY for c in chunks)


def test_dry_run_prints_counts(monkeypatch, data_dir, capsys):
    settings = dataclasses.replace(get_settings(), data_dir=data_dir)
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    n = ingest.dry_run()
    out = capsys.readouterr().out
    assert n > 0 and f"Dry run: {n} chunks" in out and "fee_structure.md" in out


# ---------- memory backend ----------

def test_memory_ingest_search_and_filter(memory_backend):
    count = ingest.ingest()
    assert count > 0
    store = retriever.get_vector_store()
    assert len(store.store) == count

    # Re-running is idempotent (same ids overwrite).
    assert ingest.ingest() == count
    assert len(store.store) == count

    results = retriever.search("tuition fee")
    assert 0 < len(results) <= memory_backend.retriever_k
    assert all(REQUIRED_KEYS <= set(d.metadata) for d in results)

    only_xyz = retriever.search("attendance at XYZ College", k=10, college=COLLEGE_KEY)
    assert only_xyz and {d.metadata["college"] for d in only_xyz} == {COLLEGE_KEY}
    assert retriever.search("anything", college="other_college") == []


class RecordingStore:
    """Stands in for AmazonS3Vectors: records the call and returns fixed documents."""

    def __init__(self, docs=(), error: Exception | None = None):
        self.docs, self.error, self.calls = list(docs), error, []

    def similarity_search(self, query, k, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        if self.error:
            raise self.error
        return self.docs[:k]


def _use_store(monkeypatch, store, search_mode="hybrid", retriever_k=4):
    settings = dataclasses.replace(get_settings(), vector_backend="s3", retriever_k=retriever_k,
                                   search_mode=search_mode)
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(retriever, "get_vector_store", lambda: store)


def test_s3_filter_syntax(monkeypatch):
    """With the S3 backend, vector mode passes k and a Mongo-style filter to the store unchanged."""
    store = RecordingStore()
    _use_store(monkeypatch, store, search_mode="vector")
    retriever.search("hostel fee", college=COLLEGE_KEY)
    assert store.calls == [{"query": "hostel fee", "k": 4, "filter": {"college": {"$eq": COLLEGE_KEY}}}]


def test_hybrid_asks_the_store_for_more_candidates_with_the_same_filter(monkeypatch):
    store = RecordingStore()
    _use_store(monkeypatch, store)
    results = retriever.search("hostel fee", college=COLLEGE_KEY)
    call = store.calls[0]
    assert call["query"] == "hostel fee" and call["filter"] == {"college": {"$eq": COLLEGE_KEY}}
    assert call["k"] == retriever.ranking.candidate_count(4) > 4
    # BM25 over the real documents still answers when the vector store has nothing.
    assert 0 < len(results) <= 4 and any("hostel" in d.page_content.lower() for d in results)


def test_search_mode_setting(monkeypatch):
    from smart_campus_assistant import config

    monkeypatch.setenv("SEARCH_MODE", " Vector ")
    assert config.search_mode_from_env() == "vector"
    monkeypatch.setenv("SEARCH_MODE", "bogus")
    assert config.search_mode_from_env() == "hybrid"  # a typo never breaks search
    monkeypatch.delenv("SEARCH_MODE")
    assert config.search_mode_from_env() == "hybrid"


# ---------- hybrid search (temp data, fake vector store) ----------

def _chunk(chunks, needle):
    return next(c for c in chunks if needle in c.page_content)


def test_hybrid_fuses_both_rankings_and_drops_stale_copies(memory_backend, monkeypatch):
    chunks = ingest.all_chunks()
    late = _chunk(chunks, "Rs 100 per day")
    canteen = _chunk(chunks, "canteen")  # shares no word with the query: only "semantic" search finds it
    stale = Document(page_content=late.page_content, id="fee_structure.md:s3:c9",
                     metadata={**late.metadata, "chunk_id": "fee_structure.md:s3:c9"})
    store = RecordingStore([canteen, stale, late])
    monkeypatch.setattr(retriever, "get_vector_store", lambda: store)

    results = retriever.search("late fine for each day", k=4)
    ids = [d.metadata["chunk_id"] for d in results]
    # 1st in BM25 and 3rd in vector search: both retrievers like it, so it beats the vector's own 1st pick.
    assert ids[0] == late.metadata["chunk_id"]
    assert ids[1] == canteen.metadata["chunk_id"]  # a vector-only hit still gets a slot
    assert "fee_structure.md:s3:c9" not in ids  # a stale copy of the same text is dropped
    assert len(ids) == 2


def test_hybrid_falls_back_when_one_side_fails(memory_backend, monkeypatch):
    chunks = ingest.all_chunks()
    canteen = _chunk(chunks, "canteen")
    monkeypatch.setattr(retriever, "get_vector_store", lambda: RecordingStore(error=RuntimeError("no AWS")))
    keyword_only = retriever.search("late fee per day")
    assert keyword_only and "Rs 100 per day" in keyword_only[0].page_content

    def broken(*args, **kwargs):
        raise OSError("documents folder missing")

    monkeypatch.setattr(local_search, "keyword_search", broken)
    monkeypatch.setattr(retriever, "get_vector_store", lambda: RecordingStore([canteen]))
    assert retriever.search("late fee per day") == [canteen]

    # Nothing from BM25 and a failing vector store: the vector error surfaces.
    monkeypatch.setattr(retriever, "get_vector_store", lambda: RecordingStore(error=RuntimeError("no AWS")))
    with pytest.raises(RuntimeError, match="no AWS"):
        retriever.search("late fee per day")


def test_hybrid_college_filter_applies_to_both_sides(memory_backend):
    ingest.ingest()
    assert retriever.search("late fee", college="other_college") == []
    hits = retriever.search("late fee", college=COLLEGE_KEY)
    assert hits and {d.metadata["college"] for d in hits} == {COLLEGE_KEY}


# ---------- prune ----------

def test_ingest_prune_removes_vectors_of_an_older_chunking(memory_backend, capsys):
    count = ingest.ingest()
    store = retriever.get_vector_store()
    store.add_documents([Document(page_content="old piece", metadata={"chunk_id": "fee_structure.md:s3:c1"})],
                        ids=["fee_structure.md:s3:c1"])
    assert ingest.ingest(prune_stale=True) == count
    assert "fee_structure.md:s3:c1" not in store.store and len(store.store) == count
    assert "Pruned 1 stale vectors." in capsys.readouterr().out


def test_prune_lists_s3_vectors_page_by_page():
    class Client:
        def __init__(self):
            self.calls = []

        def list_vectors(self, **params):
            self.calls.append(params)
            if "nextToken" not in params:
                return {"vectors": [{"key": "keep:c0"}, {"key": "old:c1"}], "nextToken": "t2"}
            return {"vectors": [{"key": "old:c2"}]}

    class S3Store:
        vector_bucket_name, index_name = "bucket", "index"

        def __init__(self):
            self.client, self.deleted = Client(), []

        def delete(self, ids=None):
            assert ids  # never None/empty: that would drop the whole index
            self.deleted += ids

    store = S3Store()
    assert ingest.prune(store, {"keep:c0"}) == 2
    assert store.deleted == ["old:c1", "old:c2"]
    assert store.client.calls[1]["nextToken"] == "t2" and store.client.calls[0]["indexName"] == "index"
    nothing = S3Store()
    nothing.client.list_vectors = lambda **params: {"vectors": [{"key": "keep:c0"}]}
    assert ingest.prune(nothing, {"keep:c0"}) == 0 and nothing.deleted == []
    assert ingest.prune(object(), {"x"}) == 0  # a store that cannot list its ids is left alone


def test_clean_query_drops_college_name():
    assert retriever.clean_query("XYZ College of Engineering hostel fee", COLLEGE_KEY) == "hostel fee"
    assert retriever.clean_query("XYZ", COLLEGE_KEY) == "XYZ"  # never returns an empty query


# ---------- formatting ----------

def _doc(text: str = "Tuition is Rs 80,000.", page: int | None = 2) -> Document:
    return Document(
        page_content=text,
        metadata={
            "college": COLLEGE_KEY,
            "doc_type": "fees",
            "source": "fee_structure.md",
            "page": page,
            "chunk_id": "fee_structure.md:s2:c0",
        },
    )


def test_format_docs():
    out = retriever.format_docs([_doc(), _doc("Second chunk.", page=3)])
    assert out.startswith(f"[1] ({COLLEGE_NAME} | fees | fee_structure.md p.2)\nTuition is Rs 80,000.")
    assert f"[2] ({COLLEGE_NAME} | fees | fee_structure.md p.3)\nSecond chunk." in out
    assert retriever.format_docs([]) == "No relevant documents found."


def test_to_citation():
    cit = retriever.to_citation(_doc("word " * 100))
    assert cit["source"] == "fee_structure.md"
    assert cit["college"] == COLLEGE_KEY
    assert cit["page"] == 2
    assert len(cit["snippet"]) <= retriever.SNIPPET_CHARS + 3
    assert cit["snippet"].endswith("...")
    short = retriever.to_citation(_doc("Short text.", page=None))
    assert short["snippet"] == "Short text." and short["page"] is None
    assert cit["section"] is None and short["section"] is None  # single-line chunks have no heading


def test_citation_names_the_section_and_quotes_the_body(data_dir):
    chunks = ingest.split_documents(ingest.load_documents(data_dir))
    cit = retriever.to_citation(_chunk(chunks, "Rs 100 per day"))
    assert cit["source"] == "fee_structure.md" and cit["page"] == 3
    assert cit["section"] == "Fee Structure 2026-27 > Late fee"
    assert cit["snippet"] == "A late fee of Rs 100 per day is charged after the due date."  # heading not repeated


def test_citation_section_falls_back_to_the_first_line():
    """Older vectors and notice chunks have no "section" key: their first line is the heading."""
    notice = Document(page_content="Notice N-2026-0001 (2026-11-02) > Library closed\nThe library is closed.",
                      metadata={"source": "notices.json", "college": COLLEGE_KEY, "page": 1})
    cit = retriever.to_citation(notice)
    assert cit["section"] == "Notice N-2026-0001 (2026-11-02) > Library closed"
    assert cit["snippet"] == "The library is closed."
    assert ingest.with_sections([notice])[0].metadata["section"] == cit["section"]


# ---------- heading-level chunking ----------

ATTENDANCE_RULE = " ".join(f"Attendance clause {i}: students must attend at least 75% of classes." for i in range(18))

RULES_LONG_MD = f"""# Academic Rules

## Attendance

{ATTENDANCE_RULE}

## Fees table

| Item | Amount |
|---|---|
""" + "\n".join(f"| Fee item {i} with a long description | Rs {i},000 |" for i in range(60)) + """

## Exam rules

### Hall tickets

```
# not a heading: shell comment inside a code block
```

Hall tickets are released a week before exams.

#### Lost hall ticket

A duplicate hall ticket costs Rs 200 at the Examination Cell.
"""


def test_a_rule_section_stays_in_one_chunk():
    assert 800 < len(ATTENDANCE_RULE) < ingest.CHUNK_SIZE - 50  # used to be cut in two at 800 characters
    docs = ingest.text_documents(RULES_LONG_MD, source="rules.md", doc_type="rules", fallback="rules")
    chunks = ingest.split_documents(docs)
    attendance = [c for c in chunks if c.metadata["section"] == "Academic Rules > Attendance"]
    assert len(attendance) == 1
    assert attendance[0].page_content == f"Academic Rules > Attendance\n{ATTENDANCE_RULE}"


def test_headings_build_the_path_and_code_fences_are_not_headings():
    sections = {doc.metadata["section"]: doc for doc in
                ingest.text_documents(RULES_LONG_MD, source="rules.md", doc_type="rules", fallback="rules")}
    hall = sections["Academic Rules > Exam rules > Hall tickets"]
    assert "# not a heading" in hall.page_content
    # A level-4 heading stays inside its rule instead of starting a new section.
    assert "#### Lost hall ticket" in hall.page_content and "Rs 200" in hall.page_content
    assert list(sections) == ["Academic Rules > Attendance", "Academic Rules > Fees table",
                              "Academic Rules > Exam rules > Hall tickets"]
    assert [d.metadata["page"] for d in sections.values()] == [1, 2, 3]


def test_long_section_is_split_with_overlap_and_repeats_the_table_header():
    docs = ingest.text_documents(RULES_LONG_MD, source="rules.md", doc_type="rules", fallback="rules")
    pieces = [c for c in ingest.split_documents(docs) if c.metadata["section"] == "Academic Rules > Fees table"]
    assert len(pieces) > 1
    assert [c.metadata["chunk_id"] for c in pieces] == [f"rules.md:s2:c{i}" for i in range(len(pieces))]
    for first, second in zip(pieces, pieces[1:], strict=False):
        body = second.page_content.split("\n")
        assert body[0] == "Academic Rules > Fees table"
        assert body[1:3] == ["| Item | Amount |", "|---|---|"]  # header repeated in every piece
        assert body[3] in first.page_content  # overlap: the piece starts with rows the previous one ended with
    assert all(len(c.page_content) <= ingest.CHUNK_SIZE for c in pieces)
    rows = {line for c in pieces for line in c.page_content.split("\n") if line.startswith("| Fee item")}
    assert len(rows) == 60  # nothing lost


def test_long_section_prefers_sub_heading_boundaries():
    sub = "\n\n".join(f"#### Rule {i}\n\n" + f"Rule {i} text sentence. " * 25 for i in range(4))
    docs = ingest.text_documents(f"## Big rules\n\n{sub}", source="big.md", doc_type="rules", fallback="big")
    pieces = ingest.split_documents(docs)
    assert len(pieces) > 1
    assert all(p.page_content.split("\n")[1].startswith("#### Rule") for p in pieces)


def test_plain_text_has_no_headings_and_uses_the_fallback_title():
    text = "# not a heading in a .txt file\n\nThe transport office opens at 08:00 on weekdays."
    docs = ingest.text_documents(text, source="transport.txt", doc_type="uploads", fallback="Transport", plain=True)
    assert len(docs) == 1 and docs[0].metadata["section"] == "Transport"
    assert docs[0].page_content.startswith("# not a heading")
    assert ingest.split_sections("", "Empty") == [("Empty", "")]


def test_real_sections_are_never_cut():
    chunks = ingest.split_documents(ingest.load_documents(REAL_DATA_DIR))
    sections = ingest.load_documents(REAL_DATA_DIR)
    assert len(chunks) == len(sections)  # every real rule fits in one chunk
    assert all(c.metadata["chunk_id"].endswith(":c0") for c in chunks)
