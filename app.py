"""Streamlit entry point: sign-in gate, then role-based navigation.

- Signed out: only the sign-in page exists (no sidebar).
- Student: "Ask" (chat about the college and their own record), "My dashboard", "Pay fees",
  "My requests" and "Account".
- Admin: "Overview", "Students", "Requests", "Notices", "Insights", "Ask" (college questions only)
  and "Account".

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

from ui.components import (  # noqa: E402
    get_college_name,
    get_student_safe,
    is_demo_mode,
    remaining_semesters_html,
    semester_plan_safe,
    user_card_html,
)
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


def render_semester_dropdown(record: dict) -> None:
    """Collapsible list of the current and remaining semesters with their published subjects."""
    plan = semester_plan_safe(record)
    remaining = [row for row in plan if row.get("status") == "upcoming"]
    if not plan or not any(row.get("status") != "done" for row in plan):
        return
    label = f"Remaining semesters · {len(remaining)}" if remaining else "Final semester"
    with st.expander(label, icon=":material/school:"):
        st.markdown(remaining_semesters_html(plan), unsafe_allow_html=True)


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
        if record:
            render_semester_dropdown(record)
        st.button("Log out", icon=":material/logout:", on_click=log_out, width="stretch", key="logout")
        st.markdown(
            '<div class="sca-side-foot">Fictional college · synthetic demo data</div>',
            unsafe_allow_html=True,
        )


def account_page(default: bool = False):
    return st.Page("ui/account.py", title="Account", icon=":material/manage_accounts:", url_path="account",
                   default=default)


# (page file, title, icon, url path) in menu order. "Ask" and "Account" are added by pages_for.
ADMIN_PAGES = [
    ("ui/admin_overview.py", "Overview", ":material/insights:", "overview"),
    ("ui/admin_students.py", "Students", ":material/group:", "students"),
    ("ui/admin_requests.py", "Requests", ":material/support_agent:", "requests"),
    ("ui/admin_notices.py", "Notices", ":material/campaign:", "notices"),
    ("ui/admin_insights.py", "Insights", ":material/query_stats:", "insights"),
]
STUDENT_PAGES = [
    ("ui/student_home.py", "My dashboard", ":material/dashboard:", "my-dashboard"),
    ("ui/student_payments.py", "Pay fees", ":material/payments:", "pay-fees"),
    ("ui/student_requests.py", "My requests", ":material/support_agent:", "my-requests"),
]


def _pages(specs: list[tuple[str, str, str, str]], default_title: str | None = None) -> list:
    """Pages whose file exists (a missing file never crashes the app)."""
    return [
        st.Page(file, title=title, icon=icon, url_path=url_path, default=title == default_title)
        for file, title, icon, url_path in specs
        if (ROOT / file).exists()
    ]


def pages_for(user: dict) -> list:
    # A user signed in with a temporary password lands on Account until it is changed.
    must_change = bool(user.get("must_change_password"))
    if user["role"] == "admin":
        pages = _pages(ADMIN_PAGES, default_title=None if must_change else "Overview")
        pages.append(st.Page("ui/chat.py", title="Ask", icon=":material/forum:", url_path="ask"))
        pages.append(account_page(default=must_change))
        return pages
    return [
        st.Page("ui/chat.py", title="Ask", icon=":material/forum:", url_path="ask", default=not must_change),
        *_pages(STUDENT_PAGES),
        account_page(default=must_change),
    ]


def must_change_flag(auth, token: str | None) -> bool:
    """After a refresh: does the restored account still have to change its temporary password?"""
    getter = getattr(auth, "get_session_user", None)
    if not token or not callable(getter):
        return False
    try:
        return bool((getter(token) or {}).get("must_change_password"))
    except Exception:  # noqa: BLE001
        return False


def must_change_banner(pages: list, current) -> None:
    """Warning on every page (except Account itself) until the temporary password is changed."""
    st.warning("You signed in with a temporary password. Please set your own password now.",
               icon=":material/password:")
    # Match by title: a default page reports an empty url_path.
    account = next((p for p in pages if p.title == "Account"), None)
    if account is not None and getattr(current, "title", None) != "Account":
        st.page_link(account, label="Change password in Account", icon=":material/arrow_forward:")


user = current_user()
if user is None and not st.session_state.get("logged_out"):
    # A refresh starts a fresh Streamlit session; sign back in from the session cookie.
    auth_module, _ = load_auth()
    user = restore_user(auth_module)
    if user:
        user["must_change_password"] = must_change_flag(auth_module, st.session_state.get("session_token"))
        st.session_state["user"] = user
flush_cookie_actions()
if user is None:
    st.navigation([st.Page(render_login_page, title="Sign in", icon=":material/login:", url_path="login")],
                  position="hidden").run()
else:
    render_sidebar(user)
    pages = pages_for(user)
    nav = st.navigation(pages, position="top")
    welcome = st.session_state.pop("welcome_toast", None)
    if welcome:
        st.toast(welcome, icon=":material/celebration:")
    if user.get("must_change_password"):
        must_change_banner(pages, nav)
    nav.run()
