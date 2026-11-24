"""Offline tests for feedback.py: anonymous answer ratings in a temporary database."""

from __future__ import annotations

import sqlite3

import pytest

from smart_campus_assistant import feedback, question_log

A1, A2, A3 = "a1" * 16, "b2" * 16, "c3" * 16  # random-looking hex answer ids


@pytest.fixture(autouse=True)
def log_on(monkeypatch):
    """conftest.py switches the Insights data off for the suite; these tests use temporary databases."""
    monkeypatch.setattr(question_log.student_data, "load_students", lambda *a, **k: {})
    monkeypatch.delenv("SCA_QUESTION_LOG", raising=False)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "app.db"


def _raw_rows(path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM answer_feedback ORDER BY id")]
    finally:
        conn.close()


# ---------- privacy ----------

def test_rating_stores_no_identity_and_masks_the_question(path):
    assert feedback.record_rating(A1, 1, "Is XYZ24CSE001 eligible? Mail aarav@xyz.edu.example or call 9876543210",
                                  "simple", role="student", branch="cse", path=path)
    (row,) = _raw_rows(path)
    assert set(row) == {"id", "answer_id", "rated_at", "role", "branch", "route", "topic", "question", "helpful",
                        "comment"}
    assert row["question"] == "Is [id] eligible? Mail [email] or call [number]"
    assert (row["role"], row["branch"], row["route"], row["helpful"], row["comment"]) == ("student", "CSE", "simple", 1, "")
    stored = " ".join(str(v) for v in row.values()).lower()
    assert "xyz24cse001" not in stored and "@" not in stored and "9876543210" not in stored


def test_personal_question_is_only_a_placeholder(path):
    feedback.record_rating(A1, 0, "Meri fees ka status kya hai?", "personal", branch="CSE", path=path)
    (row,) = _raw_rows(path)
    assert row["question"] == question_log.PERSONAL_PLACEHOLDER and row["topic"] == "fees"


def test_admin_has_no_branch_and_odd_values_are_cleaned(path):
    feedback.record_rating(A1, 1, "Hostel fee?", "simple", role="admin", branch="CSE", path=path)
    feedback.record_rating(A2, 1, "Hostel fee?", "weird", role="root", branch="aarav@xyz.edu.example", path=path)
    rows = _raw_rows(path)
    assert (rows[0]["role"], rows[0]["branch"]) == ("admin", "")
    assert (rows[1]["role"], rows[1]["branch"], rows[1]["route"]) == ("student", "", "simple")


def test_comment_is_cleaned_masked_and_cut(path, monkeypatch):
    monkeypatch.setattr(question_log.student_data, "load_students",
                        lambda *a, **k: {"XYZ24CSE001": {"name": "Aarav Sharma"}})
    feedback.record_rating(A1, 0, "Library fine?", "simple", path=path)
    assert feedback.record_comment(A1, "Wrong!\n\tAsk aarav sharma (XYZ24CSE001, aarav@x.example)\x00 " + "x" * 400,
                                   path=path)
    comment = _raw_rows(path)[0]["comment"]
    assert comment.startswith("Wrong! Ask [name] ([id], [email])")
    assert len(comment) <= feedback.MAX_COMMENT_CHARS == 300
    assert "\n" not in comment and "\x00" not in comment and "aarav" not in comment.lower()
    assert feedback.clean_comment(None) == "" and feedback.clean_comment("  ") == ""


# ---------- one rating per answer ----------

def test_changing_your_mind_updates_the_same_row(path):
    feedback.record_rating(A1, 0, "Library fine?", "simple", path=path)
    feedback.record_comment(A1, "It says Rs 5 but the notice says Rs 10", path=path)
    assert feedback.record_rating(A1, 1, "Library fine?", "simple", path=path)
    rows = _raw_rows(path)
    assert len(rows) == 1 and rows[0]["helpful"] == 1
    assert rows[0]["comment"] == ""  # the comment belonged to the earlier thumbs down
    feedback.record_rating(A1, 0, "Library fine?", "simple", path=path)
    assert len(_raw_rows(path)) == 1 and _raw_rows(path)[0]["helpful"] == 0
    # Withdrawing the rating (st.feedback returns None) deletes the row.
    assert feedback.record_rating(A1, None, "Library fine?", "simple", path=path)
    assert _raw_rows(path) == []


def test_comment_only_for_an_existing_thumbs_down(path):
    assert not feedback.record_comment(A1, "nothing rated yet", path=path)
    feedback.record_rating(A1, 1, "Library fine?", "simple", path=path)
    assert not feedback.record_comment(A1, "a comment on a thumbs up", path=path)
    assert _raw_rows(path)[0]["comment"] == ""


@pytest.mark.parametrize("bad_id", ["", "short", "NOT-HEX-" * 4, None, 42, "a" * 65, "../../etc"])
def test_invalid_answer_ids_are_ignored(path, bad_id):
    assert not feedback.record_rating(bad_id, 1, "Library fine?", "simple", path=path)
    assert not feedback.record_comment(bad_id, "x", path=path)
    assert feedback.export_rows(path) == []


@pytest.mark.parametrize("bad_rating", [2, -1, "1", "yes", 0.5])
def test_invalid_ratings_store_nothing(path, bad_rating):
    feedback.record_rating(A1, bad_rating, "Library fine?", "simple", path=path)
    assert feedback.export_rows(path) == []


# ---------- switch and robustness ----------

def test_switched_off_never_opens_the_database(monkeypatch):
    monkeypatch.setenv("SCA_QUESTION_LOG", "0")

    def fail(*args, **kwargs):
        raise AssertionError("the app database must not be written from a test run")

    monkeypatch.setattr(feedback.db, "transaction", fail)
    assert not feedback.record_rating(A1, 1, "Library fine?", "simple")
    assert not feedback.record_comment(A1, "x")


def test_never_raises(tmp_path, monkeypatch):
    assert not feedback.record_rating(A1, 1, "Library fine?", "simple", path=tmp_path)  # a folder, not a file

    def boom(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(feedback.db, "transaction", boom)
    assert not feedback.record_rating(A1, 1, "Library fine?", "simple", path=tmp_path / "x.db")
    assert not feedback.record_comment(A1, "x", path=tmp_path / "x.db")


def test_row_cap_deletes_the_oldest(path, monkeypatch):
    monkeypatch.setattr(feedback, "MAX_ROWS", 3)
    for i in range(5):
        feedback.record_rating(f"{i:032x}", 1, f"Question number {i}", "simple", path=path)
    assert [r["question"] for r in feedback.export_rows(path)] == [f"Question number {i}" for i in (2, 3, 4)]


# ---------- summary, recent, export, clear ----------

def test_reads_on_an_empty_database(path):
    stats = feedback.summary(path)
    assert stats == {"total": 0, "helpful": 0, "not_helpful": 0, "with_comment": 0, "by_route": {}, "by_topic": {}}
    assert feedback.recent_unhelpful(path=path) == [] and feedback.export_rows(path) == []
    assert feedback.clear(path) == 0


def test_summary_and_recent_unhelpful(path):
    feedback.record_rating(A1, 1, "What is the hostel fee?", "simple", path=path)
    feedback.record_rating(A2, 0, "What is the hostel curfew?", "simple", path=path)
    feedback.record_comment(A2, "The time is wrong", path=path)
    feedback.record_rating(A3, 0, "Compare CSE and ECE placements", "deep", path=path)
    stats = feedback.summary(path)
    assert (stats["total"], stats["helpful"], stats["not_helpful"], stats["with_comment"]) == (3, 1, 2, 1)
    assert stats["by_route"] == {"simple": {"total": 2, "helpful": 1}, "deep": {"total": 1, "helpful": 0}}
    assert stats["by_topic"] == {"hostel": {"total": 2, "helpful": 1}, "placements": {"total": 1, "helpful": 0}}
    recent = feedback.recent_unhelpful(path=path)
    assert [r["question"] for r in recent] == ["Compare CSE and ECE placements", "What is the hostel curfew?"]
    assert recent[1]["comment"] == "The time is wrong" and recent[0]["comment"] == ""
    assert feedback.recent_unhelpful(limit=1, path=path) == recent[:1]
    assert feedback.recent_unhelpful(limit=-3, path=path) == []


def test_export_csv_and_clear(path):
    feedback.record_rating(A1, 0, "=HYPERLINK(1) fee?", "simple", branch="ECE", path=path)
    feedback.record_comment(A1, "@SUM(1) is wrong", path=path)
    rows = feedback.export_rows(path)
    assert list(rows[0]) == list(feedback.EXPORT_COLUMNS) and rows[0]["helpful"] is False
    lines = feedback.rows_to_csv(rows).splitlines()
    assert lines[0] == ",".join(feedback.EXPORT_COLUMNS)
    assert ",'=HYPERLINK(1) fee?,no,'@SUM(1) is wrong" in lines[1]  # not runnable as spreadsheet formulas
    assert "answer_id" not in lines[0] and A1 not in lines[1]  # the random id is never exported
    assert feedback.clear(path) == 1 and feedback.export_rows(path) == []
