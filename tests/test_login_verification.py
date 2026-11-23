"""Sign-in page with email verification: the code step, live mode without email, self-registration switch."""

from __future__ import annotations

import pytest

from tests.test_login_flows import GOOD, NEW_EMAIL, NEW_ID, flows, no_secrets, register_new, view  # noqa: F401
from tests.test_ui_smoke import ACCOUNTS, all_text, backend, start  # noqa: F401  (backend is a fixture)
from ui.login import CLAIM_FAILED, RESET_UNAVAILABLE

CODE = "654321"
UNAVAILABLE = "Online sign-up needs email verification, which is not set up on this server."


@pytest.fixture
def verified(flows):
    """The stubbed auth module gets the two-step sign-up functions."""
    auth = flows.auth
    state = {"mode": "screen", "self_registration": True, "started": [], "completed": []}

    def check_mode():
        if state["mode"] == "unavailable":
            raise ValueError(UNAVAILABLE)

    def start_new_admission(name, email, password, branch, category="General", hostel=False):
        check_mode()
        state["started"].append(("new", email.lower()))
        state["pending"] = {"email": email.lower(), "name": name, "password": password, "sid": NEW_ID}
        return {"delivery": state["mode"], "code": CODE if state["mode"] == "screen" else None,
                "email": email.lower(), "throttled": False}

    def start_account_claim(student_id, college_email, password):
        check_mode()
        if student_id != "XYZ26CSE005":
            raise ValueError("No student XYZ99CSE999 with that email")  # must never reach the page
        state["started"].append(("claim", college_email.lower()))
        state["pending"] = {"email": college_email.lower(), "name": "Rohan Gupta", "password": password,
                            "sid": student_id}
        return {"delivery": state["mode"], "code": CODE if state["mode"] == "screen" else None,
                "email": college_email.lower(), "throttled": False}

    def complete_signup(email, code):
        if code != CODE or state.get("pending", {}).get("email") != email:
            raise ValueError("That code is wrong or has expired. Check it or start again to get a new one.")
        p = state.pop("pending")
        user = {"email": email, "role": "student", "name": p["name"], "student_id": p["sid"],
                "must_change_password": False}
        ACCOUNTS[email] = (p["password"], user)
        state["completed"].append(email)
        return dict(user)

    auth.start_new_admission = start_new_admission
    auth.start_account_claim = start_account_claim
    auth.complete_signup = complete_signup
    auth.self_registration_enabled = lambda: state["self_registration"]
    auth.SIGNUP_UNAVAILABLE = UNAVAILABLE
    flows.signup = state
    return flows


def test_new_admission_signs_in_only_after_the_code(verified):
    at = register_new(start())
    assert verified.signup["started"] == [("new", NEW_EMAIL)]
    assert verified.calls["register"] == []  # the old one-step function is not used
    assert "user" not in at.session_state
    text = all_text(at)
    assert f"We sent a 6-digit code to {NEW_EMAIL}" in text and CODE in text  # demo mode shows the code
    no_secrets(at, GOOD)

    at.text_input(key="reg_code").input("000000")
    at.button(key="reg_verify").click().run()
    assert "wrong or has expired" in all_text(at) and "user" not in at.session_state

    at.text_input(key="reg_code").input(CODE)
    at.button(key="reg_verify").click().run()
    assert not at.exception
    assert at.session_state["user"]["student_id"] == NEW_ID
    assert verified.signup["completed"] == [NEW_EMAIL]


def test_email_delivery_shows_no_code(verified):
    verified.signup["mode"] = "email"
    at = register_new(start())
    text = all_text(at)
    assert f"We sent a 6-digit code to {NEW_EMAIL}" in text and CODE not in text


def test_start_again_returns_to_the_form(verified):
    at = register_new(start())
    at.button(key="reg_restart").click().run()
    assert not at.exception
    assert at.button(key="reg_submit") and "reg_pending" not in at.session_state


def test_claim_mismatch_stays_generic(verified):
    at = view(start(), "Register")
    at.button_group(key="reg_mode").set_value("I already have a Student ID").run()
    at.text_input(key="claim_sid").input("XYZ99CSE999")
    at.text_input(key="claim_email").input("someone@xyz.edu.example")
    at.text_input(key="claim_password").input(GOOD)
    at.text_input(key="claim_confirm").input(GOOD)
    at.button(key="reg_submit").click().run()
    text = all_text(at)
    assert CLAIM_FAILED in text and "XYZ99CSE999 with that email" not in text


def test_live_mode_without_email_explains_why(verified):
    verified.signup["mode"] = "unavailable"
    at = register_new(start())
    assert UNAVAILABLE in all_text(at) and "reg_pending" not in at.session_state
    # The claim form shows the same message: it says nothing about whether the student exists.
    at.button_group(key="reg_mode").set_value("I already have a Student ID").run()
    at.text_input(key="claim_sid").input("XYZ26CSE005")
    at.text_input(key="claim_email").input("rohan.gupta005@xyz.edu.example")
    at.text_input(key="claim_password").input(GOOD)
    at.text_input(key="claim_confirm").input(GOOD)
    at.button(key="reg_submit").click().run()
    assert UNAVAILABLE in all_text(at)


def test_self_registration_off_hides_new_admission(verified):
    verified.signup["self_registration"] = False
    at = view(start(), "Register")
    assert at.button_group(key="reg_mode").options == ["I already have a Student ID"]
    assert at.text_input(key="claim_sid") and "registered by the admissions office" in all_text(at)


def test_forgot_password_without_email_in_live_mode(verified):
    verified.delivery["mode"] = "unavailable"
    at = view(start(), "Forgot password")
    at.text_input(key="fp_email_input").input("aarav@xyz.edu.example")
    at.button(key="fp_send").click().run()
    assert not at.exception
    assert RESET_UNAVAILABLE in all_text(at)
    assert "fp_step" not in at.session_state  # no code step: no code was issued
