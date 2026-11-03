"""Streamlit entry point: sign-in gate, then role-based navigation.

- Signed out: only the sign-in page exists (no sidebar).
- Student: "Ask" (chat about the college and their own record), "My dashboard" and "Account".
- Admin: "Overview", "Students", "Notices" (if present), "Ask" (college questions only) and "Account".

Identity comes only from ``st.session_state["user"]``, which is set by the sign-in form or,
after a page refresh, by a valid server-side session behind the ``sca_session`` cookie.
Pages a role may not see are never registered, so their URLs do not render.

Run with: streamlit run app.py
Demo (offline fake) mode is automatic: it is ON when no AWS credentials are found, or when SCA_DEMO_MODE=1.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
# src/ is on the path so hosts that only `pip install -r requirements.txt`
# (e.g. Streamlit Community Cloud) can import the package without installing it.
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ui.components import get_college_name, get_student_safe, is_demo_mode, user_card_html  # noqa: E402
from ui.login import load_auth, render_login_page  # noqa: E402
from ui.session import flush_cookie_actions, restore_user  # noqa: E402
from ui.theme import inject_css  # noqa: E402

st.set_page_config(
    page_title="XYZ College Assistant",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="auto",
)
inject_css()

# Invisible and automatic: pages read this plain flag.
st.session_state.demo_mode = is_demo_mode()


@st.cache_resource(show_spinner="Setting up demo accounts...")
def _bootstrap_hosted_demo() -> int:
    """Once per server: create demo accounts on a fresh host when SCA_DEMO_PASSWORD is set."""
    try:
        from smart_campus_assistant.auth import ensure_hosted_demo_users
    except Exception:  # noqa: BLE001  (login page shows its own setup message)
        return 0
    try:
        return ensure_hosted_demo_users()
    except Exception:  # noqa: BLE001
        return 0


_bootstrap_hosted_demo()


def log_out() -> None:
    """End the server session, forget this browser session and clear the cookie."""
    token = st.session_state.get("session_token")
    auth, _ = load_auth()
    if token and auth is not None and hasattr(auth, "revoke_session"):
        try:
            auth.revoke_session(token)
        except Exception:  # noqa: BLE001
            pass
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["clear_cookie"] = True
    st.session_state["logged_out"] = True  # do not restore from a cookie that is being cleared


def current_user() -> dict | None:
    user = st.session_state.get("user")
    if isinstance(user, dict) and user.get("role") in ("student", "admin") and user.get("email"):
        return user
    return None


def render_sidebar(user: dict) -> None:
    record = get_student_safe(user.get("student_id")) if user["role"] == "student" else None
    with st.sidebar:
        st.markdown(
            '<div class="sca-brand"><div class="logo">🎓</div><div>'
            f'<div class="name">{escape(get_college_name())}</div>'
            '<div class="tag">AI student helpdesk</div></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="sca-side-label">Signed in</div>', unsafe_allow_html=True)
        st.markdown(user_card_html(user, record), unsafe_allow_html=True)
        st.button("Log out", icon=":material/logout:", on_click=log_out, width="stretch", key="logout")
        st.markdown(
            '<div class="sca-side-foot">Fictional college · synthetic demo data</div>',
            unsafe_allow_html=True,
        )


def account_page():
    return st.Page("ui/account.py", title="Account", icon=":material/manage_accounts:", url_path="account")


def pages_for(user: dict) -> list:
    if user["role"] == "admin":
        pages = [
            st.Page("ui/admin_overview.py", title="Overview", icon=":material/insights:", url_path="overview", default=True),
            st.Page("ui/admin_students.py", title="Students", icon=":material/group:", url_path="students"),
        ]
        # Registered only when the page file exists, so a missing file never crashes the app.
        if (ROOT / "ui" / "admin_notices.py").exists():
            pages.append(st.Page("ui/admin_notices.py", title="Notices", icon=":material/campaign:", url_path="notices"))
        pages.append(st.Page("ui/chat.py", title="Ask", icon=":material/forum:", url_path="ask"))
        pages.append(account_page())
        return pages
    return [
        st.Page("ui/chat.py", title="Ask", icon=":material/forum:", url_path="ask", default=True),
        st.Page("ui/student_home.py", title="My dashboard", icon=":material/dashboard:", url_path="my-dashboard"),
        account_page(),
    ]


user = current_user()
if user is None and not st.session_state.get("logged_out"):
    # A refresh starts a fresh Streamlit session; sign back in from the session cookie.
    auth_module, _ = load_auth()
    user = restore_user(auth_module)
    if user:
        st.session_state["user"] = user
flush_cookie_actions()
if user is None:
    st.navigation([st.Page(render_login_page, title="Sign in", icon=":material/login:", url_path="login")],
                  position="hidden").run()
else:
    render_sidebar(user)
    st.navigation(pages_for(user), position="top").run()
