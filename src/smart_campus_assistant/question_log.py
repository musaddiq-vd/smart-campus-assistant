"""Anonymous log of the questions asked in the helpdesk chat (feeds the admin "Insights" page).

The log answers two things for the admin: what do students ask, and which questions could
the college documents NOT answer (content gaps to fix with a notice or a document).

Privacy rules, enforced before anything is written:
- no email, student ID or name is ever stored: only the role ("student" / "admin") and the
  asker's branch code;
- student IDs, email addresses, known student names and long digit runs in the question
  text are masked;
- for the ``personal`` route the question text is not stored at all: only a placeholder
  and the topic.

Rows live in the app SQLite database (table ``question_log``, created lazily here). Every
public function takes an optional ``path`` so tests can use a temporary database.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smart_campus_assistant import db, student_data

logger = logging.getLogger(__name__)

MAX_ROWS = 5000  # oldest rows are deleted beyond this
MAX_QUESTION_CHARS = 300
SUMMARY_DAYS = 14
PERSONAL_PLACEHOLDER = "(personal question)"
ROUTES = ("simple", "deep", "personal")
TOPICS = (
    "fees", "exams", "hostel", "library", "attendance", "results", "placements", "scholarships", "admissions", "other",
)
EXPORT_COLUMNS = ("asked_at", "role", "branch", "route", "topic", "question", "answered", "citations")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS question_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at  TEXT NOT NULL,
    role      TEXT NOT NULL,
    branch    TEXT NOT NULL DEFAULT '',
    route     TEXT NOT NULL,
    topic     TEXT NOT NULL,
    question  TEXT NOT NULL,
    answered  INTEGER NOT NULL,
    citations INTEGER NOT NULL DEFAULT 0
)
"""

# Used when student_data does not provide its pattern (same shape: XYZ24CSE001).
_FALLBACK_ID_RE = re.compile(r"\bXYZ\d{2}[A-Z]{2,3}\d{3}\b", flags=re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_LONG_NUMBER_RE = re.compile(r"\b\d{10,}\b")  # phone or account numbers
_BRANCH_RE = re.compile(r"[A-Z]{2,4}")

# Topic keywords in English and Hinglish. The order decides ties ("hostel fee" is a hostel
# question, "exam fee" an exam question), so the general "fees" bucket comes last.
_TOPIC_KEYWORDS = {
    "scholarships": r"scholarships?|chhatravritti|wazifa|stipend|fee waiver|waiver|concession|freeship",
    "placements": (
        r"placements?|placed|naukri|jobs?|packages?|salary|internships?|recruiters?|compan(?:y|ies)|ctc|lpa|campus drive"
    ),
    "admissions": (
        r"admissions?|dakhila|dakhla|eligibility|cut ?offs?|counsell?ing|entrance exam|entrance|seats?|enrol\w*"
    ),
    "hostel": r"hostels?|chhatravas|mess|warden|curfew|roommates?",
    "library": r"library|pustakalaya|books?|kitaa?b\w*|issued|jurmana|fines?|reading room",
    "attendance": r"attendance|upasthiti|haa?zri|absent|bunk\w*|leaves?|chhutti|shortage",
    "results": r"results?|natija|cgpa|sgpa|gpa|marks|ank|grades?|backlogs?|revaluation|rechecking|marksheets?",
    "exams": (
        r"exams?|examinations?|pariksha|papers?|hall ticket|admit card|date ?sheet|time ?table|mid ?sems?|end ?sems?|"
        r"end semester|mid semester|supplementary|viva|practicals?"
    ),
    "fees": r"fees?|shulk|charges?|kharcha|cost|payments?|paid|pay|dues?|receipts?|refunds?|tuition|challan|instal?lments?",
}
_TOPIC_RES = {topic: re.compile(rf"\b(?:{pattern})\b") for topic, pattern in _TOPIC_KEYWORDS.items()}

# Phrases that mean "the documents could not answer this" (the prompts make models say "do not contain").
_NOT_ANSWERED_MARKERS = (
    "do not contain", "does not contain", "don't contain", "doesn't contain", "did not contain",
    # personal.LOGIN_MESSAGE / personal.REFUSAL_MESSAGE (also compared exactly when importable)
    "please log in", "i can only share your own record",
)


# ---------------------------------------------------------------- pure helpers


def _student_id_re() -> re.Pattern[str]:
    pattern = getattr(student_data, "STUDENT_ID_RE", None)
    return pattern if isinstance(pattern, re.Pattern) else _FALLBACK_ID_RE


def _mask_names(text: str) -> str:
    """Replace full names of known students with "[name]" (best effort; the record file may be missing)."""
    try:
        names = {str(r.get("name") or "").strip() for r in student_data.load_students().values()}
    except Exception:  # noqa: BLE001  (no student data: nothing to mask against)
        return text
    for name in sorted((n for n in names if len(n) >= 5 and " " in n), key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "[name]", text, flags=re.IGNORECASE)
    return text


def mask_question(question: object) -> str:
    """Question text that is safe to store: one line, identifiers masked, at most MAX_QUESTION_CHARS."""
    text = " ".join(str(question or "").split())
    text = _EMAIL_RE.sub("[email]", text)
    text = _student_id_re().sub("[id]", text)
    text = _LONG_NUMBER_RE.sub("[number]", text)
    text = _mask_names(text)
    return text[:MAX_QUESTION_CHARS].rstrip()


def detect_topic(question: object) -> str:
    """One of TOPICS, from English / Hinglish keywords; "other" when nothing matches."""
    text = re.sub(r"[^a-z0-9]+", " ", str(question or "").lower())
    best, best_hits = "other", 0
    for topic, pattern in _TOPIC_RES.items():
        hits = len(pattern.findall(text))
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


def _fixed_messages() -> tuple[str, ...]:
    """The personal path's login / refusal texts (lazy import: that module pulls in the model stack)."""
    try:
        from smart_campus_assistant import personal

        return (personal.LOGIN_MESSAGE.strip().lower(), personal.REFUSAL_MESSAGE.strip().lower())
    except Exception:  # noqa: BLE001
        return ()


def is_answered(answer: object) -> bool:
    """False for an empty answer, a "documents do not contain it" answer, or a login / refusal message."""
    text = " ".join(str(answer or "").split()).lower()
    if not text:
        return False
    if any(marker in text for marker in _NOT_ANSWERED_MARKERS):
        return False
    return text not in _fixed_messages()


def normalize_question(question: object) -> str:
    """Grouping key: lower case, punctuation removed, whitespace collapsed."""
    text = re.sub(r"[^\w\s]|_", " ", str(question or "").lower())
    return " ".join(text.split())


def _clean_route(route: object) -> str:
    route = str(route or "").strip().lower()
    return route if route in ROUTES else "simple"


def _clean_branch(branch: object) -> str:
    """A short branch code like "CSE"; anything else (a name, an email) becomes ""."""
    code = str(branch or "").strip().upper()
    return code if _BRANCH_RE.fullmatch(code) else ""


def _logging_paused() -> bool:
    """True when the log is switched off with ``SCA_QUESTION_LOG=0`` (the test suite does this)."""
    return os.getenv("SCA_QUESTION_LOG", "").strip().lower() in ("0", "false", "no", "off")


# ---------------------------------------------------------------- storage


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS question_log_asked_at ON question_log(asked_at)")


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'question_log'").fetchone()
    return row is not None


def _rows(path: str | Path | None, where: str = "", params: tuple = ()) -> list[dict]:
    """All matching rows, oldest first ([] when nothing was logged yet)."""
    with db.reading(path) as conn:
        if not _has_table(conn):
            return []
        sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM question_log {where} ORDER BY id"
        return [{**dict(r), "answered": bool(r["answered"])} for r in conn.execute(sql, params).fetchall()]


def log_question(
    question: str,
    route: str,
    answer: str,
    citations: int = 0,
    role: str = "student",
    branch: str = "",
    path: str | Path | None = None,
) -> None:
    """Store one answered chat turn without any identity. Never raises; blank questions are ignored."""
    try:
        if not str(question or "").strip() or _logging_paused():
            return
        route = _clean_route(route)
        topic = detect_topic(question)
        stored = PERSONAL_PLACEHOLDER if route == "personal" else mask_question(question)
        if not stored:
            return
        try:
            n_citations = max(0, int(citations or 0))
        except (TypeError, ValueError):
            n_citations = 0
        row = (
            db.now_iso(),
            "admin" if role == "admin" else "student",
            _clean_branch(branch) if role != "admin" else "",
            route,
            topic,
            stored,
            int(is_answered(answer)),
            n_citations,
        )
        with db.transaction(path) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO question_log (asked_at, role, branch, route, topic, question, answered, citations)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            conn.execute(
                "DELETE FROM question_log WHERE id NOT IN (SELECT id FROM question_log ORDER BY id DESC LIMIT ?)",
                (max(1, int(MAX_ROWS)),),
            )
    except Exception as exc:  # noqa: BLE001  (logging must never break the chat)
        logger.warning("question log: could not store a question (%s)", type(exc).__name__)


def summary(path: str | Path | None = None) -> dict:
    """Totals for the Insights page.

    Returns {"total", "answered", "unanswered", "by_route": {route: n}, "by_topic": {topic: n},
    "by_day": [{"day": "YYYY-MM-DD", "total": n, "unanswered": n}, ...]} where ``by_day`` has one
    entry for each of the last 14 days (UTC, oldest first, zeros included).
    """
    rows = _rows(path)
    total = len(rows)
    answered = sum(1 for r in rows if r["answered"])
    by_route = Counter(r["route"] for r in rows)
    by_topic = Counter(r["topic"] for r in rows)
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=offset)).isoformat() for offset in range(SUMMARY_DAYS - 1, -1, -1)]
    per_day = {day: {"day": day, "total": 0, "unanswered": 0} for day in days}
    for r in rows:
        entry = per_day.get(str(r["asked_at"])[:10])
        if entry is not None:
            entry["total"] += 1
            entry["unanswered"] += 0 if r["answered"] else 1
    return {
        "total": total,
        "answered": answered,
        "unanswered": total - answered,
        "by_route": {route: by_route[route] for route in ROUTES if by_route[route]},
        "by_topic": dict(by_topic.most_common()),
        "by_day": [per_day[day] for day in days],
    }


def _grouped(rows: list[dict], limit: int) -> list[dict]:
    """Group rows by normalised question text: most frequent first, then most recent."""
    groups: dict[str, dict] = {}
    for r in rows:  # oldest first, so the newest wording and date win
        key = normalize_question(r["question"])
        if not key:
            continue
        group = groups.setdefault(key, {"question": r["question"], "count": 0, "unanswered": 0, "topic": r["topic"]})
        group["count"] += 1
        group["unanswered"] += 0 if r["answered"] else 1
        group["question"] = r["question"]
        group["topic"] = r["topic"]
        group["last_asked"] = r["asked_at"]
    ordered = sorted(groups.values(), key=lambda g: g["last_asked"], reverse=True)
    ordered.sort(key=lambda g: g["count"], reverse=True)  # stable: ties stay newest first
    return ordered[: max(0, int(limit))]


def unanswered(limit: int = 50, path: str | Path | None = None) -> list[dict]:
    """Questions the documents could not answer, grouped: [{"question", "count", "last_asked", "topic", ...}].

    Personal questions are left out (their text is never stored).
    """
    rows = _rows(path, "WHERE answered = 0 AND route != 'personal' AND question != ?", (PERSONAL_PLACEHOLDER,))
    return _grouped(rows, limit)


def top_questions(limit: int = 20, path: str | Path | None = None) -> list[dict]:
    """The most asked non-personal questions, grouped like ``unanswered`` ("unanswered" counts the misses)."""
    rows = _rows(path, "WHERE route != 'personal' AND question != ?", (PERSONAL_PLACEHOLDER,))
    return _grouped(rows, limit)


def export_rows(path: str | Path | None = None) -> list[dict]:
    """Every stored row (EXPORT_COLUMNS keys), oldest first. Contains no identity by construction."""
    return _rows(path)


def rows_to_csv(rows: list[dict]) -> str:
    """CSV text of exported rows; cells that a spreadsheet would run as a formula are neutralised."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        cells = []
        for column in EXPORT_COLUMNS:
            value = r.get(column, "")
            if column == "answered":
                value = "yes" if value else "no"
            value = str(value)
            if value[:1] in ("=", "+", "-", "@"):
                value = "'" + value
            cells.append(value)
        writer.writerow(cells)
    return out.getvalue()


def clear(path: str | Path | None = None) -> int:
    """Delete every logged question; returns how many rows were removed."""
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        return int(conn.execute("DELETE FROM question_log").rowcount)
