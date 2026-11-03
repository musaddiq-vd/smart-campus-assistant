"""Offline tests for admin notices (notices.py, ingest integration, ui/admin_notices.py)."""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from smart_campus_assistant import ingest, models, notices, retriever  # noqa: E402
from smart_campus_assistant.config import get_settings  # noqa: E402
from tests.test_ui_smoke import ACCOUNTS, backend  # noqa: E402,F401  (fixture re-used)

PAGE = str(ROOT / "ui" / "admin_notices.py")
HOME = str(ROOT / "ui" / "student_home.py")
REAL_NOTICES = ROOT / "data" / "notices.json"

SEED = {
    "notices": [
        {"id": "N-2026-0001", "title": "Library closed on Diwali", "category": "library", "branches": ["ALL"],
         "body": "The Central Library is closed on 2026-11-08 for Diwali.", "published_at": "2026-11-02",
         "author": "XYZ Admin Office"},
        {"id": "N-2026-0002", "title": "CSE lab practicals", "category": "exams", "branches": ["CSE"],
         "body": "CSE lab practical exams are on 2026-11-26 in the department labs.", "published_at": "2026-11-03",
         "author": "XYZ Admin Office"},
    ]
}


@pytest.fixture
def notices_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "notices.json"
    path.write_text(json.dumps(SEED), encoding="utf-8")
    monkeypatch.setenv("SCA_NOTICES_FILE", str(path))
    return path


@pytest.fixture
def memory_store(monkeypatch, tmp_path):
    """Memory vector store with fake embeddings; ingest reads an empty docs folder."""
    data_dir = tmp_path / "college"
    data_dir.mkdir()
    (data_dir / "fee_structure.md").write_text("# Fees\n\n## Tuition\n\nTuition is Rs 80,000 per semester.\n",
                                               encoding="utf-8")
    settings = dataclasses.replace(get_settings(), vector_backend="memory", data_dir=data_dir, retriever_k=3)
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(models, "get_embeddings", lambda: DeterministicFakeEmbedding(size=64))
    retriever.reset_vector_store_cache()
    yield retriever.get_vector_store()
    retriever.reset_vector_store_cache()


# ---------- storage ----------

def test_seed_file_is_valid():
    data = json.loads(REAL_NOTICES.read_text(encoding="utf-8"))
    assert len(data["notices"]) >= 2
    for n in data["notices"]:
        assert set(n) == {"id", "title", "category", "branches", "body", "published_at", "author"}
        notices.validate(n["title"], n["body"], n["category"], n["branches"])
        date.fromisoformat(n["published_at"])


def test_list_newest_first_and_branch_filter(notices_file):
    assert [n["id"] for n in notices.list_notices()] == ["N-2026-0002", "N-2026-0001"]
    assert [n["id"] for n in notices.list_notices(branch="CSE")] == ["N-2026-0002", "N-2026-0001"]
    assert [n["id"] for n in notices.list_notices(branch="ece")] == ["N-2026-0001"]
    assert len(notices.list_notices(limit=1)) == 1


def test_add_and_delete(notices_file):
    n = notices.add_notice("  Mid-sem exam <b>rescheduled</b> ", "<script>x</script>The ME mid-sem is moved to 2026-11-20.",
                           "exams", ["me", "ce"], "XYZ Admin Office", today=date(2026, 11, 4))
    assert n["id"] == "N-2026-0003"
    assert n["title"] == "Mid-sem exam rescheduled"
    assert "<" not in n["body"] and n["body"].endswith("2026-11-20.")
    assert n["branches"] == ["ME", "CE"] and n["published_at"] == "2026-11-04"
    stored = json.loads(notices_file.read_text(encoding="utf-8"))["notices"]
    assert stored[-1]["id"] == "N-2026-0003"
    assert notices.list_notices()[0]["id"] == "N-2026-0003"
    assert [x["id"] for x in notices.list_notices(branch="ME")] == ["N-2026-0003", "N-2026-0001"]
    assert notices.delete_notice("N-2026-0003") is True
    assert notices.delete_notice("N-2026-0003") is False
    assert [x["id"] for x in notices.list_notices()] == ["N-2026-0002", "N-2026-0001"]
    assert not list(notices_file.parent.glob("*.tmp"))


def test_missing_file_and_all_branches(tmp_path, monkeypatch):
    monkeypatch.setenv("SCA_NOTICES_FILE", str(tmp_path / "sub" / "notices.json"))
    assert notices.list_notices() == []
    n = notices.add_notice("Hostel fee deadline extended", "Hostel fee can be paid till 2026-11-30.", "hostel",
                           ["CSE", "ECE", "ME", "CE"], "Admin", today=date(2026, 11, 3))
    assert n["id"] == "N-2026-0001" and n["branches"] == ["ALL"]


@pytest.mark.parametrize(
    ("title", "body", "category", "branches", "match"),
    [
        ("Hey", "A long enough body text.", "exams", ["ALL"], "Title"),
        ("x" * 121, "A long enough body text.", "exams", ["ALL"], "Title"),
        ("<b></b>    ", "A long enough body text.", "exams", ["ALL"], "Title"),
        ("Valid title", "short", "exams", ["ALL"], "Notice text"),
        ("Valid title", "y" * 4001, "exams", ["ALL"], "Notice text"),
        ("Valid title", "A long enough body text.", "sports", ["ALL"], "category"),
        ("Valid title", "A long enough body text.", "exams", [], "branch"),
        ("Valid title", "A long enough body text.", "exams", ["IT"], "Unknown branch"),
    ],
)
def test_validation_errors(notices_file, title, body, category, branches, match):
    with pytest.raises(notices.NoticeError, match=match):
        notices.add_notice(title, body, category, branches, "Admin")
    assert len(notices.list_notices()) == 2


# ---------- documents / index ----------

def test_notice_documents_metadata():
    long = {**SEED["notices"][0], "body": "Word " * 700}
    docs = notices.notice_documents([SEED["notices"][1], long])
    first = docs[0]
    assert first.page_content.startswith("Notice N-2026-0002 (2026-11-03) > CSE lab practicals\n")
    assert first.metadata == {"college": "xyz", "doc_type": "notices", "source": "notices.json", "page": 1,
                              "chunk_id": "notices.json:N-2026-0002:c0"}
    assert first.id == first.metadata["chunk_id"]
    rest = docs[1:]
    assert len(rest) > 1 and len(rest) <= notices.MAX_CHUNKS
    assert [d.metadata["chunk_id"] for d in rest] == [f"notices.json:N-2026-0001:c{i}" for i in range(len(rest))]
    assert all(d.metadata["page"] == 2 for d in rest)
    assert all(d.page_content.startswith("Notice N-2026-0001 (2026-11-02) > ") for d in rest)
    assert all(len(d.page_content) <= notices.CHUNK_SIZE for d in docs)
    assert ingest.doc_type_for("notices.json") == "notices"


def test_index_search_and_unindex(notices_file, memory_store):
    n, indexed, msg = notices.publish_notice("Hostel fee deadline extended",
                                             "The hostel fee deadline is extended to 2026-11-30 for all students.",
                                             "fees", ["ALL"], "Admin")
    assert indexed and "Indexed" in msg
    assert notices.index_notice(n)[0]  # idempotent
    assert set(memory_store.store) == {f"notices.json:{n['id']}:c0"}
    hits = retriever.search("Hostel fee deadline extended", college="xyz")
    assert hits and hits[0].metadata["chunk_id"] == f"notices.json:{n['id']}:c0"
    deleted, unindexed, _ = notices.remove_notice(n["id"])
    assert deleted and unindexed
    assert memory_store.store == {}
    assert notices.remove_notice(n["id"])[0] is False


def test_add_survives_index_failure(notices_file, monkeypatch):
    def broken():
        raise RuntimeError("no AWS")

    monkeypatch.setattr(retriever, "get_vector_store", broken)
    n, indexed, msg = notices.publish_notice("Library open late", "Library open till midnight this week.",
                                             "library", "ALL", "Admin")
    assert not indexed and "failed" in msg
    assert notices.get_notice(n["id"])
    deleted, unindexed, msg = notices.remove_notice(n["id"])
    assert deleted and not unindexed and "failed" in msg
    assert notices.get_notice(n["id"]) is None


def test_ingest_includes_notices(notices_file, memory_store):
    count = ingest.ingest()
    ids = set(memory_store.store)
    assert len(ids) == count
    assert {"notices.json:N-2026-0001:c0", "notices.json:N-2026-0002:c0"} <= ids
    assert any(i.startswith("fee_structure.md:") for i in ids)
    assert ingest.ingest() == count and len(memory_store.store) == count
    assert ingest.dry_run() == count


# ---------- UI ----------

def _admin_page() -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.session_state["user"] = dict(ACCOUNTS["admin@xyz.edu.example"][1])
    return at.run()


def _text(at: AppTest) -> str:
    return "\n".join(str(e.value) for e in (*at.markdown, *at.caption, *at.error, *at.warning, *at.success))


def test_admin_page_publish_and_delete(backend, notices_file, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(notices, "index_notice", lambda n: (calls.append(("index", n["id"])) or (True, "Indexed for search.")))
    monkeypatch.setattr(notices, "unindex_notice", lambda i: (calls.append(("unindex", i)) or (True, "Removed from search.")))
    at = _admin_page()
    assert not at.exception
    text = _text(at)
    assert "Published notices" in text and "CSE lab practicals" in text and "Library closed on Diwali" in text

    # Invalid input shows an error and saves nothing.
    at.text_input(key="notice_title").input("Hi")
    at.text_area(key="notice_body").input("A long enough body text.")
    at.button(key="notice_publish").click().run()
    assert at.error and "Title" in at.error[0].value
    assert len(notices.list_notices()) == 2

    at.text_input(key="notice_title").input("Mid-sem exam rescheduled")
    at.text_area(key="notice_body").input("The ME mid-sem exam is moved to 2026-11-20.")
    at.selectbox(key="notice_category").select("exams")
    at.multiselect(key="notice_branches").set_value(["ME"])
    at.run()
    assert "Mid-sem exam rescheduled" in _text(at)  # preview
    at.button(key="notice_publish").click().run()
    assert not at.exception
    new_id = notices.list_notices()[0]["id"]
    assert notices.list_notices()[0]["branches"] == ["ME"]
    assert calls == [("index", new_id)]
    assert at.success and "Indexed for search" in at.success[0].value
    assert at.text_input(key="notice_title").value == ""  # form cleared

    # Delete needs a confirm step.
    at.button(key=f"notice_del_{new_id}").click().run()
    assert notices.get_notice(new_id) and at.warning
    at.button(key=f"notice_no_{new_id}").click().run()
    assert notices.get_notice(new_id) and not at.warning
    at.button(key=f"notice_del_{new_id}").click().run()
    at.button(key=f"notice_yes_{new_id}").click().run()
    assert not at.exception
    assert notices.get_notice(new_id) is None
    assert calls[-1] == ("unindex", new_id)
    assert "Removed from search" in at.success[0].value


def test_admin_page_warns_when_not_indexed(backend, notices_file, monkeypatch):  # noqa: F811
    monkeypatch.setattr(notices, "index_notice", lambda n: (False, "Saved, but search indexing failed: RuntimeError."))
    at = _admin_page()
    at.text_input(key="notice_title").input("Hostel fee deadline extended")
    at.text_area(key="notice_body").input("Hostel fee can be paid till 2026-11-30.")
    at.button(key="notice_publish").click().run()
    assert at.warning and "not searchable" in at.warning[0].value
    assert len(notices.list_notices()) == 3


@pytest.mark.parametrize("who", [None, "aarav@xyz.edu.example"])
def test_admin_page_guard(backend, notices_file, who):  # noqa: F811
    at = AppTest.from_file(PAGE, default_timeout=30)
    if who:
        at.session_state["user"] = dict(ACCOUNTS[who][1])
    at.run()
    assert at.error and "do not have access" in at.error[0].value
    text = _text(at)
    assert "Published notices" not in text and "CSE lab practicals" not in text
    assert not at.button


def test_student_home_shows_branch_notices(backend, notices_file):  # noqa: F811
    at = AppTest.from_file(HOME, default_timeout=30)
    at.session_state["user"] = dict(ACCOUNTS["aarav@xyz.edu.example"][1])
    at.run()
    assert not at.exception
    text = _text(at)
    assert "Latest notices" in text and "CSE lab practicals" in text and "Library closed on Diwali" in text
