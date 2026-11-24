"""Offline tests for run_metrics.py (model usage per question, temporary database) and usage.py's math."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from smart_campus_assistant import question_log, run_metrics, usage

NANO_LITE = "us.amazon.nova-lite-v1:0"
MICRO = "us.amazon.nova-micro-v1:0"
RECORDS = [
    {"node": "router", "model_id": MICRO, "input_tokens": 1000, "output_tokens": 100, "latency_ms": 300.0},
    {"node": "simple_answer", "model_id": NANO_LITE, "input_tokens": 2000, "output_tokens": 500, "latency_ms": 900.0},
]


@pytest.fixture(autouse=True)
def log_on(monkeypatch):
    """conftest.py switches the Insights data off for the suite; these tests use temporary databases."""
    monkeypatch.setattr(question_log.student_data, "load_students", lambda *a, **k: {})
    monkeypatch.delenv("SCA_QUESTION_LOG", raising=False)
    monkeypatch.delenv("SCA_TIMEZONE", raising=False)  # Asia/Kolkata


@pytest.fixture
def path(tmp_path):
    return tmp_path / "app.db"


def _raw_rows(path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM run_metrics ORDER BY id")]
    finally:
        conn.close()


def _set_time(path, row_id: int, stamp: str) -> None:
    with run_metrics.db.transaction(path) as conn:
        conn.execute("UPDATE run_metrics SET asked_at = ? WHERE id = ?", (stamp, row_id))


# ---------- math ----------

@pytest.mark.parametrize(("values", "pct", "expected"), [
    ([], 95, None),
    ([700], 95, 700),
    (list(range(1, 21)), 95, 19),  # rank ceil(0.95 * 20) = 19
    (list(range(1, 101)), 95, 95),
    (list(range(1, 102)), 95, 96),  # rank ceil(95.95) = 96
    ([5, 1, 4, 2, 3], 50, 3),  # unsorted input
    ([5, 1, 4, 2, 3], 100, 5),
    ([5, 1, 4, 2, 3], 0, 1),
])
def test_percentile_nearest_rank(values, pct, expected):
    assert run_metrics.percentile(values, pct) == expected


def test_usage_cost_and_totals():
    assert usage.cost_usd(RECORDS[0]) == pytest.approx(1 * 0.000035 + 0.1 * 0.00014)
    totals = usage.summarize_usage(RECORDS)
    assert (totals["llm_calls"], totals["input_tokens"], totals["output_tokens"], totals["total_tokens"]) == (2, 3000, 600, 3600)
    assert totals["cost_usd"] == pytest.approx(usage.cost_usd(RECORDS[0]) + usage.cost_usd(RECORDS[1]))
    assert totals["llm_latency_ms"] == 1200.0


def test_usage_math_tolerates_partial_records():
    """Streamed updates may carry records without a model id or with odd numbers: they count as zero."""
    odd = [{"input_tokens": 400, "output_tokens": 80, "latency_ms": 120.0}, {"input_tokens": "x", "output_tokens": None},
           {"model_id": MICRO, "input_tokens": -5, "output_tokens": float("nan")}, "not a record"]
    totals = usage.summarize_usage(odd)
    assert (totals["llm_calls"], totals["input_tokens"], totals["output_tokens"], totals["cost_usd"]) == (3, 400, 80, 0.0)
    assert usage.summarize_usage([])["llm_calls"] == 0 and usage.summarize_usage(None)["cost_usd"] == 0


# ---------- recording ----------

def test_answered_run_stores_usage_but_no_question_text(path):
    run_metrics.record_run("What is the library fine per day?", "simple", RECORDS, latency_ms=1834.56, path=path)
    (row,) = _raw_rows(path)
    assert set(row) == {"id", "asked_at", "role", "route", "topic", "question", "llm_calls", "input_tokens",
                        "output_tokens", "cost_usd", "latency_ms", "failed", "error_type"}
    assert (row["route"], row["topic"], row["question"], row["failed"], row["error_type"]) == ("simple", "library", "", 0, "")
    assert (row["llm_calls"], row["input_tokens"], row["output_tokens"], row["latency_ms"]) == (2, 3000, 600, 1834.6)
    assert row["cost_usd"] == pytest.approx(usage.summarize_usage(RECORDS)["cost_usd"])
    assert row["role"] == "student"


@pytest.mark.parametrize(("error", "expected"), [
    ("aws_not_configured", "aws_not_configured"),
    ("question_limit", "question_limit"),
    ("interrupted", "interrupted"),
    ("backend_error", "backend_error"),
    ("Something Odd", "backend_error"),  # unknown types are counted as backend errors
])
def test_failed_runs_keep_the_masked_question_and_error_type(path, error, expected):
    run_metrics.record_run("Is XYZ24CSE001 placed? mail a@b.example", "simple", [], error=error, path=path)
    (row,) = _raw_rows(path)
    assert (row["failed"], row["error_type"]) == (1, expected)
    assert row["question"] == "Is [id] placed? mail [email]"
    assert run_metrics.error_label(expected) == run_metrics.ERROR_TYPES[expected]


def test_failure_keeps_tokens_already_spent(path):
    run_metrics.record_run("Compare CSE and ECE placements", "deep", RECORDS[:1], latency_ms=40000, error="backend_error",
                           path=path)
    (row,) = _raw_rows(path)
    assert (row["route"], row["llm_calls"], row["input_tokens"], row["failed"]) == ("deep", 1, 1000, 1)


def test_personal_failures_are_placeholders(path, monkeypatch):
    run_metrics.record_run("Meri fees kitni baaki hai?", "personal", [], error="backend_error", path=path)
    # Failed before routing: the router's keyword rule decides; if it cannot be loaded, assume personal.
    monkeypatch.setattr(run_metrics, "_looks_personal", lambda q: "my" in q.lower().split())
    run_metrics.record_run("When is my next exam?", None, [], error="aws_not_configured", path=path)
    run_metrics.record_run("What is the hostel fee?", "", [], error="aws_not_configured", role="admin", path=path)
    rows = _raw_rows(path)
    assert [r["question"] for r in rows] == [question_log.PERSONAL_PLACEHOLDER, question_log.PERSONAL_PLACEHOLDER,
                                             "What is the hostel fee?"]
    assert [r["route"] for r in rows] == ["personal", "", ""]  # unknown routes stay unknown
    assert rows[2]["role"] == "admin"


def test_router_rule_unavailable_means_personal(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_router(name, *args, **kwargs):
        if name.endswith("router"):
            raise ImportError("backend broken")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_router)
    assert run_metrics._looks_personal("What is the hostel fee?") is True


def test_switched_off_and_blank_questions(path, monkeypatch):
    run_metrics.record_run("   ", "simple", RECORDS, path=path)
    assert run_metrics.export_rows(path) == []
    monkeypatch.setenv("SCA_QUESTION_LOG", "0")

    def fail(*args, **kwargs):
        raise AssertionError("the app database must not be written from a test run")

    monkeypatch.setattr(run_metrics.db, "transaction", fail)
    run_metrics.record_run("Library fine?", "simple", RECORDS)


def test_never_raises(tmp_path, monkeypatch):
    run_metrics.record_run("Library fine?", "simple", RECORDS, path=tmp_path)  # a folder, not a file
    run_metrics.record_run("Library fine?", 42, "junk", latency_ms="slow", error=object(), role=None,
                           path=tmp_path / "ok.db")
    (row,) = _raw_rows(tmp_path / "ok.db")
    assert (row["route"], row["llm_calls"], row["latency_ms"], row["error_type"]) == ("", 0, 0.0, "backend_error")

    def boom(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(run_metrics.db, "transaction", boom)
    run_metrics.record_run("Library fine?", "simple", RECORDS, path=tmp_path / "other.db")


def test_row_cap_deletes_the_oldest(path, monkeypatch):
    monkeypatch.setattr(run_metrics, "MAX_ROWS", 3)
    for i in range(5):
        run_metrics.record_run(f"Question {i}", "simple", [], error="backend_error", path=path)
    assert [r["question"] for r in run_metrics.export_rows(path)] == ["Question 2", "Question 3", "Question 4"]


# ---------- summary ----------

def test_summary_on_an_empty_database(path):
    stats = run_metrics.summary(path)
    assert stats["total"] == 0 and len(stats["by_day"]) == 14 and stats["by_route"] == {} and stats["errors"] == {}
    assert stats["today"]["questions"] == 0 and stats["today"]["avg_latency_ms"] is None
    assert stats["last_14_days"]["p95_latency_ms"] is None
    assert run_metrics.recent_failures(path=path) == [] and run_metrics.clear(path) == 0


def test_summary_windows_latency_and_errors(path):
    today = date(2026, 11, 23)
    for latency in range(100, 2100, 100):  # 20 answered questions today: 100 ms ... 2000 ms
        run_metrics.record_run("Library fine?", "simple", RECORDS, latency_ms=latency, path=path)
    run_metrics.record_run("Compare CSE and ECE", "deep", RECORDS, latency_ms=9000, path=path)
    run_metrics.record_run("Hostel fee?", "", [], latency_ms=3, error="aws_not_configured", path=path)
    run_metrics.record_run("Hostel fee?", "", [], latency_ms=2, error="aws_not_configured", path=path)
    run_metrics.record_run("Exam dates?", "simple", RECORDS[:1], latency_ms=5000, error="backend_error", path=path)
    run_metrics.record_run("Old question", "simple", RECORDS, latency_ms=50, path=path)
    # 00:30 IST on 23 Nov is still 22 Nov in UTC: it counts for today in the college's time zone.
    for row_id in range(1, 26):
        _set_time(path, row_id, "2026-11-22T19:00:00+00:00")
    _set_time(path, 21, "2026-11-20T05:00:00+00:00")  # the deep question, three days ago
    _set_time(path, 25, "2026-10-01T05:00:00+00:00")  # outside the 14-day window

    stats = run_metrics.summary(path, today=today)
    t, w = stats["today"], stats["last_14_days"]
    assert stats["total"] == 25
    assert (t["questions"], t["answered"], t["failed"]) == (23, 20, 3)
    assert t["avg_latency_ms"] == 1050.0  # failures are not part of the answer time
    assert t["p95_latency_ms"] == 1900.0  # nearest rank 19 of 20
    assert (w["questions"], w["failed"]) == (24, 3)
    assert w["p95_latency_ms"] == 2000.0  # 21 answered: rank ceil(19.95) = 20
    per_call = usage.summarize_usage(RECORDS)
    assert t["input_tokens"] == 20 * 3000 + 1000 and t["llm_calls"] == 20 * 2 + 1
    assert w["total_tokens"] == 21 * 3600 + 1100
    assert w["cost_usd"] == pytest.approx(21 * per_call["cost_usd"] + usage.cost_usd(RECORDS[0]))
    days = stats["by_day"]
    assert len(days) == 14 and days[-1]["day"] == "2026-11-23" and days[0]["day"] == "2026-11-10"
    assert days[-1]["questions"] == 23 and days[-4]["questions"] == 1 and sum(d["questions"] for d in days) == 24
    assert set(stats["by_route"]) == {"simple", "deep", ""}
    assert stats["by_route"]["deep"]["avg_latency_ms"] == 9000.0 and stats["by_route"][""]["failed"] == 2
    assert stats["errors"] == {"aws_not_configured": 2, "backend_error": 1}


def test_recent_failures_newest_first(path):
    run_metrics.record_run("Hostel fee?", "", [], error="aws_not_configured", path=path)
    run_metrics.record_run("Library fine?", "simple", RECORDS, path=path)
    run_metrics.record_run("Exam dates?", "simple", [], error="question_limit", path=path)
    failures = run_metrics.recent_failures(path=path)
    assert [(f["question"], f["error"]) for f in failures] == [("Exam dates?", "Question limit reached"),
                                                              ("Hostel fee?", "AWS not configured")]
    assert set(failures[0]) == {"asked_at", "error_type", "error", "route", "topic", "question"}
    assert run_metrics.recent_failures(limit=1, path=path) == failures[:1]
    assert run_metrics.recent_failures(limit=-1, path=path) == []


def test_summary_uses_the_local_day_by_default(path):
    run_metrics.record_run("Library fine?", "simple", RECORDS, latency_ms=100, path=path)
    now_utc = datetime.now(timezone.utc)
    _set_time(path, 1, (now_utc - timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert run_metrics.summary(path)["today"]["questions"] == 1


def test_export_csv_and_clear(path):
    run_metrics.record_run("=HYPERLINK(1) fee?", "simple", RECORDS, latency_ms=10, error="backend_error", path=path)
    rows = run_metrics.export_rows(path)
    assert list(rows[0]) == list(run_metrics.EXPORT_COLUMNS) and rows[0]["failed"] is True
    lines = run_metrics.rows_to_csv(rows).splitlines()
    assert lines[0] == ",".join(run_metrics.EXPORT_COLUMNS)
    assert ",'=HYPERLINK(1) fee?," in lines[1] and lines[1].endswith(",yes,backend_error")
    assert run_metrics.clear(path) == 1 and run_metrics.export_rows(path) == []
