"""Audited edits of student records: fee payments, library books, attendance and hostel rooms.

The synthetic records in data/students.json and the self-registered students in the
``students`` table are never rewritten. An edit stores the student's FULL updated record in
``student_record_overrides`` (it wins over both sources when ``student_data`` loads records)
and adds one row to ``record_audit``, both in a single write transaction.

Roles: an admin may call everything. A student may only call ``record_fee_payment`` and
only for their own student id (``actor_student_id``). Payments are simulated: no payment
credential is ever asked for or stored, the mode is only a label.

Errors: ``RecordError`` (a ``ValueError`` whose message is safe to show in the UI) for bad
input, ``PermissionError`` for role violations.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from smart_campus_assistant import db, student_data
from smart_campus_assistant.notices import clean_input

PAYMENT_MODES = ("UPI", "Card", "NetBanking", "Cash", "NEFT")
MAX_BOOKS = 4  # library rule: at most 4 books at a time
LOAN_DAYS = 14
RECEIPT_PREFIX = "XYZP"  # P = paid through the portal (the seeded receipts use XYZR / XYZS)
TITLE_MAX = 150
AUTHOR_MAX = 80
BOOK_ID_MAX = 30
HOSTEL_TEXT_MAX = 20
ROLES = ("admin", "student")

_BOOK_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9\-/]*")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_record_overrides (
    student_id  TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    actor_email TEXT NOT NULL,
    actor_role  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS record_audit_student ON record_audit(student_id, id);
"""


class RecordError(ValueError):
    """Invalid record change (message is safe to show in the UI)."""


# ---------------------------------------------------------------- helpers


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create this module's tables if they are missing (safe inside a transaction)."""
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _rs(amount: int) -> str:
    return student_data._rs(amount)  # noqa: SLF001  (same "Rs 1,35,000" format as the prompt view)


def _actor(actor_email: object, actor_role: object) -> tuple[str, str]:
    role = str(actor_role or "").strip().lower()
    email = clean_input(actor_email).lower()
    if role not in ROLES or not email:
        raise PermissionError("Sign in with a student or admin account to change records.")
    return email, role


def _require_admin(role: str) -> None:
    if role != "admin":
        raise PermissionError("Only an admin can make this change.")


def _base_record(student_id: object, path: str | Path | None) -> tuple[str, dict]:
    """(normalised id, current record as ``student_data`` sees it); RecordError for an unknown student."""
    sid = student_data.normalize_id(student_id)
    record = None
    if sid:
        record = student_data.get_student(sid) if path is None else student_data.get_student(sid, db_path=path)
    if not record:
        raise RecordError("No student record was found for this student ID.")
    return sid, record


def _today(record: dict) -> date:
    """The data's "today": the record's ``as_of`` date, else the real date."""
    return student_data._date(record.get("as_of")) or date.today()  # noqa: SLF001


def _whole_rupees(amount: object) -> int:
    if isinstance(amount, bool):
        raise RecordError("Enter the amount as a whole number of rupees.")
    if isinstance(amount, int):
        return amount
    if isinstance(amount, float) and math.isfinite(amount) and amount.is_integer():
        return int(amount)
    if isinstance(amount, str) and re.fullmatch(r"\s*[+-]?\d+\s*", amount):
        return int(amount)
    raise RecordError("Enter the amount as a whole number of rupees.")


def _commit(
    sid: str,
    base: dict,
    path: str | Path | None,
    action: str,
    email: str,
    role: str,
    change,
) -> dict:
    """Run ``change(record, conn, audit_id) -> detail`` on the latest record and save it with an audit row.

    Everything happens in ONE write transaction: the latest override is re-read inside it (so two
    edits cannot overwrite each other), then the override and the audit row are written together.
    """
    as_of = base.get("as_of")
    with db.transaction(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT record_json FROM student_record_overrides WHERE student_id = ?", (sid,)
        ).fetchone()
        try:
            record = json.loads(row["record_json"]) if row else None
        except (TypeError, ValueError):
            record = None
        if not isinstance(record, dict):
            record = json.loads(json.dumps(base))  # deep copy: never touch the cached JSON record
        record["as_of"] = as_of
        audit_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM record_audit").fetchone()[0]
        detail = change(record, conn, audit_id)
        stored = {k: v for k, v in record.items() if k != "as_of"}  # as_of always comes from the students file
        stamp = db.now_iso()
        conn.execute(
            "INSERT INTO student_record_overrides (student_id, record_json, updated_at, updated_by)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(student_id) DO UPDATE SET record_json = excluded.record_json,"
            " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (sid, json.dumps(stored, ensure_ascii=False), stamp, email),
        )
        conn.execute(
            "INSERT INTO record_audit (id, student_id, action, detail_json, actor_email, actor_role, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (audit_id, sid, action, json.dumps(detail, ensure_ascii=False), email, role, stamp),
        )
    return record


# ---------------------------------------------------------------- fees


def record_fee_payment(
    student_id: object,
    amount: object,
    mode: object,
    *,
    actor_email: str,
    actor_role: str,
    actor_student_id: object = None,
    path: str | Path | None = None,
) -> dict:
    """Record a (simulated) fee payment and return the updated record.

    ``amount`` is a whole number of rupees, more than 0 and at most the current due. The new
    transaction is the last item of ``record["fees"]["transactions"]``. The status becomes
    "paid" when nothing is due any more; otherwise an "overdue" bill stays "overdue" and any
    other bill is "partially_paid". A student may only pay for ``actor_student_id`` == their own id.
    """
    email, role = _actor(actor_email, actor_role)
    sid = student_data.normalize_id(student_id)
    if role == "student" and (not sid or student_data.normalize_id(actor_student_id) != sid):
        raise PermissionError("You can only pay your own fees.")
    sid, base = _base_record(sid, path)
    rupees = _whole_rupees(amount)
    if rupees <= 0:
        raise RecordError("The amount must be more than Rs 0.")
    wanted = clean_input(mode).lower()
    pay_mode = next((m for m in PAYMENT_MODES if m.lower() == wanted), None)
    if pay_mode is None:
        raise RecordError("Choose a payment mode: " + ", ".join(PAYMENT_MODES) + ".")

    def change(record: dict, conn: sqlite3.Connection, audit_id: int) -> dict:
        fees = record.get("fees")
        if not isinstance(fees, dict):
            raise RecordError("This student has no fee record.")
        due = _whole_rupees_or_zero(fees.get("due"))
        if due <= 0:
            raise RecordError("Nothing is due for this student.")
        if rupees > due:
            raise RecordError(f"The amount cannot be more than the current due of {_rs(due)}.")
        today = _today(record)
        receipt = f"{RECEIPT_PREFIX}-{today.year}-{audit_id:05d}"  # audit ids are never reused, so this is unique
        paid = _whole_rupees_or_zero(fees.get("paid")) + rupees
        new_due = due - rupees
        old_status = str(fees.get("status") or "")
        fees["paid"], fees["due"] = paid, new_due
        fees["status"] = "paid" if new_due == 0 else ("overdue" if old_status == "overdue" else "partially_paid")
        transactions = [t for t in (fees.get("transactions") or []) if isinstance(t, dict)]
        transactions.append({"date": today.isoformat(), "amount": rupees, "mode": pay_mode, "receipt_no": receipt})
        fees["transactions"] = transactions
        return {
            "summary": f"Fee payment of {_rs(rupees)} via {pay_mode} (receipt {receipt}); due now {_rs(new_due)}",
            "amount": rupees, "mode": pay_mode, "receipt_no": receipt, "date": today.isoformat(),
            "due_before": due, "due_after": new_due, "status_before": old_status, "status_after": fees["status"],
        }

    return _commit(sid, base, path, "fee_payment", email, role, change)


def _whole_rupees_or_zero(value: object) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- library


def _library(record: dict) -> tuple[dict, list[dict]]:
    library = record.get("library")
    if not isinstance(library, dict):
        library = record["library"] = {"books_issued": [], "fine_due": 0}
    books = [b for b in (library.get("books_issued") or []) if isinstance(b, dict)]
    library["books_issued"] = books
    return library, books


def _book_id(book_id: object) -> str:
    bid = re.sub(r"\s+", "", clean_input(book_id)).upper()
    if not bid or len(bid) > BOOK_ID_MAX or not _BOOK_ID_RE.fullmatch(bid):
        raise RecordError("Enter a book ID such as LIB-CS-0421 (letters, digits and hyphens).")
    return bid


def _text(value: object, label: str, max_len: int) -> str:
    text = clean_input(value)
    if len(text) < 2 or len(text) > max_len:
        raise RecordError(f"{label} must be 2 to {max_len} characters.")
    return text


def issue_book(
    student_id: object,
    book_id: object,
    title: object,
    author: object,
    *,
    actor_email: str,
    actor_role: str,
    path: str | Path | None = None,
) -> dict:
    """Issue a library book (admin only): at most 4 books, no duplicate book id, due in 14 days."""
    email, role = _actor(actor_email, actor_role)
    _require_admin(role)
    sid, base = _base_record(student_id, path)
    bid = _book_id(book_id)
    book_title = _text(title, "The title", TITLE_MAX)
    book_author = _text(author, "The author", AUTHOR_MAX)

    def change(record: dict, conn: sqlite3.Connection, audit_id: int) -> dict:
        library, books = _library(record)
        if any(str(b.get("book_id") or "").upper() == bid for b in books):
            raise RecordError(f"Book {bid} is already issued to this student.")
        if len(books) >= MAX_BOOKS:
            raise RecordError(f"A student can hold at most {MAX_BOOKS} library books at a time.")
        today = _today(record)
        due = today + timedelta(days=LOAN_DAYS)
        books.append({"book_id": bid, "title": book_title, "author": book_author,
                      "issue_date": today.isoformat(), "due_date": due.isoformat(), "status": "issued"})
        return {"summary": f"Issued book {bid} \"{book_title}\", due {due.isoformat()}",
                "book_id": bid, "title": book_title, "author": book_author,
                "issue_date": today.isoformat(), "due_date": due.isoformat()}

    return _commit(sid, base, path, "issue_book", email, role, change)


def return_book(
    student_id: object,
    book_id: object,
    *,
    actor_email: str,
    actor_role: str,
    path: str | Path | None = None,
) -> dict:
    """Take back a library book (admin only) and recompute the fine that is still due."""
    email, role = _actor(actor_email, actor_role)
    _require_admin(role)
    sid, base = _base_record(student_id, path)
    bid = re.sub(r"\s+", "", clean_input(book_id)).upper()
    if not bid:
        raise RecordError("Choose the book to return.")

    def change(record: dict, conn: sqlite3.Connection, audit_id: int) -> dict:
        library, books = _library(record)
        returned = next((b for b in books if str(b.get("book_id") or "").upper() == bid), None)
        if returned is None:
            raise RecordError(f"Book {bid} is not issued to this student.")
        today = _today(record)
        remaining = [b for b in books if b is not returned]
        library["books_issued"] = remaining
        # Choice: the fine of the returned book is collected at the library counter when the book
        # comes back, so ``fine_due`` becomes the sum of the fines of the books that are still out
        # (student_data.library_book_fine: Rs 5 per day after the due date, capped at Rs 500 per book).
        fine_paid = student_data.library_book_fine(returned.get("due_date"), today)
        fine_before = library.get("fine_due", 0)
        library["fine_due"] = sum(student_data.library_book_fine(b.get("due_date"), today) for b in remaining)
        note = f" (fine {_rs(fine_paid)} settled)" if fine_paid else ""
        return {"summary": f"Returned book {bid} \"{returned.get('title', '')}\"{note}",
                "book_id": bid, "title": returned.get("title"), "fine_settled": fine_paid,
                "fine_due_before": fine_before, "fine_due_after": library["fine_due"]}

    return _commit(sid, base, path, "return_book", email, role, change)


# ---------------------------------------------------------------- attendance


def update_attendance(
    student_id: object,
    course_code: object,
    pct: object,
    *,
    actor_email: str,
    actor_role: str,
    path: str | Path | None = None,
) -> dict:
    """Set one course's attendance (admin only): 0-100, one decimal; overall = mean of recorded courses."""
    email, role = _actor(actor_email, actor_role)
    _require_admin(role)
    sid, base = _base_record(student_id, path)
    code = re.sub(r"\s+", "", clean_input(course_code)).upper()
    try:
        value = float(pct) if not isinstance(pct, bool) else math.nan
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or value < 0 or value > 100:
        raise RecordError("Attendance must be a number from 0 to 100.")
    value = round(value, 1)

    def change(record: dict, conn: sqlite3.Connection, audit_id: int) -> dict:
        attendance = record.get("attendance")
        courses = (attendance or {}).get("courses") if isinstance(attendance, dict) else None
        course = next(
            (c for c in (courses or []) if isinstance(c, dict) and str(c.get("code") or "").upper() == code), None
        ) if code else None
        if course is None:
            raise RecordError("This course is not in the student's record.")
        before, overall_before = course.get("pct"), attendance.get("overall_pct")
        course["pct"] = value
        recorded = [float(c["pct"]) for c in courses
                    if isinstance(c, dict) and isinstance(c.get("pct"), (int, float)) and not isinstance(c["pct"], bool)]
        attendance["overall_pct"] = round(sum(recorded) / len(recorded), 1)
        return {"summary": f"Attendance in {code} set to {value}% (overall now {attendance['overall_pct']}%)",
                "course_code": code, "pct_before": before, "pct_after": value,
                "overall_before": overall_before, "overall_after": attendance["overall_pct"]}

    return _commit(sid, base, path, "update_attendance", email, role, change)


# ---------------------------------------------------------------- hostel


def set_hostel(
    student_id: object,
    block: object = None,
    room: object = None,
    *,
    actor_email: str,
    actor_role: str,
    path: str | Path | None = None,
) -> dict:
    """Allot a hostel room (admin only); leave ``block`` and ``room`` empty to make the student a day scholar.

    Only the room changes. The semester bill is not recalculated here.
    """
    email, role = _actor(actor_email, actor_role)
    _require_admin(role)
    sid, base = _base_record(student_id, path)
    new_block, new_room = clean_input(block), clean_input(room)
    if not new_block and not new_room:
        hostel = None
    elif not new_block or not new_room:
        raise RecordError("Enter both the block and the room, or leave both empty for a day scholar.")
    elif len(new_block) > HOSTEL_TEXT_MAX or len(new_room) > HOSTEL_TEXT_MAX:
        raise RecordError(f"Block and room can be at most {HOSTEL_TEXT_MAX} characters each.")
    else:
        hostel = {"block": new_block, "room": new_room}

    def change(record: dict, conn: sqlite3.Connection, audit_id: int) -> dict:
        before = record.get("hostel")
        record["hostel"] = dict(hostel) if hostel else None
        summary = f"Hostel set to Block {new_block}, Room {new_room}" if hostel else "Hostel cleared (day scholar)"
        return {"summary": summary, "before": before, "after": hostel}

    return _commit(sid, base, path, "set_hostel", email, role, change)


# ---------------------------------------------------------------- audit trail


def list_audit(student_id: object = None, limit: int = 50, path: str | Path | None = None) -> list[dict]:
    """Audit rows, newest first: {id, student_id, action, detail, actor_email, actor_role, created_at}.

    ``student_id`` limits the list to one student. Returns [] when nothing was ever changed.
    """
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 50
    sid = student_data.normalize_id(student_id)
    sql = "SELECT id, student_id, action, detail_json, actor_email, actor_role, created_at FROM record_audit"
    args: list = []
    if sid:
        sql += " WHERE student_id = ?"
        args.append(sid)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    try:
        if not db.db_path(path).exists():
            return []
        with db.reading(path) as conn:
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error:  # the table is created by the first change
        return []
    out = []
    for row in rows:
        try:
            detail = json.loads(row["detail_json"])
        except (TypeError, ValueError):
            detail = {}
        out.append({
            "id": row["id"], "student_id": row["student_id"], "action": row["action"],
            "detail": detail if isinstance(detail, dict) else {}, "actor_email": row["actor_email"],
            "actor_role": row["actor_role"], "created_at": row["created_at"],
        })
    return out
