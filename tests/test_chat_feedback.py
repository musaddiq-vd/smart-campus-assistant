"""The Ask page: answer ratings, the model usage row of every question, and source card numbers.

Runs the real page with the stubbed backend of test_ui_smoke. The app database is redirected to
a temporary file, so the Insights tables can be switched on without touching data/app.db.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

# router is imported here, with the real modules, before any test stubs them (run_metrics uses its rule).
from smart_campus_assistant import chat_store, db, feedback, question_log, router, run_metrics  # noqa: F401
from tests.test_ui_smoke import (  # noqa: F401  (backend is a fixture)
    ACCOUNTS,
    APP,
    BrokenGraph,
    all_text,
    as_admin,
    as_student,
    backend,
)
from ui import components

STUDENT = "aarav@xyz.edu.example"
FINE = "What is the library fine per day?"


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    """Every app-database access without an explicit path goes to a temporary file."""
    path = tmp_path / "app.db"
    real_db_path = db.db_path
    monkeypatch.setattr(db, "db_path", lambda p=None: real_db_path(p) if p else Path(path))
    return path


@pytest.fixture
def insights_on(app_db, monkeypatch):
    """conftest.py switches the anonymous Insights tables off for UI tests; these tests switch them on."""
    monkeypatch.delenv("SCA_QUESTION_LOG", raising=False)
    return app_db


def answer_id(at: AppTest, index: int = -1) -> str:
    return at.session_state["messages"][index]["result"]["answer_id"]


def refreshed() -> AppTest:
    """A browser refresh: a new Streamlit session for the same signed-in student."""
    fresh = AppTest.from_file(APP, default_timeout=30)
    fresh.session_state["user"] = dict(ACCOUNTS[STUDENT][1])
    return fresh.run()


# ---------- answer feedback ----------

def test_rating_is_stored_once_per_answer_and_survives_a_refresh(backend, insights_on):
    at = as_student()
    at.chat_input[0].set_value(FINE).run()
    assert not at.exception
    aid = answer_id(at)
    assert len(at.feedback) == 1 and len(at.button_group) == 0

    at.feedback(key=f"fb_{aid}").set_value(1).run()
    (row,) = feedback.export_rows(insights_on)
    assert row["helpful"] is True and row["question"] == FINE
    assert (row["role"], row["branch"], row["route"], row["topic"]) == ("student", "CSE", "simple", "library")
    assert not at.text_input  # no comment box after a thumbs up

    at.feedback(key=f"fb_{aid}").set_value(0).run()  # changing your mind updates the same row
    (row,) = feedback.export_rows(insights_on)
    assert row["helpful"] is False
    at.text_input(key=f"fb_note_{aid}").input("It is Rs 10 now, ask XYZ24CSE001")
    at.button(key=f"fb_send_{aid}").click().run()
    assert not at.exception
    (row,) = feedback.export_rows(insights_on)
    assert row["comment"] == "It is Rs 10 now, ask [id]"
    assert "Thanks for telling us what was wrong." in all_text(at) and not at.text_input

    saved = chat_store.load_history(STUDENT)[-1]
    assert saved["answer_id"] == aid and saved["feedback"] == {"rating": 0, "comment": "It is Rs 10 now, ask [id]"}
    fresh = refreshed()
    assert not fresh.exception
    assert fresh.feedback(key=f"fb_{aid}").value == 0
    assert "Thanks for telling us what was wrong." in all_text(fresh)

    fresh.feedback(key=f"fb_{aid}").set_value(None).run()  # withdrawn: the row goes, the saved rating too
    assert feedback.export_rows(insights_on) == []
    assert "feedback" not in chat_store.load_history(STUDENT)[-1]


def test_each_answer_has_its_own_rating(backend, insights_on):
    at = as_student()
    at.chat_input[0].set_value(FINE).run()
    at.chat_input[0].set_value("Meri fees ka status kya hai?").run()
    first, second = answer_id(at, 1), answer_id(at, 3)
    assert first != second and len(at.feedback) == 2
    at.feedback(key=f"fb_{first}").set_value(1).run()
    at.feedback(key=f"fb_{second}").set_value(0).run()
    rows = {r["route"]: r for r in feedback.export_rows(insights_on)}
    assert rows["simple"]["helpful"] is True
    assert rows["personal"]["helpful"] is False
    assert rows["personal"]["question"] == question_log.PERSONAL_PLACEHOLDER  # personal text is never stored
    assert "aarav" not in str(rows).lower() and "XYZ24CSE001" not in str(rows)


def test_admin_rating_has_no_branch(backend, insights_on):
    at = as_admin()
    at.switch_page("ui/chat.py").run()
    at.chat_input[0].set_value(FINE).run()
    at.feedback(key=f"fb_{answer_id(at)}").set_value(1).run()
    (row,) = feedback.export_rows(insights_on)
    assert (row["role"], row["branch"]) == ("admin", "")


def test_switched_off_insights_keep_the_rating_in_the_chat_only(backend, app_db):
    """With SCA_QUESTION_LOG=0 (the test default) nothing reaches the Insights tables."""
    at = as_student()
    at.chat_input[0].set_value(FINE).run()
    at.feedback(key=f"fb_{answer_id(at)}").set_value(0).run()
    assert not at.exception
    assert feedback.export_rows(app_db) == [] and run_metrics.export_rows(app_db) == []
    assert chat_store.load_history(STUDENT)[-1]["feedback"]["rating"] == 0


def test_old_answers_without_an_id_have_no_rating_widget(backend):
    at = as_student()
    at.session_state["messages"] = [{"role": "user", "content": FINE},
                                    {"role": "assistant", "result": {"answer": "Rs 5 per day.", "route": "simple"}}]
    at.run()
    assert not at.exception and len(at.feedback) == 0


# ---------- model usage per question ----------

def test_answered_question_records_usage(backend, insights_on):
    at = as_student()
    at.chat_input[0].set_value(FINE).run()
    (row,) = run_metrics.export_rows(insights_on)
    assert (row["route"], row["llm_calls"], row["input_tokens"], row["output_tokens"]) == ("simple", 2, 800, 160)
    assert row["failed"] is False and row["error_type"] == "" and row["question"] == ""
    assert row["latency_ms"] > 0 and row["role"] == "student"


def test_failed_question_records_its_error(backend, insights_on):
    backend.graph.graph = BrokenGraph()
    at = as_student()
    at.chat_input[0].set_value(FINE).run()
    assert "Something went wrong" in at.error[0].value
    (row,) = run_metrics.export_rows(insights_on)
    assert (row["failed"], row["error_type"], row["question"], row["route"]) == (True, "backend_error", FINE, "")
    assert len(at.feedback) == 0  # an error cannot be rated


def test_missing_aws_credentials_are_recorded(backend, insights_on, monkeypatch):
    at = as_student()
    monkeypatch.setenv("SCA_DEMO_MODE", "0")
    monkeypatch.setattr(components, "has_aws_credentials", lambda: False)
    at.chat_input[0].set_value(FINE).run()
    assert not at.exception and "AWS is not configured" in all_text(at)
    (row,) = run_metrics.export_rows(insights_on)
    assert (row["failed"], row["error_type"], row["llm_calls"]) == (True, "aws_not_configured", 0)


def test_question_limit_refusal_is_recorded(backend, insights_on, monkeypatch):
    monkeypatch.setenv("SCA_CHAT_UNITS_PER_HOUR", "1")
    at = as_student()
    for _ in range(2):
        at.chat_input[0].set_value(FINE).run()
    assert "Question limit reached" in all_text(at)
    errors = [r["error_type"] for r in run_metrics.export_rows(insights_on)]
    assert len(errors) == 2 and "question_limit" in errors


# ---------- source card numbers ----------

class DuplicateCitationGraph:
    """Answer text cites [4]; citation 3 repeats citation 1 and is merged into its card."""

    def stream(self, inputs, config=None, stream_mode="updates"):
        cite = {"source": "xyz/fees.md", "college": "xyz", "page": 1, "snippet": "Tuition fee per semester"}
        other = {"source": "xyz/hostel.md", "college": "xyz", "page": 2, "snippet": "Hostel fee"}
        last = {"source": "xyz/library.md", "college": "xyz", "page": 3, "snippet": "Fine per day"}
        yield {"router": {"route": "simple", "route_reason": "Single fact lookup."}}
        yield {"simple_answer": {"answer": "Fees [1], hostel [2], fine [4].", "citations": [cite, other, dict(cite), last]}}


def test_source_cards_keep_the_numbers_used_in_the_answer(backend):
    backend.graph.graph = DuplicateCitationGraph()
    at = as_student()
    at.chat_input[0].set_value(FINE).run()
    assert not at.exception
    text = all_text(at)
    assert "Sources · 3" in text
    cards = next(h.value for h in at.html if "sca-cites" in str(h.value))
    numbers = [part.split("</span>")[0] for part in cards.split('<span class="num">')[1:]]
    assert numbers == ["[1, 3]", "[2]", "[4]"]  # [4] in the text is card 4, not the third card
    assert cards.index("fees.md") < cards.index("hostel.md") < cards.index("library.md")
