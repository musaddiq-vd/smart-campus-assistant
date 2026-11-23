"""Email notifications to students: admin replies on requests, fee reminders and new notices.

Every email first goes into an outbox table in the app's SQLite database, then a background
thread sends it over ONE SMTP connection per batch (``mailer.smtp_session``). This keeps the
admin's click fast, never queues the same email twice (each email has a unique ``dedupe_key``)
and lets the admin see what was sent.

Delivery rules:
- Without SMTP settings nothing is sent: the email is kept as "skipped", so turning SMTP on
  later does not flood students with old emails.
- A failed email is tried again after 5 and then 30 minutes; after 3 attempts it is "failed".
- An email stuck in "sending" for 15 minutes (the server stopped mid-send) is picked up again.
  So delivery is at-least-once: in that rare case a student may get the same email twice.
- Retries run whenever a new email is queued, when the admin opens the Overview page, and
  with ``python -m smart_campus_assistant.notifications deliver``.
- ``SCA_EMAIL_NOTIFICATIONS=0`` turns queueing off completely.
- Only a student's own data ever goes into their email. Notices go only to students of the
  notice's branches who have an account.

Fee reminders are not sent on their own: run ``python -m smart_campus_assistant.notifications
fee-reminders`` daily (cron / Task Scheduler) or use the button on the admin Overview page.
A student gets one "due soon" email in the last 7 days before the due date and, once the fee
is overdue, at most one email per week. Reminders use the same "today" as the student
dashboard (``reminder_date``): the records' ``as_of`` date when they carry one (the synthetic
demo file is pinned to one day, so its numbers never drift), else the real date.

Every public function takes an optional ``path`` (database file) so tests can use a temporary database.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from smart_campus_assistant import db, mailer, student_data
from smart_campus_assistant.config import COLLEGE_NAME

log = logging.getLogger(__name__)

KINDS: dict[str, str] = {
    "request_reply": "Request reply",
    "fee_reminder": "Fee reminder",
    "new_notice": "New notice",
}
STATUSES = ("pending", "sending", "sent", "skipped", "failed")
REMIND_DAYS_BEFORE = 7
OVERDUE_REPEAT_DAYS = 7
MAX_BODY_QUOTE = 1500
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (5 * 60, 30 * 60)  # wait before the 2nd and the 3rd attempt
STALE_SENDING_SECONDS = 15 * 60
SIGN_OFF = (f"\n\n{COLLEGE_NAME} helpdesk (fictional demo)\n"
            "This is an automatic email; please reply in the helpdesk, not to this email.\n")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        kind            TEXT NOT NULL,
        dedupe_key      TEXT NOT NULL UNIQUE,
        student_id      TEXT NOT NULL DEFAULT '',
        recipient       TEXT NOT NULL,
        subject         TEXT NOT NULL,
        body            TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        error           TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL,
        sent_at         TEXT NOT NULL DEFAULT '',
        attempts        INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL DEFAULT 0,
        claimed_at      REAL NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS notifications_status ON notifications(status)",
)
# Columns added after the first version of the table (an existing outbox gets them on first use).
_ADDED_COLUMNS = {
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "next_attempt_at": "REAL NOT NULL DEFAULT 0",
    "claimed_at": "REAL NOT NULL DEFAULT 0",
}
_DUE_SQL = "(status = 'pending' AND next_attempt_at <= :now) OR (status = 'sending' AND claimed_at <= :stale)"

_deliver_lock = threading.Lock()


def enabled() -> bool:
    """False when SCA_EMAIL_NOTIFICATIONS is "0" / "false" / "off" / "no"."""
    return os.getenv("SCA_EMAIL_NOTIFICATIONS", "1").strip().lower() not in ("0", "false", "off", "no")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        conn.execute(statement)
    have = {row["name"] for row in conn.execute("PRAGMA table_info(notifications)")}
    for name, definition in _ADDED_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE notifications ADD COLUMN {name} {definition}")


def _clip(text: object, limit: int = MAX_BODY_QUOTE) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _rupees(amount: object) -> str:
    try:
        value = int(round(float(amount or 0)))
    except (TypeError, ValueError):
        return f"Rs {amount}"
    digits = str(abs(value))
    if len(digits) > 3:
        head, groups = digits[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join(([head] if head else []) + groups + [digits[-3:]])
    return f"Rs {'-' if value < 0 else ''}{digits}"


# ---------------------------------------------------------------- queue


def queue_many(emails: Iterable[Mapping], path: str | Path | None = None) -> int:
    """Add emails to the outbox in ONE transaction; returns how many were new.

    Each email is a mapping with kind, recipient, subject, body, dedupe_key and optional student_id.
    Nothing is queued when notifications are off; emails without a valid address or with a
    dedupe_key that is already in the outbox are left out.
    """
    rows = []
    for email in emails:
        if email["kind"] not in KINDS:
            raise ValueError(f"Unknown notification kind: {email['kind']}")
        recipient = str(email.get("recipient") or "").strip().lower()
        if "@" in recipient:
            rows.append((email["kind"], email["dedupe_key"], str(email.get("student_id") or "").upper(),
                         recipient, email["subject"], email["body"]))
    if not enabled() or not rows:
        return 0
    stamp = db.now_iso()
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO notifications (kind, dedupe_key, student_id, recipient, subject, body, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(*row, stamp) for row in rows],
        )
        return conn.total_changes - before


def queue(
    kind: str, recipient: str, subject: str, body: str, dedupe_key: str,
    student_id: str = "", path: str | Path | None = None,
) -> bool:
    """Add one email to the outbox; False when notifications are off, the address is empty or it is a duplicate."""
    email = {"kind": kind, "recipient": recipient, "subject": subject, "body": body,
             "dedupe_key": dedupe_key, "student_id": student_id}
    return queue_many([email], path) == 1


def _student_accounts(path: str | Path | None) -> dict[str, str]:
    """Student ID -> sign-in email of every student who has an account."""
    with db.reading(path) as conn:
        return {row["student_id"]: row["email"] for row in conn.execute(
            "SELECT student_id, email FROM users WHERE role = 'student' AND student_id IS NOT NULL")}


def notify_request_reply(request: Mapping, path: str | Path | None = None) -> bool:
    """Queue an email for the newest admin reply on ``request`` (as returned by support_requests.add_message)."""
    replies = [m for m in request.get("messages") or [] if m.get("author_role") == "admin"]
    if not replies:
        return False
    reply = replies[-1]
    sid = student_data.normalize_id(request.get("student_id"))
    recipient = _student_accounts(path).get(sid) or request.get("student_email") or ""
    status = str(request.get("status") or "").replace("_", " ")
    body = (
        f"Hello {request.get('student_name') or 'student'},\n\n"
        f"The admin office replied to your request {request.get('id')} \"{request.get('subject')}\":\n\n"
        f"{_clip(reply.get('body'))}\n\n"
        f"Status: {status}.\n"
        "Sign in to the helpdesk and open My requests to read the whole conversation or reply."
        + SIGN_OFF
    )
    return queue(
        "request_reply", recipient, f"Reply on your request {request.get('id')}: {request.get('subject')}",
        body, f"reply:{request.get('id')}:{reply.get('id')}", sid, path,
    )


def notice_recipients(notice: Mapping, path: str | Path | None = None) -> list[tuple[str, str, str]]:
    """(student_id, name, account email) of every student with an account in the notice's branches."""
    branches = {str(b).upper() for b in notice.get("branches") or ["ALL"]}
    accounts = _student_accounts(path)
    out = []
    for record in student_data.all_records(path):
        sid = record.get("student_id")
        if sid in accounts and ("ALL" in branches or str(record.get("branch") or "").upper() in branches):
            out.append((sid, record.get("name") or "student", accounts[sid]))
    return out


def notify_new_notice(notice: Mapping, path: str | Path | None = None) -> int:
    """Queue one email per student of the notice's branches (one transaction); returns how many were queued."""
    subject = f"New notice: {notice.get('title')}"
    emails = [
        {
            "kind": "new_notice", "recipient": email, "subject": subject, "student_id": sid,
            "dedupe_key": f"notice:{notice.get('id')}:{sid}",
            "body": (
                f"Hello {name},\n\nA new notice was published on {notice.get('published_at')}:\n\n"
                f"{notice.get('title')}\n\n{_clip(notice.get('body'))}\n\n"
                "You can also ask the helpdesk assistant about it."
                + SIGN_OFF
            ),
        }
        for sid, name, email in notice_recipients(notice, path)
    ]
    return queue_many(emails, path)


# ---------------------------------------------------------------- fee reminders


def reminder_date(record: Mapping | None) -> date:
    """The "today" fee reminders use: the record's ``as_of`` date if it has one, else the real date.

    This is the same date the student dashboard and the assistant use, so an email never shows
    other amounts than the app.
    """
    return _parse_date((record or {}).get("as_of")) or date.today()


def _parse_date(value: object) -> date | None:
    """A YYYY-MM-DD date (or a datetime string starting with one); None when missing or invalid."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def fee_reminder(record: Mapping, today: date | None = None) -> tuple[str, str, str] | None:
    """(dedupe stage, subject, body) when this student should get a fee reminder today, else None.

    Money and dates are computed here in code (student_data), never by a model.
    """
    fees = record.get("fees") or {}
    try:
        due = int(round(float(fees.get("due") or 0)))
    except (TypeError, ValueError):
        return None
    due_date = _parse_date(fees.get("due_date"))
    today = today or reminder_date(record)
    if due <= 0 or due_date is None:
        return None
    days_left = (due_date - today).days
    semester = fees.get("semester") or "this semester"
    name = record.get("name") or "student"
    if 0 <= days_left <= REMIND_DAYS_BEFORE:
        when = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
        body = (
            f"Hello {name},\n\nYour fee for {semester} is due {when}, on {due_date.isoformat()}.\n\n"
            f"Amount due: {_rupees(due)}\n\n"
            f"After the due date a late fee of {_rupees(student_data.FEE_LATE_FEE_PER_DAY)} per day is added "
            f"(up to {_rupees(student_data.FEE_LATE_FEE_CAP)}). You can pay on the Pay fees page of the helpdesk."
            + SIGN_OFF
        )
        return f"due-soon:{semester}", f"Fee reminder: {_rupees(due)} due on {due_date.isoformat()}", body
    if days_left < 0:
        late = student_data.fee_late_fee(due, due_date, today)
        week = late["days_overdue"] // OVERDUE_REPEAT_DAYS
        body = (
            f"Hello {name},\n\nYour fee for {semester} was due on {due_date.isoformat()} and is "
            f"{late['days_overdue']} day{'s' if late['days_overdue'] != 1 else ''} overdue.\n\n"
            f"Amount due: {_rupees(due)}\nLate fee so far: {_rupees(late['late_fee'])}\n"
            f"Total payable now: {_rupees(late['total_payable'])}\n\n"
            "Students with dues pending for a long time may not get their exam hall ticket. "
            f"You can pay the {_rupees(due)} due on the Pay fees page of the helpdesk; the late fee is settled "
            "at the accounts office. Raise a request in the helpdesk if something is wrong."
            + SIGN_OFF
        )
        return (f"overdue:{semester}:w{week}",
                f"Fee overdue: {_rupees(late['total_payable'])} payable now", body)
    return None


def queue_fee_reminders(path: str | Path | None = None, today: date | None = None,
                        records: Iterable[Mapping] | None = None) -> int:
    """Queue the fee reminders that are due for every student with an account; returns how many were new.

    ``today`` overrides the date for every student (CLI ``--date``); by default each record's
    ``reminder_date`` is used.
    """
    if not enabled():
        return 0
    accounts = _student_accounts(path)
    emails = []
    for record in records if records is not None else student_data.all_records(path):
        sid = record.get("student_id")
        if sid not in accounts:
            continue
        reminder = fee_reminder(record, today)
        if reminder:
            stage, subject, body = reminder
            emails.append({"kind": "fee_reminder", "recipient": accounts[sid], "subject": subject, "body": body,
                           "dedupe_key": f"fee:{sid}:{stage}", "student_id": sid})
    return queue_many(emails, path)


# ---------------------------------------------------------------- delivery


def _claim(path: str | Path | None, limit: int, now: float) -> list[dict]:
    """Mark up to ``limit`` due emails as "sending" (one more attempt each) and return them.

    Due: pending and past its retry time, or stuck in "sending" for STALE_SENDING_SECONDS.
    A stuck email that already used every attempt is marked "failed" instead.
    """
    params = {"now": now, "stale": now - STALE_SENDING_SECONDS, "limit": limit}
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE notifications SET status = 'failed', error = 'Sending was interrupted too often'"
            " WHERE status = 'sending' AND claimed_at <= :stale AND attempts >= :max",
            {**params, "max": MAX_ATTEMPTS},
        )
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, recipient, subject, body, attempts FROM notifications WHERE {_DUE_SQL}"
            " ORDER BY id LIMIT :limit", params)]
        conn.executemany(
            "UPDATE notifications SET status = 'sending', claimed_at = ?, attempts = attempts + 1 WHERE id = ?",
            [(now, r["id"]) for r in rows],
        )
    for row in rows:
        row["attempts"] += 1
    return rows


def _finish(path: str | Path | None, row: dict, ok: bool, configured: bool, now: float) -> str:
    """Store the outcome of one attempt; returns the new status."""
    if not configured:
        status, error, next_at = "skipped", "Email (SMTP) is not configured", 0.0
    elif ok:
        status, error, next_at = "sent", "", 0.0
    elif row["attempts"] >= MAX_ATTEMPTS:
        status, error, next_at = "failed", f"The mail server did not accept it ({row['attempts']} attempts)", 0.0
    else:
        delay = RETRY_DELAYS_SECONDS[min(row["attempts"], len(RETRY_DELAYS_SECONDS)) - 1]
        status, error, next_at = "pending", f"Attempt {row['attempts']} failed; trying again later", now + delay
    with db.transaction(path) as conn:
        conn.execute(
            "UPDATE notifications SET status = ?, error = ?, next_attempt_at = ?, sent_at = ? WHERE id = ?",
            (status, error, next_at, db.now_iso() if status == "sent" else "", row["id"]),
        )
    return status


def deliver_pending(path: str | Path | None = None, limit: int = 200, now: float | None = None) -> dict:
    """Send the emails that are due now over one SMTP connection.

    Returns counts {"sent", "skipped", "retry", "failed"}; never raises for one bad email.
    """
    result = {"sent": 0, "skipped": 0, "retry": 0, "failed": 0}
    with _deliver_lock:
        now = time.time() if now is None else now
        configured = mailer.email_configured()
        rows = _claim(path, limit, now)
        if not rows:
            return result
        with mailer.smtp_session() as send:
            for row in rows:
                ok = configured and send(row["recipient"], row["subject"], row["body"])
                status = _finish(path, row, ok, configured, now)
                result["retry" if status == "pending" else status] += 1
    return result


def due_count(path: str | Path | None = None, now: float | None = None) -> int:
    """How many emails are waiting to be (re)tried right now."""
    now = time.time() if now is None else now
    with db.reading(path) as conn:
        _ensure_schema(conn)
        return int(conn.execute(f"SELECT COUNT(*) FROM notifications WHERE {_DUE_SQL}",
                                {"now": now, "stale": now - STALE_SENDING_SECONDS}).fetchone()[0])


def deliver_in_background(path: str | Path | None = None) -> threading.Thread:
    """Send due emails on a daemon thread, so a page never waits for the mail server."""
    def run() -> None:
        try:
            deliver_pending(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Delivering notifications failed (%s)", type(exc).__name__)

    thread = threading.Thread(target=run, name="sca-notifications", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------- admin view


def recent(limit: int = 20, path: str | Path | None = None) -> list[dict]:
    """Newest outbox rows (without the email body)."""
    with db.reading(path) as conn:
        _ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT id, kind, student_id, recipient, subject, status, error, attempts, created_at, sent_at"
            " FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]


def counts(path: str | Path | None = None) -> dict:
    """Number of outbox rows per status (every status present) plus "total"."""
    result = {status: 0 for status in STATUSES}
    with db.reading(path) as conn:
        _ensure_schema(conn)
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM notifications GROUP BY status"):
            if row["status"] in result:
                result[row["status"]] = int(row["n"])
    result["total"] = sum(result.values())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Student email notifications")
    parser.add_argument("command", choices=("fee-reminders", "deliver"),
                        help="fee-reminders: queue the reminders that are due and send them; "
                             "deliver: send (or retry) waiting emails")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="fee-reminders only: work out reminders as of this day (YYYY-MM-DD) instead of "
                             "each record's own date")
    args = parser.parse_args(argv)
    if args.command == "fee-reminders":
        if not enabled():
            print("Email notifications are turned off (SCA_EMAIL_NOTIFICATIONS=0).")
            return 0
        when = args.date.isoformat() if args.date else "each student record's date (as_of, else today)"
        print(f"Queued {queue_fee_reminders(today=args.date)} fee reminder(s) as of {when}.")
    result = deliver_pending()
    print(f"Sent {result['sent']}, skipped {result['skipped']} (no SMTP), "
          f"will retry {result['retry']}, failed {result['failed']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
