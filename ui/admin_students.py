"""Page "Students" (admin only): searchable, filterable list of all students plus a detail view."""

from __future__ import annotations

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
        render_account_box(record)
