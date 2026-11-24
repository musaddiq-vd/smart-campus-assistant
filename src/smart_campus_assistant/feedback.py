"""Anonymous "helpful / not helpful" ratings of chat answers (feeds the admin "Insights" page).

The ratings answer one question for the admin: which answers do students find wrong or
useless, and why (an optional short comment after a thumbs down).

Privacy follows ``question_log``: only role, branch code, kind of answer, topic and the
masked question are kept (a personal question only as a placeholder); never an email,
student ID or name. The comment is cleaned and masked the same way.

One rating per answer: every answer gets a random ``answer_id`` when it is created, and a
second click on the same answer updates its row instead of adding one. The id is a random
token, not derived from the user, and is never shown on the Insights page.

Rows live in the app SQLite database (table ``answer_feedback``, created lazily here). The
``SCA_QUESTION_LOG=0`` switch turns writing off, like the question log. Every public
function takes an optional ``path`` so tests can use a temporary database.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path

from smart_campus_assistant import db, question_log

logger = logging.getLogger(__name__)

MAX_ROWS = 5000  # oldest rows are deleted beyond this
MAX_COMMENT_CHARS = 300
EXPORT_COLUMNS = ("rated_at", "role", "branch", "route", "topic", "question", "helpful", "comment")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answer_feedback (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id TEXT NOT NULL UNIQUE,
    rated_at  TEXT NOT NULL,
    role      TEXT NOT NULL,
    branch    TEXT NOT NULL DEFAULT '',
    route     TEXT NOT NULL,
    topic     TEXT NOT NULL,
    question  TEXT NOT NULL,
    helpful   INTEGER NOT NULL,
    comment   TEXT NOT NULL DEFAULT ''
)
"""

_ANSWER_ID_RE = re.compile(r"[0-9a-f]{8,64}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------- pure helpers


def valid_answer_id(answer_id: object) -> bool:
    """True for the random hex ids the chat gives each answer (anything else is never stored)."""
    return isinstance(answer_id, str) and bool(_ANSWER_ID_RE.fullmatch(answer_id))


def clean_comment(text: object) -> str:
    """Comment text that is safe to store: one line, identifiers masked, at most MAX_COMMENT_CHARS."""
    text = _CONTROL_RE.sub(" ", str(text or ""))
    return question_log.mask_question(text)[:MAX_COMMENT_CHARS].rstrip()


def _helpful(value: object) -> int | None:
    """1 (helpful), 0 (not helpful) or None (no rating) from what ``st.feedback("thumbs")`` returns."""
    if isinstance(value, int) and value in (0, 1):  # bool is an int too
        return int(value)
    return None


# ---------------------------------------------------------------- storage


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS answer_feedback_rated_at ON answer_feedback(rated_at)")


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'answer_feedback'").fetchone()
    return row is not None


def _rows(path: str | Path | None, where: str = "", params: tuple = ()) -> list[dict]:
    """All matching rows, oldest first ([] when nothing was rated yet)."""
    with db.reading(path) as conn:
        if not _has_table(conn):
            return []
        sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM answer_feedback {where} ORDER BY rated_at, id"
        return [{**dict(r), "helpful": bool(r["helpful"])} for r in conn.execute(sql, params).fetchall()]


def record_rating(
    answer_id: str,
    helpful: object,
    question: str,
    route: str,
    role: str = "student",
    branch: str = "",
    path: str | Path | None = None,
) -> bool:
    """Store, change or (``helpful=None``) withdraw the rating of one answer. Never raises.

    A changed rating starts without a comment: the comment belonged to the earlier thumbs down.
    Returns True when the database was changed.
    """
    try:
        if not valid_answer_id(answer_id) or not question_log.logging_enabled():
            return False
        rating = _helpful(helpful)
        if rating is None:
            with db.transaction(path) as conn:
                _ensure_schema(conn)
                return conn.execute("DELETE FROM answer_feedback WHERE answer_id = ?", (answer_id,)).rowcount > 0
        fields = question_log.anonymous_fields(question, route, role, branch)
        if not fields["question"]:
            return False
        with db.transaction(path) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO answer_feedback (answer_id, rated_at, role, branch, route, topic, question, helpful)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(answer_id) DO UPDATE SET rated_at = excluded.rated_at, role = excluded.role,"
                " branch = excluded.branch, route = excluded.route, topic = excluded.topic,"
                " question = excluded.question, helpful = excluded.helpful, comment = ''",
                (answer_id, db.now_iso(), fields["role"], fields["branch"], fields["route"], fields["topic"],
                 fields["question"], rating),
            )
            conn.execute(
                "DELETE FROM answer_feedback WHERE id NOT IN (SELECT id FROM answer_feedback ORDER BY id DESC LIMIT ?)",
                (max(1, int(MAX_ROWS)),),
            )
        return True
    except Exception as exc:  # noqa: BLE001  (feedback must never break the chat)
        logger.warning("answer feedback: could not store a rating (%s)", type(exc).__name__)
        return False


def record_comment(answer_id: str, comment: object, path: str | Path | None = None) -> bool:
    """Attach the cleaned comment to an existing "not helpful" rating. Never raises; True when stored."""
    try:
        if not valid_answer_id(answer_id) or not question_log.logging_enabled():
            return False
        with db.transaction(path) as conn:
            _ensure_schema(conn)
            cur = conn.execute(
                "UPDATE answer_feedback SET comment = ? WHERE answer_id = ? AND helpful = 0",
                (clean_comment(comment), answer_id),
            )
            return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer feedback: could not store a comment (%s)", type(exc).__name__)
        return False


def _split(rows: list[dict], key: str) -> dict[str, dict]:
    """{value of ``key``: {"total", "helpful"}}, most rated first."""
    totals = Counter(r[key] for r in rows)
    helpful = Counter(r[key] for r in rows if r["helpful"])
    return {value: {"total": n, "helpful": helpful[value]} for value, n in totals.most_common()}


def summary(path: str | Path | None = None) -> dict:
    """Totals for the Insights page.

    Returns {"total", "helpful", "not_helpful", "with_comment", "by_route": {route: {"total", "helpful"}},
    "by_topic": {topic: {"total", "helpful"}}} (most rated first).
    """
    rows = _rows(path)
    helpful = sum(1 for r in rows if r["helpful"])
    return {
        "total": len(rows),
        "helpful": helpful,
        "not_helpful": len(rows) - helpful,
        "with_comment": sum(1 for r in rows if r["comment"]),
        "by_route": _split(rows, "route"),
        "by_topic": _split(rows, "topic"),
    }


def recent_unhelpful(limit: int = 20, path: str | Path | None = None) -> list[dict]:
    """The latest "not helpful" ratings, newest first: [{"rated_at", "route", "topic", "question", "comment", ...}]."""
    rows = _rows(path, "WHERE helpful = 0")
    return list(reversed(rows))[: max(0, int(limit))]


def export_rows(path: str | Path | None = None) -> list[dict]:
    """Every stored rating (EXPORT_COLUMNS keys), oldest first. Contains no identity by construction."""
    return _rows(path)


def rows_to_csv(rows: list[dict]) -> str:
    """CSV text of exported ratings; formula-like cells are neutralised (comments are user text)."""
    return question_log.to_csv(EXPORT_COLUMNS, rows, yes_no=("helpful",))


def clear(path: str | Path | None = None) -> int:
    """Delete every rating; returns how many rows were removed."""
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        return int(conn.execute("DELETE FROM answer_feedback").rowcount)
