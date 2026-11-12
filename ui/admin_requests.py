"""Page "Requests" (admin only): work the queue of student support requests."""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import chip_html, empty_state, hero_html, kpi_card_html, require_role, section_header  # noqa: E402
from ui.theme import PERSONAL_COLOR, ROUTER_COLOR, SIMPLE_COLOR, inject_css  # noqa: E402

inject_css(max_width="72rem")
user = require_role("admin")

try:
    from smart_campus_assistant import support_requests as requests_mod
except Exception as exc:  # noqa: BLE001
    st.error(f"The requests module could not be loaded: {type(exc).__name__}.")
    st.stop()

CATEGORIES = requests_mod.CATEGORIES
STATUSES = requests_mod.STATUSES
ALL = "all"
ADMIN_NAME = user.get("name") or user.get("email") or "Admin office"
FILTER_KEYS = {"req_f_status": ALL, "req_f_category": ALL, "req_f_search": ""}
FLASH_ICONS = {"success": ":material/check_circle:", "warning": ":material/warning:", "error": ":material/error:"}
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
    st.session_state.setdefault("req_admin_flash", []).append((kind, text))


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
    _flash("success", f"Reply sent on {request_id}. Status: {requests_mod.status_label(request['status'])}.")


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
    cards = [
        kpi_card_html("Open", str(numbers.get("open", 0)), "Waiting for a first reply", ROUTER_COLOR),
        kpi_card_html("In progress", str(numbers.get("in_progress", 0)), "Being worked on", PERSONAL_COLOR),
        kpi_card_html("Resolved", str(numbers.get("resolved", 0)),
                      f"{numbers.get('closed', 0)} closed · {numbers.get('total', 0)} in total", SIMPLE_COLOR),
    ]
    return f'<div class="sca-kpis">{"".join(cards)}</div>'


def student_info_html(request: dict) -> str:
    facts = [("Name", request.get("student_name")), ("Student ID", request.get("student_id")),
             ("Email", request.get("student_email")), ("Category", requests_mod.category_label(request.get("category")))]
    rows = "".join(
        f'<div class="fact"><span class="k">{escape(label)}</span><span class="v">{escape(str(value))}</span></div>'
        for label, value in facts if value
    )
    return f'<div class="sca-req-facts">{rows}</div>'


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
                st.warning(f"Close {rid}? The student will not be able to reply on it again.",
                           icon=":material/warning:")
                yes, no = st.columns(2)
                with yes:
                    st.button("Yes, close", key=f"req_admin_close_yes_{rid}", type="primary",
                              on_click=change_status, args=(rid, "closed"), width="stretch")
                with no:
                    st.button("Cancel", key=f"req_admin_close_no_{rid}", on_click=cancel_close, width="stretch")
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
        apply, reset, _ = st.columns([1, 1, 4])
        with apply:
            st.form_submit_button("Apply filters", type="primary", icon=":material/filter_alt:", width="stretch")
        with reset:
            st.form_submit_button("Reset", on_click=reset_filters, width="stretch")

    status = st.session_state.get("req_f_status")
    category = st.session_state.get("req_f_category")
    try:
        requests = requests_mod.list_requests(
            status=None if status in (None, ALL) else status,
            category=None if category in (None, ALL) else category,
            search=st.session_state.get("req_f_search") or None,
        )
    except requests_mod.RequestError as exc:
        st.error(str(exc), icon=":material/error:")
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"The requests could not be loaded: {type(exc).__name__}.")
        return

    st.caption(f"{len(requests)} request{'s' if len(requests) != 1 else ''}, newest first")
    if not requests:
        empty_state("📭", "No requests here", "Nothing matches these filters. Student requests appear here.")
    for request in requests:
        card, action = st.columns([6, 1], vertical_alignment="center")
        with card:
            st.markdown(requests_mod.request_card_html(request, show_student=True), unsafe_allow_html=True)
        with action:
            st.button("Open", key=f"req_admin_open_{request['id']}", icon=":material/chat:", on_click=open_request,
                      args=(request["id"],))


PAGE_CSS = """
<style>
.sca-req-facts { display: flex; flex-direction: column; gap: .35rem; margin: .4rem 0 .2rem 0; }
.sca-req-facts .fact {
  display: flex; justify-content: space-between; gap: .75rem; font-size: .86rem; padding: .35rem .6rem;
  border: 1px solid var(--sca-card-border, rgba(128,128,128,.25)); border-radius: 10px;
  background: var(--sca-card-bg, transparent);
}
.sca-req-facts .k { color: var(--sca-muted, gray); white-space: nowrap; }
.sca-req-facts .v { font-weight: 600; text-align: right; overflow-wrap: anywhere; }
</style>
"""

try:
    numbers = requests_mod.counts()
except Exception as exc:  # noqa: BLE001
    st.error(f"The requests could not be loaded: {type(exc).__name__}.")
    st.stop()

waiting = numbers.get("open", 0)
st.markdown(
    hero_html(
        "Admin · help desk", "Requests",
        "Students raise requests about fees, hostel, library, exams and certificates. Reply and track them here.",
        chips=[chip_html(f"{waiting} waiting for a reply", "demo" if waiting else "live")],
    ),
    unsafe_allow_html=True,
)
st.markdown(requests_mod.REQUESTS_CSS + PAGE_CSS, unsafe_allow_html=True)

for kind, text in st.session_state.pop("req_admin_flash", []):
    st.toast(text, icon=FLASH_ICONS[kind])
    getattr(st, kind)(text, icon=FLASH_ICONS[kind])

st.markdown(kpis_html(numbers), unsafe_allow_html=True)

open_id = st.session_state.get("req_admin_open_id")
opened = None
if open_id:
    try:
        opened = requests_mod.get_request(open_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"The request could not be loaded: {type(exc).__name__}.")
    if opened is None:
        st.session_state.pop("req_admin_open_id", None)
        st.warning("That request is not available.", icon=":material/warning:")

if opened:
    render_detail(opened)
else:
    render_queue()
