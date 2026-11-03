"""Offline tests for db.py: schema creation, idempotent init and the one-time legacy JSON import."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from smart_campus_assistant import auth, db


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_init_creates_schema(tmp_path):
    path = tmp_path / "app.db"
    info = db.init_db(path)
    assert path.exists() and info["schema_version"] == db.SCHEMA_VERSION and info["users"] == 0
    assert {"users", "sessions", "password_resets", "students", "schema_version"} <= _tables(path)
    with db.reading(path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert isinstance(conn.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "app.db"
    db.init_db(path)
    auth.create_user("a@xyz.edu.example", "pass-word-1", "admin", "A", path=path)
    info = db.init_db(path)
    assert info["users"] == 1 and info["imported_users"] == 0
    with db.reading(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_email_must_be_lower_case(tmp_path):
    path = tmp_path / "app.db"
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction(path) as conn:
            conn.execute(
                "INSERT INTO users (email, role, name, password_hash, created_at) VALUES ('A@x.example', 'admin', '', 'h', 'now')"
            )


def test_transaction_rolls_back_on_error(tmp_path):
    path = tmp_path / "app.db"
    with pytest.raises(RuntimeError):
        with db.transaction(path) as conn:
            conn.execute(
                "INSERT INTO users (email, role, name, password_hash, created_at) VALUES ('a@x.example', 'admin', '', 'h', 'now')"
            )
            raise RuntimeError("boom")
    assert auth.load_users(path) == {}


def test_legacy_json_import_keeps_hashes(tmp_path):
    old_hash = auth.hash_password("old-password-7")
    (tmp_path / "users.json").write_text(json.dumps({"users": [
        {"email": "Old@XYZ.edu.example", "role": "student", "name": "Old", "student_id": "xyz24cse009",
         "password_hash": old_hash},
        {"email": auth.ADMIN_EMAIL, "role": "admin", "name": "Admin", "student_id": None, "password_hash": old_hash},
        {"email": "broken@xyz.edu.example", "role": "nobody", "password_hash": old_hash},
    ]}), encoding="utf-8")
    token = "T" * 43
    (tmp_path / "sessions.json").write_text(json.dumps({"sessions": {
        auth._token_hash(token): {"email": "old@xyz.edu.example", "role": "student", "expires": time.time() + 3600},
        "expired": {"email": "old@xyz.edu.example", "role": "student", "expires": 5.0},
        "orphan": {"email": "ghost@xyz.edu.example", "role": "student", "expires": time.time() + 3600},
    }}), encoding="utf-8")

    path = tmp_path / "app.db"
    info = db.init_db(path)
    assert info["imported_users"] == 2 and info["imported_sessions"] == 1
    users = auth.load_users(path)
    assert users["old@xyz.edu.example"]["password_hash"] == old_hash
    assert users["old@xyz.edu.example"]["student_id"] == "XYZ24CSE009"
    user = auth.authenticate("old@xyz.edu.example", "old-password-7", path=path)
    assert user and user["must_change_password"] is False
    assert auth.get_session_user(token, path=path)["email"] == "old@xyz.edu.example"

    # Importing happens only once, even if the users table is emptied later.
    auth.save_users({}, path)
    assert db.init_db(path)["imported_users"] == 0 and auth.load_users(path) == {}


def test_no_legacy_files_means_empty_db(tmp_path):
    info = db.init_db(tmp_path / "app.db")
    assert info["imported_users"] == 0 and info["imported_sessions"] == 0
