"""Admin document uploads (documents.py) and the admin "Documents" page (ui/admin_documents.py). Offline."""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from smart_campus_assistant import documents, ingest, local_search, models, retriever  # noqa: E402
from smart_campus_assistant.config import get_settings  # noqa: E402
from tests.test_ui_smoke import ACCOUNTS, backend  # noqa: E402,F401  (fixture re-used)

PAGE = str(ROOT / "ui" / "admin_documents.py")

TRANSPORT_MD = b"""# Transport Policy 2026-27

College buses leave the main gate at 17:15 on weekdays.

## Bus pass

A semester bus pass costs Rs 4,200 and is issued by the Transport Office in Block C.

## Lost bus pass

A duplicate zorblax pass costs Rs 300.
"""


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Temp college folder, uploads folder and notices file; memory vector store with fake embeddings."""
    college = tmp_path / "college"
    college.mkdir()
    (college / "fee_structure.md").write_text("# Fees\n\n## Late fee\n\nA late fee of Rs 100 per day is charged.\n",
                                              encoding="utf-8")
    notices_path = tmp_path / "notices.json"
    notices_path.write_text(json.dumps({"notices": []}), encoding="utf-8")
    settings = dataclasses.replace(get_settings(), vector_backend="memory", data_dir=college, retriever_k=3,
                                   search_mode="hybrid")
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(models, "get_embeddings", lambda: DeterministicFakeEmbedding(size=64))
    monkeypatch.setenv("SCA_UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SCA_NOTICES_FILE", str(notices_path))
    clear_store = retriever.get_vector_store.cache_clear
    clear_store()
    local_search.reset_cache()
    yield types.SimpleNamespace(college=college, uploads=tmp_path / "uploads")
    clear_store()
    local_search.reset_cache()


# ---------- names and validation ----------

@pytest.mark.parametrize(("filename", "expected"), [
    ("Hostel Rules (2027).MD", "hostel_rules_2027.md"),
    ("../../etc/evil.md", "evil.md"),
    ("C:\\Users\\x\\Bus  Timings.txt", "bus_timings.txt"),
    ("Policy.pdf", "policy.md"),
])
def test_stored_name_is_a_safe_slug(filename, expected):
    assert documents.stored_name(filename) == expected


def test_stored_name_needs_letters():
    with pytest.raises(documents.DocumentError, match="no letters"):
        documents.stored_name("???.md")
    assert documents.title_for("hostel_rules_2027.md") == "Hostel rules 2027"


@pytest.mark.parametrize(("filename", "data", "match"), [
    ("virus.exe", b"MZ....", "Only .md, .txt"),
    ("notes.docx", b"PK..", "Only"),
    ("noext", b"hello world, long enough text", "no extension"),
    ("empty.md", b"", "empty"),
    ("big.md", b"a" * (documents.MAX_UPLOAD_BYTES + 1), "too large"),
    ("binary.txt", b"\x00\x01\x02 hello world binary data here", "text file"),
    ("headings.md", b"# Title\n\n## Only headings\n", "No readable text"),
    ("long.txt", b"word " * (documents.MAX_TEXT_CHARS // 4), "too long"),
])
def test_read_upload_rejects_bad_files(monkeypatch, filename, data, match):
    monkeypatch.setattr(documents, "pdf_supported", lambda: False)
    with pytest.raises(documents.DocumentError, match=match):
        documents.read_upload(filename, data)


def test_read_upload_cleans_text_but_keeps_markdown():
    raw = ("\ufeff# Rules\r\n\r\n<script>alert(1)</script>CGPA < 6 and > 4 is <b>probation</b>.\r\n"
           "<!-- hidden -->\t| A | B |\r\n|---|---|\r\n\r\n\r\n\r\nEnd &amp; more\x07.").encode("utf-8")
    name, text = documents.read_upload("Rules.MD", raw)
    assert name == "rules.md"
    assert text == "# Rules\n\nalert(1)CGPA < 6 and > 4 is probation.\n | A | B |\n|---|---|\n\nEnd & more."


def test_read_upload_decodes_other_encodings():
    assert documents.read_upload("a.txt", "Café timings are 08:00 to 20:00.".encode("cp1252"))[1].startswith("Café")
    assert "Café" in documents.read_upload("b.txt", "Café opens at 08:00 daily.".encode("utf-16"))[1]


def test_allowed_types_follow_pypdf(monkeypatch):
    monkeypatch.setattr(documents, "pdf_supported", lambda: False)
    assert documents.allowed_types() == ["md", "txt"]
    with pytest.raises(documents.DocumentError, match="Only .md, .txt"):
        documents.read_upload("x.pdf", b"%PDF-1.7 ...")
    monkeypatch.setattr(documents, "pdf_supported", lambda: True)
    assert documents.allowed_types() == ["md", "txt", "pdf"]


# ---------- PDF (pypdf replaced by a small fake: it is optional) ----------

def _fake_pypdf(monkeypatch, pages, encrypted=False, broken=False):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class PdfReader:
        def __init__(self, stream):
            if broken:
                raise ValueError("bad xref")
            self.pages = [Page(t) for t in pages]
            self.is_encrypted = encrypted

        def decrypt(self, password):
            return 0

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=PdfReader))
    monkeypatch.setattr(documents, "pdf_supported", lambda: True)


def test_pdf_becomes_markdown_with_one_section_per_page(monkeypatch):
    _fake_pypdf(monkeypatch, ["Bus rules apply to all students.\n# 1 not a heading", "", "Passes cost Rs 4,200 each."])
    name, text = documents.read_upload("Transport Rules.pdf", b"%PDF-1.7 fake")
    assert name == "transport_rules.md"
    assert text.startswith("# Transport rules\n\n## Page 1\n\nBus rules apply to all students.\n1 not a heading")
    assert "## Page 3\n\nPasses cost Rs 4,200 each." in text and "## Page 2" not in text  # empty page skipped
    sections = [d.metadata["section"] for d in documents._text_documents(text, name)]
    assert sections == ["Transport rules > Page 1", "Transport rules > Page 3"]


@pytest.mark.parametrize(("kwargs", "data", "match"), [
    ({"encrypted": True}, b"%PDF-1.7", "password-protected"),
    ({"broken": True}, b"%PDF-1.7", "could not be read"),
    ({}, b"not a pdf at all", "not a valid PDF"),
    ({"pages": ["", "  "]}, b"%PDF-1.7", "No readable text"),
])
def test_pdf_errors(monkeypatch, kwargs, data, match):
    _fake_pypdf(monkeypatch, kwargs.pop("pages", ["Some text that is long enough."]), **kwargs)
    with pytest.raises(documents.DocumentError, match=match):
        documents.read_upload("x.pdf", data)


# ---------- save / list / delete ----------

def test_save_list_and_delete(corpus):
    info = documents.save_document("Transport Policy.md", TRANSPORT_MD)
    assert info["name"] == "transport_policy.md" and info["sections"] == 3 and info["chunks"] == 3
    assert (corpus.uploads / "transport_policy.md").is_file()
    assert [d["name"] for d in documents.list_documents()] == ["transport_policy.md"]
    with pytest.raises(documents.DocumentError, match="already uploaded"):
        documents.save_document("transport_policy.md", TRANSPORT_MD)
    assert documents.delete_document("transport_policy.md") is True
    assert documents.delete_document("transport_policy.md") is False
    assert documents.list_documents() == []
    assert not list(corpus.uploads.glob("*.tmp"))


def test_college_documents_cannot_be_replaced_or_deleted(corpus):
    with pytest.raises(documents.DocumentError, match="built-in college document"):
        documents.save_document("Fee_Structure.txt", b"Fees are waived for everyone from today.")
    for name in ("fee_structure.md", "../college/fee_structure.md", str(corpus.college / "fee_structure.md")):
        assert documents.delete_document(name) is False
    assert (corpus.college / "fee_structure.md").is_file()
    assert documents.college_documents() == [{"name": "fee_structure.md", "title": "Fee structure",
                                              "sections": 1, "chunks": 1}]


def test_document_limit(corpus, monkeypatch):
    monkeypatch.setattr(documents, "MAX_DOCUMENTS", 1)
    documents.save_document("one.txt", b"The first uploaded document text.")
    with pytest.raises(documents.DocumentError, match="Delete one first"):
        documents.save_document("two.txt", b"The second uploaded document text.")


def test_upload_chunks_match_college_chunking(corpus):
    documents.save_document("Transport Policy.md", TRANSPORT_MD)
    chunks = documents.document_chunks("transport_policy.md")
    assert [c.metadata["chunk_id"] for c in chunks] == [f"transport_policy.md:s{i}:c0" for i in (1, 2, 3)]
    assert {c.metadata["doc_type"] for c in chunks} == {"uploads"}
    assert chunks[1].page_content.startswith("Transport Policy 2026-27 > Bus pass\n")
    assert [c.metadata["chunk_id"] for c in ingest.all_chunks() if c.metadata["source"] == "transport_policy.md"] == \
        [c.metadata["chunk_id"] for c in chunks]
    cit = retriever.to_citation(chunks[2])
    assert cit["source"] == "transport_policy.md" and cit["section"] == "Transport Policy 2026-27 > Lost bus pass"
    assert documents.document_chunks("missing.md") == []


# ---------- search index ----------

def test_upload_is_searchable_at_once_offline_and_gone_after_delete(corpus):
    assert local_search.search("zorblax pass") == []
    info, indexed, message = documents.publish_document("Transport Policy.md", TRANSPORT_MD, live=False)
    assert indexed and "Searchable" in message
    hits = local_search.search("zorblax pass")
    assert hits and hits[0].metadata["source"] == "transport_policy.md"
    deleted, unindexed, _ = documents.remove_document(info["name"], live=False)
    assert deleted and unindexed
    assert local_search.search("zorblax pass") == []


def test_offline_index_notices_a_file_change_without_a_reset(corpus):
    corpus.uploads.mkdir()
    (corpus.uploads / "canteen.txt").write_text("The canteen serves quokkaburgers on Friday.", encoding="utf-8")
    assert local_search.search("quokkaburgers")  # the cache signature includes the uploads folder


def test_live_upload_adds_and_removes_vectors(corpus):
    store = retriever.get_vector_store()
    info, indexed, message = documents.publish_document("Transport Policy.md", TRANSPORT_MD, live=True)
    assert indexed and "3 chunks" in message
    assert set(store.store) == {f"transport_policy.md:s{i}:c0" for i in (1, 2, 3)}
    hits = retriever.search("zorblax duplicate pass")  # hybrid: vector + BM25
    assert hits and hits[0].metadata["chunk_id"] == "transport_policy.md:s3:c0"
    deleted, unindexed, _ = documents.remove_document(info["name"], live=True)
    assert deleted and unindexed and store.store == {}
    assert documents.remove_document(info["name"])[0] is False


def test_vector_failures_never_crash(corpus, monkeypatch):
    def broken():
        raise RuntimeError("no AWS")

    monkeypatch.setattr(retriever, "get_vector_store", broken)
    info, indexed, message = documents.publish_document("Transport Policy.md", TRANSPORT_MD, live=True)
    assert not indexed and "vector index failed: RuntimeError" in message
    assert local_search.search("zorblax pass")  # still saved and keyword-searchable
    deleted, unindexed, message = documents.remove_document(info["name"], live=True)
    assert deleted and not unindexed and "failed" in message
    assert documents.list_documents() == []
    assert documents.unindex_document("x", []) == (True, "Removed from search.")  # never delete(ids=[])


# ---------- page ----------

def _admin_page(demo: bool = True) -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.session_state["user"] = dict(ACCOUNTS["admin@xyz.edu.example"][1])
    at.session_state["demo_mode"] = demo
    return at.run()


def _text(at: AppTest) -> str:
    return "\n".join(str(e.value) for e in (*at.markdown, *at.caption, *at.error, *at.warning, *at.success))


def _upload(at: AppTest, name: str, data: bytes) -> AppTest:
    at.file_uploader[0].set_value((name, data, "text/markdown"))
    at.run()
    at.button(key="doc_upload").click().run()
    return at


def test_admin_page_upload_and_delete(backend, corpus):  # noqa: F811
    at = _admin_page()
    assert not at.exception
    text = _text(at)
    assert "Upload a document" in text and "No uploaded documents yet" in text
    assert not any(b.key and b.key.startswith("doc_del_") for b in at.button)  # college docs: no delete

    _upload(at, "Transport Policy.md", TRANSPORT_MD)
    assert not at.exception
    assert at.success and "Uploaded transport_policy.md" in at.success[0].value
    assert "Transport policy" in _text(at) and "3 sections" in _text(at)
    assert at.file_uploader[0].value is None  # the picker is emptied for the next file
    assert local_search.search("zorblax pass")[0].metadata["source"] == "transport_policy.md"

    # Delete needs a confirm step.
    at.button(key="doc_del_transport_policy.md").click().run()
    assert at.warning and "cannot be undone" in at.warning[0].value
    at.button(key="doc_no_transport_policy.md").click().run()
    assert documents.list_documents() and not at.warning
    at.button(key="doc_del_transport_policy.md").click().run()
    at.button(key="doc_yes_transport_policy.md").click().run()
    assert not at.exception
    assert documents.list_documents() == [] and "Deleted transport_policy.md" in at.success[0].value
    assert local_search.search("zorblax pass") == []


def test_admin_page_shows_validation_errors(backend, corpus):  # noqa: F811
    at = _upload(_admin_page(), "headings.md", b"# Title\n\n## Only headings\n")
    assert not at.exception
    assert at.error and "No readable text" in at.error[0].value
    assert documents.list_documents() == []
    assert at.file_uploader[0].value is not None  # a rejected file stays chosen so it can be retried
    at.file_uploader[0].clear()
    at.run()
    at.button(key="doc_upload").click().run()
    assert at.warning and "Choose a file first" in at.warning[0].value


def test_admin_page_live_index_failure_is_a_warning(backend, corpus, monkeypatch):  # noqa: F811
    monkeypatch.setattr(documents, "index_document", lambda name: (False, "Keyword search has it, but X failed."))
    at = _upload(_admin_page(demo=False), "Transport Policy.md", TRANSPORT_MD)
    assert not at.exception
    assert at.warning and "Keyword search has it" in at.warning[0].value
    assert [d["name"] for d in documents.list_documents()] == ["transport_policy.md"]


@pytest.mark.parametrize("who", [None, "aarav@xyz.edu.example"])
def test_admin_page_guard(backend, corpus, who):  # noqa: F811
    at = AppTest.from_file(PAGE, default_timeout=30)
    if who:
        at.session_state["user"] = dict(ACCOUNTS[who][1])
    at.run()
    assert at.error and "do not have access" in at.error[0].value
    assert "Upload a document" not in _text(at)
    assert not at.button and not at.file_uploader
