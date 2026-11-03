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
