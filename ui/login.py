"""Sign-in screen with three views: Sign in, Register and Forgot password.

- Sign in: Student / Admin toggle, email + password, lockout after repeated failures.
- Register (students only): a new admission gets a new Student ID; an existing student
  claims an account with their Student ID + college email. Success signs the user in.
- Forgot password: request a 6-digit code by email, then set a new password with it.

Passwords are read once inside submit callbacks and then wiped from session state;
they are never displayed, logged or stored. Only the public User dict lands in
``st.session_state["user"]``. Messages never reveal whether an account exists.
"""

from __future__ import annotations

import importlib
import math
import time
from html import escape

import streamlit as st

from ui.components import get_branches, get_college_name
from ui.theme import inject_css

ROLES = {"Student": "student", "Admin": "admin"}
VIEWS = ("Sign in", "Register", "Forgot password")
REG_MODES = ("New admission", "I already have a Student ID")
DEFAULT_CATEGORIES = ("General", "OBC-NCL", "EWS", "SC", "ST")
DEFAULT_RULES = "At least 8 characters, with a letter and a digit."
SEED_COMMAND = "python -m smart_campus_assistant.auth seed-demo"
INVALID = "Invalid email or password."
CLAIM_FAILED = "We could not match that Student ID and college email, or the account already exists."
REGISTER_FAILED = "Registration failed. Please check your details and try again."
RESET_SENT = "If an account exists for that email, we sent a 6-digit code."
WRONG_CODE = "That code is wrong or has expired. Check it or request a new one."
RESET_DONE = "Password changed. Sign in with your new password."
RESEND_AFTER = 60  # seconds before another code can be requested
REG_LIMIT = 5  # registration attempts per browser session ...
REG_WINDOW = 600  # ... within this many seconds

# Every key that can hold a password; wiped after each submit and on every view switch.
PASSWORD_KEYS = ("login_password", "reg_password", "reg_confirm", "claim_password", "claim_confirm",
                 "fp_new", "fp_confirm")

# Hide the sidebar (and its toggle) while nobody is signed in.
_LOGIN_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] { display: none !important; }
</style>
"""


def load_auth():
    """Return ``(auth_module, None)`` when login can work, else ``(None, reason)``."""
    try:
        auth = importlib.import_module("smart_campus_assistant.auth")
    except Exception:  # ImportError or an error inside the module
        return None, "The login module (smart_campus_assistant.auth) could not be loaded."
    # count_users is one COUNT query; load_users (older backends, test stubs) reads every account.
    loader = getattr(auth, "count_users", None) or getattr(auth, "load_users", None)
    if loader is not None:
        try:
            if not loader():
                return None, "No user accounts exist yet (the accounts database is empty)."
        except Exception:
            return None, "The accounts database could not be read."
    return auth, None


@st.cache_resource(show_spinner=False)
def _throttle(cls_key: str, _cls):
    """One LoginThrottle per server process, shared by every session (keyed by lower-cased email)."""
    return _cls()


def get_throttle(auth):
    cls = auth.LoginThrottle
    return _throttle(f"{cls.__module__}.{cls.__qualname__}:{id(cls)}", cls)


def _lockout_message(seconds: float) -> str:
    secs = max(1, math.ceil(seconds))
    return f"Too many failed attempts. Try again in {secs} second{'s' if secs != 1 else ''}."


# ---------- shared helpers ----------

def password_rules(auth) -> str:
    """PASSWORD_RULES as one line (the backend may give a string or a list of rules)."""
    rules = getattr(auth, "PASSWORD_RULES", None) if auth is not None else None
    if isinstance(rules, (list, tuple)):
        rules = " · ".join(str(r) for r in rules if r)
    return str(rules or DEFAULT_RULES)


def problems_text(problems: list[str]) -> str:
    """Backend problems are full sentences; local ones are short phrases."""
    if all(p.rstrip().endswith(".") for p in problems):
        return " ".join(p.strip() for p in problems)
    return "It needs: " + "; ".join(problems) + "."


def password_problems(auth, password: str) -> list[str]:
    """Rules a new password breaks, from the backend when it has a validator."""
    validate = getattr(auth, "validate_new_password", None)
    if callable(validate):
        try:
            return [str(p) for p in (validate(password) or [])]
        except Exception:  # noqa: BLE001
            pass
    min_len = int(getattr(auth, "MIN_PASSWORD_LENGTH", 8) or 8)
    problems = []
    if len(password) < min_len:
        problems.append(f"at least {min_len} characters")
    if not any(c.isalpha() for c in password):
        problems.append("at least one letter")
    if not any(c.isdigit() for c in password):
        problems.append("at least one digit")
    return problems


def wipe_passwords() -> None:
    for key in PASSWORD_KEYS:
        if key in st.session_state:
            st.session_state[key] = ""


def session_user(user, fallback_email: str = "") -> dict:
    """The public fields of a User that the app keeps in session state."""
    is_student = user.get("role") != "admin"
    return {
        "email": str(user.get("email") or fallback_email),
        "role": "student" if is_student else "admin",
        "name": str(user.get("name") or ""),
        "student_id": user.get("student_id") if is_student else None,
        "must_change_password": bool(user.get("must_change_password")),
    }


def start_session(auth, user, fallback_email: str = "") -> None:
    """Sign the user in here: server session + cookie (when available) and the session user."""
    create_session = getattr(auth, "create_session", None)
    if callable(create_session):
        try:
            token = create_session(user)
        except Exception:  # noqa: BLE001  (sign-in still works, just not across refreshes)
            token = None
        if token:
            st.session_state["session_token"] = token
            st.session_state["set_cookie"] = token
            st.session_state["cookie_max_age"] = getattr(auth, "SESSION_TTL_SECONDS", 12 * 3600)
    st.session_state["user"] = session_user(user, fallback_email)
    st.session_state.pop("logged_out", None)


def _on_view_change() -> None:
    wipe_passwords()
    for key in ("login_error", "reg_error", "fp_error"):
        st.session_state.pop(key, None)


# ---------- sign in ----------

def _sign_in() -> None:
    """Form submit callback: check the throttle, authenticate, then wipe the password."""
    email = str(st.session_state.get("login_email") or "").strip()
    password = str(st.session_state.get("login_password") or "")
    st.session_state["login_password"] = ""  # never keep the password in session state
    st.session_state.pop("login_error", None)
    st.session_state.pop("login_notice", None)
    role = ROLES.get(st.session_state.get("login_role") or "Student", "student")

    auth, problem = load_auth()
    if auth is None:
        st.session_state["login_error"] = problem
        return
    if not email or not password:
        st.session_state["login_error"] = "Enter your email and password."
        return

    key = email.lower()
    throttle = get_throttle(auth)
    wait = throttle.check(key)
    if wait > 0:
        st.session_state["login_error"] = _lockout_message(wait)
        return

    user = auth.authenticate(email, password, role=role)
    password = ""
    if not user:
        throttle.record_failure(key)
        wait = throttle.check(key)
        st.session_state["login_error"] = _lockout_message(wait) if wait > 0 else INVALID
        return

    throttle.record_success(key)
    start_session(auth, user, key)
    for k in ("login_email", "login_error"):
        st.session_state.pop(k, None)


def _render_sign_in() -> None:
    st.segmented_control(
        "Sign in as",
        list(ROLES),
        default="Student",
        key="login_role",
        required=True,
        width="stretch",
        label_visibility="collapsed",
    )
    notice = st.session_state.pop("login_notice", None)
    if notice:
        st.success(notice, icon=":material/check_circle:")
    with st.form("login_form", border=False, enter_to_submit=True):
        st.text_input("Email", key="login_email", placeholder="name@xyz.edu.example", autocomplete="username")
        st.text_input("Password", key="login_password", type="password", autocomplete="current-password")
        st.form_submit_button("Sign in", type="primary", width="stretch", on_click=_sign_in, icon=":material/login:",
                              key="login_submit")

    error = st.session_state.get("login_error")
    if error:
        icon = ":material/lock_clock:" if error.startswith("Too many") else ":material/error:"
        st.error(error, icon=icon)
    st.markdown(
        '<div class="sca-login-note">Demo accounts: see <code>data/demo_credentials.csv</code></div>',
        unsafe_allow_html=True,
    )


# ---------- register ----------

def _registration_wait() -> float:
    """Seconds until this browser session may try to register again (0 = allowed now)."""
    now = time.time()
    recent = [t for t in st.session_state.get("reg_attempts", []) if now - t < REG_WINDOW]
    st.session_state["reg_attempts"] = recent
    if len(recent) < REG_LIMIT:
        return 0.0
    return REG_WINDOW - (now - recent[0])


def _register() -> None:
    """Submit callback for both registration modes."""
    mode = st.session_state.get("reg_mode") or REG_MODES[0]
    new_admission = mode == REG_MODES[0]
    pw_key, confirm_key = ("reg_password", "reg_confirm") if new_admission else ("claim_password", "claim_confirm")
    password = str(st.session_state.get(pw_key) or "")
    confirm = str(st.session_state.get(confirm_key) or "")
    wipe_passwords()
    st.session_state.pop("reg_error", None)

    def fail(text: str) -> None:
        st.session_state["reg_error"] = text

    auth, _ = load_auth()
    if auth is None:
        try:
            auth = importlib.import_module("smart_campus_assistant.auth")
        except Exception:  # noqa: BLE001
            return fail(REGISTER_FAILED)

    if new_admission:
        name = str(st.session_state.get("reg_name") or "").strip()
        email = str(st.session_state.get("reg_email") or "").strip()
        if not name or not email or not password or not confirm:
            return fail("Fill in your name, email and both password fields.")
    else:
        student_id = str(st.session_state.get("claim_sid") or "").strip().upper()
        email = str(st.session_state.get("claim_email") or "").strip()
        if not student_id or not email or not password or not confirm:
            return fail("Fill in your Student ID, college email and both password fields.")
    if "@" not in email:
        return fail("Enter a valid email address.")
    if password != confirm:
        return fail("The passwords do not match.")
    problems = password_problems(auth, password)
    if problems:
        return fail("The password is too weak. " + problems_text(problems))

    wait = _registration_wait()
    if wait > 0:
        return fail(f"Too many registration attempts. Try again in {math.ceil(wait / 60)} min.")
    st.session_state["reg_attempts"].append(time.time())

    try:
        if new_admission:
            user = auth.register_new_student(
                name, email, password,
                str(st.session_state.get("reg_branch") or "CSE"),
                category=str(st.session_state.get("reg_category") or "General"),
                hostel=st.session_state.get("reg_hostel") == "Yes",
            )
        else:
            user = auth.claim_student_account(student_id, email, password)
    except ValueError as exc:
        return fail(str(exc) if new_admission else CLAIM_FAILED)
    except Exception:  # noqa: BLE001
        return fail(REGISTER_FAILED if new_admission else CLAIM_FAILED)
    finally:
        password = confirm = ""

    start_session(auth, user, email.lower())
    sid = user.get("student_id") or ""
    if new_admission:
        st.session_state["welcome_toast"] = f"Welcome! Your new Student ID is {sid}."
    else:
        st.session_state["welcome_toast"] = f"Account created for {sid}. Welcome!"
    for k in ("reg_name", "reg_email", "claim_sid", "claim_email", "reg_error"):
        st.session_state.pop(k, None)


def _render_register(auth) -> None:
    st.markdown('<div class="sca-login-sub">Student registration</div>', unsafe_allow_html=True)
    mode = st.segmented_control("Registration type", REG_MODES, default=REG_MODES[0], key="reg_mode",
                                required=True, width="stretch", label_visibility="collapsed",
                                on_change=_on_view_change)
    # A form sends every field together on submit. Outside a form a password field is only saved
    # when it loses focus to another widget, and pressing Tab moves focus to its own "show password"
    # icon instead, so keyboard users lost what they had typed.
    with st.form("reg_form", border=False, enter_to_submit=True):
        if mode == REG_MODES[0]:
            branches = get_branches()
            categories = list(getattr(auth, "STUDENT_CATEGORIES", None) or DEFAULT_CATEGORIES)
            st.text_input("Full name", key="reg_name", autocomplete="name")
            st.text_input("Email", key="reg_email", placeholder="you@example.com", autocomplete="email")
            c1, c2 = st.columns(2)
            c1.selectbox("Branch", list(branches), key="reg_branch", format_func=lambda b: f"{b} · {branches.get(b, b)}")
            c2.selectbox("Category", categories, key="reg_category")
            st.radio("Hostel", ["No", "Yes"], key="reg_hostel", horizontal=True, help="Do you want a hostel room?")
            st.text_input("Password", key="reg_password", type="password", autocomplete="new-password")
            st.caption(password_rules(auth))
            st.text_input("Confirm password", key="reg_confirm", type="password", autocomplete="new-password")
            label = "Create account"
        else:
            st.text_input("Student ID", key="claim_sid", placeholder="XYZ24CSE001")
            st.text_input("College email", key="claim_email", placeholder="name@xyz.edu.example", autocomplete="email")
            st.text_input("Password", key="claim_password", type="password", autocomplete="new-password")
            st.caption(password_rules(auth))
            st.text_input("Confirm password", key="claim_confirm", type="password", autocomplete="new-password")
            label = "Activate account"
        st.form_submit_button(label, type="primary", width="stretch", on_click=_register,
                              icon=":material/person_add:", key="reg_submit")
    error = st.session_state.get("reg_error")
    if error:
        st.error(error, icon=":material/error:")
    st.markdown('<div class="sca-login-note">Admin accounts are created by the college office.</div>',
                unsafe_allow_html=True)


# ---------- forgot password ----------

def _request_code() -> None:
    email = str(st.session_state.get("fp_email_input") or st.session_state.get("fp_email") or "").strip()
    st.session_state.pop("fp_error", None)
    if "@" not in email:
        st.session_state["fp_error"] = "Enter the email of your account."
        return
    sent_at = st.session_state.get("fp_sent_at")
    if sent_at and time.time() - sent_at < RESEND_AFTER and st.session_state.get("fp_email") == email:
        return  # too soon; the button is disabled anyway
    auth, _ = load_auth()
    result = {}
    if auth is not None and hasattr(auth, "request_password_reset"):
        try:
            result = auth.request_password_reset(email) or {}
        except Exception:  # noqa: BLE001  (same neutral message either way)
            result = {}
    code = result.get("code") if result.get("delivery") == "screen" else None
    st.session_state["fp_email"] = email
    st.session_state["fp_sent_at"] = time.time()
    st.session_state["fp_demo_code"] = str(code) if code else None
    st.session_state["fp_step"] = 2


def _confirm_reset() -> None:
    code = str(st.session_state.get("fp_code") or "").strip()
    new = str(st.session_state.get("fp_new") or "")
    confirm = str(st.session_state.get("fp_confirm") or "")
    wipe_passwords()
    st.session_state.pop("fp_error", None)
    email = str(st.session_state.get("fp_email") or "")

    def fail(text: str) -> None:
        st.session_state["fp_error"] = text

    if not code or not new or not confirm:
        return fail("Enter the code and your new password twice.")
    if new != confirm:
        return fail("The new passwords do not match.")
    auth, _ = load_auth()
    if auth is None or not hasattr(auth, "confirm_password_reset"):
        return fail(WRONG_CODE)
    problems = password_problems(auth, new)
    if problems:
        return fail("The new password is too weak. " + problems_text(problems))
    try:
        ok = auth.confirm_password_reset(email, code, new)
    except ValueError as exc:
        return fail(str(exc) or "The new password is too weak.")
    except Exception:  # noqa: BLE001
        ok = False
    finally:
        new = confirm = ""
    if not ok:
        st.session_state["fp_code"] = ""
        return fail(WRONG_CODE)

    # Back to Sign in with the email filled in.
    for k in ("fp_step", "fp_email", "fp_sent_at", "fp_demo_code", "fp_code", "fp_email_input"):
        st.session_state.pop(k, None)
    st.session_state["login_view"] = VIEWS[0]
    st.session_state["login_email"] = email
    st.session_state["login_notice"] = RESET_DONE


def _restart_reset() -> None:
    wipe_passwords()
    for k in ("fp_step", "fp_sent_at", "fp_demo_code", "fp_code", "fp_error"):
        st.session_state.pop(k, None)
    st.session_state["fp_email_input"] = st.session_state.get("fp_email") or ""


def _render_forgot() -> None:
    st.markdown('<div class="sca-login-sub">Reset your password</div>', unsafe_allow_html=True)
    if st.session_state.get("fp_step") != 2:
        with st.form("fp_request_form", border=False, enter_to_submit=True):
            st.text_input("Account email", key="fp_email_input", placeholder="name@xyz.edu.example", autocomplete="email")
            st.form_submit_button("Send code", type="primary", width="stretch", on_click=_request_code,
                                  icon=":material/mail:", key="fp_send")
    else:
        st.info(RESET_SENT, icon=":material/mark_email_read:")
        code = st.session_state.get("fp_demo_code")
        if code:
            st.markdown(
                '<div class="sca-demo-code"><div class="lbl">Demo mode · no email server configured</div>'
                f'<div class="code">{escape(code)}</div>'
                '<div class="hint">In a real deployment this code arrives by email.</div></div>',
                unsafe_allow_html=True,
            )
        with st.form("fp_confirm_form", border=False, enter_to_submit=True):
            st.text_input("6-digit code", key="fp_code", max_chars=6, autocomplete="one-time-code")
            st.text_input("New password", key="fp_new", type="password", autocomplete="new-password")
            st.text_input("Confirm new password", key="fp_confirm", type="password", autocomplete="new-password")
            st.caption(password_rules(load_auth()[0]))
            st.form_submit_button("Set new password", type="primary", width="stretch", on_click=_confirm_reset,
                                  icon=":material/key:", key="fp_reset")
        left = RESEND_AFTER - (time.time() - float(st.session_state.get("fp_sent_at") or 0))
        c1, c2 = st.columns(2)
        c1.button("Resend code" if left <= 0 else f"Resend in {math.ceil(left)} s", disabled=left > 0,
                  on_click=_request_code, key="fp_resend", width="stretch")
        c2.button("Use another email", on_click=_restart_reset, key="fp_restart", width="stretch")
    error = st.session_state.get("fp_error")
    if error:
        st.error(error, icon=":material/error:")


# ---------- page ----------

def render_setup_message(reason: str) -> None:
    st.warning(f"**Sign-in is not set up yet.** {reason}", icon=":material/build:")
    st.markdown("Create the demo accounts once, from the project folder:")
    st.code(SEED_COMMAND, language="bash")
    st.caption("Then reload this page. Demo passwords are written to data/demo_credentials.csv.")


def render_login_page() -> None:
    inject_css()
    st.html(_LOGIN_CSS)
    college = get_college_name()
    view = st.session_state.get("login_view") or VIEWS[0]
    subtitle = {"Register": "Create your student account", "Forgot password": "Get back into your account"}
    with st.container(key="login_card"):
        st.markdown(
            '<div class="sca-login-head"><div class="logo">🎓</div>'
            f"<h1>{escape(college)}</h1><p>{escape(subtitle.get(view, 'Sign in to the AI helpdesk'))}</p></div>",
            unsafe_allow_html=True,
        )
        auth, problem = load_auth()
        if auth is None:
            render_setup_message(problem or "")
            return

        if st.session_state.get("login_view") not in VIEWS:
            st.session_state["login_view"] = VIEWS[0]
        with st.container(key="login_views"):
            view = st.segmented_control("View", VIEWS, key="login_view", required=True,
                                        width="stretch", label_visibility="collapsed", on_change=_on_view_change)
        if view == "Register":
            _render_register(auth)
        elif view == "Forgot password":
            _render_forgot()
        else:
            _render_sign_in()
