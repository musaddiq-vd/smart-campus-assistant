"""Support requests (helpdesk tickets): students raise a request to the admin office and follow it.

Storage: two tables in the app's SQLite database (``smart_campus_assistant.db``), created
lazily on first use::

    support_requests          id "R-2026-0001", student_id, student_email, student_name, category,
                              subject, body, status, created_at, updated_at
    support_request_messages  id, request_id, author_role "student"|"admin", author_name, body, created_at

Every public function takes an optional ``path`` (database file) so tests can use a
temporary database. Student isolation is enforced here, not only in the UI: any call
made on behalf of a student passes ``student_id`` and only ever sees that student's rows.

The ``*_html`` helpers at the bottom are pure string builders shared by the two pages
(ui/student_requests.py and ui/admin_requests.py); every user-supplied value is escaped.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Mapping

from smart_campus_assistant import db
from smart_campus_assistant.notices import clean_input

CATEGORIES: dict[str, str] = {
    "fees": "Fees",
    "hostel": "Hostel",
    "library": "Library",
    "exams": "Exams",
    "certificates": "Certificates",
    "other": "Other",
}
STATUSES: dict[str, str] = {
    "open": "Open",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed": "Closed",
}
ROLES = ("student", "admin")

SUBJECT_MIN, SUBJECT_MAX = 5, 120
BODY_MIN, BODY_MAX = 10, 2000
MESSAGE_MIN, MESSAGE_MAX = 1, 2000
MAX_ACTIVE_PER_STUDENT = 5  # requests that are not closed

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS support_requests (
        id            TEXT PRIMARY KEY,
        student_id    TEXT NOT NULL,
        student_email TEXT NOT NULL DEFAULT '',
        student_name  TEXT NOT NULL DEFAULT '',
        category      TEXT NOT NULL,
        subject       TEXT NOT NULL,
        body          TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS support_requests_student ON support_requests(student_id)",
    """
    CREATE TABLE IF NOT EXISTS support_request_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id  TEXT NOT NULL REFERENCES support_requests(id) ON DELETE CASCADE,
        author_role TEXT NOT NULL CHECK (author_role IN ('student', 'admin')),
        author_name TEXT NOT NULL DEFAULT '',
        body        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS support_request_messages_request ON support_request_messages(request_id)",
)

# Numeric part of "R-2026-0001" (starts at character 8), used for the id sequence and for ordering.
_SEQ_SQL = "CAST(substr(id, 8) AS INTEGER)"


class RequestError(ValueError):
    """Invalid input or an action that is not allowed (message is safe to show in the UI)."""


# ---------- helpers ----------

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the two tables if they are missing (idempotent, cheap when they exist)."""
    for statement in _SCHEMA:
        conn.execute(statement)


def _norm_student_id(student_id) -> str:
    return str(student_id or "").strip().upper()


def _norm_request_id(request_id) -> str:
    return str(request_id or "").strip().upper()


def _clean_category(category) -> str:
    category = str(category or "").strip().lower()
    if category not in CATEGORIES:
        raise RequestError(f"Unknown category: {clean_input(category)[:40] or '(empty)'}.")
    return category


def _clean_status(status) -> str:
    status = str(status or "").strip().lower()
    if status not in STATUSES:
        raise RequestError(f"Unknown status: {clean_input(status)[:40] or '(empty)'}.")
    return status


def _clean_message(body) -> str:
    body = clean_input(body, multiline=True)
    if not MESSAGE_MIN <= len(body) <= MESSAGE_MAX:
        raise RequestError(f"Message must be {MESSAGE_MIN}-{MESSAGE_MAX} characters (got {len(body)}).")
    return body


def _not_found(request_id: str) -> RequestError:
    return RequestError(f"Request {clean_input(request_id)[:40] or '(empty)'} was not found.")


def _fetch(conn: sqlite3.Connection, request_id: str, student_id: str | None) -> dict | None:
    """One request row as a dict, or None (also None when ``student_id`` is given and is not the owner)."""
    row = conn.execute("SELECT * FROM support_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        return None
    if student_id is not None and row["student_id"] != _norm_student_id(student_id):
        return None
    return dict(row)


def _with_messages(conn: sqlite3.Connection, request: dict) -> dict:
    rows = conn.execute(
        "SELECT id, request_id, author_role, author_name, body, created_at FROM support_request_messages"
        " WHERE request_id = ? ORDER BY id",
        (request["id"],),
    ).fetchall()
    request["messages"] = [dict(r) for r in rows]
    return request


def _require(conn: sqlite3.Connection, request_id: str, student_id: str | None) -> dict:
    request = _fetch(conn, request_id, student_id)
    if request is None:
        # Same message for "does not exist" and "belongs to someone else": do not reveal other students' ids.
        raise _not_found(request_id)
    return request


# ---------- create / read ----------

def create_request(
    student_id, student_email, student_name, category, subject, body, path: str | Path | None = None
) -> dict:
    """Validate and store a new request for one student; returns it (status "open", no messages yet).

    Raises RequestError on bad input or when the student already has 5 requests that are not closed.
    """
    sid = _norm_student_id(student_id)
    if not sid:
        raise RequestError("Only a signed-in student can raise a request.")
    category = _clean_category(category)
    subject = clean_input(subject)
    body = clean_input(body, multiline=True)
    if not SUBJECT_MIN <= len(subject) <= SUBJECT_MAX:
        raise RequestError(f"Subject must be {SUBJECT_MIN}-{SUBJECT_MAX} characters (got {len(subject)}).")
    if not BODY_MIN <= len(body) <= BODY_MAX:
        raise RequestError(f"Details must be {BODY_MIN}-{BODY_MAX} characters (got {len(body)}).")
    email = clean_input(student_email).lower()
    name = clean_input(student_name) or "Student"
    stamp = db.now_iso()
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        active = conn.execute(
            "SELECT COUNT(*) FROM support_requests WHERE student_id = ? AND status != 'closed'", (sid,)
        ).fetchone()[0]
        if active >= MAX_ACTIVE_PER_STUDENT:
            raise RequestError(
                f"You already have {MAX_ACTIVE_PER_STUDENT} requests that are not closed. "
                "Close one before raising a new request."
            )
        seq = (conn.execute(f"SELECT MAX({_SEQ_SQL}) FROM support_requests").fetchone()[0] or 0) + 1
        request_id = f"R-{stamp[:4]}-{seq:04d}"
        conn.execute(
            "INSERT INTO support_requests (id, student_id, student_email, student_name, category, subject, body,"
            " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (request_id, sid, email, name, category, subject, body, stamp, stamp),
        )
        return _with_messages(conn, _require(conn, request_id, None))


def list_requests(
    student_id=None, status=None, category=None, search=None, path: str | Path | None = None
) -> list[dict]:
    """Requests newest first (without messages). With ``student_id`` only that student's.

    ``status`` / ``category`` filter exactly (unknown values raise RequestError); ``search`` matches
    the subject, the student's name, ID or email, and the request id (case-insensitive).
    """
    where: list[str] = []
    params: list[str] = []
    if student_id is not None:
        where.append("student_id = ?")
        params.append(_norm_student_id(student_id))
    if status:
        where.append("status = ?")
        params.append(_clean_status(status))
    if category:
        where.append("category = ?")
        params.append(_clean_category(category))
    needle = clean_input(search)[:100] if search else ""
    if needle:
        like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        fields = ("subject", "student_name", "student_id", "student_email", "id")
        where.append("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ")")
        params.extend([like] * len(fields))
    sql = "SELECT * FROM support_requests"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY created_at DESC, {_SEQ_SQL} DESC"
    with db.reading(path) as conn:
        _ensure_schema(conn)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_request(request_id, student_id=None, path: str | Path | None = None) -> dict | None:
    """One request with its "messages" (oldest first), or None.

    When ``student_id`` is given and that student does not own the request, returns None.
    """
    rid = _norm_request_id(request_id)
    if not rid:
        return None
    with db.reading(path) as conn:
        _ensure_schema(conn)
        request = _fetch(conn, rid, student_id)
        return _with_messages(conn, request) if request else None


def counts(path: str | Path | None = None) -> dict:
    """Number of requests per status (every status present, zero included) plus "total"."""
    result = {status: 0 for status in STATUSES}
    with db.reading(path) as conn:
        _ensure_schema(conn)
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM support_requests GROUP BY status"):
            if row["status"] in result:
                result[row["status"]] = int(row["n"])
    result["total"] = sum(result.values())
    return result


# ---------- replies / status ----------

def add_message(
    request_id, author_role, author_name, body, student_id=None, path: str | Path | None = None
) -> dict:
    """Add a reply to the thread and return the updated request (with "messages").

    A student (``author_role`` "student", ``student_id`` required) may only write on their own
    request. Nobody can reply on a closed request. An admin reply on an "open" request moves it
    to "in_progress"; a student reply on a "resolved" request reopens it to "open".
    """
    rid = _norm_request_id(request_id)
    role = str(author_role or "").strip().lower()
    if role not in ROLES:
        raise RequestError("Unknown author role.")
    if role == "student" and not _norm_student_id(student_id):
        raise RequestError("Only a signed-in student can reply to their request.")
    text = _clean_message(body)
    name = clean_input(author_name) or ("Admin office" if role == "admin" else "Student")
    stamp = db.now_iso()
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        request = _require(conn, rid, student_id if role == "student" else None)
        if request["status"] == "closed":
            raise RequestError(f"Request {rid} is closed, so it cannot take new replies.")
        new_status = request["status"]
        if role == "admin" and new_status == "open":
            new_status = "in_progress"
        elif role == "student" and new_status == "resolved":
            new_status = "open"
        conn.execute(
            "INSERT INTO support_request_messages (request_id, author_role, author_name, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (rid, role, name, text, stamp),
        )
        conn.execute(
            "UPDATE support_requests SET status = ?, updated_at = ? WHERE id = ?", (new_status, stamp, rid)
        )
        return _with_messages(conn, _require(conn, rid, None))


def set_status(request_id, status, path: str | Path | None = None) -> dict:
    """Admin action: set any valid status. Returns the updated request (with "messages")."""
    rid = _norm_request_id(request_id)
    status = _clean_status(status)
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        request = _require(conn, rid, None)
        if request["status"] != status:
            conn.execute(
                "UPDATE support_requests SET status = ?, updated_at = ? WHERE id = ?", (status, db.now_iso(), rid)
            )
        return _with_messages(conn, _require(conn, rid, None))


def close_own_request(request_id, student_id, path: str | Path | None = None) -> dict:
    """Student action: close one of their own requests (already closed is fine). Returns the request."""
    rid = _norm_request_id(request_id)
    if not _norm_student_id(student_id):
        raise RequestError("Only a signed-in student can close their request.")
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        request = _require(conn, rid, student_id)
        if request["status"] != "closed":
            conn.execute(
                "UPDATE support_requests SET status = 'closed', updated_at = ? WHERE id = ?", (db.now_iso(), rid)
            )
        return _with_messages(conn, _require(conn, rid, None))


# ---------- HTML helpers for the two pages (pure; all user text escaped) ----------

CATEGORY_ICONS = {"fees": "💳", "hostel": "🏠", "library": "📚", "exams": "📝", "certificates": "📜", "other": "💬"}

REQUESTS_CSS = """
<style>
.sca-chip.req-open .dot { background: var(--sca-brand, #5B5BD6); }
.sca-chip.req-in_progress .dot { background: var(--sca-personal, #E5A50A); }
.sca-chip.req-resolved .dot { background: var(--sca-simple, #0E9F6E); }
.sca-chip.req-closed .dot { background: var(--sca-muted, gray); }
.sca-req {
  border: 1px solid var(--sca-card-border, rgba(128,128,128,.25)); border-radius: var(--sca-radius, 14px);
  background: var(--sca-card-bg, transparent); padding: .75rem 1rem; margin: .15rem 0;
  border-left: 4px solid var(--c, var(--sca-brand, #5B5BD6));
}
.sca-req.open { --c: var(--sca-brand, #5B5BD6); }
.sca-req.in_progress { --c: var(--sca-personal, #E5A50A); }
.sca-req.resolved { --c: var(--sca-simple, #0E9F6E); }
.sca-req.closed { --c: var(--sca-muted, gray); }
.sca-req .head { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
.sca-req .subject { font-weight: 650; font-size: 1rem; overflow-wrap: anywhere; }
.sca-req .meta { font-size: .78rem; color: var(--sca-muted, gray); margin-top: .2rem; overflow-wrap: anywhere; }
.sca-req .who { font-size: .84rem; margin-top: .3rem; overflow-wrap: anywhere; }
.sca-thread { display: flex; flex-direction: column; gap: .55rem; margin: .5rem 0 1rem 0; }
.sca-msg {
  max-width: 85%; border: 1px solid var(--sca-card-border, rgba(128,128,128,.25)); border-radius: 14px;
  background: var(--sca-card-bg, transparent); padding: .55rem .8rem; align-self: flex-start;
}
.sca-msg.mine {
  align-self: flex-end; background: color-mix(in srgb, var(--sca-brand, #5B5BD6) 10%, transparent);
  border-color: color-mix(in srgb, var(--sca-brand, #5B5BD6) 30%, transparent);
}
.sca-msg .by { font-size: .74rem; color: var(--sca-muted, gray); }
.sca-msg .by b { color: color-mix(in srgb, currentColor 88%, transparent); font-weight: 650; }
.sca-msg .text { font-size: .9rem; margin-top: .2rem; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
"""


def category_label(category) -> str:
    return CATEGORIES.get(str(category or ""), "Other")


def status_label(status) -> str:
    return STATUSES.get(str(status or ""), "Open")


def nice_time(stamp) -> str:
    """``2026-11-03T10:15:00+00:00`` -> ``03 Nov 2026, 10:15 UTC`` (the text itself if it is not a timestamp)."""
    try:
        return datetime.fromisoformat(str(stamp)).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return str(stamp or "")


def status_chip_html(status) -> str:
    """Status pill, same markup as ``ui.components.chip_html`` with a class per status."""
    status = str(status or "") if str(status or "") in STATUSES else "open"
    return f'<span class="sca-chip req-{status}"><span class="dot"></span>{escape(STATUSES[status])}</span>'


def request_card_html(request: Mapping, show_student: bool = False) -> str:
    """One request as a card: subject, status chip, id / category / dates; the student line for admins."""
    status = str(request.get("status") or "") if str(request.get("status") or "") in STATUSES else "open"
    category = str(request.get("category") or "other")
    meta = (
        f"{request.get('id', '')} · {category_label(category)} · raised {nice_time(request.get('created_at'))}"
        f" · updated {nice_time(request.get('updated_at'))}"
    )
    who = ""
    if show_student:
        bits = [str(request.get(k) or "") for k in ("student_name", "student_id", "student_email")]
        who = f'<div class="who">👤 {escape(" · ".join(b for b in bits if b))}</div>'
    return (
        f'<div class="sca-req {status}"><div class="head"><span>{CATEGORY_ICONS.get(category, "💬")}</span>'
        f'<span class="subject">{escape(str(request.get("subject") or ""))}</span>{status_chip_html(status)}</div>'
        f'<div class="meta">{escape(meta)}</div>{who}</div>'
    )


def _message_html(role: str, name: str, body: str, stamp, viewer_role: str) -> str:
    mine = " mine" if role == viewer_role else ""
    tag = "Admin office" if role == "admin" else "Student"
    return (
        f'<div class="sca-msg {escape(role)}{mine}"><div class="by"><b>{escape(name or tag)}</b> · {tag}'
        f' · {escape(nice_time(stamp))}</div><div class="text">{escape(body)}</div></div>'
    )


def thread_html(request: Mapping, viewer_role: str = "student") -> str:
    """The conversation: the original details first, then every reply (the viewer's own on the right)."""
    parts = [
        _message_html("student", str(request.get("student_name") or ""), str(request.get("body") or ""),
                      request.get("created_at"), viewer_role)
    ]
    for message in request.get("messages") or []:
        role = "admin" if message.get("author_role") == "admin" else "student"
        parts.append(_message_html(role, str(message.get("author_name") or ""), str(message.get("body") or ""),
                                   message.get("created_at"), viewer_role))
    return f'<div class="sca-thread">{"".join(parts)}</div>'
