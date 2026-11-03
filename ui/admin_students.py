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
from ui.theme import inject_css  # noqa: E402

BRANCH_ORDER = ["CSE", "ECE", "ME", "CE"]
NONE = "—"


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
