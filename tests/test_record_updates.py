"""Offline tests for audited record edits: fee payments, library books, attendance, hostel."""

from __future__ import annotations

import json

import pytest

from smart_campus_assistant import auth, db, record_updates, student_data
from smart_campus_assistant.record_updates import RecordError

ADMIN = {"actor_email": "admin@xyz.edu.example", "actor_role": "admin"}
AARAV = "XYZ24CSE001"
MEERA = "XYZ25ECE002"


def _student(sid, name, branch, email, **changes):
    record = {
        "student_id": sid, "name": name, "branch": branch, "year": 3, "semester": 5, "email": email,
        "hostel": {"block": "A", "room": "A-214"},
        "fees": {"semester": "Odd 2026-27", "total": 149000, "paid": 89400, "due": 59600, "due_date": "2026-11-30",
                 "status": "partially_paid", "scholarship": None,
                 "transactions": [{"date": "2026-08-10", "amount": 89400, "mode": "UPI",
                                   "receipt_no": "XYZR-2026-01000"}]},
        "library": {"books_issued": [
            {"book_id": "LIB-CS-0421", "title": "Operating Systems", "author": "N. Raghavan",
             "issue_date": "2026-10-10", "due_date": "2026-10-24", "status": "overdue"},  # 10 days late: Rs 50
            {"book_id": "LIB-CS-0517", "title": "Database Systems", "author": "A. Deshmukh",
             "issue_date": "2026-10-16", "due_date": "2026-10-30", "status": "overdue"},  # 4 days late: Rs 20
        ], "fine_due": 70},
        "attendance": {"overall_pct": 80.0, "courses": [
            {"code": "CS501", "name": "Operating Systems", "pct": 84.0},
            {"code": "CS502", "name": "Database Management Systems", "pct": 76.0},
        ]},
        "results": {"cgpa": 8.12, "last_sgpa": 8.4, "backlogs": 0},
        "exams": {"upcoming": []},
    }
    record.update(changes)
    return record


def _overdue_fees():
    return {"semester": "Odd 2026-27", "total": 95000, "paid": 0, "due": 95000, "due_date": "2026-10-31",
            "status": "overdue", "scholarship": None, "transactions": []}


STUDENTS = [
    _student(AARAV, "Aarav Sharma", "CSE", "aarav.sharma001@xyz.edu.example"),
    _student(MEERA, "Meera Nair", "ECE", "meera.nair002@xyz.edu.example", hostel=None, fees=_overdue_fees(),
             library={"books_issued": [], "fine_due": 0}),
    dict(_student("XYZ26CSE005", "Rohan Gupta", "CSE", "rohan.gupta005@xyz.edu.example"), year=1, semester=1),
]


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    students = tmp_path / "students.json"
    students.write_text(json.dumps({"as_of": "2026-11-03", "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: students)
    student_data.clear_cache()
    yield tmp_path / "app.db"
    student_data.clear_cache()


def _get(sid, db_file):
    return student_data.get_student(sid, db_path=db_file)


# ------------------------------------------------------------ fee payments


def test_fee_payment_happy_path_and_persistence(db_file):
    record = record_updates.record_fee_payment(AARAV, 10000, "upi", **ADMIN, path=db_file)
    fees = record["fees"]
    assert (fees["paid"], fees["due"], fees["status"]) == (99400, 49600, "partially_paid")
    txn = fees["transactions"][-1]
    assert txn["date"] == "2026-11-03" and txn["amount"] == 10000 and txn["mode"] == "UPI"  # date = as_of
    assert txn["receipt_no"].startswith("XYZP-2026-") and len(fees["transactions"]) == 2
    assert record["as_of"] == "2026-11-03"

    student_data.clear_cache()  # a fresh load reads the override from the database
    again = _get(AARAV, db_file)
    assert again == record
    assert student_data.all_records(db_path=db_file)[0]["fees"]["due"] == 49600
    assert student_data.load_students()[AARAV]["fees"]["due"] == 59600  # tmp JSON without db_path: no overrides
    assert json.loads((db_file.parent / "students.json").read_text(encoding="utf-8"))["students"][0]["fees"]["due"] == 59600


def test_receipt_numbers_are_unique(db_file):
    receipts = set()
    for sid in (AARAV, MEERA, AARAV):
        record = record_updates.record_fee_payment(sid, 100, "Cash", **ADMIN, path=db_file)
        receipts.add(record["fees"]["transactions"][-1]["receipt_no"])
    record_updates.set_hostel(AARAV, "B", "B-101", **ADMIN, path=db_file)
    record = record_updates.record_fee_payment(AARAV, 100, "NEFT", **ADMIN, path=db_file)
    receipts.add(record["fees"]["transactions"][-1]["receipt_no"])
    assert len(receipts) == 4 and record["fees"]["paid"] == 89400 + 300


def test_full_payment_sets_status_paid(db_file):
    record = record_updates.record_fee_payment(AARAV, 59600, "NetBanking", **ADMIN, path=db_file)
    assert record["fees"]["due"] == 0 and record["fees"]["paid"] == 149000 and record["fees"]["status"] == "paid"
    with pytest.raises(RecordError, match="Nothing is due"):
        record_updates.record_fee_payment(AARAV, 1, "UPI", **ADMIN, path=db_file)


def test_partial_payment_keeps_overdue_then_paid(db_file):
    record = record_updates.record_fee_payment(MEERA, 5000, "Card", **ADMIN, path=db_file)
    assert record["fees"]["status"] == "overdue" and record["fees"]["due"] == 90000
    record = record_updates.record_fee_payment(MEERA, 90000, "Card", **ADMIN, path=db_file)
    assert record["fees"]["status"] == "paid" and len(record["fees"]["transactions"]) == 2


@pytest.mark.parametrize("amount", [0, -500, 59601, 100.5, "abc", "12.5", None, True, float("nan")])
def test_fee_payment_bad_amount(db_file, amount):
    with pytest.raises(RecordError):
        record_updates.record_fee_payment(AARAV, amount, "UPI", **ADMIN, path=db_file)
    assert _get(AARAV, db_file)["fees"]["due"] == 59600
    assert record_updates.list_audit(path=db_file) == []


def test_fee_payment_bad_mode_and_whole_float(db_file):
    with pytest.raises(RecordError, match="payment mode"):
        record_updates.record_fee_payment(AARAV, 100, "Bitcoin", **ADMIN, path=db_file)
    assert record_updates.record_fee_payment(AARAV, 100.0, "NEFT", **ADMIN, path=db_file)["fees"]["due"] == 59500


def test_student_context_reflects_payment(db_file):
    record = record_updates.record_fee_payment(AARAV, 9600, "UPI", **ADMIN, path=db_file)
    receipt = record["fees"]["transactions"][-1]["receipt_no"]
    text = student_data.student_context(_get(AARAV, db_file))
    assert "paid Rs 99,000, due Rs 50,000" in text
    assert f"Payment: 2026-11-03 Rs 9,600 via UPI (receipt {receipt})" in text
    assert "Student record (as of 2026-11-03)" in text


# ------------------------------------------------------------ permissions


def test_student_pays_own_fees_only(db_file):
    me = {"actor_email": "aarav.sharma001@xyz.edu.example", "actor_role": "student"}
    record = record_updates.record_fee_payment(" xyz24cse001 ", 600, "UPI", **me, actor_student_id=AARAV, path=db_file)
    assert record["fees"]["due"] == 59000
    with pytest.raises(PermissionError):
        record_updates.record_fee_payment(MEERA, 600, "UPI", **me, actor_student_id=AARAV, path=db_file)
    with pytest.raises(PermissionError):  # the acting student's id is required
        record_updates.record_fee_payment(AARAV, 600, "UPI", **me, path=db_file)
    assert _get(MEERA, db_file)["fees"]["due"] == 95000
    audit = record_updates.list_audit(path=db_file)
    assert len(audit) == 1 and audit[0]["actor_role"] == "student" and audit[0]["student_id"] == AARAV


def test_student_cannot_call_admin_actions(db_file):
    me = {"actor_email": "aarav.sharma001@xyz.edu.example", "actor_role": "student"}
    calls = [
        lambda: record_updates.issue_book(AARAV, "LIB-CS-0001", "A Book", "An Author", **me, path=db_file),
        lambda: record_updates.return_book(AARAV, "LIB-CS-0421", **me, path=db_file),
        lambda: record_updates.update_attendance(AARAV, "CS501", 100, **me, path=db_file),
        lambda: record_updates.set_hostel(AARAV, "B", "B-1", **me, path=db_file),
    ]
    for call in calls:
        with pytest.raises(PermissionError):
            call()
    assert record_updates.list_audit(path=db_file) == []
    assert _get(AARAV, db_file) == student_data.load_students()[AARAV]


@pytest.mark.parametrize("actor", [{"actor_email": "x@y.example", "actor_role": "guest"},
                                   {"actor_email": "", "actor_role": "admin"},
                                   {"actor_email": "x@y.example", "actor_role": None}])
def test_unknown_role_or_missing_actor_refused(db_file, actor):
    with pytest.raises(PermissionError):
        record_updates.record_fee_payment(AARAV, 100, "UPI", **actor, path=db_file)


def test_unknown_student(db_file):
    calls = [
        lambda: record_updates.record_fee_payment("XYZ99CSE999", 100, "UPI", **ADMIN, path=db_file),
        lambda: record_updates.issue_book("XYZ99CSE999", "LIB-CS-0001", "A Book", "An Author", **ADMIN, path=db_file),
        lambda: record_updates.return_book("", "LIB-CS-0421", **ADMIN, path=db_file),
        lambda: record_updates.update_attendance(None, "CS501", 50, **ADMIN, path=db_file),
        lambda: record_updates.set_hostel("XYZ99CSE999", "B", "B-1", **ADMIN, path=db_file),
    ]
    for call in calls:
        with pytest.raises(RecordError, match="No student record"):
            call()


# ------------------------------------------------------------ library


def test_issue_book(db_file):
    record = record_updates.issue_book(AARAV, " lib-cs-0900 ", "<b>Compilers</b>  in\x07 Practice", "R. Iyer",
                                       **ADMIN, path=db_file)
    book = record["library"]["books_issued"][-1]
    assert book == {"book_id": "LIB-CS-0900", "title": "Compilers in Practice", "author": "R. Iyer",
                    "issue_date": "2026-11-03", "due_date": "2026-11-17", "status": "issued"}
    assert len(_get(AARAV, db_file)["library"]["books_issued"]) == 3
    assert record["library"]["fine_due"] == 70  # issuing does not touch the fine


def test_issue_book_limits(db_file):
    with pytest.raises(RecordError, match="already issued"):
        record_updates.issue_book(AARAV, "lib-cs-0421", "Operating Systems", "N. Raghavan", **ADMIN, path=db_file)
    for n in (1, 2):
        record_updates.issue_book(AARAV, f"LIB-CS-090{n}", f"Book {n}", "Some Author", **ADMIN, path=db_file)
    with pytest.raises(RecordError, match="at most 4"):  # the 5th book
        record_updates.issue_book(AARAV, "LIB-CS-0999", "Fifth Book", "Some Author", **ADMIN, path=db_file)
    assert len(_get(AARAV, db_file)["library"]["books_issued"]) == 4


@pytest.mark.parametrize("book_id, title, author", [
    ("", "A Title", "An Author"), ("LIB CS!", "A Title", "An Author"), ("L" * 31, "A Title", "An Author"),
    ("LIB-CS-0001", "", "An Author"), ("LIB-CS-0001", "<i></i>", "An Author"), ("LIB-CS-0001", "T" * 151, "An Author"),
    ("LIB-CS-0001", "A Title", " "),
])
def test_issue_book_validation(db_file, book_id, title, author):
    with pytest.raises(RecordError):
        record_updates.issue_book(AARAV, book_id, title, author, **ADMIN, path=db_file)
    assert len(_get(AARAV, db_file)["library"]["books_issued"]) == 2


def test_return_book_recomputes_fine(db_file):
    record = record_updates.return_book(AARAV, "lib-cs-0421", **ADMIN, path=db_file)
    assert [b["book_id"] for b in record["library"]["books_issued"]] == ["LIB-CS-0517"]
    assert record["library"]["fine_due"] == 20  # only the book that is still out (4 days x Rs 5)
    record = record_updates.return_book(AARAV, "LIB-CS-0517", **ADMIN, path=db_file)
    assert record["library"] == {"books_issued": [], "fine_due": 0}
    assert "Library: 0 book(s) issued, fine due Rs 0" in student_data.student_context(_get(AARAV, db_file))
    with pytest.raises(RecordError, match="not issued"):  # unknown book
        record_updates.return_book(AARAV, "LIB-CS-0421", **ADMIN, path=db_file)
    with pytest.raises(RecordError):
        record_updates.return_book(AARAV, "", **ADMIN, path=db_file)


# ------------------------------------------------------------ attendance


def test_update_attendance(db_file):
    record = record_updates.update_attendance(AARAV, "cs502", 90.26, **ADMIN, path=db_file)
    attendance = record["attendance"]
    assert attendance["courses"][1]["pct"] == 90.3 and attendance["courses"][0]["pct"] == 84.0
    assert attendance["overall_pct"] == 87.2  # mean(84.0, 90.3) = 87.15 -> one decimal
    assert _get(AARAV, db_file)["attendance"] == attendance
    assert record_updates.update_attendance(AARAV, "CS501", 0, **ADMIN, path=db_file)["attendance"]["overall_pct"] == 45.1
    assert record_updates.update_attendance(AARAV, "CS501", 100, **ADMIN, path=db_file)["attendance"]["courses"][0]["pct"] == 100.0


@pytest.mark.parametrize("code, pct", [("CS501", -1), ("CS501", 100.1), ("CS501", "abc"), ("CS501", None),
                                       ("CS501", float("nan")), ("CS501", True), ("CS999", 80), ("", 80)])
def test_update_attendance_validation(db_file, code, pct):
    with pytest.raises(RecordError):
        record_updates.update_attendance(AARAV, code, pct, **ADMIN, path=db_file)
    assert _get(AARAV, db_file)["attendance"]["overall_pct"] == 80.0


def test_attendance_mean_skips_unrecorded_courses(db_file):
    user = auth.register_new_student("Nisha Kapoor", "nisha@mail.example", "nisha-pass-1", "CSE", path=db_file)
    record = record_updates.update_attendance(user["student_id"], "CS501", 71.4, **ADMIN, path=db_file)
    assert record["attendance"]["overall_pct"] == 71.4
    assert record["attendance"]["courses"][1]["pct"] is None


# ------------------------------------------------------------ hostel


def test_set_and_clear_hostel(db_file):
    record = record_updates.set_hostel(MEERA, " C ", "<b>C-310</b>", **ADMIN, path=db_file)
    assert record["hostel"] == {"block": "C", "room": "C-310"}
    assert "Hostel: Block C, Room C-310" in student_data.student_context(_get(MEERA, db_file))
    record = record_updates.set_hostel(MEERA, "", None, **ADMIN, path=db_file)
    assert record["hostel"] is None
    assert "day scholar" in student_data.student_context(_get(MEERA, db_file))
    assert record_updates.set_hostel(AARAV, **ADMIN, path=db_file)["hostel"] is None  # defaults clear the room


@pytest.mark.parametrize("block, room", [("C", ""), ("", "C-310"), ("C" * 21, "C-310"), ("<br>", "C-310")])
def test_set_hostel_validation(db_file, block, room):
    with pytest.raises(RecordError):
        record_updates.set_hostel(AARAV, block, room, **ADMIN, path=db_file)
    assert _get(AARAV, db_file)["hostel"] == {"block": "A", "room": "A-214"}


# ------------------------------------------------------------ overrides and audit


def test_override_wins_over_self_registered_student(db_file):
    user = auth.register_new_student("Nisha Kapoor", "nisha@mail.example", "nisha-pass-1", "CSE", hostel=True,
                                     path=db_file)
    sid = user["student_id"]
    me = {"actor_email": user["email"], "actor_role": "student", "actor_student_id": sid}
    record = record_updates.record_fee_payment(sid, 49000, "UPI", **me, path=db_file)
    assert record["fees"]["paid"] == 49000 and record["fees"]["due"] == 100000
    assert record["fees"]["status"] == "partially_paid"
    record_updates.set_hostel(sid, "D", "D-007", **ADMIN, path=db_file)

    loaded = _get(sid, db_file)
    assert loaded["fees"]["due"] == 100000 and loaded["hostel"] == {"block": "D", "room": "D-007"}
    assert loaded["as_of"] == "2026-11-03"  # still the date of the students file
    assert student_data.load_db_students(db_file)[sid]["fees"]["due"] == 149000  # the students table is untouched
    assert student_data.find_student_by_email("nisha@mail.example", db_path=db_file)["hostel"]["room"] == "D-007"
    # A later registration still gets the next roll number.
    assert auth.register_new_student("Second New", "second@mail.example", "pass-word-1", "CSE",
                                     path=db_file)["student_id"] == "XYZ26CSE007"


def test_as_of_follows_the_students_file(db_file):
    record_updates.set_hostel(AARAV, "B", "B-101", **ADMIN, path=db_file)
    with db.reading(db_file) as conn:
        stored = json.loads(conn.execute("SELECT record_json FROM student_record_overrides").fetchone()[0])
    assert "as_of" not in stored
    students = db_file.parent / "students.json"
    students.write_text(json.dumps({"as_of": "2026-12-01", "students": STUDENTS}), encoding="utf-8")
    student_data.clear_cache()
    record = _get(AARAV, db_file)
    assert record["as_of"] == "2026-12-01" and record["hostel"]["room"] == "B-101"
    paid = record_updates.record_fee_payment(AARAV, 100, "UPI", **ADMIN, path=db_file)
    assert paid["fees"]["transactions"][-1]["date"] == "2026-12-01" and paid["hostel"]["room"] == "B-101"


def test_edits_stack_on_the_latest_record(db_file):
    record_updates.record_fee_payment(AARAV, 600, "UPI", **ADMIN, path=db_file)
    record_updates.update_attendance(AARAV, "CS501", 90, **ADMIN, path=db_file)
    record = record_updates.issue_book(AARAV, "LIB-CS-0900", "Compilers", "R. Iyer", **ADMIN, path=db_file)
    assert record["fees"]["due"] == 59000 and record["attendance"]["courses"][0]["pct"] == 90.0
    assert len(record["library"]["books_issued"]) == 3
    with db.reading(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM student_record_overrides").fetchone()[0] == 1


def test_audit_rows(db_file):
    assert record_updates.list_audit(path=db_file) == []  # no database yet
    assert not db_file.exists()
    record_updates.record_fee_payment(AARAV, 600, "UPI", **ADMIN, path=db_file)
    record_updates.set_hostel(MEERA, "C", "C-310", **ADMIN, path=db_file)
    record_updates.return_book(AARAV, "LIB-CS-0421", actor_email=" Admin@XYZ.edu.example ", actor_role="ADMIN",
                               path=db_file)
    with pytest.raises(RecordError):
        record_updates.return_book(AARAV, "LIB-CS-0421", **ADMIN, path=db_file)  # failed edits leave no row

    rows = record_updates.list_audit(path=db_file)
    assert [r["action"] for r in rows] == ["return_book", "set_hostel", "fee_payment"]  # newest first
    assert all(r["actor_email"] == "admin@xyz.edu.example" and r["actor_role"] == "admin" for r in rows)
    assert all(r["created_at"] and r["detail"]["summary"] for r in rows)
    payment = rows[-1]["detail"]
    assert payment["amount"] == 600 and payment["due_before"] == 59600 and payment["due_after"] == 59000
    assert rows[0]["detail"]["fine_settled"] == 50

    mine = record_updates.list_audit(AARAV.lower(), path=db_file)
    assert [r["action"] for r in mine] == ["return_book", "fee_payment"]
    assert len(record_updates.list_audit(limit=1, path=db_file)) == 1
    assert record_updates.list_audit("XYZ99CSE999", path=db_file) == []


def test_failed_edit_rolls_back_everything(db_file, monkeypatch):
    record_updates.set_hostel(AARAV, "B", "B-101", **ADMIN, path=db_file)
    with monkeypatch.context() as patch:
        patch.setattr(db, "now_iso", lambda: (_ for _ in ()).throw(RuntimeError("clock broke")))
        with pytest.raises(RuntimeError):
            record_updates.record_fee_payment(AARAV, 600, "UPI", **ADMIN, path=db_file)
    student_data.clear_cache()
    assert _get(AARAV, db_file)["fees"]["due"] == 59600
    assert len(record_updates.list_audit(path=db_file)) == 1
