"""Offline tests for ingest.py and retriever.py (no AWS: fake embeddings, memory backend)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from smart_campus_assistant import ingest, models, retriever
from smart_campus_assistant.config import COLLEGE_KEY, COLLEGE_NAME, get_settings

REQUIRED_KEYS = {"college", "doc_type", "source", "page", "chunk_id"}
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
def memory_backend(monkeypatch, data_dir):
    """Memory vector store with fake embeddings and settings pointing at the temp data."""
    settings = dataclasses.replace(get_settings(), vector_backend="memory", data_dir=data_dir, retriever_k=3)
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(models, "get_embeddings", lambda: DeterministicFakeEmbedding(size=64))
    retriever.reset_vector_store_cache()
    yield settings
    retriever.reset_vector_store_cache()


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


def test_s3_filter_syntax(monkeypatch):
    """With the S3 backend, search passes a Mongo-style filter to the store."""
    calls = {}

    class FakeStore:
        def similarity_search(self, query, k, filter=None):
            calls.update(query=query, k=k, filter=filter)
            return []

    settings = dataclasses.replace(get_settings(), vector_backend="s3", retriever_k=4)
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(retriever, "get_vector_store", lambda: FakeStore())
    retriever.search("hostel fee", college=COLLEGE_KEY)
    assert calls == {"query": "hostel fee", "k": 4, "filter": {"college": {"$eq": COLLEGE_KEY}}}


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
