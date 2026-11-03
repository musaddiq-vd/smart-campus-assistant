"""Offline tests for staying signed in: server sessions (auth.py, SQLite) and cookie restore (ui/session.py)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from smart_campus_assistant import auth

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import session as ui_session  # noqa: E402


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "app.db"
    auth.create_user("asha@xyz.edu.example", "student-pass-1", "student", "Asha", "XYZ24CSE001", path)
    auth.create_user(auth.ADMIN_EMAIL, "admin-pass-1", "admin", auth.ADMIN_NAME, path=path)
    return path


def _login(db_file, email="asha@xyz.edu.example", password="student-pass-1", now=None):
    user = auth.authenticate(email, password, path=db_file)
    return auth.create_session(user, path=db_file, now=now)


def _session_rows(db_file):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT token_hash, email, role, expires FROM sessions").fetchall()
    finally:
        conn.close()


def test_session_round_trip_and_only_hash_stored(db_file):
    token = _login(db_file)
    user = auth.get_session_user(token, path=db_file)
    assert user == {"email": "asha@xyz.edu.example", "role": "student", "name": "Asha", "student_id": "XYZ24CSE001",
                    "must_change_password": False}
    rows = _session_rows(db_file)
    assert [r[0] for r in rows] == [auth._token_hash(token)]
    assert token not in repr(rows)


def test_users_path_still_accepted(db_file):
    user = auth.authenticate("asha@xyz.edu.example", "student-pass-1", path=db_file)
    token = auth.create_session(user, users_path=db_file)
    assert auth.get_session_user(token, users_path=db_file)["email"] == "asha@xyz.edu.example"


def test_tokens_are_unique_and_random(db_file):
    assert _login(db_file) != _login(db_file)


@pytest.mark.parametrize("bad", [None, "", "short", "x" * 200, "has spaces in it but is long enough ok", 123])
def test_malformed_or_unknown_token_rejected(db_file, bad):
    assert auth.get_session_user(bad, path=db_file) is None


def test_unknown_but_well_formed_token_rejected(db_file):
    _login(db_file)
    assert auth.get_session_user("A" * 43, path=db_file) is None


def test_session_expires(db_file):
    token = _login(db_file, now=1_000.0)
    assert auth.get_session_user(token, path=db_file, now=1_000.0 + 60) is not None
    assert auth.get_session_user(token, path=db_file, now=1_000.0 + auth.SESSION_TTL_SECONDS + 1) is None


def test_expired_sessions_are_pruned(db_file):
    _login(db_file, now=1_000.0)
    fresh = _login(db_file)  # real clock: the old one is long expired
    assert [r[0] for r in _session_rows(db_file)] == [auth._token_hash(fresh)]


def test_session_for_unknown_account_refused(db_file):
    with pytest.raises(KeyError):
        auth.create_session({"email": "ghost@xyz.edu.example", "role": "student"}, path=db_file)


def test_logout_revokes_only_that_session(db_file):
    a, b = _login(db_file), _login(db_file)
    auth.revoke_session(a, path=db_file)
    assert auth.get_session_user(a, path=db_file) is None
    assert auth.get_session_user(b, path=db_file) is not None
    auth.revoke_session("not-a-token", path=db_file)  # ignored, no error


def test_revoke_user_sessions_counts(db_file):
    _login(db_file), _login(db_file)
    admin_token = _login(db_file, auth.ADMIN_EMAIL, "admin-pass-1")
    assert auth.revoke_user_sessions("ASHA@xyz.edu.example", path=db_file) == 2
    assert auth.get_session_user(admin_token, path=db_file) is not None


def test_password_change_signs_out_everywhere(db_file):
    token = _login(db_file)
    admin_token = _login(db_file, auth.ADMIN_EMAIL, "admin-pass-1")
    auth.set_password("asha@xyz.edu.example", "brand-new-pass-2", path=db_file)
    assert auth.get_session_user(token, path=db_file) is None
    assert auth.get_session_user(admin_token, path=db_file) is not None


def test_deleted_account_or_changed_role_ends_session(db_file):
    token = _login(db_file)
    users = auth.load_users(db_file)
    users["asha@xyz.edu.example"]["role"] = "admin"
    auth.save_users(users, db_file)
    assert auth.get_session_user(token, path=db_file) is None
    users.pop("asha@xyz.edu.example")
    auth.save_users(users, db_file)
    assert auth.get_session_user(token, path=db_file) is None


# ------------------------------------------------------------ cookie restore (ui/session.py)


class _FakeAuth:
    def __init__(self, user):
        self.user = user

    def get_session_user(self, token):
        return self.user if token == "good-token" else None


def test_restore_user_from_valid_cookie(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(ui_session.st, "session_state", state)
    monkeypatch.setattr(ui_session, "read_token", lambda: "good-token")
    fake = _FakeAuth({"email": "asha@xyz.edu.example", "role": "student", "name": "Asha", "student_id": "XYZ24CSE001"})
    user = ui_session.restore_user(fake)
    assert user["student_id"] == "XYZ24CSE001" and user["role"] == "student"
    assert state["session_token"] == "good-token"
    assert "clear_cookie" not in state


def test_restore_user_bad_cookie_is_cleared(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(ui_session.st, "session_state", state)
    monkeypatch.setattr(ui_session, "read_token", lambda: "stale-token")
    assert ui_session.restore_user(_FakeAuth(None)) is None
    assert state["clear_cookie"] is True


def test_restore_user_without_cookie(monkeypatch):
    monkeypatch.setattr(ui_session, "read_token", lambda: None)
    assert ui_session.restore_user(_FakeAuth({"role": "student"})) is None


def test_admin_cookie_never_gets_a_student_id(monkeypatch):
    monkeypatch.setattr(ui_session.st, "session_state", {})
    monkeypatch.setattr(ui_session, "read_token", lambda: "good-token")
    fake = _FakeAuth({"email": auth.ADMIN_EMAIL, "role": "admin", "name": "Admin", "student_id": "XYZ24CSE001"})
    assert ui_session.restore_user(fake)["student_id"] is None
