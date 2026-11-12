"""Offline tests for question_log.py (temp database only) and the admin Insights page."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smart_campus_assistant import personal, question_log  # noqa: E402

NO_ANSWER = "The college documents provided do not contain this information."
GOOD_ANSWER = "The library fine is Rs 5 per day [1]."
FORBIDDEN = ("Cost", "cost", "token", "Token", "LLM", "Latency", "latency", "model", "Model")


@pytest.fixture(autouse=True)
def no_real_students(monkeypatch):
    """Name masking must not read the real students file or app database.

    conftest.py switches the log off for the whole suite; these tests use temporary databases,
    so it is switched back on here.
    """
    monkeypatch.setattr(question_log.student_data, "load_students", lambda *a, **k: {})
    monkeypatch.delenv("SCA_QUESTION_LOG", raising=False)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "app.db"


def _raw_rows(path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM question_log ORDER BY id")]
    finally:
        conn.close()


# ---------- privacy ----------

def test_ids_and_emails_are_masked(path):
    question_log.log_question(
        "Is XYZ24CSE001 or xyz26ece009 eligible? Mail Aarav.Sharma@xyz.edu.example or call 9876543210",
        "simple", GOOD_ANSWER, citations=2, role="student", branch="CSE", path=path,
    )
    (row,) = _raw_rows(path)
    assert row["question"] == "Is [id] or [id] eligible? Mail [email] or call [number]"
    stored = " ".join(str(v) for v in row.values()).lower()
    assert "xyz24cse001" not in stored and "xyz26ece009" not in stored and "@" not in stored
    assert row["role"] == "student" and row["branch"] == "CSE" and row["citations"] == 2 and row["answered"] == 1


def test_known_student_names_are_masked(path, monkeypatch):
    monkeypatch.setattr(question_log.student_data, "load_students",
                        lambda *a, **k: {"XYZ24CSE001": {"name": "Aarav Sharma"}})
    assert question_log.mask_question("Does aarav sharma get a hostel room?") == "Does [name] get a hostel room?"


def test_personal_question_text_is_never_stored(path):
    question_log.log_question("Meri fees ka status kya hai? I am XYZ24CSE001", "personal",
                              "Your fee due is Rs 50,000.", role="student", branch="cse", path=path)
    (row,) = _raw_rows(path)
    assert row["question"] == question_log.PERSONAL_PLACEHOLDER
    assert row["topic"] == "fees" and row["route"] == "personal" and row["branch"] == "CSE"
    assert question_log.top_questions(path=path) == [] and question_log.unanswered(path=path) == []
    assert question_log.summary(path)["by_route"] == {"personal": 1}


def test_no_identity_columns_and_safe_role_branch(path):
    question_log.log_question("Hostel fee?", "simple", GOOD_ANSWER, role="admin", branch="CSE", path=path)
    question_log.log_question("Hostel fee?", "weird", GOOD_ANSWER, role="root", branch="aarav@xyz.edu.example", path=path)
    rows = _raw_rows(path)
    assert set(rows[0]) == {"id", "asked_at", "role", "branch", "route", "topic", "question", "answered", "citations"}
    assert (rows[0]["role"], rows[0]["branch"]) == ("admin", "")  # admins have no branch
    assert (rows[1]["role"], rows[1]["branch"], rows[1]["route"]) == ("student", "", "simple")


# ---------- topic ----------

@pytest.mark.parametrize(("question", "topic"), [
    ("What is the tuition fee per semester?", "fees"),
    ("Hostel ka kharcha kitna hai?", "hostel"),
    ("Shulk kab tak jama karna hai?", "fees"),
    ("When do CSE end-semester exams start?", "exams"),
    ("Pariksha kab hai?", "exams"),
    ("What is the hostel curfew time?", "hostel"),
    ("Chhatravas me mess ka menu kya hai?", "hostel"),
    ("What is the library fine per day?", "library"),
    ("Kitni kitabein issue ho sakti hain?", "library"),
    ("What is the minimum attendance to sit the exams?", "attendance"),
    ("Hazri kam ho to kya hoga?", "attendance"),
    ("How is CGPA calculated?", "results"),
    ("Backlog clear karne ka rule kya hai?", "results"),
    ("Compare CSE and ECE placements", "placements"),
    ("Sabse zyada package kis branch ka hai? naukri milegi?", "placements"),
    ("What scholarships does the college offer?", "scholarships"),
    ("Chhatravritti ke liye kaun eligible hai?", "scholarships"),
    ("What is the admission eligibility for B.Tech?", "admissions"),
    ("Where is the canteen?", "other"),
    ("", "other"),
])
def test_topic_detection(question, topic):
    assert question_log.detect_topic(question) == topic
    assert topic in question_log.TOPICS


# ---------- answered ----------

@pytest.mark.parametrize(("answer", "expected"), [
    (GOOD_ANSWER, True),
    (NO_ANSWER, False),
    ("Sorry, the documents DO NOT   contain the bus timings.", False),
    ("The handbook does not contain that rule.", False),
    ("", False),
    ("   ", False),
    (None, False),
    (personal.LOGIN_MESSAGE, False),
    (personal.REFUSAL_MESSAGE, False),
])
def test_answered_detection(answer, expected):
    assert question_log.is_answered(answer) is expected


def test_answered_flag_is_stored(path):
    question_log.log_question("Bus timings?", "simple", NO_ANSWER, path=path)
    question_log.log_question("Library fine?", "simple", GOOD_ANSWER, path=path)
    question_log.log_question("My friend's fees?", "personal", personal.REFUSAL_MESSAGE, path=path)
    assert [r["answered"] for r in _raw_rows(path)] == [0, 1, 0]
    stats = question_log.summary(path)
    assert (stats["total"], stats["answered"], stats["unanswered"]) == (3, 1, 2)


# ---------- grouping ----------

def test_grouping_ignores_case_punctuation_and_whitespace(path):
    for q in ("What are the bus timings?", "what are the  BUS timings", "  What are the bus timings ?!  "):
        question_log.log_question(q, "simple", NO_ANSWER, path=path)
    question_log.log_question("Is there a gym?", "simple", NO_ANSWER, path=path)
    question_log.log_question("What is the library fine per day?", "simple", GOOD_ANSWER, path=path)
    question_log.log_question("What is my fine?", "personal", NO_ANSWER, path=path)

    gaps = question_log.unanswered(path=path)
    assert [(g["question"], g["count"]) for g in gaps] == [("What are the bus timings ?!", 3), ("Is there a gym?", 1)]
    assert all(g["last_asked"] and g["unanswered"] == g["count"] for g in gaps)
    assert question_log.normalize_question("  What's  the FEE?? ") == "what s the fee"

    top = question_log.top_questions(path=path)
    assert top[0]["count"] == 3 and len(top) == 3
    assert {g["question"] for g in top} >= {"What is the library fine per day?"}
    assert question_log.unanswered(limit=1, path=path) == gaps[:1]
    assert question_log.top_questions(limit=0, path=path) == []


# ---------- limits and robustness ----------

def test_question_is_cut_to_300_chars(path):
    question_log.log_question("fee " * 200, "simple", GOOD_ANSWER, path=path)
    assert len(_raw_rows(path)[0]["question"]) <= question_log.MAX_QUESTION_CHARS == 300


def test_row_cap_deletes_the_oldest(path, monkeypatch):
    monkeypatch.setattr(question_log, "MAX_ROWS", 5)
    for i in range(8):
        question_log.log_question(f"Question number {i}", "simple", GOOD_ANSWER, path=path)
    rows = question_log.export_rows(path)
    assert [r["question"] for r in rows] == [f"Question number {i}" for i in range(3, 8)]


@pytest.mark.parametrize("blank", ["", "   ", "\n\t", None])
def test_blank_question_is_ignored(path, blank):
    question_log.log_question(blank, "simple", GOOD_ANSWER, path=path)
    assert question_log.export_rows(path) == [] and question_log.summary(path)["total"] == 0


def test_log_question_never_raises(tmp_path, monkeypatch):
    question_log.log_question("Library fine?", "simple", GOOD_ANSWER, path=tmp_path)  # a folder, not a file
    question_log.log_question("Library fine?", None, None, citations="many", role=None, branch=None,
                              path=tmp_path / "ok.db")
    assert question_log.export_rows(tmp_path / "ok.db")[0]["citations"] == 0

    def boom(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(question_log.db, "transaction", boom)
    question_log.log_question("Library fine?", "simple", GOOD_ANSWER, path=tmp_path / "other.db")


def test_switched_off_log_never_opens_the_database(monkeypatch):
    """With SCA_QUESTION_LOG=0 (set by conftest.py for UI tests) nothing touches the app database."""
    monkeypatch.setenv("SCA_QUESTION_LOG", "0")

    def fail(*args, **kwargs):
        raise AssertionError("the app database must not be written from a test run")

    monkeypatch.setattr(question_log.db, "transaction", fail)
    question_log.log_question("Library fine?", "simple", GOOD_ANSWER)


def test_log_can_be_switched_off(path, monkeypatch):
    monkeypatch.setenv("SCA_QUESTION_LOG", "0")
    question_log.log_question("Library fine?", "simple", GOOD_ANSWER, path=path)
    assert question_log.export_rows(path) == []


# ---------- summary, export, clear ----------

def test_reads_on_an_empty_database(path):
    stats = question_log.summary(path)
    assert (stats["total"], stats["answered"], stats["unanswered"]) == (0, 0, 0)
    assert stats["by_route"] == {} and stats["by_topic"] == {} and len(stats["by_day"]) == 14
    assert question_log.unanswered(path=path) == [] and question_log.top_questions(path=path) == []
    assert question_log.export_rows(path) == [] and question_log.clear(path) == 0


def test_summary_shape(path):
    question_log.log_question("What is the hostel fee?", "simple", GOOD_ANSWER, path=path)
    question_log.log_question("Compare CSE and ECE placements", "deep", GOOD_ANSWER, path=path)
    question_log.log_question("Placement record of ECE?", "simple", NO_ANSWER, path=path)
    question_log.log_question("Meri attendance?", "personal", GOOD_ANSWER, path=path)
    with question_log.db.transaction(path) as conn:  # an old row falls outside the 14-day window
        conn.execute("UPDATE question_log SET asked_at = '2020-01-01T10:00:00+00:00' WHERE route = 'deep'")

    stats = question_log.summary(path)
    assert set(stats) == {"total", "answered", "unanswered", "by_route", "by_topic", "by_day"}
    assert (stats["total"], stats["answered"], stats["unanswered"]) == (4, 3, 1)
    assert stats["by_route"] == {"simple": 2, "deep": 1, "personal": 1}
    assert stats["by_topic"] == {"placements": 2, "hostel": 1, "attendance": 1}
    assert list(stats["by_topic"])[0] == "placements"  # most asked first
    days = stats["by_day"]
    assert len(days) == 14 and [d["day"] for d in days] == sorted(d["day"] for d in days)
    assert all(set(d) == {"day", "total", "unanswered"} for d in days)
    assert days[-1]["total"] == 3 and days[-1]["unanswered"] == 1 and sum(d["total"] for d in days) == 3


def test_export_and_csv(path):
    question_log.log_question("=HYPERLINK(1) fee?", "simple", NO_ANSWER, role="student", branch="ECE", path=path)
    rows = question_log.export_rows(path)
    assert list(rows[0]) == list(question_log.EXPORT_COLUMNS)
    assert rows[0]["answered"] is False and rows[0]["branch"] == "ECE"
    lines = question_log.rows_to_csv(rows).splitlines()
    assert lines[0] == ",".join(question_log.EXPORT_COLUMNS)
    assert ",'=HYPERLINK(1) fee?,no," in lines[1]  # not runnable as a spreadsheet formula
    assert question_log.rows_to_csv([]).splitlines() == [lines[0]]


def test_clear(path):
    for i in range(3):
        question_log.log_question(f"Question {i}", "simple", GOOD_ANSWER, path=path)
    assert question_log.clear(path) == 3
    assert question_log.export_rows(path) == [] and question_log.summary(path)["total"] == 0
    assert question_log.clear(path) == 0


# ---------- the Insights page ----------

PAGE = str(ROOT / "ui" / "admin_insights.py")
ADMIN = {"email": "admin@xyz.edu.example", "role": "admin", "name": "Admin"}


def _page_text(at) -> str:
    parts = [str(m.value) for m in at.markdown] + [str(h.value) for h in at.html]
    parts += [str(x.value) for x in (*at.caption, *at.error, *at.warning, *at.info, *at.success)]
    parts += [str(b.label) for b in at.button]
    return "\n".join(parts)


@pytest.fixture
def page_db(path, monkeypatch):
    """Point the page's question_log calls at the temp database (the app database is never opened)."""
    def to_temp(real):
        def wrapper(*args, **kwargs):
            if not args or not isinstance(args[0], Path):  # the page never passes a path
                kwargs["path"] = path
            return real(*args, **kwargs)

        return wrapper

    for name in ("summary", "unanswered", "top_questions", "export_rows", "clear"):
        monkeypatch.setattr(question_log, name, to_temp(getattr(question_log, name)))
    return path


def _run_page(user=ADMIN):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(PAGE, default_timeout=30)
    at.session_state["user"] = dict(user)
    return at.run()


def test_page_empty_state(page_db):
    at = _run_page()
    assert not at.exception
    text = _page_text(at)
    assert "No questions yet" in text and "Clear log" not in text


def test_page_is_admin_only(page_db):
    at = _run_page({"email": "s@xyz.edu.example", "role": "student", "name": "S", "student_id": "XYZ24CSE001"})
    assert not at.exception and len(at.error) == 1
    assert "Helpdesk insights" not in _page_text(at)


def test_page_shows_gaps_and_clears_after_confirm(page_db):
    for _ in range(2):
        question_log.log_question("What are the <b>bus</b> timings?", "simple", NO_ANSWER, branch="CSE", path=page_db)
    question_log.log_question("What is the library fine per day?", "simple", GOOD_ANSWER, branch="ECE", path=page_db)
    question_log.log_question("Meri fees?", "personal", GOOD_ANSWER, branch="CSE", path=page_db)

    at = _run_page()
    assert not at.exception
    text = _page_text(at)
    for expected in ("Questions asked", "75%", "Unanswered", "Most asked topic", "could not answer", "Publish a notice",
                     "Most asked questions"):
        assert expected in text, expected
    for word in FORBIDDEN:
        assert word not in text, word
    gaps_table = at.dataframe[0].value
    assert list(gaps_table["Question"]) == ["What are the <b>bus</b> timings?"]
    assert list(gaps_table["Times asked"]) == [2]
    assert "(personal question)" not in set(at.dataframe[1].value["Question"])

    at.button(key="ins_clear").click().run()
    assert question_log.summary(page_db)["total"] == 4  # nothing is deleted before the confirm step
    at.button(key="ins_clear_no").click().run()
    assert question_log.summary(page_db)["total"] == 4
    at.button(key="ins_clear").click().run()
    at.button(key="ins_clear_yes").click().run()
    assert not at.exception
    assert question_log.summary(page_db)["total"] == 0
    assert "No questions yet" in _page_text(at)
