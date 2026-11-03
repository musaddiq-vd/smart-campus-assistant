"""Offline tests for auth.py (tmp files, low PBKDF2 iteration count)."""

from __future__ import annotations

import csv
import json

import pytest

from smart_campus_assistant import auth


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


@pytest.fixture
def users_file(tmp_path):
    path = tmp_path / "users.json"
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


# ------------------------------------------------------------ hashing


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


# ------------------------------------------------------------ authenticate


def test_authenticate_case_insensitive_email(users_file):
    user = auth.authenticate("  ASHA@xyz.EDU.example ", "student-pass-1", path=users_file)
    assert user == {"email": "asha@xyz.edu.example", "role": "student", "name": "Asha", "student_id": "XYZ24CSE001"}
    assert "password_hash" not in user


def test_authenticate_wrong_password_and_role(users_file):
    assert auth.authenticate("asha@xyz.edu.example", "nope-nope-1", path=users_file) is None
    assert auth.authenticate("asha@xyz.edu.example", "student-pass-1", role="admin", path=users_file) is None
    admin = auth.authenticate(auth.ADMIN_EMAIL, "admin-pass-1", role="admin", path=users_file)
    assert admin and admin["role"] == "admin" and admin["student_id"] is None


def test_unknown_email_still_verifies(users_file, monkeypatch):
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, e: calls.append(e) or real(p, e))
    assert auth.authenticate("ghost@xyz.edu.example", "whatever-1", path=users_file) is None
    assert len(calls) == 1 and calls[0].startswith("pbkdf2_sha256$")


def test_create_user_duplicate_and_set_password(users_file):
    with pytest.raises(ValueError):
        auth.create_user("asha@xyz.edu.example", "another-pass", "student", "A", "XYZ24CSE001", users_file)
    auth.set_password("ASHA@xyz.edu.example", "brand-new-pass", users_file)
    assert auth.authenticate("asha@xyz.edu.example", "brand-new-pass", path=users_file)
    assert auth.authenticate("asha@xyz.edu.example", "student-pass-1", path=users_file) is None
    with pytest.raises(KeyError):
        auth.set_password("ghost@xyz.edu.example", "brand-new-pass", users_file)


def test_load_users_missing_file(tmp_path):
    assert auth.load_users(tmp_path / "none.json") == {}


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
    users, creds = tmp_path / "users.json", tmp_path / "creds.csv"
    count = auth.seed_demo_users(users, creds, _students_file(tmp_path))
    assert count == 21
    records = auth.load_users(users)
    assert len(records) == 21 and records[auth.ADMIN_EMAIL]["role"] == "admin"
    assert "password" not in users.read_text(encoding="utf-8").lower().replace("password_hash", "")

    with creds.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 21 and list(rows[0]) == list(auth.CSV_COLUMNS)
    for row in rows:
        assert len(row["password"]) >= 10
        user = auth.authenticate(row["email"], row["password"], role=row["role"], path=users)
        assert user and user["student_id"] == (row["student_id"] or None)


def test_seed_refuses_overwrite_without_force(tmp_path):
    users, creds, students = tmp_path / "users.json", tmp_path / "creds.csv", _students_file(tmp_path, 2)
    auth.seed_demo_users(users, creds, students)
    with pytest.raises(FileExistsError):
        auth.seed_demo_users(users, creds, students)
    assert auth.seed_demo_users(users, creds, students, force=True) == 3


def test_generate_password_readable():
    for _ in range(50):
        pw = auth.generate_password()
        assert len(pw) >= 10 and not set(pw) & set("0O1lI")


def test_cli_list_users_hides_hashes(users_file, capsys):
    assert auth.main(["--users-file", str(users_file), "list-users"]) == 0
    out = capsys.readouterr().out
    assert "asha@xyz.edu.example" in out and "XYZ24CSE001" in out
    assert "pbkdf2" not in out and "student-pass-1" not in out


def test_cli_seed_demo_refuses_existing(users_file, capsys):
    assert auth.main(["--users-file", str(users_file), "seed-demo"]) == 1
    assert "already exists" in capsys.readouterr().err


# ------------------------------------------------------------ hosted demo bootstrap


def test_ensure_hosted_demo_users(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    students = _students_file(tmp_path, n=3)
    monkeypatch.setattr(auth, "get_settings", lambda: type("S", (), {"students_file": students, "users_file": users})())
    monkeypatch.delenv("SCA_DEMO_PASSWORD", raising=False)
    assert auth.ensure_hosted_demo_users(users) == 0 and not users.exists()
    monkeypatch.setenv("SCA_DEMO_PASSWORD", "shared-demo-42")
    assert auth.ensure_hosted_demo_users(users) == 4
    assert not (tmp_path / "demo_credentials.csv").exists()
    assert auth.authenticate("s1@xyz.edu.example", "shared-demo-42", path=users)["role"] == "student"
    assert auth.authenticate(auth.ADMIN_EMAIL, "shared-demo-42", path=users)["role"] == "admin"
    assert auth.ensure_hosted_demo_users(users) == 0  # never re-seeds existing accounts


def test_shared_password_too_short(tmp_path):
    with pytest.raises(ValueError):
        auth.seed_demo_users(tmp_path / "u.json", students_file=_students_file(tmp_path, 2), shared_password="short")
