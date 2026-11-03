"""Synthetic student records: loading, lookup and a compact prompt view of ONE student.

Privacy: callers must only pass the logged-in student's own record to a prompt.
Nothing here ever builds text that mixes several students.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from smart_campus_assistant.config import BRANCHES, get_settings

LOW_ATTENDANCE_PCT = 75.0

# Money rules from the college documents (fee_structure.md "Late fee", library rules "Overdue fine").
FEE_LATE_FEE_PER_DAY = 100  # Rs per day, from the day after the due date
FEE_LATE_FEE_CAP = 5000  # Rs per semester
LIBRARY_FINE_PER_DAY = 5  # Rs per day per book, after the due date
LIBRARY_FINE_CAP = 500  # Rs per book
STUDENT_ID_RE = re.compile(r"\bXYZ\d{2}[A-Z]{2,3}\d{3}\b", flags=re.IGNORECASE)

# Parsed files, keyed by resolved path (clear with clear_cache()).
_CACHE: dict[str, dict[str, dict]] = {}


def _default_path() -> Path:
    return get_settings().students_file


def normalize_id(student_id: object) -> str:
    """Upper-case ID with all whitespace removed ("" for None)."""
    return re.sub(r"\s+", "", str(student_id or "")).upper()


def load_students(path: str | Path | None = None) -> dict[str, dict]:
    """Return {STUDENT_ID: record}; empty dict if the file is missing or invalid.

    Each record gets an ``as_of`` key copied from the file (used for "days left").
    """
    path = Path(path) if path else _default_path()
    key = str(path.resolve())
    if key in _CACHE:
        return _CACHE[key]
    students: dict[str, dict] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        as_of = data.get("as_of") if isinstance(data, dict) else None
        rows = data.get("students", []) if isinstance(data, dict) else data
        for row in rows or []:
            if isinstance(row, dict) and row.get("student_id"):
                record = dict(row)
                record.setdefault("as_of", as_of)
                students[normalize_id(row["student_id"])] = record
    except (OSError, ValueError, AttributeError):
        students = {}
    _CACHE[key] = students
    return students


def clear_cache() -> None:
    """Forget parsed files (tests use this after writing a new students.json)."""
    _CACHE.clear()


def get_student(student_id: object) -> dict | None:
    """Look up one student, case-insensitive and ignoring spaces."""
    sid = normalize_id(student_id)
    return load_students().get(sid) if sid else None


def list_students() -> list[dict]:
    """Minimal fields for the UI login picker (no fees, marks or contact data)."""
    return [
        {"student_id": sid, "name": r.get("name", ""), "branch": r.get("branch", ""), "year": r.get("year")}
        for sid, r in sorted(load_students().items())
    ]


def _name_in(name: str, text: str) -> bool:
    """Full name, or first name (3+ letters) as a whole word."""
    name = name.lower().strip()
    if not name:
        return False
    if name in text:
        return True
    first = name.split()[0]
    return len(first) >= 3 and re.search(rf"\b{re.escape(first)}\b", text) is not None


def students_named_in(text: str, exclude_id: object = None) -> list[str]:
    """IDs of students whose name appears in ``text`` (optionally skipping one ID).

    When another student shares a first name with the excluded student, only the full name counts.
    """
    lowered = text.lower()
    students = load_students()
    skip = normalize_id(exclude_id)
    own_first = str((students.get(skip) or {}).get("name", "")).lower().split()[:1]
    hits = []
    for sid, record in students.items():
        if sid == skip:
            continue
        name = str(record.get("name", ""))
        if name.lower().split()[:1] == own_first and own_first:
            if name.lower() in lowered:
                hits.append(sid)
        elif _name_in(name, lowered):
            hits.append(sid)
    return hits


# ---------------------------------------------------------------- prompt view


def _rs(amount: object) -> str:
    """Rs amount with Indian digit grouping: 135000 -> "Rs 1,35,000"."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return f"Rs {amount}"
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        s = f"{head},{tail}"
    return f"Rs {sign}{s}"


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def days_overdue(due_date: object, as_of: object) -> int:
    """Whole days after ``due_date`` up to ``as_of`` (0 when not yet overdue or a date is invalid).

    Paying on the due date is on time, so the first overdue day is the day after it.
    """
    due, today = _date(due_date), _date(as_of)
    if due is None or today is None:
        return 0
    return max(0, (today - due).days)


def accrued_charge(due_date: object, as_of: object, per_day: int, cap: int | None = None) -> tuple[int, int]:
    """(days overdue, charge so far) for a per-day charge with an optional cap. Pure function."""
    days = days_overdue(due_date, as_of)
    amount = days * per_day
    if cap is not None:
        amount = min(amount, cap)
    return days, amount


def fee_late_fee(due_amount: object, due_date: object, as_of: object) -> dict:
    """Late fee facts for unpaid semester dues, following the fee_structure.md rule.

    Rs 100 for every day after the due date, capped at Rs 5,000 per semester; nothing when
    nothing is due. Returns {"days_overdue", "late_fee", "total_payable"} (amounts in Rs).
    """
    try:
        due = max(0, int(round(float(due_amount or 0))))
    except (TypeError, ValueError):
        due = 0
    days, fee = accrued_charge(due_date, as_of, FEE_LATE_FEE_PER_DAY, FEE_LATE_FEE_CAP) if due else (0, 0)
    return {"days_overdue": days, "late_fee": fee, "total_payable": due + fee}


def library_book_fine(due_date: object, as_of: object) -> int:
    """Overdue fine for ONE book: Rs 5 per day after the due date, capped at Rs 500."""
    return accrued_charge(due_date, as_of, LIBRARY_FINE_PER_DAY, LIBRARY_FINE_CAP)[1]


def _days_note(due: object, today: date) -> str:
    """"(27 days left)" / "(due today)" / "(OVERDUE by 3 days)"."""
    d = _date(due)
    if d is None:
        return ""
    days = (d - today).days
    if days > 0:
        return f"({days} day{'s' if days != 1 else ''} left)"
    if days == 0:
        return "(due today)"
    return f"(OVERDUE by {-days} day{'s' if days != -1 else ''})"


def _late_fee_line(due: object, due_date: object, today: date) -> str:
    """Computed late fee and total payable, so the answer model never does date math."""
    late = fee_late_fee(due, due_date, today)
    if not late["late_fee"]:
        return (
            f"  Late fee so far: Rs 0 (not overdue); total payable now: {_rs(late['total_payable'])}. "
            f"A late fee of {_rs(FEE_LATE_FEE_PER_DAY)} per day starts after {due_date}"
        )
    days = late["days_overdue"]
    capped = late["late_fee"] >= FEE_LATE_FEE_CAP
    rule = (
        f"cap of {_rs(FEE_LATE_FEE_CAP)} per semester reached"
        if capped
        else f"{days} day{'s' if days != 1 else ''} x {_rs(FEE_LATE_FEE_PER_DAY)} per day, "
        f"cap {_rs(FEE_LATE_FEE_CAP)} per semester"
    )
    growth = "" if capped else f"; it grows by {_rs(FEE_LATE_FEE_PER_DAY)} each day until paid"
    return (
        f"  Late fee so far: {_rs(late['late_fee'])} ({rule}); total payable now: "
        f"{_rs(late['total_payable'])} (due {_rs(due)} + late fee {_rs(late['late_fee'])}){growth}"
    )


def student_context(record: dict) -> str:
    """Readable text block of ONE student's record for prompts."""
    today = _date(record.get("as_of")) or date.today()
    branch = record.get("branch", "")
    lines = [
        f"Student record (as of {today.isoformat()})",
        f"Name: {record.get('name', '')} | ID: {record.get('student_id', '')} | "
        f"Branch: {branch} ({BRANCHES.get(branch, branch)}) | Year {record.get('year')}, Semester {record.get('semester')}",
    ]
    if record.get("email"):
        lines.append(f"Email: {record['email']}")

    hostel = record.get("hostel")
    if hostel:
        lines.append(f"Hostel: Block {hostel.get('block')}, Room {hostel.get('room')}")
    else:
        lines.append("Hostel: not allotted (day scholar)")

    fees = record.get("fees") or {}
    if fees:
        due = fees.get("due", 0) or 0
        status = str(fees.get("status", "")).replace("_", " ")
        line = (
            f"Fees ({fees.get('semester', '')}): total {_rs(fees.get('total'))}, paid {_rs(fees.get('paid'))}, "
            f"due {_rs(due)}"
        )
        if due:
            line += f" by {fees.get('due_date')} {_days_note(fees.get('due_date'), today)}".rstrip()
        lines.append(f"{line} | status: {status.upper() if status == 'overdue' else status}")
        if due:
            lines.append(_late_fee_line(due, fees.get("due_date"), today))
        scholarship = fees.get("scholarship")
        if scholarship:
            lines.append(f"  Scholarship: {scholarship.get('name')} {_rs(scholarship.get('amount'))}")
        for t in fees.get("transactions") or []:
            lines.append(f"  Payment: {t.get('date')} {_rs(t.get('amount'))} via {t.get('mode')} (receipt {t.get('receipt_no')})")

    library = record.get("library") or {}
    if library:
        books = library.get("books_issued") or []
        lines.append(f"Library: {len(books)} book(s) issued, fine due {_rs(library.get('fine_due', 0))}")
        for b in books:
            note = _days_note(b.get("due_date"), today)
            if b.get("status") == "overdue" and "OVERDUE" not in note:
                note = "(OVERDUE)"
            fine = library_book_fine(b.get("due_date"), today)
            if fine:
                note = f"{note[:-1]}, fine {_rs(fine)})" if note.endswith(")") else f"(fine {_rs(fine)})"
            lines.append(
                f"  - {b.get('title')} by {b.get('author')} [{b.get('book_id')}], issued {b.get('issue_date')}, "
                f"due {b.get('due_date')} {note}".rstrip()
            )

    attendance = record.get("attendance") or {}
    if attendance:
        overall = attendance.get("overall_pct")
        flag = " (BELOW 75%)" if isinstance(overall, (int, float)) and overall < LOW_ATTENDANCE_PCT else ""
        lines.append(f"Attendance: overall {overall}%{flag}")
        for c in attendance.get("courses") or []:
            pct = c.get("pct")
            low = isinstance(pct, (int, float)) and pct < LOW_ATTENDANCE_PCT
            lines.append(f"  - {c.get('code')} {c.get('name')}: {pct}%{' (BELOW 75%)' if low else ''}")

    results = record.get("results") or {}
    if results:
        if results.get("cgpa") is None:
            # First-semester students have no graded semester yet.
            grades = "CGPA not available yet (no semester completed)"
        else:
            grades = f"CGPA {results.get('cgpa')}, last SGPA {results.get('last_sgpa')}"
        lines.append(f"Results: {grades}, backlogs {results.get('backlogs', 0)}")

    upcoming = (record.get("exams") or {}).get("upcoming") or []
    if upcoming:
        lines.append("Upcoming exams:")
        for e in sorted(upcoming, key=lambda e: (str(e.get("date")), str(e.get("time")))):
            lines.append(
                f"  - {e.get('date')} {e.get('time')}: {e.get('course_code')} {e.get('course_name')} "
                f"({e.get('type')}), room {e.get('room')} {_days_note(e.get('date'), today).replace('left', 'to go')}".rstrip()
            )
    else:
        lines.append("Upcoming exams: none scheduled")
    return "\n".join(lines)
