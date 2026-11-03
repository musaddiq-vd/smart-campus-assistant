"""Offline tests for auth.py (tmp SQLite database, low PBKDF2 iteration count)."""

from __future__ import annotations

import csv
import json

import pytest

from smart_campus_assistant import auth


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "app.db"
    auth.create_user("Asha@XYZ.edu.example", "student-pass-1", "student", "Asha", "xyz24cse001", path)
    auth.create_user(auth.ADMIN_EMAIL, "admin-pass-1", "admin", auth.ADMIN_NAME, path=path)
    return path


def _students_file(tmp_path, n=20):
    rows = [
        {"student_id": f"XYZ24CSE{i:03d}", "name": f"Student {i}", "email": f"s{i}@xyz.edu.example"}
        for i in range(1, n + 1)
    ]
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"students": rows}), encoding="utf-8")
    return path


ASHA = {"email": "asha@xyz.edu.example", "role": "student", "name": "Asha", "student_id": "XYZ24CSE001",
        "must_change_password": False}


# ------------------------------------------------------------ hashing + password rules


def test_hash_format_and_unique_salt():
    a, b = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    parts = a.split("$")
    assert parts[0] == "pbkdf2_sha256" and parts[1] == "1000" and len(parts) == 4
    assert a != b and a.split("$")[2] != b.split("$")[2]


def test_verify_ok_wrong_and_malformed():
    encoded = auth.hash_password("secret-123")
    assert auth.verify_password("secret-123", encoded)
    assert not auth.verify_password("secret-124", encoded)
    for bad in ["", "garbage", "md5$1$a$b", "pbkdf2_sha256$x$a$b", "pbkdf2_sha256$10$!!$??", None, 42]:
        assert not auth.verify_password("secret-123", bad)
    assert not auth.verify_password(None, encoded)


def test_min_password_length():
    with pytest.raises(ValueError):
        auth.hash_password("short7!")


def test_validate_new_password():
    assert auth.validate_new_password("abcdefg1") == []
    assert len(auth.PASSWORD_RULES) == 3
    assert any("8 characters" in p for p in auth.validate_new_password("abc1"))
    assert any("digit" in p for p in auth.validate_new_password("abcdefgh"))
    assert any("letter" in p for p in auth.validate_new_password("12345678"))
    assert auth.validate_new_password(None)
    assert auth.validate_new_password("a1" * 100)  # too long


def test_weak_passwords_refused_everywhere(db_file):
    with pytest.raises(ValueError, match="digit"):
        auth.create_user("x@xyz.edu.example", "no-digits-here", "admin", "X", path=db_file)
    with pytest.raises(ValueError, match="digit"):
        auth.set_password("asha@xyz.edu.example", "brand-new-pass", db_file)
    assert auth.authenticate("asha@xyz.edu.example", "student-pass-1", path=db_file)


# ------------------------------------------------------------ authenticate


def test_authenticate_case_insensitive_email(db_file):
    user = auth.authenticate("  ASHA@xyz.EDU.example ", "student-pass-1", path=db_file)
    assert user == ASHA
    assert "password_hash" not in user


def test_authenticate_wrong_password_and_role(db_file):
    assert auth.authenticate("asha@xyz.edu.example", "nope-nope-1", path=db_file) is None
    assert auth.authenticate("asha@xyz.edu.example", "student-pass-1", role="admin", path=db_file) is None
    admin = auth.authenticate(auth.ADMIN_EMAIL, "admin-pass-1", role="admin", path=db_file)
    assert admin and admin["role"] == "admin" and admin["student_id"] is None


def test_unknown_email_still_verifies(db_file, monkeypatch):
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, e: calls.append(e) or real(p, e))
    assert auth.authenticate("ghost@xyz.edu.example", "whatever-1", path=db_file) is None
    assert len(calls) == 1 and calls[0].startswith("pbkdf2_sha256$")


def test_create_user_duplicate_and_set_password(db_file):
    with pytest.raises(ValueError):
        auth.create_user("asha@xyz.edu.example", "another-pass-2", "student", "A", "XYZ24CSE002", db_file)
    with pytest.raises(ValueError):  # same student id, other email
        auth.create_user("other@xyz.edu.example", "another-pass-2", "student", "A", "XYZ24CSE001", db_file)
    auth.set_password("ASHA@xyz.edu.example", "brand-new-pass-9", db_file)
    assert auth.authenticate("asha@xyz.edu.example", "brand-new-pass-9", path=db_file)
    assert auth.authenticate("asha@xyz.edu.example", "student-pass-1", path=db_file) is None
    with pytest.raises(KeyError):
        auth.set_password("ghost@xyz.edu.example", "brand-new-pass-9", db_file)


def test_load_and_save_users_compat(db_file, tmp_path):
    assert auth.load_users(tmp_path / "empty.db") == {}
    users = auth.load_users(db_file)
    assert set(users) == {"asha@xyz.edu.example", auth.ADMIN_EMAIL}
    assert users["asha@xyz.edu.example"]["password_hash"].startswith("pbkdf2_sha256$")
    users["asha@xyz.edu.example"]["name"] = "Asha R"
    users.pop(auth.ADMIN_EMAIL)
    auth.save_users(users, db_file)
    assert set(auth.load_users(db_file)) == {"asha@xyz.edu.example"}
    assert auth.authenticate("asha@xyz.edu.example", "student-pass-1", path=db_file)["name"] == "Asha R"


# ------------------------------------------------------------ throttle


def test_throttle_lockout_and_reset():
    now = [1000.0]
    t = auth.LoginThrottle(clock=lambda: now[0])
    for _ in range(4):
        t.record_failure("A@x.example")
    assert t.check("a@x.example") == 0
    t.record_failure("a@x.example")
    assert t.check("a@x.example") == pytest.approx(60)
    now[0] += 30
    assert t.check("a@x.example") == pytest.approx(30)
    now[0] += 31
    assert t.check("a@x.example") == 0
    # Old failures fall out of the 10-minute window.
    for _ in range(4):
        t.record_failure("b")
    now[0] += 601
    t.record_failure("b")
    assert t.check("b") == 0
    # Success clears everything.
    for _ in range(5):
        t.record_failure("c")
    assert t.check("c") > 0
    t.record_success("c")
    assert t.check("c") == 0


# ------------------------------------------------------------ seeding + CLI


def test_seed_demo_users(tmp_path):
    db_path, creds = tmp_path / "app.db", tmp_path / "creds.csv"
    count = auth.seed_demo_users(db_path, creds, _students_file(tmp_path))
    assert count == 21
    records = auth.load_users(db_path)
    assert len(records) == 21 and records[auth.ADMIN_EMAIL]["role"] == "admin"

    with creds.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 21 and list(rows[0]) == list(auth.CSV_COLUMNS)
    for row in rows:
        assert len(row["password"]) >= 10 and auth.validate_new_password(row["password"]) == []
        user = auth.authenticate(row["email"], row["password"], role=row["role"], path=db_path)
        assert user and user["student_id"] == (row["student_id"] or None)


def test_seed_refuses_overwrite_without_force(tmp_path):
    db_path, creds, students = tmp_path / "app.db", tmp_path / "creds.csv", _students_file(tmp_path, 2)
    auth.seed_demo_users(db_path, creds, students)
    with pytest.raises(FileExistsError):
        auth.seed_demo_users(db_path, creds, students)
    auth.create_user("extra@xyz.edu.example", "extra-pass-1", "admin", "Extra", path=db_path)
    assert auth.seed_demo_users(db_path, creds, students, force=True) == 3
    assert "extra@xyz.edu.example" in auth.load_users(db_path)  # force keeps other accounts


def test_generate_password_readable():
    for _ in range(50):
        pw = auth.generate_password()
        assert len(pw) >= 10 and not set(pw) & set("0O1lI")


def test_cli_list_users_hides_hashes(db_file, capsys):
    assert auth.main(["--db-file", str(db_file), "list-users"]) == 0
    out = capsys.readouterr().out
    assert "asha@xyz.edu.example" in out and "XYZ24CSE001" in out
    assert "pbkdf2" not in out and "student-pass-1" not in out


def test_cli_seed_demo_refuses_existing(db_file, capsys):
    assert auth.main(["--db-file", str(db_file), "seed-demo"]) == 1
    assert "already" in capsys.readouterr().err


def test_cli_init_db(tmp_path, capsys):
    assert auth.main(["--db-file", str(tmp_path / "new.db"), "init-db"]) == 0
    assert "schema v1" in capsys.readouterr().out


# ------------------------------------------------------------ hosted demo bootstrap


def test_ensure_hosted_demo_users(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    students = _students_file(tmp_path, n=3)
    settings = type("S", (), {"students_file": students, "users_file": tmp_path / "users.json", "db_file": db_path})()
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.delenv("SCA_DEMO_PASSWORD", raising=False)
    assert auth.ensure_hosted_demo_users(db_path) == 0 and auth.load_users(db_path) == {}
    monkeypatch.setenv("SCA_DEMO_PASSWORD", "shared-demo-42")
    assert auth.ensure_hosted_demo_users(db_path) == 4
    assert not (tmp_path / "demo_credentials.csv").exists()
    assert auth.authenticate("s1@xyz.edu.example", "shared-demo-42", path=db_path)["role"] == "student"
    assert auth.authenticate(auth.ADMIN_EMAIL, "shared-demo-42", path=db_path)["role"] == "admin"
    assert auth.ensure_hosted_demo_users(db_path) == 0  # never re-seeds existing accounts


def test_shared_password_too_short(tmp_path):
    with pytest.raises(ValueError):
        auth.seed_demo_users(tmp_path / "u.db", students_file=_students_file(tmp_path, 2), shared_password="short")
