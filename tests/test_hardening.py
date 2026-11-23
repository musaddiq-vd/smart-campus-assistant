"""Edge cases found in the production review: question budget, comparison signs in text, local time zone."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from smart_campus_assistant import config, notifications, question_log, rate_limit
from smart_campus_assistant.notices import clean_input
from tests.test_ui_smoke import all_text, as_student, backend  # noqa: F401  (backend is a fixture)

# ---------------------------------------------------------------- question budget


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_budget_counts_units_in_a_sliding_hour():
    clock = Clock()
    budget = rate_limit.QuestionBudget(units=5, clock=clock)
    budget.record("A@x.example", 1)
    clock.now += 10
    budget.record("a@x.example", rate_limit.units_for("deep"))  # same account, any case
    assert budget.used("a@x.example") == 5
    assert budget.wait_seconds("a@x.example") == pytest.approx(3590)  # until the first unit is an hour old
    assert budget.wait_seconds("b@x.example") == 0.0  # other accounts are not affected
    clock.now += 3590
    assert budget.wait_seconds("a@x.example") == 0.0 and budget.used("a@x.example") == 4


def test_budget_waits_only_as_long_as_needed():
    clock = Clock()
    budget = rate_limit.QuestionBudget(units=3, clock=clock)
    for _ in range(3):
        budget.record("a", 1)
        clock.now += 100
    assert budget.wait_seconds("a") == pytest.approx(3600 - 300)


def test_budget_off_and_bad_settings(monkeypatch):
    budget = rate_limit.QuestionBudget(units=0)
    budget.record("a", 100)
    assert budget.wait_seconds("a") == 0.0 and budget.used("a") == 0
    monkeypatch.setenv("SCA_CHAT_UNITS_PER_HOUR", "lots")
    assert rate_limit.units_per_hour() == rate_limit.DEFAULT_UNITS_PER_HOUR
    monkeypatch.setenv("SCA_CHAT_UNITS_PER_HOUR", "-5")
    assert rate_limit.units_per_hour() == 0
    assert rate_limit.units_for("simple") == rate_limit.units_for("personal") == 1
    assert "about 2 minutes" in rate_limit.wait_message(61)


def test_chat_stops_at_the_budget(backend, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SCA_CHAT_UNITS_PER_HOUR", "4")
    at = as_student()
    at.chat_input[0].set_value("Compare CSE and ECE placements").run()  # deep: 4 units
    calls = len(backend.graph.graph.calls)
    at.chat_input[0].set_value("What is the library fine per day?").run()
    assert not at.exception
    assert len(backend.graph.graph.calls) == calls  # the model was not called
    assert "Question limit reached" in all_text(at)
    # A refused question is not sent as history later.
    assert at.session_state["messages"][-1].get("error")


# ---------------------------------------------------------------- comparison signs in notices and requests


@pytest.mark.parametrize("raw, cleaned", [
    ("Attendance < 75% means debarred; CGPA >= 8.5 gets merit.", "Attendance ＜ 75% means debarred; CGPA ≥ 8.5 gets merit."),
    ("Fine <= Rs 500 and marks > 40", "Fine ≤ Rs 500 and marks ＞ 40"),
    ("<b>Bold</b> text", "Bold text"),
    ("<scr<script>ipt>alert(1)</script>", "alert(1)"),
    ("&lt;img src=x onerror=alert(1)&gt;", ""),
    ("<!-- note --> visible", "visible"),
])
def test_clean_input_keeps_comparisons_and_removes_tags(raw, cleaned):
    assert clean_input(raw) == cleaned
    assert "<" not in clean_input(raw) and ">" not in clean_input(raw)


# ---------------------------------------------------------------- local time zone


def test_local_timezone_defaults_to_india(monkeypatch):
    monkeypatch.delenv("SCA_TIMEZONE", raising=False)
    assert config.local_timezone().utcoffset(datetime(2026, 11, 3)) == timedelta(hours=5, minutes=30)
    monkeypatch.setenv("SCA_TIMEZONE", "No/Such_Zone")
    assert config.local_timezone().utcoffset(datetime(2026, 11, 3)) == timedelta(0)
    assert config.local_time("not a time") is None


def test_insights_count_a_question_on_its_local_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SCA_QUESTION_LOG", "1")
    monkeypatch.delenv("SCA_TIMEZONE", raising=False)
    path = tmp_path / "app.db"
    question_log.log_question("What is the hostel fee?", "simple", "Rs 60,000 per year [1].", path=path)
    # 00:30 today in India is still "yesterday" in UTC: it must count for today.
    tz = config.local_timezone()
    today = datetime.now(tz).date()
    early = datetime(today.year, today.month, today.day, 0, 30, tzinfo=tz).astimezone(timezone.utc)
    with question_log.db.transaction(path) as conn:
        conn.execute("UPDATE question_log SET asked_at = ?", (early.isoformat(timespec="seconds"),))
    days = question_log.summary(path)["by_day"]
    assert days[-1]["day"] == today.isoformat() and days[-1]["total"] == 1


# ---------------------------------------------------------------- fee reminder wording


def test_overdue_reminder_matches_the_late_fee_policy():
    record = {"name": "A", "as_of": "2026-11-03",
              "fees": {"semester": "Odd", "due": 55000, "due_date": "2026-10-31"}}
    body = notifications.fee_reminder(record)[2]
    assert "Total payable now: Rs 55,300" in body
    assert "pay the Rs 55,000 due on the Pay fees page" in body and "accounts office" in body


# ---------------------------------------------------------------- one fee status for pages and the assistant


@pytest.mark.parametrize("fees, as_of, expected", [
    ({"status": "partially_paid", "paid": 0, "due": 95000, "due_date": "2026-11-30"}, "2026-11-03", "unpaid"),
    ({"status": "partially_paid", "paid": 100, "due": 94900, "due_date": "2026-11-30"}, "2026-11-03", "partially_paid"),
    ({"status": "partially_paid", "paid": 100, "due": 94900, "due_date": "2026-11-30"}, "2026-12-01", "overdue"),
    ({"status": "overdue", "paid": 0, "due": 500, "due_date": "2026-12-30"}, "2026-11-03", "overdue"),
    ({"status": "overdue", "paid": 95000, "due": 0, "due_date": "2026-10-31"}, "2026-11-03", "paid"),
    ({"status": "", "due": "bad"}, None, "paid"),
    (None, None, "paid"),
])
def test_fee_status_rule(fees, as_of, expected):
    from smart_campus_assistant.student_data import fee_status

    assert fee_status(fees, as_of) == expected


def test_new_admission_reads_unpaid_for_the_assistant_too():
    from smart_campus_assistant.student_data import student_context

    record = {"student_id": "XYZ26CSE099", "name": "New One", "branch": "CSE", "year": 1, "semester": 1,
              "as_of": "2026-11-03", "hostel": None,
              "fees": {"semester": "Odd 2026-27", "total": 95000, "paid": 0, "due": 95000,
                       "due_date": "2026-11-30", "status": "partially_paid", "transactions": []}}
    text = student_context(record)
    assert "status: unpaid" in text and "partially paid" not in text
