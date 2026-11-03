"""Sign-in screen: Student / Admin toggle, email + password form, lockout after repeated failures.

The password is read once inside the submit callback and then wiped from session state;
it is never displayed, logged or stored. Only the public User dict lands in
``st.session_state["user"]``.
"""

from __future__ import annotations

import importlib
import math
from html import escape

import streamlit as st

from ui.components import get_college_name
from ui.theme import inject_css

ROLES = {"Student": "student", "Admin": "admin"}
SEED_COMMAND = "python -m smart_campus_assistant.auth seed-demo"
INVALID = "Invalid email or password."

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
    loader = getattr(auth, "load_users", None)
    if loader is not None:
        try:
            if not loader():
                return None, "No user accounts exist yet (data/users.json is missing or empty)."
        except Exception:
            return None, "The users file (data/users.json) could not be read."
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


def _sign_in() -> None:
    """Form submit callback: check the throttle, authenticate, then wipe the password."""
    email = str(st.session_state.get("login_email") or "").strip()
    password = str(st.session_state.get("login_password") or "")
    st.session_state["login_password"] = ""  # never keep the password in session state
    st.session_state.pop("login_error", None)
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
    # Start a server-side session so a page refresh keeps the user signed in.
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
    st.session_state["user"] = {
        "email": str(user.get("email") or key),
        "role": "admin" if user.get("role") == "admin" else "student",
        "name": str(user.get("name") or ""),
        "student_id": user.get("student_id") if user.get("role") == "student" else None,
    }
    for k in ("login_email", "login_error"):
        st.session_state.pop(k, None)


def render_setup_message(reason: str) -> None:
    st.warning(f"**Sign-in is not set up yet.** {reason}", icon=":material/build:")
    st.markdown("Create the demo accounts once, from the project folder:")
    st.code(SEED_COMMAND, language="bash")
    st.caption("Then reload this page. Demo passwords are written to data/demo_credentials.csv.")


def render_login_page() -> None:
    inject_css()
    st.html(_LOGIN_CSS)
    college = get_college_name()
    with st.container(key="login_card"):
        st.markdown(
            '<div class="sca-login-head"><div class="logo">🎓</div>'
            f"<h1>{escape(college)}</h1><p>Sign in to the AI helpdesk</p></div>",
            unsafe_allow_html=True,
        )
        auth, problem = load_auth()
        if auth is None:
            render_setup_message(problem or "")
            return

        st.segmented_control(
            "Sign in as",
            list(ROLES),
            default="Student",
            key="login_role",
            required=True,
            width="stretch",
            label_visibility="collapsed",
        )
        with st.form("login_form", border=False, enter_to_submit=True):
            st.text_input("Email", key="login_email", placeholder="name@xyz.edu.example", autocomplete="username")
            st.text_input("Password", key="login_password", type="password", autocomplete="current-password")
            st.form_submit_button("Sign in", type="primary", width="stretch", on_click=_sign_in, icon=":material/login:")

        error = st.session_state.get("login_error")
        if error:
            icon = ":material/lock_clock:" if error.startswith("Too many") else ":material/error:"
            st.error(error, icon=icon)
        st.markdown(
            '<div class="sca-login-note">Demo accounts: see <code>data/demo_credentials.csv</code></div>',
            unsafe_allow_html=True,
        )
