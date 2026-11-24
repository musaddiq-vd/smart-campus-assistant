"""Page "My requests" (students only): raise a request to the admin office and follow its thread."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    alert,
    chip_html,
    confirm_prompt,
    empty_state,
    flash,
    hero,
    require_role,
    section_header,
    show_flashes,
)
from ui.theme import CONTENT_WIDTH, inject_css  # noqa: E402

inject_css(max_width=CONTENT_WIDTH)
user = require_role("student")

try:
    from smart_campus_assistant import support_requests as requests_mod
except Exception as exc:  # noqa: BLE001
    alert("error", f"The requests module could not be loaded: {type(exc).__name__}.")
    st.stop()

# Only ever the signed-in student's own ID, taken from the authenticated session.
STUDENT_ID = str(user.get("student_id") or "").strip().upper()
CATEGORIES = requests_mod.CATEGORIES
FORM_KEYS = {"req_new_category": "fees", "req_new_subject": "", "req_new_body": ""}
FLASH_KEY = "req_flash"

for key, default in FORM_KEYS.items():
    st.session_state.setdefault(key, default)


def _flash(kind: str, text: str) -> None:
    flash(FLASH_KEY, kind, text)


def _reply_key(request_id: str) -> str:
    return f"req_reply_{request_id}"


# ---------- callbacks ----------

def submit_request() -> None:
    """Form callback: validate + save, then clear the form."""
    try:
        request = requests_mod.create_request(
            STUDENT_ID,
            user.get("email"),
            user.get("name"),
            st.session_state.get("req_new_category"),
            st.session_state.get("req_new_subject"),
            st.session_state.get("req_new_body"),
        )
    except requests_mod.RequestError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not save your request: {type(exc).__name__}.")
        return
    _flash("success", f"Request {request['id']} sent to the admin office.")
    for key, default in FORM_KEYS.items():
        st.session_state[key] = default


def open_request(request_id: str) -> None:
    st.session_state["req_open_id"] = request_id
    st.session_state.pop("req_confirm_close", None)


def back_to_list() -> None:
    st.session_state.pop("req_open_id", None)
    st.session_state.pop("req_confirm_close", None)


def send_reply(request_id: str) -> None:
    """Form callback: add the student's reply to their own request."""
    try:
        requests_mod.add_message(
            request_id, "student", user.get("name"), st.session_state.get(_reply_key(request_id)),
            student_id=STUDENT_ID,
        )
    except requests_mod.RequestError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not send your reply: {type(exc).__name__}.")
        return
    st.session_state[_reply_key(request_id)] = ""
    _flash("success", "Reply sent.")


def ask_close(request_id: str) -> None:
    st.session_state["req_confirm_close"] = request_id


def cancel_close() -> None:
    st.session_state.pop("req_confirm_close", None)


def confirm_close(request_id: str) -> None:
    st.session_state.pop("req_confirm_close", None)
    try:
        requests_mod.close_own_request(request_id, STUDENT_ID)
    except requests_mod.RequestError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not close {request_id}: {type(exc).__name__}.")
        return
    _flash("success", f"Request {request_id} closed.")


# ---------- page ----------

def render_detail(request: dict) -> None:
    """One of the student's own requests: thread, reply form and the close action."""
    rid = request["id"]
    st.button("Back to my requests", key="req_back", icon=":material/arrow_back:", on_click=back_to_list)
    st.markdown(requests_mod.request_card_html(request), unsafe_allow_html=True)
    section_header("Conversation", "Your request and the replies from the admin office, oldest first")
    st.markdown(requests_mod.thread_html(request, viewer_role="student"), unsafe_allow_html=True)

    if request["status"] == "closed":
        st.info("This request is closed. Raise a new request if you still need help.", icon=":material/lock:")
        return

    if request["status"] == "resolved":
        st.caption("The admin office marked this as resolved. Replying will reopen it.")
    with st.form(f"req_reply_form_{rid}", border=False):
        st.text_area("Your reply", key=_reply_key(rid), height=110, max_chars=requests_mod.MESSAGE_MAX,
                     placeholder="Add more details or answer the admin office.")
        st.form_submit_button("Send reply", type="primary", icon=":material/send:", on_click=send_reply, args=(rid,))

    pending = st.session_state.get("req_confirm_close") == rid
    st.button("Close request", key=f"req_close_{rid}", icon=":material/lock:", on_click=ask_close,
              args=(rid,), disabled=pending)
    if pending:
        confirm_prompt(
            f"Close {rid}? You will not be able to reply on it again.",
            yes_label="Yes, close", yes_key=f"req_close_yes_{rid}", on_yes=confirm_close, yes_args=(rid,),
            no_key=f"req_close_no_{rid}", on_no=cancel_close,
        )


def render_new_form(active: int) -> None:
    limit = requests_mod.MAX_ACTIVE_PER_STUDENT
    section_header(
        "Raise a request",
        f"Subject {requests_mod.SUBJECT_MIN}-{requests_mod.SUBJECT_MAX} characters, details "
        f"{requests_mod.BODY_MIN}-{requests_mod.BODY_MAX} characters. HTML is removed.",
    )
    if active >= limit:
        st.info(f"You have {active} requests that are not closed (the limit is {limit}). "
                "Close one to raise a new request.", icon=":material/info:")
        return
    with st.form("req_new_form"):
        st.selectbox("Category", list(CATEGORIES), key="req_new_category", format_func=lambda c: CATEGORIES[c])
        st.text_input("Subject", key="req_new_subject", max_chars=requests_mod.SUBJECT_MAX,
                      placeholder="e.g. Fee receipt not generated")
        st.text_area("Details", key="req_new_body", height=170, max_chars=requests_mod.BODY_MAX,
                     placeholder="What happened, when, and what you need from the admin office.")
        st.form_submit_button("Send request", type="primary", icon=":material/send:", on_click=submit_request)


def render_list(requests: list[dict]) -> None:
    section_header("My requests", f"{len(requests)} request{'s' if len(requests) != 1 else ''}, newest first")
    if not requests:
        empty_state("📮", "No requests yet", "Raise a request and the admin office will reply here.")
    # The list sits in a half-width column, so the button goes under its card (beside it, it got squeezed).
    for request in requests:
        with st.container(gap="xsmall"):  # card and button stay visibly together
            st.markdown(requests_mod.request_card_html(request), unsafe_allow_html=True)
            st.button("Open conversation", key=f"req_open_{request['id']}", icon=":material/chat:",
                      on_click=open_request, args=(request["id"],))


try:
    my_requests = requests_mod.list_requests(student_id=STUDENT_ID) if STUDENT_ID else []
except Exception as exc:  # noqa: BLE001
    alert("error", f"Your requests could not be loaded: {type(exc).__name__}.")
    st.stop()

active_count = sum(1 for r in my_requests if r["status"] != "closed")
hero(
    "Student · help desk", "My requests",
    "Raise a request to the admin office (fees, hostel, library, exams, certificates) and follow the replies here.",
    chips=[chip_html(f"{active_count} active", "info" if active_count else ""),
           chip_html(f"{len(my_requests)} total")],
)
st.markdown(requests_mod.REQUESTS_CSS, unsafe_allow_html=True)

# The outcome of the last action: one inline message (no toast on top of it).
show_flashes(FLASH_KEY)

if not STUDENT_ID:
    empty_state("🗂️", "Your record is not available",
                "Your account has no student ID, so requests cannot be raised. Please contact the admin office.")
    st.stop()

open_id = st.session_state.get("req_open_id")
opened = None
if open_id:
    try:
        # The backend returns None unless this student owns the request.
        opened = requests_mod.get_request(open_id, student_id=STUDENT_ID)
    except Exception as exc:  # noqa: BLE001
        alert("error", f"The request could not be loaded: {type(exc).__name__}.")
    if opened is None:
        st.session_state.pop("req_open_id", None)
        alert("warning", "That request is not available.")

if opened:
    render_detail(opened)
else:
    left, right = st.columns([1, 1], gap="large")
    with left:
        render_new_form(active_count)
    with right:
        render_list(my_requests)
