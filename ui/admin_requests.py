"""Page "Requests" (admin only): work the queue of student support requests."""

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
    email_note,
    empty_state,
    facts_html,
    flash,
    hero,
    kpi_card_html,
    kpi_row_html,
    notify_students,
    require_role,
    section_header,
    show_flashes,
)
from ui.theme import CONTENT_WIDTH, INFO_COLOR, OK_COLOR, WARN_COLOR, inject_css  # noqa: E402

inject_css(max_width=CONTENT_WIDTH)
user = require_role("admin")

try:
    from smart_campus_assistant import support_requests as requests_mod
except Exception as exc:  # noqa: BLE001
    alert("error", f"The requests module could not be loaded: {type(exc).__name__}.")
    st.stop()

CATEGORIES = requests_mod.CATEGORIES
STATUSES = requests_mod.STATUSES
ALL = "all"
ADMIN_NAME = user.get("name") or user.get("email") or "Admin office"
FILTER_KEYS = {"req_f_status": ALL, "req_f_category": ALL, "req_f_search": ""}
FLASH_KEY = "req_admin_flash"
# Status buttons in the detail view: (target status, label, icon). "closed" goes through a confirm step.
STATUS_ACTIONS = (
    ("open", "Reopen", ":material/undo:"),
    ("in_progress", "Mark in progress", ":material/pending:"),
    ("resolved", "Mark resolved", ":material/check_circle:"),
)

for key, default in FILTER_KEYS.items():
    st.session_state.setdefault(key, default)
    # Re-assign so the filters survive while the detail view (which does not draw them) is open.
    st.session_state[key] = st.session_state[key]


def _flash(kind: str, text: str) -> None:
    flash(FLASH_KEY, kind, text)


def _reply_key(request_id: str) -> str:
    return f"req_admin_reply_{request_id}"


# ---------- callbacks ----------

def open_request(request_id: str) -> None:
    st.session_state["req_admin_open_id"] = request_id
    st.session_state.pop("req_admin_confirm_close", None)


def back_to_list() -> None:
    st.session_state.pop("req_admin_open_id", None)
    st.session_state.pop("req_admin_confirm_close", None)


def reset_filters() -> None:
    for key, default in FILTER_KEYS.items():
        st.session_state[key] = default


def send_reply(request_id: str) -> None:
    """Form callback: add the admin's reply (an open request moves to "in progress")."""
    try:
        request = requests_mod.add_message(
            request_id, "admin", ADMIN_NAME, st.session_state.get(_reply_key(request_id))
        )
    except requests_mod.RequestError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not send the reply: {type(exc).__name__}.")
        return
    st.session_state[_reply_key(request_id)] = ""
    emailed = email_note("Reply to the student") if notify_students("notify_request_reply", request) else ""
    _flash("success", f"Reply sent on {request_id}. Status: {requests_mod.status_label(request['status'])}.{emailed}")


def change_status(request_id: str, status: str) -> None:
    st.session_state.pop("req_admin_confirm_close", None)
    try:
        request = requests_mod.set_status(request_id, status)
    except requests_mod.RequestError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not update {request_id}: {type(exc).__name__}.")
        return
    _flash("success", f"{request_id} is now {requests_mod.status_label(request['status']).lower()}.")


def ask_close(request_id: str) -> None:
    st.session_state["req_admin_confirm_close"] = request_id


def cancel_close() -> None:
    st.session_state.pop("req_admin_confirm_close", None)


# ---------- page ----------

def kpis_html(numbers: dict) -> str:
    # Same colours as the status chips on the request cards: open = indigo, in progress = amber, resolved = green.
    cards = [
        kpi_card_html("Open", str(numbers.get("open", 0)), "Waiting for a first reply", INFO_COLOR),
        kpi_card_html("In progress", str(numbers.get("in_progress", 0)), "Being worked on", WARN_COLOR),
        kpi_card_html("Resolved", str(numbers.get("resolved", 0)),
                      f"{numbers.get('closed', 0)} closed · {numbers.get('total', 0)} in total", OK_COLOR),
    ]
    return kpi_row_html(cards)


def student_info_html(request: dict) -> str:
    facts = [("Name", request.get("student_name")), ("Student ID", request.get("student_id")),
             ("Email", request.get("student_email")), ("Category", requests_mod.category_label(request.get("category")))]
    return facts_html(facts, extra_class="sca-req-facts")  # old class name kept for CSS that targets it


def render_detail(request: dict) -> None:
    """One request: student info, thread, reply form and status actions."""
    rid = request["id"]
    status = request["status"]
    st.button("Back to the queue", key="req_admin_back", icon=":material/arrow_back:", on_click=back_to_list)
    st.markdown(requests_mod.request_card_html(request), unsafe_allow_html=True)

    main, side = st.columns([2, 1], gap="large")
    with side:
        section_header("Student")
        st.markdown(student_info_html(request), unsafe_allow_html=True)
        section_header("Status", f"Currently {requests_mod.status_label(status).lower()}")
        for target, label, icon in STATUS_ACTIONS:
            if target == status or (target == "open" and status == "in_progress"):
                continue
            st.button(label, key=f"req_admin_set_{target}_{rid}", icon=icon, on_click=change_status,
                      args=(rid, target), width="stretch")
        if status != "closed":
            pending = st.session_state.get("req_admin_confirm_close") == rid
            st.button("Close request", key=f"req_admin_close_{rid}", icon=":material/lock:", on_click=ask_close,
                      args=(rid,), disabled=pending, width="stretch")
            if pending:
                confirm_prompt(
                    f"Close {rid}? The student will not be able to reply on it again.",
                    yes_label="Yes, close", yes_key=f"req_admin_close_yes_{rid}", on_yes=change_status,
                    yes_args=(rid, "closed"), no_key=f"req_admin_close_no_{rid}", on_no=cancel_close,
                )
    with main:
        section_header("Conversation", "The student's request and every reply, oldest first")
        st.markdown(requests_mod.thread_html(request, viewer_role="admin"), unsafe_allow_html=True)
        if status == "closed":
            st.info("This request is closed. Reopen it to reply.", icon=":material/lock:")
        else:
            if status == "open":
                st.caption("Your first reply moves this request to \"in progress\".")
            with st.form(f"req_admin_reply_form_{rid}", border=False):
                st.text_area("Reply to the student", key=_reply_key(rid), height=120,
                             max_chars=requests_mod.MESSAGE_MAX, placeholder="Write your reply. HTML is removed.")
                st.form_submit_button("Send reply", type="primary", icon=":material/send:", on_click=send_reply,
                                      args=(rid,))


def render_queue() -> None:
    section_header("Queue", "Filter by status or category, or search by subject, student name, ID or email")
    with st.form("req_admin_filters", border=False):
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            st.selectbox("Status", [ALL, *STATUSES], key="req_f_status",
                         format_func=lambda s: "All statuses" if s == ALL else STATUSES[s])
        with c2:
            st.selectbox("Category", [ALL, *CATEGORIES], key="req_f_category",
                         format_func=lambda c: "All categories" if c == ALL else CATEGORIES[c])
        with c3:
            st.text_input("Search", key="req_f_search", max_chars=100, placeholder="Subject, student name, ID or email")
        with st.container(horizontal=True, gap="small"):
            st.form_submit_button("Apply filters", type="primary", icon=":material/filter_alt:")
            st.form_submit_button("Reset", on_click=reset_filters, icon=":material/filter_alt_off:")

    status = st.session_state.get("req_f_status")
    category = st.session_state.get("req_f_category")
    try:
        requests = requests_mod.list_requests(
            status=None if status in (None, ALL) else status,
            category=None if category in (None, ALL) else category,
            search=st.session_state.get("req_f_search") or None,
        )
    except requests_mod.RequestError as exc:
        alert("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        alert("error", f"The requests could not be loaded: {type(exc).__name__}.")
        return

    st.caption(f"{len(requests)} request{'s' if len(requests) != 1 else ''}, newest first")
    if not requests:
        empty_state("📭", "No requests here", "Nothing matches these filters. Student requests appear here.")
    # Each action sits right under its card, as on the student page and the Notices page (in a narrow
    # column beside the card the label was cut off on smaller screens).
    for request in requests:
        with st.container(gap="xsmall"):
            st.markdown(requests_mod.request_card_html(request, show_student=True), unsafe_allow_html=True)
            st.button("Open conversation", key=f"req_admin_open_{request['id']}", icon=":material/chat:",
                      on_click=open_request, args=(request["id"],))


try:
    numbers = requests_mod.counts()
except Exception as exc:  # noqa: BLE001
    alert("error", f"The requests could not be loaded: {type(exc).__name__}.")
    st.stop()

waiting = numbers.get("open", 0)
hero(
    "Admin · help desk", "Requests",
    "Students raise requests about fees, hostel, library, exams and certificates. Reply and track them here.",
    chips=[chip_html(f"{waiting} waiting for a reply", "warn" if waiting else "ok")],
)
st.markdown(requests_mod.REQUESTS_CSS, unsafe_allow_html=True)

# The outcome of the last action: one inline message (no toast on top of it).
show_flashes(FLASH_KEY)

st.markdown(kpis_html(numbers), unsafe_allow_html=True)

open_id = st.session_state.get("req_admin_open_id")
opened = None
if open_id:
    try:
        opened = requests_mod.get_request(open_id)
    except Exception as exc:  # noqa: BLE001
        alert("error", f"The request could not be loaded: {type(exc).__name__}.")
    if opened is None:
        st.session_state.pop("req_admin_open_id", None)
        alert("warning", "That request is not available.")

if opened:
    render_detail(opened)
else:
    render_queue()
