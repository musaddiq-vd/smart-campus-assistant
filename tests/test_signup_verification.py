"""Sign-up with email verification, and one-time codes in live mode (no code on screen without demo mode)."""

from __future__ import annotations

import re

import pytest

from smart_campus_assistant import auth, db, mailer, student_data
from tests.test_registration import db_file, fast_hashing  # noqa: F401  (fixtures)

NEW = dict(name="Nisha Kapoor", email="Nisha@Mail.example", password="nisha-pass-1", branch="cse")
ROHAN = ("XYZ26CSE005", "rohan.gupta005@xyz.edu.example")
T0 = 1_000_000.0


@pytest.fixture
def smtp(monkeypatch):
    """SMTP configured: record emails instead of sending them."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def pending_rows(path):
    with db.reading(path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM pending_signups")]


def account(path, email):
    return auth.load_users(path).get(email)


# ---------------------------------------------------------------- code delivery rules


def test_code_delivery_rules(monkeypatch):
    monkeypatch.setattr(mailer, "email_configured", lambda: False)
    monkeypatch.setenv("SCA_DEMO_MODE", "1")
    assert auth.code_delivery() == "screen"
    monkeypatch.setenv("SCA_DEMO_MODE", "0")
    assert auth.code_delivery() == "unavailable"
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    assert auth.code_delivery() == "email"


def test_live_mode_without_smtp_never_shows_a_reset_code(db_file, monkeypatch):
    auth.create_user("admin@xyz.edu.example", "admin-pass-1", "admin", "Admin", path=db_file)
    monkeypatch.setenv("SCA_DEMO_MODE", "0")
    for email in ("admin@xyz.edu.example", "ghost@xyz.example"):
        assert auth.request_password_reset(email, path=db_file) == {"delivery": "unavailable", "code": None}
    with db.reading(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0


def test_live_mode_without_smtp_has_no_online_signup(db_file, monkeypatch):
    monkeypatch.setenv("SCA_DEMO_MODE", "0")
    with pytest.raises(ValueError, match="not set up"):
        auth.start_new_admission(**NEW, path=db_file)
    with pytest.raises(ValueError, match="not set up"):
        auth.start_account_claim(*ROHAN, "rohan-pass-1", path=db_file)
    assert pending_rows(db_file) == []


# ---------------------------------------------------------------- new admission


def test_new_admission_needs_the_code(db_file):
    result = auth.start_new_admission(**NEW, path=db_file, now=T0)
    assert result["delivery"] == "screen" and result["email"] == "nisha@mail.example"
    code = result["code"]
    assert len(code) == 6 and code.isdigit()
    assert account(db_file, "nisha@mail.example") is None  # nothing exists before the code comes back
    assert student_data.find_student_by_email("nisha@mail.example", db_path=db_file) is None
    row = pending_rows(db_file)[0]
    assert "nisha-pass-1" not in row["payload_json"] and code not in row["code_hash"]  # hashes only

    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(ValueError, match="wrong or has expired"):
        auth.complete_signup("nisha@mail.example", wrong, path=db_file, now=T0 + 5)
    user = auth.complete_signup(" NISHA@mail.example ", f" {code[:3]} {code[3:]} ", path=db_file, now=T0 + 10)
    assert user["role"] == "student" and user["student_id"].startswith("XYZ26CSE")
    assert auth.authenticate("nisha@mail.example", "nisha-pass-1", path=db_file)
    record = student_data.get_student(user["student_id"], db_path=db_file)
    assert record["name"] == "Nisha Kapoor" and record["branch"] == "CSE"
    assert pending_rows(db_file) == []
    with pytest.raises(ValueError):  # a code works once
        auth.complete_signup("nisha@mail.example", code, path=db_file, now=T0 + 11)


def test_new_admission_validates_before_sending(db_file):
    with pytest.raises(ValueError, match="valid branch"):
        auth.start_new_admission(**{**NEW, "branch": "XX"}, path=db_file)
    with pytest.raises(ValueError, match="claim their account"):
        auth.start_new_admission(**{**NEW, "email": ROHAN[1]}, path=db_file)
    with pytest.raises(ValueError, match="digit"):
        auth.start_new_admission(**{**NEW, "password": "no-digits-here"}, path=db_file)
    assert pending_rows(db_file) == []


def test_self_registration_can_be_switched_off(db_file, monkeypatch):
    monkeypatch.setenv("SCA_SELF_REGISTRATION", "0")
    assert not auth.self_registration_enabled()
    with pytest.raises(ValueError, match="admissions office"):
        auth.start_new_admission(**NEW, path=db_file)
    assert auth.start_account_claim(*ROHAN, "rohan-pass-1", path=db_file)["code"]  # claiming still works


def test_expired_and_used_up_codes(db_file):
    code = auth.start_new_admission(**NEW, path=db_file, now=T0)["code"]
    with pytest.raises(ValueError, match="expired"):
        auth.complete_signup("nisha@mail.example", code, path=db_file, now=T0 + auth.SIGNUP_CODE_TTL_SECONDS + 1)
    assert pending_rows(db_file) == []

    code = auth.start_new_admission(**NEW, path=db_file, now=T0 + 2000)["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(auth.SIGNUP_MAX_ATTEMPTS):
        with pytest.raises(ValueError):
            auth.complete_signup("nisha@mail.example", wrong, path=db_file, now=T0 + 2001)
    with pytest.raises(ValueError, match="Too many"):
        auth.complete_signup("nisha@mail.example", code, path=db_file, now=T0 + 2002)  # even the right code


def test_new_code_only_after_a_minute(db_file):
    first = auth.start_new_admission(**NEW, path=db_file, now=T0)
    again = auth.start_new_admission(**NEW, path=db_file, now=T0 + 30)
    assert again["throttled"] and again["code"] is None
    later = auth.start_new_admission(**NEW, path=db_file, now=T0 + 61)
    assert later["code"] and not later["throttled"]
    with pytest.raises(ValueError):  # the newer code replaced the old one
        if first["code"] != later["code"]:
            auth.complete_signup("nisha@mail.example", first["code"], path=db_file, now=T0 + 62)
        else:
            raise ValueError("same code by chance")
    assert auth.complete_signup("nisha@mail.example", later["code"], path=db_file, now=T0 + 63)


def test_email_taken_while_waiting_for_the_code(db_file):
    code = auth.start_new_admission(**NEW, path=db_file, now=T0)["code"]
    auth.register_new_student("Someone Else", "nisha@mail.example", "other-pass-2", "ECE", path=db_file)
    with pytest.raises(ValueError, match="already exists"):
        auth.complete_signup("nisha@mail.example", code, path=db_file, now=T0 + 5)


# ---------------------------------------------------------------- claim an existing student


def test_claim_sends_the_code_to_the_college_email(db_file):
    with pytest.raises(ValueError, match="do not match"):
        auth.start_account_claim(ROHAN[0], "attacker@evil.example", "rohan-pass-1", path=db_file)
    result = auth.start_account_claim(" xyz26cse005 ", ROHAN[1].upper(), "rohan-pass-1", path=db_file, now=T0)
    assert result["email"] == ROHAN[1]
    assert account(db_file, ROHAN[1]) is None
    user = auth.complete_signup(ROHAN[1], result["code"], path=db_file, now=T0 + 5)
    assert user["student_id"] == "XYZ26CSE005" and account(db_file, ROHAN[1])
    with pytest.raises(ValueError, match="already exists"):
        auth.start_account_claim(*ROHAN, "rohan-pass-2", path=db_file, now=T0 + 100)


# ---------------------------------------------------------------- with an email server


def test_with_smtp_the_code_is_only_emailed(db_file, smtp):
    result = auth.start_new_admission(**NEW, path=db_file, now=T0)
    assert result == {"delivery": "email", "code": None, "email": "nisha@mail.example", "throttled": False}
    (to, subject, body), = smtp
    assert to == "nisha@mail.example" and subject == mailer.SIGNUP_SUBJECT
    code = re.search(r"\b(\d{6})\b", body).group(1)
    assert auth.complete_signup("nisha@mail.example", code, path=db_file, now=T0 + 5)["student_id"]


def test_failed_email_lets_the_user_try_again(db_file, monkeypatch):
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a: False)
    with pytest.raises(ValueError, match="could not send"):
        auth.start_new_admission(**NEW, path=db_file, now=T0)
    assert pending_rows(db_file) == []  # no 60-second wait after a failure
