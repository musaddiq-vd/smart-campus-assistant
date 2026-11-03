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


def _uses_app_db(path: str | Path | None) -> bool:
    """Self-registered students live in the app database, which belongs to the configured students file.

    Tests that point ``_default_path`` (or ``path``) at a temporary file therefore never see
    the real app database unless they pass ``db_path`` explicitly.
    """
    try:
        json_path = Path(path) if path else _default_path()
        return json_path.resolve() == Path(get_settings().students_file).resolve()
    except OSError:
        return False


def _load_json_students(path: str | Path | None = None) -> dict[str, dict]:
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


def load_db_students(db_path: str | Path | None = None, conn=None) -> dict[str, dict]:
    """Self-registered students from the database ({} when the database file does not exist yet)."""
    from smart_campus_assistant import db  # local import keeps pure-JSON use free of SQLite setup

    try:
        if conn is not None:
            rows = conn.execute("SELECT record_json FROM students").fetchall()
        elif not db.db_path(db_path).exists():
            return {}
        else:
            with db.reading(db_path) as c:
                rows = c.execute("SELECT record_json FROM students").fetchall()
    except Exception:  # noqa: BLE001  (a broken database must not break the JSON students)
        return {}
    students: dict[str, dict] = {}
    for row in rows:
        try:
            record = json.loads(row["record_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("student_id"):
            students[normalize_id(record["student_id"])] = record
    return students


def _merge(json_students: dict[str, dict], db_students: dict[str, dict]) -> dict[str, dict]:
    """JSON records win on an id clash; DB records get the JSON file's ``as_of`` date."""
    extra = {sid: r for sid, r in db_students.items() if sid not in json_students}
    if not extra:
        return json_students
    as_of = next((r.get("as_of") for r in json_students.values() if r.get("as_of")), None)
    merged = dict(json_students)
    for sid, record in extra.items():
        record = dict(record)
        if as_of:
            record["as_of"] = as_of
        merged[sid] = record
    return merged


def load_students(path: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, dict]:
    """Return {STUDENT_ID: record}; empty dict if the file is missing or invalid.

    Each record gets an ``as_of`` key copied from the file (used for "days left").
    Self-registered students from the database are included (same schema; JSON wins on an
    id clash) when ``db_path`` is given or when the configured students file is used.
    """
    students = _load_json_students(path)
    if db_path is None and not _uses_app_db(path):
        return students
    return _merge(students, load_db_students(db_path))


def _all(db_path: str | Path | None) -> dict[str, dict]:
    # Without db_path, call the module-level load_students (fake mode may wrap it).
    return load_students() if db_path is None else load_students(None, db_path)


def clear_cache() -> None:
    """Forget parsed files (tests use this after writing a new students.json)."""
    _CACHE.clear()


def get_student(student_id: object, db_path: str | Path | None = None) -> dict | None:
    """Look up one student, case-insensitive and ignoring spaces."""
    sid = normalize_id(student_id)
    return _all(db_path).get(sid) if sid else None


def find_student_by_email(email: object, db_path: str | Path | None = None) -> dict | None:
    """The student record that uses this email (case-insensitive), else None."""
    wanted = str(email or "").strip().lower()
    if not wanted:
        return None
    for record in _all(db_path).values():
        if str(record.get("email", "")).strip().lower() == wanted:
            return record
    return None


def list_students(db_path: str | Path | None = None) -> list[dict]:
    """Minimal fields for the UI login picker (no fees, marks or contact data)."""
    return [
        {"student_id": sid, "name": r.get("name", ""), "branch": r.get("branch", ""), "year": r.get("year")}
        for sid, r in sorted(_all(db_path).items())
    ]


# ---------------------------------------------------------------- new admissions (self-registration)

CATEGORIES = ("General", "OBC-NCL", "EWS", "SC", "ST")
NEW_ADMISSION_BATCH = "26"  # new student IDs are XYZ26<BRANCH><roll>
NEW_ADMISSION_SEMESTER = "Odd 2026-27"
NEW_ADMISSION_DUE_DATE = "2026-11-30"
HOSTEL_TO_BE_ALLOTTED = "To be allotted"
# Fallback semester bills (day scholar, hostel resident) if fee_structure.md cannot be read.
_FALLBACK_BILLS = {"open": (95000, 149000), "obc": (75000, 129000), "sc": (35000, 89000)}


def _category_key(category: str) -> str:
    c = str(category).strip().upper()
    if c in ("OBC-NCL", "EWS"):
        return "obc"
    if c in ("SC", "ST"):
        return "sc"
    return "open"


def semester_bill(category: str, hostel: bool, fee_file: str | Path | None = None) -> int:
    """Semester bill for a category from fee_structure.md ("Total semester bill" table)."""
    fee_file = Path(fee_file) if fee_file else get_settings().data_dir / "fee_structure.md"
    bills = dict(_FALLBACK_BILLS)
    try:
        section = fee_file.read_text(encoding="utf-8").split("## Total semester bill", 1)[1].split("\n## ", 1)[0]
        for line in section.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 3 or not all(re.fullmatch(r"[\d,]+", c) for c in cells[1:]):
                continue
            label = cells[0].lower()
            key = "obc" if "obc" in label else "sc" if label.startswith("sc") else "open"
            bills[key] = (int(cells[1].replace(",", "")), int(cells[2].replace(",", "")))
    except (OSError, IndexError, ValueError):
        pass
    day, resident = bills[_category_key(category)]
    return resident if hostel else day


def _first_year_template(branch: str, students: dict[str, dict]) -> dict | None:
    """An existing first-year student of the branch, used for the course list and exams."""
    prefix = f"XYZ{NEW_ADMISSION_BATCH}{branch}"
    for sid, record in sorted(students.items()):
        if sid.startswith(prefix) and record.get("year") == 1:
            return record
    return None


def next_student_id(branch: str, students: dict[str, dict]) -> str:
    """XYZ26<BRANCH><next 3-digit roll after the highest existing one for that branch>."""
    prefix = f"XYZ{NEW_ADMISSION_BATCH}{branch}"
    rolls = [int(sid[len(prefix):]) for sid in students if sid.startswith(prefix) and sid[len(prefix):].isdigit()]
    roll = max(rolls, default=0) + 1
    if roll > 999:
        raise ValueError(f"No roll numbers left for {branch}.")
    return f"{prefix}{roll:03d}"


def create_student_record(
    name: str,
    email: str,
    branch: str,
    category: str = "General",
    hostel: bool = False,
    db_path: str | Path | None = None,
    conn=None,
    json_path: str | Path | None = None,
) -> dict:
    """Build (not save) the record of a newly admitted first-year student.

    Same schema as data/students.json. The id is XYZ26<BRANCH><roll>, where roll is the next
    number for that branch across the JSON file and the database (pass ``conn`` from inside a
    write transaction so two registrations cannot get the same id).

    Fees: a brand-new student has paid nothing, so ``due`` = ``total`` (fee_structure.md bill for
    the category, including the hostel fee for hostel residents), due date 2026-11-30. The
    status is "partially_paid", which in this schema means "not fully paid and not overdue"
    (the schema only uses "paid", "partially_paid" and "overdue").
    Attendance percentages are None ("not recorded yet"); CGPA and SGPA are None.
    """
    branch = str(branch).strip().upper()
    if branch not in BRANCHES:
        raise ValueError(f"Unknown branch: {branch}")
    json_students = _load_json_students(json_path)
    db_students = load_db_students(db_path, conn=conn)
    student_id = next_student_id(branch, {**db_students, **json_students})
    template = _first_year_template(branch, json_students) or _first_year_template(branch, db_students) or {}
    courses = [
        {"code": c.get("code"), "name": c.get("name"), "pct": None}
        for c in ((template.get("attendance") or {}).get("courses") or [])
    ]
    exams = [
        dict(e)
        for e in ((template.get("exams") or {}).get("upcoming") or [])
        if re.sub(r"[\s-]", "", str(e.get("type", ""))).lower() == "endsem"
    ]
    total = semester_bill(category, hostel)
    return {
        "student_id": student_id,
        "name": str(name).strip(),
        "branch": branch,
        "year": 1,
        "semester": 1,
        "email": str(email).strip().lower(),
        "category": category,
        "hostel": {"block": HOSTEL_TO_BE_ALLOTTED, "room": HOSTEL_TO_BE_ALLOTTED} if hostel else None,
        "fees": {
            "semester": NEW_ADMISSION_SEMESTER,
            "total": total,
            "paid": 0,
            "due": total,
            "due_date": NEW_ADMISSION_DUE_DATE,
            "status": "partially_paid",
            "scholarship": None,
            "transactions": [],
        },
        "library": {"books_issued": [], "fine_due": 0},
        "attendance": {"overall_pct": None, "courses": courses},
        "results": {"cgpa": None, "last_sgpa": None, "backlogs": 0},
        "exams": {"upcoming": exams},
    }


def save_student_record(conn, record: dict) -> None:
    """Insert a self-registered student's record (call inside ``db.transaction``)."""
    from smart_campus_assistant import db

    conn.execute(
        "INSERT INTO students (student_id, email, record_json, created_at) VALUES (?, ?, ?, ?)",
        (normalize_id(record["student_id"]), str(record["email"]).strip().lower(),
         json.dumps(record, ensure_ascii=False), db.now_iso()),
    )


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


# Every B.Tech programme has 8 semesters (academic_rules.md, branches_and_programmes.md).
PROGRAMME_SEMESTERS = 8


def current_subjects(record: dict) -> list[str]:
    """This semester's courses as "CODE Name": from attendance, else from upcoming exams."""
    courses = (record.get("attendance") or {}).get("courses") or []
    subjects = [f"{c.get('code', '')} {c.get('name', '')}".strip() for c in courses if isinstance(c, dict)]
    if not subjects:
        exams = (record.get("exams") or {}).get("upcoming") or []
        subjects = [f"{e.get('course_code', '')} {e.get('course_name', '')}".strip() for e in exams if isinstance(e, dict)]
    return [s for s in subjects if s]


def _programme_lines(record: dict) -> list[str]:
    """Spell out semester progress and current subjects so the model does not have to infer them."""
    lines = []
    try:
        semester = int(record.get("semester"))
    except (TypeError, ValueError):
        semester = None
    if semester:
        left_after = max(PROGRAMME_SEMESTERS - semester, 0)
        lines.append(
            f"Programme: B.Tech, {PROGRAMME_SEMESTERS} semesters in total; currently in Semester {semester}; "
            f"{left_after} semester(s) left after the current one "
            f"({left_after + 1} including the current one)."
        )
    subjects = current_subjects(record)
    if subjects:
        label = f"Semester {semester}" if semester else "this semester"
        lines.append(f"Current semester subjects ({label}): " + "; ".join(subjects))
    return lines


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
    lines.extend(_programme_lines(record))

    hostel = record.get("hostel")
    if hostel and hostel.get("room") == HOSTEL_TO_BE_ALLOTTED:
        lines.append("Hostel: hostel resident, room to be allotted")
    elif hostel:
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
        if isinstance(overall, (int, float)):
            flag = " (BELOW 75%)" if overall < LOW_ATTENDANCE_PCT else ""
            lines.append(f"Attendance: overall {overall}%{flag}")
        else:
            lines.append("Attendance: overall not recorded yet")
        for c in attendance.get("courses") or []:
            pct = c.get("pct")
            if isinstance(pct, (int, float)):
                low = " (BELOW 75%)" if pct < LOW_ATTENDANCE_PCT else ""
                lines.append(f"  - {c.get('code')} {c.get('name')}: {pct}%{low}")
            else:
                lines.append(f"  - {c.get('code')} {c.get('name')}: not recorded yet")

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
