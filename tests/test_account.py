"""Tests for the "Account" page (change password). Backend modules are stubbed; no AWS, no real files."""

from __future__ import annotations

import pytest

from tests.test_ui_smoke import ACCOUNTS, all_text, backend, sign_in, start  # noqa: F401  (backend is a fixture)
from ui.account import password_problems, strength_label

STUDENT = "aarav@xyz.edu.example"


@pytest.fixture
def account_backend(backend):
    """Add set_password / create_session to the auth stub and record their calls."""
    calls = {"set_password": [], "create_session": []}

    def set_password(email, new_password):
        calls["set_password"].append((email, new_password))
        old = ACCOUNTS[email]
        ACCOUNTS[email] = (new_password, old[1])

    def create_session(user):
        calls["create_session"].append(dict(user))
        return "fresh-token"

    backend.auth.set_password = set_password
    backend.auth.create_session = create_session
    backend.auth.MIN_PASSWORD_LENGTH = 8
    backend.auth.SESSION_TTL_SECONDS = 3600
    saved = dict(ACCOUNTS)
    backend.calls = calls
    yield backend
    ACCOUNTS.clear()
    ACCOUNTS.update(saved)


def open_account(email=STUDENT, password="student-pass", role="Student"):
    at = sign_in(start(), email, password, role=role)
    at.switch_page("ui/account.py").run()
    assert not at.exception
    return at


def submit(at, current, new, confirm=None):
    at.text_input(key="pw_current").input(current)
    at.text_input(key="pw_new").input(new)
    at.text_input(key="pw_confirm").input(new if confirm is None else confirm)
    at.button(key="pw_submit").click().run()
    assert not at.exception
    return at


def assert_cleared(at):
    for key in ("pw_current", "pw_new", "pw_confirm"):
        assert at.session_state[key] == ""


def test_rules_and_strength():
    assert password_problems("abc", "x", 8)
    assert "at least one digit" in password_problems("abcdefgh", "x", 8)
    assert "at least one letter" in password_problems("12345678", "x", 8)
    assert "different from the current password" in password_problems("same1234", "same1234", 8)
    assert password_problems("newpass123", "old", 8) == []
    assert strength_label("abc") == "Weak" and strength_label("Longer-Pass-123") == "Strong"


def test_account_page_for_both_roles(account_backend):
    at = open_account()
    assert "Change password" in all_text(at) and STUDENT in all_text(at)
    assert account_backend.pages[-1][-1] == "Account"
    open_account("admin@xyz.edu.example", "admin-pass", role="Admin")
    assert account_backend.pages[-1][-1] == "Account" and "Overview" in account_backend.pages[-1]


def test_wrong_current_password(account_backend):
    at = submit(open_account(), "wrong-pass1", "newpass123")
    assert at.error and "current password is incorrect" in at.error[0].value
    assert account_backend.calls["set_password"] == []
    assert_cleared(at)


def test_wrong_current_password_is_throttled(account_backend):
    at = open_account()
    for _ in range(3):
        submit(at, "wrong-pass1", "newpass123")
    assert "Too many failed attempts" in at.error[0].value
    submit(at, "student-pass", "newpass123")  # locked even with the right password
    assert "Too many failed attempts" in at.error[0].value
    assert account_backend.calls["set_password"] == []


def test_mismatch(account_backend):
    at = submit(open_account(), "student-pass", "newpass123", "newpass124")
    assert "do not match" in at.error[0].value
    assert account_backend.calls["set_password"] == []
    assert_cleared(at)


@pytest.mark.parametrize("new", ["short1", "onlyletters", "1234567890", "student-pass"])
def test_too_weak_or_same(account_backend, new):
    at = submit(open_account(), "student-pass", new)
    assert "too weak" in at.error[0].value
    assert account_backend.calls["set_password"] == []
    assert_cleared(at)


def test_success_refreshes_session_and_cookie(account_backend):
    at = open_account()
    account_backend.calls["create_session"].clear()  # ignore the sign-in session
    submit(at, "student-pass", "newpass123")
    assert at.success and "other devices were signed out" in at.success[0].value
    assert account_backend.calls["set_password"] == [(STUDENT, "newpass123")]
    assert [u["email"] for u in account_backend.calls["create_session"]] == [STUDENT]
    assert at.session_state["session_token"] == "fresh-token"
    # app.py flushed the queued cookie on this run (pops set_cookie / cookie_max_age).
    assert "set_cookie" not in at.session_state and "cookie_max_age" not in at.session_state
    assert at.session_state["user"]["email"] == STUDENT  # still signed in here
    assert_cleared(at)
    assert "newpass123" not in repr(at.session_state)


def test_success_queues_cookie(account_backend, monkeypatch):
    """The callback queues set_cookie exactly like the sign-in form does."""
    import ui.session

    written = []
    monkeypatch.setattr(ui.session, "write_cookie", lambda token, age: written.append((token, age)))
    at = open_account()
    written.clear()
    submit(at, "student-pass", "newpass123")
    assert ("fresh-token", 3600) in written
