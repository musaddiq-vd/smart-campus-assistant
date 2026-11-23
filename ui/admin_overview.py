"""Page "Overview" (admin only): college-wide fees, library, attendance and results numbers."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    all_records_safe,
    as_of_date,
    days_text,
    empty_state,
    format_rupees,
    get_college_name,
    hero_html,
    kpi_card_html,
    nice_date,
    require_role,
    section_header,
    student_flags,
)
from ui.theme import BASELINE_COLOR, DEEP_COLOR, PERSONAL_COLOR, ROUTER_COLOR, SIMPLE_COLOR, inject_css  # noqa: E402

BRANCH_ORDER = ["CSE", "ECE", "ME", "CE"]


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def action_rows(records: list[dict]) -> list[dict]:
    """One row per problem that needs follow-up: overdue fees, overdue books, low attendance."""
    rows = []
    for r in records:
        flags = student_flags(r)
        base = {"Student ID": r.get("student_id", ""), "Name": r.get("name", ""), "Branch": r.get("branch", "")}
        fees = r.get("fees") or {}
        if flags["fee_status"] == "overdue":
            due = fees.get("due_date")
            when = f" (due {nice_date(due)}, {days_text(due, as_of_date(r))})" if due else ""
            rows.append({**base, "Issue": "Fees overdue", "Detail": f"{format_rupees(flags['fees_due'])} unpaid{when}"})
        if flags["overdue_books"]:
            rows.append({**base, "Issue": "Overdue books",
                         "Detail": f"{plural(flags['overdue_books'], 'book')} overdue · fine {format_rupees(flags['fine'])}"})
        if flags["low_attendance"]:
            rows.append({**base, "Issue": "Low attendance", "Detail": f"{flags['attendance']:.1f}% overall (minimum 75%)"})
    order = {"Fees overdue": 0, "Overdue books": 1, "Low attendance": 2}
    return sorted(rows, key=lambda x: (order[x["Issue"]], x["Student ID"]))


inject_css()
require_role("admin")

records = all_records_safe()
st.markdown(
    hero_html("Admin · overview", get_college_name(),
              "College-wide fees, library, attendance and results from all student records."),
    unsafe_allow_html=True,
)

if not records:
    empty_state("🗂️", "No student records", "data/students.json is missing or empty.")
    st.stop()

flags = [student_flags(r) for r in records]
by_branch = Counter(r.get("branch") or "?" for r in records)
branches = [b for b in BRANCH_ORDER if b in by_branch] + sorted(b for b in by_branch if b not in BRANCH_ORDER)
collected = sum(f["fees_paid"] for f in flags)
outstanding = sum(f["fees_due"] for f in flags)
overdue_accounts = sum(1 for f in flags if f["fee_status"] == "overdue")
overdue_book_students = sum(1 for f in flags if f["overdue_books"])
fines = sum(f["fine"] for f in flags)
low_att = sum(1 for f in flags if f["low_attendance"])
no_att = sum(1 for f in flags if f["attendance"] is None)
backlog_students = sum(1 for f in flags if f["backlogs"])
total_backlogs = sum(f["backlogs"] for f in flags)

cards = [
    kpi_card_html("Students", str(len(records)), " · ".join(f"{b} {by_branch[b]}" for b in branches), ROUTER_COLOR),
    kpi_card_html("Fees collected", format_rupees(collected), "This semester, all branches", SIMPLE_COLOR),
    kpi_card_html("Fees outstanding", format_rupees(outstanding), f"{plural(overdue_accounts, 'account')} overdue",
                  BASELINE_COLOR if overdue_accounts else PERSONAL_COLOR),
    kpi_card_html("Overdue books", plural(overdue_book_students, "student"), f"Library fines {format_rupees(fines)} in total",
                  BASELINE_COLOR if overdue_book_students else SIMPLE_COLOR),
    kpi_card_html("Low attendance", plural(low_att, "student"),
                  "Below the 75% minimum" + (f" · {no_att} not recorded yet" if no_att else ""),
                  BASELINE_COLOR if low_att else SIMPLE_COLOR),
    kpi_card_html("Backlogs", plural(backlog_students, "student"), f"{plural(total_backlogs, 'backlog')} in total", DEEP_COLOR),
]
st.markdown(f'<div class="sca-kpis">{"".join(cards)}</div>', unsafe_allow_html=True)

left, right = st.columns([1, 1], gap="large")
with left:
    section_header("Outstanding fees by branch", "Unpaid amount this semester, split by overdue vs not yet due")
    fee_rows = []
    for r, f in zip(records, flags, strict=True):
        if f["fees_due"]:
            kind = "Overdue" if f["fee_status"] == "overdue" else "Not yet due"
            fee_rows.append({"Branch": r.get("branch") or "?", "Status": kind, "Outstanding (₹)": f["fees_due"]})
    if fee_rows:
        df = pd.DataFrame(fee_rows).groupby(["Branch", "Status"], as_index=False)["Outstanding (₹)"].sum()
        chart = alt.Chart(df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Branch:N", sort=branches, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Outstanding (₹):Q", title="₹ outstanding", axis=alt.Axis(format="~s")),
            color=alt.Color("Status:N", scale=alt.Scale(domain=["Overdue", "Not yet due"], range=[BASELINE_COLOR, PERSONAL_COLOR]),
                            legend=alt.Legend(orient="bottom", title=None)),
            order=alt.Order("Status:N", sort="descending"),
            tooltip=["Branch", "Status", alt.Tooltip("Outstanding (₹):Q", format=",.0f")],
        )
        st.altair_chart(chart.properties(height=280), width="stretch")
    else:
        st.caption("No fees outstanding. 🎉")

with right:
    section_header("Students by branch")
    branch_df = pd.DataFrame(
        [{"Branch": b, "Students": by_branch[b],
          "Low attendance": sum(1 for r, f in zip(records, flags, strict=True) if r.get("branch") == b and f["low_attendance"]),
          "Fees overdue": sum(1 for r, f in zip(records, flags, strict=True) if r.get("branch") == b and f["fee_status"] == "overdue")}
         for b in branches]
    )
    st.dataframe(branch_df, hide_index=True, width="stretch")

actions = action_rows(records)
section_header("Action needed", f"{plural(len(actions), 'item')} to follow up (open a student in the Students page)")
if actions:
    kinds = ["All", "Fees overdue", "Overdue books", "Low attendance"]
    pick = st.segmented_control("Show", kinds, default="All", key="overview_action_filter", label_visibility="collapsed")
    shown = actions if pick in (None, "All") else [a for a in actions if a["Issue"] == pick]
    st.dataframe(pd.DataFrame(shown), hide_index=True, width="stretch")
else:
    st.success("Nothing needs follow-up right now.", icon=":material/check_circle:")


# ---------- email notifications ----------

def queue_fee_reminders_now() -> None:
    """Button callback: queue today's fee reminders and send them in the background."""
    from ui.components import notify_students

    queued = notify_students("queue_fee_reminders")
    if queued is None:  # also when the notifications module cannot be loaded
        st.session_state["overview_flash"] = ("error", "Could not queue fee reminders.")
    elif queued:
        st.session_state["overview_flash"] = ("success", f"{plural(queued, 'fee reminder')} queued.")
    else:
        st.session_state["overview_flash"] = (
            "info", "No new fee reminders today (each student gets at most one per stage or week).")


def render_notifications() -> None:
    try:
        from smart_campus_assistant import mailer, notifications
    except Exception:  # noqa: BLE001
        return
    section_header(
        "Email notifications",
        "Students are emailed when you reply to a request or publish a notice for their branch, "
        "and get fee reminders 7 days before the due date and weekly while overdue.",
    )
    flash = st.session_state.pop("overview_flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])
    on = notifications.enabled()
    if not on:
        st.info("Email notifications are turned off (SCA_EMAIL_NOTIFICATIONS=0).", icon=":material/notifications_off:")
    elif not mailer.email_configured():
        st.info("Email (SMTP) is not configured, so notifications are recorded below as skipped but not sent.",
                icon=":material/mail:")
    st.button("Send fee reminders now", icon=":material/notifications_active:", on_click=queue_fee_reminders_now,
              key="overview_fee_reminders", disabled=not on)
    # Same "today" as the dashboards, so reminder amounts always match what students see.
    st.caption(f"Fee reminders are worked out as of {nice_date(notifications.reminder_date(records[0]))}, "
               "the date of the student records.")
    try:
        if on and notifications.due_count():
            notifications.deliver_in_background()  # retry emails that failed earlier or were interrupted
        rows = notifications.recent(20)
    except Exception:  # noqa: BLE001
        rows = []
    if rows:
        st.dataframe(
            pd.DataFrame([{
                "Time": nice_date(r["created_at"][:10]), "Type": notifications.KINDS.get(r["kind"], r["kind"]),
                "Student": r["student_id"], "To": r["recipient"], "Subject": r["subject"],
                "Status": "Waiting to retry" if r["status"] == "pending" and r["attempts"] else r["status"].title(),
                "Note": r["error"],
            } for r in rows]),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("No emails yet.")


render_notifications()
