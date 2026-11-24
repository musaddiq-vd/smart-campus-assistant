"""Anonymous model usage per chat question: latency, tokens, cost and failures (admin "Insights" page).

The Ask page writes one row for every question, answered or not: the kind of answer, the
number of model calls, input and output tokens, the cost in USD (``usage.cost_usd``), the
total time until the answer was ready, and for a failure its type (AWS not configured, the
question limit, a backend error...). That is what an admin needs to watch the bill and spot
an outage.

Privacy follows ``question_log``: no email, student ID or name, only the role. The question
text is kept for failed questions only (to see what broke), masked the same way, and a
personal question (or one that failed before it was routed and looks personal) only as a
placeholder.

Rows live in the app SQLite database (table ``run_metrics``, created lazily here). The
``SCA_QUESTION_LOG=0`` switch turns writing off, like the question log. Every public
function takes an optional ``path`` so tests can use a temporary database.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from smart_campus_assistant import db, question_log, usage
from smart_campus_assistant.config import local_time, local_timezone

logger = logging.getLogger(__name__)

MAX_ROWS = 5000  # oldest rows are deleted beyond this
SUMMARY_DAYS = 14
FALLBACK_ERROR = "backend_error"
# Error type -> label shown to admins. The Ask page decides the type (BackendError.kind).
ERROR_TYPES = {
    "aws_not_configured": "AWS not configured",
    "aws_unreachable": "AWS not reachable",
    "backend_unavailable": "Backend not available",
    "question_limit": "Question limit reached",
    "backend_error": "Backend error",
    "interrupted": "Stopped before the answer",
}
EXPORT_COLUMNS = (
    "asked_at", "role", "route", "topic", "question", "llm_calls", "input_tokens", "output_tokens", "cost_usd",
    "latency_ms", "failed", "error_type",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at      TEXT NOT NULL,
    role          TEXT NOT NULL,
    route         TEXT NOT NULL DEFAULT '',
    topic         TEXT NOT NULL,
    question      TEXT NOT NULL DEFAULT '',
    llm_calls     INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    latency_ms    REAL NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    error_type    TEXT NOT NULL DEFAULT ''
)
"""


# ---------------------------------------------------------------- pure helpers


def percentile(values: Iterable[float], pct: float) -> float | None:
    """Nearest-rank percentile (the value at rank ceil(pct/100 * n)); None for no values.

    Nearest rank always returns a real measurement, so "p95 2.4 s" is a latency someone waited.
    """
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return None
    rank = math.ceil(max(0.0, min(100.0, float(pct))) / 100 * len(ordered))
    return ordered[max(1, rank) - 1]


def error_label(error_type: object) -> str:
    return ERROR_TYPES.get(str(error_type or ""), ERROR_TYPES[FALLBACK_ERROR])


def _clean_error(error: object) -> str:
    """"" for an answered question, else a known error type (anything unknown is a backend error)."""
    if not error:
        return ""
    error = str(error).strip().lower()
    return error if error in ERROR_TYPES else FALLBACK_ERROR


def _looks_personal(question: str) -> bool:
    """The router's keyword rule for a question that failed before it was routed.

    If the rule cannot be loaded (the backend is the thing that failed), assume it is personal:
    losing the text of one failed question is better than storing a personal one.
    """
    try:
        from smart_campus_assistant.router import is_personal

        return bool(is_personal(question))
    except Exception:  # noqa: BLE001
        return True


def _number(value: object) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 and math.isfinite(number) else 0.0


def _local_day(stamp: object) -> str | None:
    moment = local_time(stamp)  # stored in UTC; counted for the college's local day
    return moment.date().isoformat() if moment else None


# ---------------------------------------------------------------- storage


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS run_metrics_asked_at ON run_metrics(asked_at)")


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_metrics'").fetchone()
    return row is not None


def _rows(path: str | Path | None, where: str = "", params: tuple = ()) -> list[dict]:
    """All matching rows, oldest first ([] when nothing was recorded yet)."""
    with db.reading(path) as conn:
        if not _has_table(conn):
            return []
        sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM run_metrics {where} ORDER BY id"
        return [{**dict(r), "failed": bool(r["failed"])} for r in conn.execute(sql, params).fetchall()]


def record_run(
    question: str,
    route: str | None = None,
    usage_records: Iterable[dict] | None = None,
    latency_ms: float = 0.0,
    error: str | None = None,
    role: str = "student",
    path: str | Path | None = None,
) -> None:
    """Store one asked question's usage and outcome without any identity. Never raises.

    ``route`` is None or "" when the question failed before it was routed; ``error`` is None or
    "" for an answered question, else one of ERROR_TYPES.
    """
    try:
        if not question_log.logging_enabled() or not str(question or "").strip():
            return
        error_type = _clean_error(error)
        known_route = str(route or "").strip().lower()
        known_route = known_route if known_route in question_log.ROUTES else ""
        personal = known_route == "personal" or (not known_route and _looks_personal(str(question)))
        fields = question_log.anonymous_fields(question, "personal" if personal else known_route, role)
        totals = usage.summarize_usage(usage_records or [])
        row = (
            db.now_iso(),
            fields["role"],
            known_route,
            fields["topic"],
            fields["question"] if error_type else "",  # the text is only needed to see what broke
            totals["llm_calls"],
            totals["input_tokens"],
            totals["output_tokens"],
            totals["cost_usd"],
            round(_number(latency_ms), 1),
            int(bool(error_type)),
            error_type,
        )
        with db.transaction(path) as conn:
            _ensure_schema(conn)
            conn.execute(
                f"INSERT INTO run_metrics ({', '.join(EXPORT_COLUMNS)}) VALUES ({', '.join('?' * len(row))})",
                row,
            )
            conn.execute(
                "DELETE FROM run_metrics WHERE id NOT IN (SELECT id FROM run_metrics ORDER BY id DESC LIMIT ?)",
                (max(1, int(MAX_ROWS)),),
            )
    except Exception as exc:  # noqa: BLE001  (monitoring must never break the chat)
        logger.warning("run metrics: could not store a run (%s)", type(exc).__name__)


def window_stats(rows: list[dict]) -> dict:
    """Totals for a set of rows. Latency is over answered questions only.

    A failure such as "AWS not configured" ends in a few milliseconds, so counting it would make
    the assistant look faster exactly when it is broken.
    """
    answered = [r for r in rows if not r["failed"]]
    latencies = [_number(r["latency_ms"]) for r in answered]
    input_tokens = sum(int(r["input_tokens"] or 0) for r in rows)
    output_tokens = sum(int(r["output_tokens"] or 0) for r in rows)
    return {
        "questions": len(rows),
        "answered": len(answered),
        "failed": len(rows) - len(answered),
        "llm_calls": sum(int(r["llm_calls"] or 0) for r in rows),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(sum(_number(r["cost_usd"]) for r in rows), 8),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "p95_latency_ms": percentile(latencies, 95),
    }


def summary(path: str | Path | None = None, today: date | None = None) -> dict:
    """Usage for the Insights page, by day in the college's time zone.

    Returns {"total": rows ever kept, "today": stats, "last_14_days": stats,
    "by_day": [{"day": "YYYY-MM-DD", **stats}, ...14 entries, oldest first, zeros included],
    "by_route": {route: stats} and "errors": {error_type: n} for the last 14 days}, where
    ``stats`` is ``window_stats``.
    """
    rows = _rows(path)
    today = today or datetime.now(local_timezone()).date()
    days = [(today - timedelta(days=offset)).isoformat() for offset in range(SUMMARY_DAYS - 1, -1, -1)]
    per_day: dict[str, list[dict]] = {day: [] for day in days}
    for r in rows:
        bucket = per_day.get(_local_day(r["asked_at"]) or "")
        if bucket is not None:
            bucket.append(r)
    recent = [r for day in days for r in per_day[day]]
    by_route: dict[str, list[dict]] = {}
    for r in recent:
        by_route.setdefault(r["route"] or "", []).append(r)
    return {
        "total": len(rows),
        "today": window_stats(per_day[today.isoformat()]),
        "last_14_days": window_stats(recent),
        "by_day": [{"day": day, **window_stats(per_day[day])} for day in days],
        "by_route": {route: window_stats(by_route[route]) for route in (*question_log.ROUTES, "") if route in by_route},
        "errors": dict(Counter(r["error_type"] for r in recent if r["failed"]).most_common()),
    }


def recent_failures(limit: int = 20, path: str | Path | None = None) -> list[dict]:
    """The latest failed questions, newest first: [{"asked_at", "error_type", "error", "route", "topic", "question"}]."""
    newest = list(reversed(_rows(path, "WHERE failed = 1")))[: max(0, int(limit))]
    return [{"asked_at": r["asked_at"], "error_type": r["error_type"], "error": error_label(r["error_type"]),
             "route": r["route"], "topic": r["topic"], "question": r["question"]} for r in newest]


def export_rows(path: str | Path | None = None) -> list[dict]:
    """Every stored row (EXPORT_COLUMNS keys), oldest first. Contains no identity by construction."""
    return _rows(path)


def rows_to_csv(rows: list[dict]) -> str:
    """CSV text of exported rows; formula-like cells are neutralised (question text is user input)."""
    return question_log.to_csv(EXPORT_COLUMNS, rows, yes_no=("failed",))


def clear(path: str | Path | None = None) -> int:
    """Delete every usage row; returns how many rows were removed."""
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        return int(conn.execute("DELETE FROM run_metrics").rowcount)
