"""Page "Account": change your own password (students and admin).

The three password inputs are read once in the submit callback and wiped right after.
``auth.set_password`` signs out every device, so this browser immediately gets a fresh
session (and cookie) to stay signed in here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import hero_html, require_role  # noqa: E402
from ui.login import _lockout_message, get_throttle, load_auth, problems_text  # noqa: E402
from ui.theme import inject_css  # noqa: E402

FIELDS = ("pw_current", "pw_new", "pw_confirm")
DEFAULT_MIN_LENGTH = 8
WRONG_CURRENT = "The current password is incorrect."
FAILED = "The password could not be changed. Please try again."


def min_length(auth) -> int:
    return int(getattr(auth, "MIN_PASSWORD_LENGTH", DEFAULT_MIN_LENGTH) or DEFAULT_MIN_LENGTH)


def rule_problems(auth, new: str, current: str) -> list[str]:
    """Rules a new password breaks: the backend validator when it exists, else the local checks."""
    validate = getattr(auth, "validate_new_password", None)
    if callable(validate):
        try:
            problems = [str(p) for p in (validate(new) or [])]
        except Exception:  # noqa: BLE001
            problems = None
        if problems is not None:
            if new and new == current:
                problems.append("It must be different from the current password.")
            return problems
    return password_problems(new, current, min_length(auth))


def rules_text(auth, min_len: int) -> str:
    rules = getattr(auth, "PASSWORD_RULES", None) if auth is not None else None
    if isinstance(rules, (list, tuple)):
        rules = " · ".join(str(r) for r in rules if r)
    if rules:
        return str(rules)
    return (f"At least {min_len} characters, with a letter and a digit. "
            "Longer passwords with mixed case and symbols are stronger.")


def password_problems(new: str, current: str, min_len: int) -> list[str]:
    """Rules a new password breaks (empty list = acceptable)."""
    problems = []
    if len(new) < min_len:
        problems.append(f"at least {min_len} characters")
    if not any(c.isalpha() for c in new):
        problems.append("at least one letter")
    if not any(c.isdigit() for c in new):
        problems.append("at least one digit")
    if new and new == current:
        problems.append("different from the current password")
    return problems


def strength_label(password: str) -> str:
    """Rough hint only: Weak / Fair / Strong."""
    score = sum((
        len(password) >= 8,
        len(password) >= 12,
        any(c.islower() for c in password) and any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ))
    return "Strong" if score >= 4 else "Fair" if score >= 3 else "Weak"


def _set_result(kind: str, text: str) -> None:
    st.session_state["pw_result"] = {"kind": kind, "text": text}


def _change_password() -> None:
    """Form submit callback: validate, verify the current password, update, refresh this session."""
    values = {k: str(st.session_state.get(k) or "") for k in FIELDS}
    for k in FIELDS:
        st.session_state[k] = ""  # never keep passwords in session state
    current, new, confirm = values["pw_current"], values["pw_new"], values["pw_confirm"]
    st.session_state.pop("pw_result", None)

    user = st.session_state.get("user") or {}
    email = str(user.get("email") or "").strip().lower()
    auth, _ = load_auth()
    if auth is None or not email:
        _set_result("error", FAILED)
        return
    if not current or not new or not confirm:
        _set_result("error", "Fill in all three fields.")
        return
    if new != confirm:
        _set_result("error", "The new passwords do not match.")
        return
    problems = rule_problems(auth, new, current)
    if problems:
        _set_result("error", f"The new password is too weak ({strength_label(new)}). {problems_text(problems)}")
        return

    throttle = get_throttle(auth)
    wait = throttle.check(email)
    if wait > 0:
        _set_result("error", _lockout_message(wait))
        return
    if not auth.authenticate(email, current, role=user.get("role")):
        throttle.record_failure(email)
        wait = throttle.check(email)
        _set_result("error", _lockout_message(wait) if wait > 0 else WRONG_CURRENT)
        return
    throttle.record_success(email)

    try:
        auth.set_password(email, new)  # also revokes every session of this user (and clears must_change_password)
    except ValueError as exc:
        _set_result("error", str(exc) or FAILED)
        return
    except Exception:  # noqa: BLE001
        _set_result("error", FAILED)
        return

    # The temporary password is gone: drop the banner and the forced Account landing.
    user = {**user, "must_change_password": False}
    st.session_state["user"] = user

    # The old session is gone: start a new one so this browser stays signed in.
    token = None
    try:
        token = auth.create_session(user)
    except Exception:  # noqa: BLE001
        token = None
    if token:
        st.session_state["session_token"] = token
        st.session_state["set_cookie"] = token
        st.session_state["cookie_max_age"] = getattr(auth, "SESSION_TTL_SECONDS", 12 * 3600)
        _set_result("success", "Password changed. You stay signed in here; all other devices were signed out.")
    else:
        st.session_state.pop("session_token", None)
        _set_result("success", "Password changed. Other devices were signed out; after a refresh you may need to sign in again.")


# ---------- page ----------

def render_page() -> None:
    inject_css(max_width="40rem")
    user = require_role("student", "admin")
    auth_module, _ = load_auth()
    min_len = min_length(auth_module) if auth_module is not None else DEFAULT_MIN_LENGTH

    must_change = bool(user.get("must_change_password"))

    st.markdown(
        hero_html("Account", str(user.get("name") or user.get("email") or ""),
                  f"Signed in as {user.get('email') or ''}. Change your password below."),
        unsafe_allow_html=True,
    )
    if must_change:
        st.info(
            "**Set your own password.** You signed in with a temporary password from the admin office. "
            "Enter that temporary password as the current password, then choose a new one.",
            icon=":material/password:",
        )

    with st.form("password_form", border=True, enter_to_submit=True):
        st.markdown("**Set a new password**" if must_change else "**Change password**")
        st.text_input("Temporary password" if must_change else "Current password", key="pw_current",
                      type="password", autocomplete="current-password")
        st.text_input("New password", key="pw_new", type="password", autocomplete="new-password")
        st.text_input("Confirm new password", key="pw_confirm", type="password", autocomplete="new-password")
        st.caption(rules_text(auth_module, min_len))
        st.form_submit_button("Change password", type="primary", on_click=_change_password, icon=":material/key:",
                              key="pw_submit")

    result = st.session_state.pop("pw_result", None)
    if result:
        if result["kind"] == "success":
            st.success(result["text"], icon=":material/check_circle:")
        else:
            icon = ":material/lock_clock:" if result["text"].startswith("Too many") else ":material/error:"
            st.error(result["text"], icon=icon)


# Streamlit runs page scripts as __main__; importing this module (tests) renders nothing.
if __name__ == "__main__":
    render_page()
