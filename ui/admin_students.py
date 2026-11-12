"""Page "Students" (admin only): searchable, filterable list of all students plus a detail view."""

from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    all_records_safe,
    empty_state,
    fee_status_label,
    format_rupees,
    hero_html,
    render_student_dashboard,
    require_role,
    section_header,
    student_flags,
    student_label,
)
from ui.login import load_auth  # noqa: E402
from ui.theme import inject_css  # noqa: E402

BRANCH_ORDER = ["CSE", "ECE", "ME", "CE"]
NONE = "—"
RESET_FAILED = "The password could not be reset. Please try again."


def account_emails(auth) -> dict[str, str] | None:
    """Student ID -> account email for every student account; None when accounts cannot be read."""
    loader = getattr(auth, "load_users", None) if auth is not None else None
    if not callable(loader):
        return None
    try:
        users = loader() or {}
    except Exception:  # noqa: BLE001
        return None
    out = {}
    for email, record in users.items():
        record = record if isinstance(record, dict) else {}
        sid = str(record.get("student_id") or "").upper()
        if sid and record.get("role", "student") == "student":
            out[sid] = str(record.get("email") or email)
    return out


def account_email(auth, student_id: str) -> tuple[bool, str | None]:
    """(accounts readable?, sign-in email of this student or None)."""
    lookup = getattr(auth, "account_email_for_student", None) if auth is not None else None
    if callable(lookup):  # one row instead of every account
        try:
            return True, lookup(student_id)
        except Exception:  # noqa: BLE001
            return False, None
    emails = account_emails(auth)
    return (False, None) if emails is None else (True, emails.get(student_id))


def _ask_reset(student_id: str) -> None:
    st.session_state["reset_confirm"] = student_id
    st.session_state.pop("reset_result", None)


def _cancel_reset() -> None:
    st.session_state.pop("reset_confirm", None)


def _do_reset(student_id: str, email: str) -> None:
    """Confirmed: create a temporary password; it is shown exactly once on the next run."""
    st.session_state.pop("reset_confirm", None)
    admin = st.session_state.get("user") or {}
    auth, _ = load_auth()
    try:
        temp = auth.admin_reset_password(email, by_admin_email=str(admin.get("email") or ""))
    except ValueError as exc:
        st.session_state["reset_result"] = {"sid": student_id, "error": str(exc) or RESET_FAILED}
        return
    except Exception:  # noqa: BLE001
        st.session_state["reset_result"] = {"sid": student_id, "error": RESET_FAILED}
        return
    st.session_state["reset_result"] = {"sid": student_id, "temp": str(temp)}


def render_account_box(record: dict) -> None:
    """Account status and the admin "Reset password" flow for one student."""
    sid = str(record.get("student_id") or "").upper()
    auth, _ = load_auth()
    readable, email = account_email(auth, sid)
    section_header("Account")
    if not readable:
        st.caption("Account status is not available right now.")
        return
    if not email:
        st.caption("No account yet. The student has not registered.")
        return
    st.caption(f"Sign-in email: {email}")

    result = st.session_state.get("reset_result")
    if result and result.get("sid") == sid:
        st.session_state.pop("reset_result", None)  # show once: gone on the next run
        if result.get("error"):
            st.error(result["error"], icon=":material/error:")
        else:
            with st.container(key="temp_password_box"):
                st.markdown("**Temporary password** (shown only once)")
                st.code(result["temp"], language=None)
                st.caption("Use the copy button, then share it securely with the student. "
                           "The student must change it at next sign-in. All their devices were signed out.")
        return
    if not hasattr(auth, "admin_reset_password"):
        return
    if st.session_state.get("reset_confirm") == sid:
        st.warning(f"Reset the password of {record.get('name') or sid}? Their current password stops working "
                   "and every device is signed out.", icon=":material/warning:")
        c1, c2 = st.columns(2)
        c1.button("Yes, reset password", type="primary", key="reset_yes", on_click=_do_reset, args=(sid, email),
                  width="stretch")
        c2.button("Cancel", key="reset_cancel", on_click=_cancel_reset, width="stretch")
    else:
        st.button("Reset password", icon=":material/lock_reset:", key="reset_ask", on_click=_ask_reset, args=(sid,))


# ---------- manage record (audited edits through record_updates) ----------

REC_SAVE_FAILED = "The change could not be saved. Please try again."
REC_CSS = """
<style>
.rec-audit { display: flex; flex-direction: column; gap: .45rem; margin: .4rem 0 .6rem 0; }
.rec-audit .row {
  border: 1px solid var(--sca-card-border, rgba(128,128,128,.25)); border-radius: 10px;
  border-left: 3px solid var(--sca-brand, #5B5BD6); padding: .5rem .8rem;
}
.rec-audit .what { font-size: .9rem; font-weight: 600; }
.rec-audit .meta { font-size: .76rem; color: var(--sca-muted, gray); margin-top: .1rem; }
</style>
"""


def _rec_gen(sid: str) -> int:
    """Counter in the form widget keys: it changes after a saved edit, so the forms start fresh."""
    return int(st.session_state.get("rec_gen", {}).get(sid, 0))


def _rec_key(name: str, sid: str) -> str:
    return f"rec_{name}_{sid}_{_rec_gen(sid)}"


def _rec_apply(sid: str, action: str, *args) -> None:
    """Run one record_updates action as the signed-in admin and leave a flash message for the next run."""
    admin = st.session_state.get("user") or {}
    try:
        from smart_campus_assistant import record_updates

        getattr(record_updates, action)(
            sid, *args, actor_email=str(admin.get("email") or ""), actor_role=str(admin.get("role") or "")
        )
        latest = record_updates.list_audit(sid, limit=1)
    except (ValueError, PermissionError) as exc:  # RecordError messages are written for the UI
        st.session_state["rec_flash"] = {"sid": sid, "kind": "error", "text": str(exc) or REC_SAVE_FAILED}
        return
    except Exception:  # noqa: BLE001
        st.session_state["rec_flash"] = {"sid": sid, "kind": "error", "text": REC_SAVE_FAILED}
        return
    summary = str((latest[0].get("detail") or {}).get("summary") or "") if latest else ""
    st.session_state["rec_flash"] = {"sid": sid, "kind": "success", "text": f"Saved. {summary}".strip()}
    gens = dict(st.session_state.get("rec_gen", {}))
    gens[sid] = gens.get(sid, 0) + 1
    st.session_state["rec_gen"] = gens


def _rec_pay(sid: str) -> None:
    _rec_apply(sid, "record_fee_payment", st.session_state.get(_rec_key("pay_amount", sid)),
               st.session_state.get(_rec_key("pay_mode", sid)))


def _rec_issue(sid: str) -> None:
    _rec_apply(sid, "issue_book", st.session_state.get(_rec_key("book_id", sid)),
               st.session_state.get(_rec_key("book_title", sid)), st.session_state.get(_rec_key("book_author", sid)))


def _rec_return(sid: str) -> None:
    _rec_apply(sid, "return_book", st.session_state.get(_rec_key("return_book", sid)))


def _rec_attendance(sid: str) -> None:
    _rec_apply(sid, "update_attendance", st.session_state.get(_rec_key("att_course", sid)),
               st.session_state.get(_rec_key("att_pct", sid)))


def _rec_hostel(sid: str) -> None:
    if st.session_state.get(_rec_key("hostel_clear", sid)):
        _rec_apply(sid, "set_hostel", None, None)
    else:
        _rec_apply(sid, "set_hostel", st.session_state.get(_rec_key("hostel_block", sid)),
                   st.session_state.get(_rec_key("hostel_room", sid)))


def audit_list_html(rows: list[dict]) -> str:
    """Recent changes of one student as a list (all fields escaped)."""
    items = []
    for row in rows:
        detail = row.get("detail") or {}
        what = str(detail.get("summary") or str(row.get("action") or "").replace("_", " ").capitalize())
        when = str(row.get("created_at") or "")[:16].replace("T", " ")
        meta = f"{when} UTC · {row.get('actor_email', '')} ({row.get('actor_role', '')})"
        items.append(f'<div class="row"><div class="what">{html.escape(what)}</div>'
                     f'<div class="meta">{html.escape(meta)}</div></div>')
    return f'<div class="rec-audit">{"".join(items)}</div>'


def render_manage_record(record: dict) -> None:
    """Admin forms that edit one student's record, plus the audit trail of that student."""
    try:
        from smart_campus_assistant import record_updates
    except Exception:  # noqa: BLE001
        return
    sid = str(record.get("student_id") or "").upper()
    section_header("Manage record", "Changes are saved immediately and logged with your account.")
    st.markdown(REC_CSS, unsafe_allow_html=True)

    flash = st.session_state.get("rec_flash")
    if flash and flash.get("sid") == sid:
        st.session_state.pop("rec_flash", None)
        icon = ":material/check_circle:" if flash["kind"] == "success" else ":material/error:"
        getattr(st, flash["kind"])(flash["text"], icon=icon)

    fees = record.get("fees") or {}
    books = [b for b in (record.get("library") or {}).get("books_issued") or [] if isinstance(b, dict)]
    courses = [c for c in (record.get("attendance") or {}).get("courses") or [] if isinstance(c, dict) and c.get("code")]
    hostel = record.get("hostel") or {}
    try:
        due = int(round(float(fees.get("due") or 0)))
    except (TypeError, ValueError):
        due = 0

    t_fee, t_issue, t_return, t_att, t_hostel, t_log = st.tabs(
        ["Fee payment", "Issue book", "Return book", "Attendance", "Hostel", "Recent changes"]
    )
    with t_fee:
        if due <= 0:
            st.caption("Nothing is due for this student.")
        else:
            with st.form(_rec_key("pay_form", sid), border=False):
                c1, c2 = st.columns(2)
                c1.number_input("Amount received (₹)", min_value=1, max_value=due, value=due, step=1,
                                key=_rec_key("pay_amount", sid), help=f"Whole rupees, up to the due of {format_rupees(due)}.")
                c2.selectbox("Payment mode", list(record_updates.PAYMENT_MODES), key=_rec_key("pay_mode", sid))
                st.caption("Records a payment received at the accounts office. The mode is only a label; "
                           "no payment details are stored.")
                st.form_submit_button("Record payment", type="primary", icon=":material/payments:",
                                      on_click=_rec_pay, args=(sid,))
    with t_issue:
        if len(books) >= record_updates.MAX_BOOKS:
            st.caption(f"This student already holds {record_updates.MAX_BOOKS} books, the library limit.")
        else:
            with st.form(_rec_key("issue_form", sid), border=False):
                c1, c2 = st.columns([1, 2])
                c1.text_input("Book ID", key=_rec_key("book_id", sid), max_chars=record_updates.BOOK_ID_MAX,
                              placeholder="LIB-CS-0421")
                c2.text_input("Title", key=_rec_key("book_title", sid), max_chars=record_updates.TITLE_MAX)
                st.text_input("Author", key=_rec_key("book_author", sid), max_chars=record_updates.AUTHOR_MAX)
                st.caption(f"Issued today, due back in {record_updates.LOAN_DAYS} days.")
                st.form_submit_button("Issue book", type="primary", icon=":material/menu_book:",
                                      on_click=_rec_issue, args=(sid,))
    with t_return:
        if not books:
            st.caption("No books are issued to this student.")
        else:
            titles = {str(b.get("book_id")): f"{b.get('book_id')} · {b.get('title') or 'Book'}" for b in books}
            with st.form(_rec_key("return_form", sid), border=False):
                st.selectbox("Book to return", list(titles), format_func=lambda bid: titles.get(bid, bid),
                             key=_rec_key("return_book", sid))
                st.caption("The fine of the returned book is settled at the counter; the fine due is recalculated "
                           "for the books still out.")
                st.form_submit_button("Mark as returned", type="primary", icon=":material/assignment_return:",
                                      on_click=_rec_return, args=(sid,))
    with t_att:
        if not courses:
            st.caption("This student has no courses on record.")
        else:
            names = {str(c["code"]): f"{c['code']} · {c.get('name') or ''}"
                     + (f" (now {c['pct']}%)" if c.get("pct") is not None else " (not recorded)") for c in courses}
            with st.form(_rec_key("att_form", sid), border=False):
                c1, c2 = st.columns([2, 1])
                c1.selectbox("Course", list(names), format_func=lambda code: names.get(code, code),
                             key=_rec_key("att_course", sid))
                c2.number_input("Attendance (%)", min_value=0.0, max_value=100.0, value=75.0, step=0.5, format="%.1f",
                                key=_rec_key("att_pct", sid))
                st.caption("The overall percentage is recalculated as the mean of the recorded courses.")
                st.form_submit_button("Update attendance", type="primary", icon=":material/event_available:",
                                      on_click=_rec_attendance, args=(sid,))
    with t_hostel:
        with st.form(_rec_key("hostel_form", sid), border=False):
            c1, c2 = st.columns(2)
            c1.text_input("Block", value=str(hostel.get("block") or ""), key=_rec_key("hostel_block", sid),
                          max_chars=record_updates.HOSTEL_TEXT_MAX, placeholder="A")
            c2.text_input("Room", value=str(hostel.get("room") or ""), key=_rec_key("hostel_room", sid),
                          max_chars=record_updates.HOSTEL_TEXT_MAX, placeholder="A-214")
            st.checkbox("Day scholar (clear the hostel room)", key=_rec_key("hostel_clear", sid))
            st.form_submit_button("Save hostel", type="primary", icon=":material/home:",
                                  on_click=_rec_hostel, args=(sid,))
    with t_log:
        try:
            rows = record_updates.list_audit(sid, limit=10)
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            st.markdown(audit_list_html(rows), unsafe_allow_html=True)
        else:
            st.caption("No changes have been made to this record yet.")


def matches(record: dict, flags: dict, query: str, branches: list, years: list, statuses: list, low_only: bool) -> bool:
    if query:
        hay = " ".join(str(record.get(k) or "") for k in ("student_id", "name", "email")).lower()
        if query.lower() not in hay:
            return False
    if branches and record.get("branch") not in branches:
        return False
    if years and record.get("year") not in years:
        return False
    if statuses and fee_status_label(flags["fee_status"]) not in statuses:
        return False
    return not (low_only and not flags["low_attendance"])


def table_row(record: dict, flags: dict) -> dict:
    cgpa = (record.get("results") or {}).get("cgpa")
    return {
        "Student ID": record.get("student_id", ""),
        "Name": record.get("name", ""),
        "Branch": record.get("branch", ""),
        "Year": record.get("year"),
        "Fee status": fee_status_label(flags["fee_status"]),
        "Fees due": format_rupees(flags["fees_due"]),
        "Attendance (%)": flags["attendance"],
        "Overdue books": flags["overdue_books"],
        "Backlogs": flags["backlogs"],
        "CGPA": f"{float(cgpa):.2f}" if cgpa is not None else "N/A",
    }


inject_css()
require_role("admin")

st.markdown(
    hero_html("Admin · students", "Students", "Search and filter every student record, then open one for the full view."),
    unsafe_allow_html=True,
)

records = all_records_safe()
if not records:
    empty_state("🗂️", "No student records", "data/students.json is missing or empty.")
    st.stop()

flags_by_id = {r["student_id"]: student_flags(r) for r in records}
branch_options = [b for b in BRANCH_ORDER if any(r.get("branch") == b for r in records)]
branch_options += sorted({r.get("branch") for r in records if r.get("branch") and r.get("branch") not in BRANCH_ORDER})
year_options = sorted({r.get("year") for r in records if r.get("year") is not None})
status_options = sorted({fee_status_label(f["fee_status"]) for f in flags_by_id.values()})

with st.container(border=True, key="student_filters"):
    query = st.text_input("Search", placeholder="Name, student ID or email", key="students_query",
                          icon=":material/search:")
    c1, c2, c3 = st.columns(3)
    branches = c1.multiselect("Branch", branch_options, key="students_branch", placeholder="All branches")
    years = c2.multiselect("Year", year_options, key="students_year", placeholder="All years")
    statuses = c3.multiselect("Fee status", status_options, key="students_fee", placeholder="Any status")
    low_only = st.toggle("Only attendance below 75%", key="students_low")

shown = [r for r in records if matches(r, flags_by_id[r["student_id"]], query.strip(), branches, years, statuses, low_only)]
shown.sort(key=lambda r: r["student_id"])

section_header("All students", f"{len(shown)} of {len(records)} shown")
if not shown:
    st.info("No students match these filters.", icon=":material/filter_alt_off:")
    st.stop()

df = pd.DataFrame([table_row(r, flags_by_id[r["student_id"]]) for r in shown])
st.dataframe(
    df,
    hide_index=True,
    width="stretch",
    column_config={
        "Attendance (%)": st.column_config.ProgressColumn("Attendance", min_value=0, max_value=100, format="%.1f%%"),
        "Year": st.column_config.NumberColumn("Year", format="%d"),
    },
)

section_header("Student details")
labels = {r["student_id"]: student_label(r) for r in shown}
picked = st.selectbox("Open a student", [NONE, *labels], format_func=lambda sid: labels.get(sid, "Choose a student…"),
                      key="students_pick")
if picked and picked != NONE:
    record = next(r for r in shown if r["student_id"] == picked)
    with st.container(border=True, key="student_detail"):
        render_student_dashboard(record)
        render_manage_record(record)
        render_account_box(record)
