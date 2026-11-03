"""Login page flows: register, claim, forgot password, must-change banner and admin reset (auth stubbed)."""

from __future__ import annotations

import pytest

from tests.test_ui_smoke import (  # noqa: F401  (backend is a fixture)
    ACCOUNTS,
    RECORDS,
    STUDENT_PAGES,
    all_text,
    as_admin,
    as_student,
    backend,
    sign_in,
    start,
)
from ui.login import CLAIM_FAILED, RESET_DONE, RESET_SENT, WRONG_CODE

GOOD = "Fresh-pass-42"
NEW_ID = "XYZ26CSE021"
NEW_EMAIL = "test.student@xyz.edu.example"


@pytest.fixture
def flows(backend, monkeypatch):
    """Add the new auth functions to the stub and record their calls."""
    calls: dict[str, list] = {k: [] for k in ("register", "claim", "request", "confirm", "admin_reset", "set_password",
                                              "create_session")}
    auth = backend.auth
    codes: dict[str, str] = {}
    delivery = {"mode": "screen"}

    def validate_new_password(password):
        problems = []
        if len(password) < 8:
            problems.append("at least 8 characters")
        if not any(c.isdigit() for c in password):
            problems.append("at least one digit")
        return problems

    def register_new_student(name, email, password, branch, category="General", hostel=False):
        calls["register"].append((name, email, branch, category, hostel))
        user = {"email": email.lower(), "role": "student", "name": name, "student_id": NEW_ID,
                "must_change_password": False}
        ACCOUNTS[email.lower()] = (password, user)
        return dict(user)

    def claim_student_account(student_id, college_email, password):
        calls["claim"].append((student_id, college_email))
        raise ValueError("No student XYZ99CSE999 with that email")  # specific text must never reach the UI

    def request_password_reset(email):
        calls["request"].append(email)
        if email.lower() not in ACCOUNTS:
            return {"delivery": "none", "code": None}
        codes[email.lower()] = "123456"
        return {"delivery": delivery["mode"], "code": "123456" if delivery["mode"] == "screen" else None}

    def confirm_password_reset(email, code, new_password):
        calls["confirm"].append((email, code))
        if codes.get(email.lower()) != code:
            return False
        ACCOUNTS[email.lower()] = (new_password, ACCOUNTS[email.lower()][1])
        return True

    def admin_reset_password(email, by_admin_email):
        calls["admin_reset"].append((email, by_admin_email))
        if email == by_admin_email:
            raise ValueError("Use Account to change your own password.")
        user = dict(ACCOUNTS[email][1], must_change_password=True)
        ACCOUNTS[email] = ("Temp-9xq2-Kp", user)
        return "Temp-9xq2-Kp"

    def set_password(email, new_password):
        calls["set_password"].append(email)
        ACCOUNTS[email] = (new_password, dict(ACCOUNTS[email][1], must_change_password=False))

    def create_session(user):
        calls["create_session"].append(dict(user))
        return "tok-123"

    for fn in (validate_new_password, register_new_student, claim_student_account, request_password_reset,
               confirm_password_reset, admin_reset_password, set_password, create_session):
        setattr(auth, fn.__name__, fn)
    auth.PASSWORD_RULES = ("At least 8 characters", "At least one digit")
    auth.SESSION_TTL_SECONDS = 3600
    saved = dict(ACCOUNTS)
    backend.calls, backend.delivery = calls, delivery
    yield backend
    ACCOUNTS.clear()
    ACCOUNTS.update(saved)


def view(at, name):
    at.button_group(key="login_view").set_value(name).run()
    assert not at.exception
    return at


def register_new(at, password=GOOD, confirm=None):
    view(at, "Register")
    at.text_input(key="reg_name").input("Test Student")
    at.text_input(key="reg_email").input(NEW_EMAIL)
    at.selectbox(key="reg_branch").select("CSE")
    at.selectbox(key="reg_category").select("OBC-NCL")
    at.radio(key="reg_hostel").set_value("Yes")
    at.text_input(key="reg_password").input(password)
    at.text_input(key="reg_confirm").input(password if confirm is None else confirm)
    at.button(key="reg_submit").click().run()
    assert not at.exception
    return at


def no_secrets(at, *values):
    state = repr(at.session_state)
    for v in values:
        assert v not in state, v


# ---------- register ----------

def test_register_new_admission_signs_in_and_shows_new_id(flows):
    at = register_new(start())
    assert flows.calls["register"] == [("Test Student", NEW_EMAIL, "CSE", "OBC-NCL", True)]
    user = at.session_state["user"]
    assert user["student_id"] == NEW_ID and user["role"] == "student"
    assert flows.pages[-1] == STUDENT_PAGES
    assert at.session_state["session_token"] == "tok-123"
    assert any(NEW_ID in t.value for t in at.toast)
    no_secrets(at, GOOD)


def test_register_live_rule_hints(flows):
    at = view(start(), "Register")
    assert "At least 8 characters · At least one digit" in all_text(at)
    at.text_input(key="reg_password").input("short").run()
    assert "✗ It needs: at least 8 characters" in all_text(at)
    at.text_input(key="reg_password").input(GOOD).run()
    assert "meets the rules" in all_text(at)


@pytest.mark.parametrize(("password", "confirm", "message"), [
    ("weakpass", None, "too weak"),
    (GOOD, GOOD + "x", "do not match"),
])
def test_register_errors(flows, password, confirm, message):
    at = register_new(start(), password, confirm)
    assert at.error and message in at.error[0].value
    assert flows.calls["register"] == [] and "user" not in at.session_state
    no_secrets(at, password, GOOD + "x")


def test_register_is_throttled(flows):
    def taken(*args, **kwargs):
        flows.calls["register"].append(args)
        raise ValueError("This email is already registered.")

    flows.auth.register_new_student = taken
    at = start()
    for _ in range(5):
        register_new(at)
        assert "already registered" in at.error[0].value  # user-safe backend message is shown
    register_new(at)
    assert at.error and "Too many registration attempts" in at.error[0].value
    assert len(flows.calls["register"]) == 5


def test_claim_mismatch_is_generic(flows):
    at = view(start(), "Register")
    at.button_group(key="reg_mode").set_value("I already have a Student ID").run()
    at.text_input(key="claim_sid").input("xyz99cse999")
    at.text_input(key="claim_email").input("someone@xyz.edu.example")
    at.text_input(key="claim_password").input(GOOD)
    at.text_input(key="claim_confirm").input(GOOD)
    at.button(key="reg_submit").click().run()
    assert not at.exception
    assert flows.calls["claim"] == [("XYZ99CSE999", "someone@xyz.edu.example")]
    assert at.error[0].value == CLAIM_FAILED and "XYZ99CSE999" not in all_text(at)
    assert "user" not in at.session_state
    no_secrets(at, GOOD)


def test_role_toggle_only_on_sign_in(flows):
    at = start()
    assert at.button_group(key="login_role")
    for name in ("Register", "Forgot password"):
        view(at, name)
        assert not [b for b in at.button_group if b.key == "login_role"]


# ---------- forgot password ----------

def request_code(at, email):
    view(at, "Forgot password")
    at.text_input(key="fp_email_input").input(email)
    at.button(key="fp_send").click().run()
    assert not at.exception
    return at


def test_forgot_unknown_email_is_neutral(flows):
    at = request_code(start(), "nobody@xyz.edu.example")
    assert at.info and at.info[0].value == RESET_SENT
    assert 'class="sca-demo-code"' not in all_text(at)


def test_forgot_email_delivery_has_no_code_box(flows):
    flows.delivery["mode"] = "email"
    at = request_code(start(), "aarav@xyz.edu.example")
    assert at.info[0].value == RESET_SENT and 'class="sca-demo-code"' not in all_text(at)
    assert at.button(key="fp_resend").disabled  # resend only after 60 s


def test_forgot_demo_code_wrong_code_then_success(flows):
    at = request_code(start(), "aarav@xyz.edu.example")
    assert at.info[0].value == RESET_SENT
    assert 'class="sca-demo-code"' in all_text(at) and "123456" in all_text(at)
    # Wrong code: generic error.
    at.text_input(key="fp_code").input("000000")
    at.text_input(key="fp_new").input(GOOD)
    at.text_input(key="fp_confirm").input(GOOD)
    at.button(key="fp_reset").click().run()
    assert at.error and at.error[0].value == WRONG_CODE
    no_secrets(at, GOOD)
    # Right code: back to Sign in with the email filled in.
    at.text_input(key="fp_code").input("123456")
    at.text_input(key="fp_new").input(GOOD)
    at.text_input(key="fp_confirm").input(GOOD)
    at.button(key="fp_reset").click().run()
    assert not at.exception
    assert at.session_state["login_view"] == "Sign in"
    assert at.text_input(key="login_email").value == "aarav@xyz.edu.example"
    assert at.success and at.success[0].value == RESET_DONE
    no_secrets(at, GOOD, "123456")
    at.text_input(key="login_password").input(GOOD)
    at.button(key="login_submit").click().run()
    assert at.session_state["user"]["email"] == "aarav@xyz.edu.example"


# ---------- must change password ----------

def test_must_change_banner_until_changed(flows):
    email = "aarav@xyz.edu.example"
    ACCOUNTS[email] = ("Temp-9xq2-Kp", dict(ACCOUNTS[email][1], must_change_password=True))
    at = sign_in(start(), email, "Temp-9xq2-Kp")
    assert not at.exception
    assert at.session_state["user"]["must_change_password"] is True
    assert at.warning and "temporary password" in at.warning[0].value
    assert "temporary password" in all_text(at) and at.text_input(key="pw_current").label == "Temporary password"
    # Other pages show the banner with a link to Account.
    at.switch_page("ui/student_home.py").run()
    assert "temporary password" in at.warning[0].value and len(at.get("page_link")) == 1
    at.switch_page("ui/account.py").run()
    at.text_input(key="pw_current").input("Temp-9xq2-Kp")
    at.text_input(key="pw_new").input(GOOD)
    at.text_input(key="pw_confirm").input(GOOD)
    at.button(key="pw_submit").click().run()
    assert not at.exception
    assert at.success and at.session_state["user"]["must_change_password"] is False
    assert not [w for w in at.warning if "temporary password" in w.value]
    no_secrets(at, GOOD, "Temp-9xq2-Kp")


# ---------- admin reset ----------

def test_admin_reset_shows_temp_password_once(flows):
    at = as_admin()
    at.switch_page("ui/admin_students.py").run()
    at.selectbox(key="students_pick").select("XYZ26ECE009").run()
    assert "No account yet" in all_text(at) and not [b for b in at.button if b.key == "reset_ask"]
    at.selectbox(key="students_pick").select("XYZ24CSE001").run()
    assert "aarav@xyz.edu.example" in all_text(at)
    at.button(key="reset_ask").click().run()
    assert flows.calls["admin_reset"] == []  # confirm step first
    at.button(key="reset_yes").click().run()
    assert not at.exception
    assert flows.calls["admin_reset"] == [("aarav@xyz.edu.example", "admin@xyz.edu.example")]
    assert any(c.value == "Temp-9xq2-Kp" for c in at.code)
    assert "must change it at next sign-in" in all_text(at)
    at.run()  # shown once only
    assert not any(c.value == "Temp-9xq2-Kp" for c in at.code)
    no_secrets(at, "Temp-9xq2-Kp")


def test_admin_reset_cancel(flows):
    at = as_admin()
    at.switch_page("ui/admin_students.py").run()
    at.selectbox(key="students_pick").select("XYZ24CSE001").run()
    at.button(key="reset_ask").click().run()
    at.button(key="reset_cancel").click().run()
    assert flows.calls["admin_reset"] == [] and at.button(key="reset_ask")


def test_student_never_sees_reset_control(flows):
    at = as_student()
    for page in ("ui/student_home.py", "ui/account.py", "ui/chat.py"):
        at.switch_page(page).run()
        assert not [b for b in at.button if str(b.key or "").startswith("reset_")]
        assert "Reset password" not in [b.label for b in at.button]


# ---------- brand-new students ----------

def test_new_student_without_attendance_or_payments(flows, monkeypatch):
    record = {
        "student_id": NEW_ID, "name": "Test Student", "branch": "CSE", "year": 1, "semester": 1,
        "as_of": "2026-11-03", "hostel": None,
        "fees": {"total": 150000, "paid": 0, "due": 150000, "due_date": "2026-12-15", "status": "due",
                 "transactions": []},
        "library": {"books_issued": [], "fine_due": 0},
        "attendance": {"overall_pct": None, "courses": [{"code": "CS101", "name": "Programming", "pct": None}]},
        "results": {"cgpa": None, "last_sgpa": None, "backlogs": 0},
        "exams": {"upcoming": []},
    }
    monkeypatch.setitem(RECORDS, NEW_ID, record)
    at = register_new(start())
    at.switch_page("ui/student_home.py").run()
    assert not at.exception
    text = all_text(at)
    assert "Not recorded yet" in text and "No payments recorded" in text and "No books issued" in text
    admin = as_admin()
    assert not admin.exception and "1 not recorded yet" in all_text(admin)
    admin.switch_page("ui/admin_students.py").run()
    assert NEW_ID in list(admin.dataframe[0].value["Student ID"])
    admin.selectbox(key="students_pick").select(NEW_ID).run()
    assert not admin.exception and "Not recorded yet" in all_text(admin)
