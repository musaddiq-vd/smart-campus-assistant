"""Offline tests for password reset (code by email or on screen), admin resets and the mailer."""

from __future__ import annotations

import pytest

from smart_campus_assistant import auth, db, mailer

ASHA = "asha@xyz.edu.example"
SMTP_ENV = {"SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587", "SMTP_USER": "bot",
            "SMTP_PASSWORD": "smtp-secret", "SMTP_FROM": "helpdesk@xyz.edu.example"}


@pytest.fixture(autouse=True)
def fast_hashing_no_smtp(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)
    for name in SMTP_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "app.db"
    auth.create_user(ASHA, "student-pass-1", "student", "Asha", "XYZ24CSE001", path)
    auth.create_user(auth.ADMIN_EMAIL, "admin-pass-1", "admin", auth.ADMIN_NAME, path=path)
    return path


def _reset_row(path, email=ASHA):
    with db.reading(path) as conn:
        return conn.execute("SELECT * FROM password_resets WHERE email = ?", (email,)).fetchone()


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise OSError("connection reset")
        FakeSMTP.sent.append((self, msg))


@pytest.fixture
def smtp(monkeypatch):
    for name, value in SMTP_ENV.items():
        monkeypatch.setenv(name, value)
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


# ------------------------------------------------------------ screen delivery (demo, no SMTP)


def test_code_on_screen_without_smtp_and_only_hash_stored(db_file):
    result = auth.request_password_reset(" ASHA@xyz.edu.example ", path=db_file, now=1000.0)
    assert result["delivery"] == "screen"
    code = result["code"]
    assert len(code) == 6 and code.isdigit()
    row = _reset_row(db_file)
    assert row["code_hash"].startswith("pbkdf2_sha256$") and code not in row["code_hash"]
    assert row["expires"] == 1000.0 + 15 * 60 and row["attempts"] == 0


def test_unknown_email_indistinguishable(db_file):
    for email in ("ghost@xyz.edu.example", "not an email", ""):
        assert auth.request_password_reset(email, path=db_file) == {"delivery": "none", "code": None}
    with db.reading(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0


def test_successful_reset_revokes_sessions(db_file):
    user = auth.authenticate(ASHA, "student-pass-1", path=db_file)
    token = auth.create_session(user, path=db_file)
    code = auth.request_password_reset(ASHA, path=db_file)["code"]
    assert auth.confirm_password_reset(ASHA, f" {code[:3]} {code[3:]} ", "fresh-pass-77", path=db_file) is True
    assert auth.authenticate(ASHA, "fresh-pass-77", path=db_file)
    assert auth.authenticate(ASHA, "student-pass-1", path=db_file) is None
    assert auth.get_session_user(token, path=db_file) is None
    assert _reset_row(db_file) is None
    assert auth.confirm_password_reset(ASHA, code, "fresh-pass-88", path=db_file) is False  # single use


def test_wrong_code_and_max_attempts(db_file):
    code = auth.request_password_reset(ASHA, path=db_file, now=1000.0)["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(auth.RESET_MAX_ATTEMPTS):
        assert auth.confirm_password_reset(ASHA, wrong, "fresh-pass-77", path=db_file, now=1010.0) is False
    assert _reset_row(db_file)["attempts"] == 5
    # The right code no longer works after 5 failed attempts.
    assert auth.confirm_password_reset(ASHA, code, "fresh-pass-77", path=db_file, now=1020.0) is False
    assert auth.authenticate(ASHA, "student-pass-1", path=db_file)


def test_code_expires(db_file):
    code = auth.request_password_reset(ASHA, path=db_file, now=1000.0)["code"]
    assert auth.confirm_password_reset(ASHA, code, "fresh-pass-77", path=db_file, now=1000.0 + 15 * 60 + 1) is False
    assert _reset_row(db_file) is None  # expired rows are removed


def test_weak_new_password_raises_without_using_an_attempt(db_file):
    code = auth.request_password_reset(ASHA, path=db_file)["code"]
    with pytest.raises(ValueError, match="digit"):
        auth.confirm_password_reset(ASHA, code, "only-letters", path=db_file)
    assert _reset_row(db_file)["attempts"] == 0


def test_rate_limit_and_new_request_replaces_old(db_file):
    first = auth.request_password_reset(ASHA, path=db_file, now=1000.0)["code"]
    assert auth.request_password_reset(ASHA, path=db_file, now=1030.0) == {"delivery": "none", "code": None}
    second = auth.request_password_reset(ASHA, path=db_file, now=1061.0)
    assert second["delivery"] == "screen"
    if first != second["code"]:
        assert auth.confirm_password_reset(ASHA, first, "fresh-pass-77", path=db_file, now=1070.0) is False
    assert auth.confirm_password_reset(ASHA, second["code"], "fresh-pass-77", path=db_file, now=1070.0) is True


def test_unknown_email_confirm_is_false(db_file):
    assert auth.confirm_password_reset("ghost@xyz.edu.example", "123456", "fresh-pass-77", path=db_file) is False


# ------------------------------------------------------------ email delivery (SMTP configured)


def test_mailer_called_when_configured(db_file, smtp):
    assert mailer.email_configured()
    result = auth.request_password_reset(ASHA, path=db_file)
    assert result == {"delivery": "email", "code": None}
    assert len(smtp.sent) == 1
    conn, msg = smtp.sent[0]
    assert (conn.host, conn.port, conn.timeout) == ("smtp.example.test", 587, 10)
    assert conn.calls[:3] == ["ehlo", "starttls", "ehlo"] and ("login", "bot") in conn.calls
    assert msg["To"] == ASHA and msg["From"] == "helpdesk@xyz.edu.example"
    body = msg.get_content()
    code = next(word for word in body.split() if word.isdigit() and len(word) == 6)
    assert auth.confirm_password_reset(ASHA, code, "fresh-pass-77", path=db_file) is True


def test_with_smtp_unknown_email_looks_the_same(db_file, smtp):
    assert auth.request_password_reset("ghost@xyz.edu.example", path=db_file) == {"delivery": "email", "code": None}
    assert smtp.sent == []


def test_send_failure_never_raises_and_allows_retry(db_file, smtp, caplog):
    smtp.fail = True
    assert auth.request_password_reset(ASHA, path=db_file, now=1000.0) == {"delivery": "email", "code": None}
    assert _reset_row(db_file) is None
    assert "smtp-secret" not in caplog.text
    smtp.fail = False
    auth.request_password_reset(ASHA, path=db_file, now=1001.0)
    assert len(smtp.sent) == 1


def test_send_email_not_configured_returns_false():
    assert mailer.email_configured() is False
    assert mailer.send_email(ASHA, "Hi", "Body") is False


def test_send_email_bad_port(monkeypatch):
    for name, value in SMTP_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SMTP_PORT", "abc")
    assert mailer.email_configured() is False


# ------------------------------------------------------------ admin reset + must_change_password


def test_admin_reset_sets_temp_password_and_flag(db_file):
    token = auth.create_session(auth.authenticate(ASHA, "student-pass-1", path=db_file), path=db_file)
    temp = auth.admin_reset_password(ASHA, auth.ADMIN_EMAIL, path=db_file)
    assert auth.validate_new_password(temp) == []
    assert auth.get_session_user(token, path=db_file) is None
    assert auth.authenticate(ASHA, "student-pass-1", path=db_file) is None
    user = auth.authenticate(ASHA, temp, path=db_file)
    assert user["must_change_password"] is True
    token = auth.create_session(user, path=db_file)
    assert auth.get_session_user(token, path=db_file)["must_change_password"] is True

    auth.set_password(ASHA, "my-own-pass-5", path=db_file)  # clears the flag
    assert auth.authenticate(ASHA, "my-own-pass-5", path=db_file)["must_change_password"] is False


def test_admin_reset_refusals(db_file):
    with pytest.raises(ValueError):
        auth.admin_reset_password(auth.ADMIN_EMAIL, auth.ADMIN_EMAIL.upper(), path=db_file)
    with pytest.raises(PermissionError):
        auth.admin_reset_password(auth.ADMIN_EMAIL, ASHA, path=db_file)
    with pytest.raises(KeyError):
        auth.admin_reset_password("ghost@xyz.edu.example", auth.ADMIN_EMAIL, path=db_file)
    assert auth.authenticate(auth.ADMIN_EMAIL, "admin-pass-1", path=db_file)
