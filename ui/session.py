"""Keep users signed in across page refreshes with a session cookie.

The cookie holds only a random token; the server keeps its hash (see auth.create_session)
and re-checks the account on every new page load. Streamlit has no API to set cookies,
so a tiny same-origin script writes them. That means the cookie cannot be HttpOnly;
SameSite=Strict, a short lifetime and server-side revocation limit the risk.
"""

from __future__ import annotations

import json

import streamlit as st

COOKIE_NAME = "sca_session"


def read_token() -> str | None:
    """Session token sent by the browser when this page was loaded, if any."""
    try:
        token = st.context.cookies.get(COOKIE_NAME)
    except Exception:  # noqa: BLE001  (no browser context, e.g. in tests)
        return None
    return token or None


def _run_cookie_script(cookie: str) -> None:
    secure = "; Secure" if _is_https() else ""
    # json.dumps gives a safely quoted JS string. st.html runs in the page itself
    # (components.v1.html is deprecated and ran in an iframe).
    st.html(f"<script>document.cookie = {json.dumps(cookie + secure)};</script>", unsafe_allow_javascript=True)


def _is_https() -> bool:
    try:
        return str(st.context.url or "").startswith("https://")
    except Exception:  # noqa: BLE001
        return False


def write_cookie(token: str, max_age_seconds: int) -> None:
    _run_cookie_script(f"{COOKIE_NAME}={token}; Path=/; Max-Age={int(max_age_seconds)}; SameSite=Strict")


def clear_cookie() -> None:
    _run_cookie_script(f"{COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Strict")


def restore_user(auth) -> dict | None:
    """Return the session user for this browser's cookie, or None (and mark a bad cookie for clearing)."""
    token = read_token()
    if not token or auth is None:
        return None
    try:
        user = auth.get_session_user(token)
    except Exception:  # noqa: BLE001  (broken sessions file must not lock everyone out)
        user = None
    if not user:
        st.session_state["clear_cookie"] = True
        return None
    st.session_state["session_token"] = token
    return {
        "email": str(user.get("email") or ""),
        "role": "admin" if user.get("role") == "admin" else "student",
        "name": str(user.get("name") or ""),
        "student_id": user.get("student_id") if user.get("role") == "student" else None,
    }


def flush_cookie_actions() -> None:
    """Run any pending cookie write/clear queued by sign-in or log-out."""
    token = st.session_state.pop("set_cookie", None)
    if token:
        write_cookie(token, st.session_state.pop("cookie_max_age", 12 * 3600))
    if st.session_state.pop("clear_cookie", False):
        clear_cookie()
