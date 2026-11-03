"""Offline tests for self-registration of new students and claiming existing student accounts."""

from __future__ import annotations

import json

import pytest

from smart_campus_assistant import auth, student_data


def _first_year(sid, name, branch, email):
    return {
        "student_id": sid, "name": name, "branch": branch, "year": 1, "semester": 1, "email": email,
        "hostel": None,
        "fees": {"semester": "Odd 2026-27", "total": 95000, "paid": 95000, "due": 0, "due_date": "2026-10-31",
                 "status": "paid", "scholarship": None, "transactions": []},
        "library": {"books_issued": [], "fine_due": 0},
        "attendance": {"overall_pct": 85.0, "courses": [{"code": "MA101", "name": "Engineering Mathematics I", "pct": 88.0},
                                                        {"code": "CS101", "name": "Programming", "pct": 82.0}]},
        "results": {"cgpa": None, "last_sgpa": None, "backlogs": 0},
        "exams": {"upcoming": [
            {"course_code": "MA101", "course_name": "Engineering Mathematics I", "type": "End-Sem",
             "date": "2026-12-01", "time": "10:00-13:00", "room": "EH-1"},
            {"course_code": "CS101", "course_name": "Programming", "type": "Mid-Sem",
             "date": "2026-09-14", "time": "10:00-11:30", "room": "EH-1"},
        ]},
    }


STUDENTS = [
    _first_year("XYZ26CSE005", "Rohan Gupta", "CSE", "rohan.gupta005@xyz.edu.example"),
    _first_year("XYZ26ECE009", "Arjun Das", "ECE", "arjun.das009@xyz.edu.example"),
    dict(_first_year("XYZ24CSE001", "Aarav Sharma", "CSE", "aarav.sharma001@xyz.edu.example"), year=3, semester=5),
]


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    students = tmp_path / "students.json"
    students.write_text(json.dumps({"as_of": "2026-11-03", "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: students)
    student_data.clear_cache()
    yield tmp_path / "app.db"
    student_data.clear_cache()


# ------------------------------------------------------------ register a new student


def test_register_creates_record_and_account(db_file):
    user = auth.register_new_student("  Nisha   Kapoor ", "Nisha@Mail.example", "nisha-pass-1", "cse",
                                     category="obc-ncl", hostel=True, path=db_file)
    assert user == {"email": "nisha@mail.example", "role": "student", "name": "Nisha Kapoor",
                    "student_id": "XYZ26CSE006", "must_change_password": False}
    assert auth.authenticate("nisha@mail.example", "nisha-pass-1", role="student", path=db_file) == user

    record = student_data.get_student("XYZ26CSE006", db_path=db_file)
    assert record["year"] == 1 and record["semester"] == 1 and record["category"] == "OBC-NCL"
    assert record["as_of"] == "2026-11-03"  # copied from the JSON file
    fees = record["fees"]
    assert fees["total"] == 129000 and fees["paid"] == 0 and fees["due"] == 129000
    assert fees["status"] == "partially_paid" and fees["due_date"] == "2026-11-30" and fees["transactions"] == []
    assert record["hostel"]["room"] == "To be allotted"
    assert record["library"] == {"books_issued": [], "fine_due": 0}
    assert record["attendance"]["overall_pct"] is None
    assert [c["code"] for c in record["attendance"]["courses"]] == ["MA101", "CS101"]
    assert all(c["pct"] is None for c in record["attendance"]["courses"])
    assert record["results"] == {"cgpa": None, "last_sgpa": None, "backlogs": 0}
    assert [e["course_code"] for e in record["exams"]["upcoming"]] == ["MA101"]  # End-Sem only

    ids = [r["student_id"] for r in student_data.list_students(db_path=db_file)]
    assert "XYZ26CSE006" in ids and "XYZ24CSE001" in ids
    assert "XYZ26CSE006" not in student_data.load_students()  # tmp JSON without db_path: no DB rows


def test_next_roll_counts_json_and_db(db_file):
    auth.register_new_student("First New", "first@mail.example", "pass-word-1", "CSE", path=db_file)
    second = auth.register_new_student("Second New", "second@mail.example", "pass-word-1", "CSE", path=db_file)
    other = auth.register_new_student("Third New", "third@mail.example", "pass-word-1", "ME", path=db_file)
    assert second["student_id"] == "XYZ26CSE007" and other["student_id"] == "XYZ26ME001"
    me = student_data.get_student("XYZ26ME001", db_path=db_file)
    assert me["fees"]["total"] == 95000 and me["hostel"] is None
    assert me["attendance"]["courses"] == [] and me["exams"]["upcoming"] == []  # no first-year ME template


def test_student_context_handles_missing_attendance(db_file):
    auth.register_new_student("Nisha Kapoor", "nisha@mail.example", "nisha-pass-1", "ECE", category="SC", path=db_file)
    text = student_data.student_context(student_data.get_student("XYZ26ECE010", db_path=db_file))
    assert "Attendance: overall not recorded yet" in text
    assert "MA101 Engineering Mathematics I: not recorded yet" in text
    assert "None%" not in text and "CGPA not available yet" in text
    assert "total Rs 35,000, paid Rs 0, due Rs 35,000" in text


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"name": "A"}, "Name"),
        ({"name": "x" * 61}, "Name"),
        ({"email": "not-an-email"}, "valid email"),
        ({"branch": "EEE"}, "branch"),
        ({"category": "VIP"}, "category"),
        ({"password": "weakpass"}, "digit"),
        ({"password": "short1"}, "8 characters"),
    ],
)
def test_register_validation(db_file, kwargs, message):
    args = {"name": "Nisha Kapoor", "email": "nisha@mail.example", "password": "nisha-pass-1", "branch": "CSE"}
    args.update(kwargs)
    with pytest.raises(ValueError, match=message):
        auth.register_new_student(**args, path=db_file)
    assert auth.load_users(db_file) == {} and student_data.load_db_students(db_file) == {}


def test_register_duplicates_refused(db_file):
    auth.register_new_student("Nisha Kapoor", "nisha@mail.example", "nisha-pass-1", "CSE", path=db_file)
    with pytest.raises(ValueError, match="already"):
        auth.register_new_student("Nisha Again", "NISHA@mail.example", "nisha-pass-2", "ECE", path=db_file)
    with pytest.raises(ValueError, match="claim"):  # an existing student's college email
        auth.register_new_student("Rohan G", "rohan.gupta005@xyz.edu.example", "rohan-pass-1", "CSE", path=db_file)
    auth.create_user("staff@mail.example", "staff-pass-1", "admin", "Staff", path=db_file)
    with pytest.raises(ValueError, match="already"):
        auth.register_new_student("Staff Person", "staff@mail.example", "staff-pass-1", "CSE", path=db_file)
    assert len(student_data.load_db_students(db_file)) == 1


# ------------------------------------------------------------ claim an existing student's account


def test_claim_success(db_file):
    user = auth.claim_student_account(" xyz26cse005 ", "ROHAN.GUPTA005@xyz.edu.example", "rohan-pass-1", path=db_file)
    assert user == {"email": "rohan.gupta005@xyz.edu.example", "role": "student", "name": "Rohan Gupta",
                    "student_id": "XYZ26CSE005", "must_change_password": False}
    assert auth.authenticate("rohan.gupta005@xyz.edu.example", "rohan-pass-1", path=db_file) == user


@pytest.mark.parametrize(
    "sid, email",
    [
        ("XYZ26CSE005", "arjun.das009@xyz.edu.example"),  # someone else's email
        ("XYZ99CSE999", "rohan.gupta005@xyz.edu.example"),  # unknown id
        ("", "rohan.gupta005@xyz.edu.example"),
        ("XYZ26CSE005", ""),
    ],
)
def test_claim_mismatch_is_generic(db_file, sid, email):
    with pytest.raises(ValueError) as err:
        auth.claim_student_account(sid, email, "rohan-pass-1", path=db_file)
    assert str(err.value) == auth.CLAIM_MISMATCH
    assert auth.load_users(db_file) == {}


def test_claim_twice_refused(db_file):
    auth.claim_student_account("XYZ26CSE005", "rohan.gupta005@xyz.edu.example", "rohan-pass-1", path=db_file)
    with pytest.raises(ValueError, match="already exists"):
        auth.claim_student_account("XYZ26CSE005", "rohan.gupta005@xyz.edu.example", "other-pass-2", path=db_file)
    assert auth.authenticate("rohan.gupta005@xyz.edu.example", "rohan-pass-1", path=db_file)


def test_claim_refused_when_seeded_account_exists(db_file):
    auth.create_user("rohan.gupta005@xyz.edu.example", "seeded-pass-1", "student", "Rohan Gupta", "XYZ26CSE005",
                     path=db_file)
    with pytest.raises(ValueError, match="already exists"):
        auth.claim_student_account("XYZ26CSE005", "rohan.gupta005@xyz.edu.example", "rohan-pass-1", path=db_file)


def test_claim_weak_password(db_file):
    with pytest.raises(ValueError, match="digit"):
        auth.claim_student_account("XYZ26CSE005", "rohan.gupta005@xyz.edu.example", "no-digits-here", path=db_file)
