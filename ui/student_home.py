"""Page "My dashboard": the signed-in student's own record (fees, books, attendance, exams, hostel)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    empty_state,
    get_student_safe,
    list_notices_safe,
    notice_cards_html,
    render_student_dashboard,
    require_role,
    section_header,
)
from ui.theme import inject_css  # noqa: E402

inject_css(max_width="72rem")
user = require_role("student")

# Only ever the signed-in student's own ID, taken from the authenticated session.
record = get_student_safe(user.get("student_id"))
if not record:
    empty_state(
        "🗂️",
        "Your record is not available",
        "We could not find a student record for your account. Please contact the admin office.",
    )
else:
    render_student_dashboard(record)

# Latest admin notices for this student's branch (plus notices for all branches).
branch = (record or {}).get("branch") or str(user.get("student_id") or "")[5:-3] or None
latest = list_notices_safe(branch=branch, limit=5)
section_header("Latest notices", "Newest first, for your branch and the whole college")
if latest:
    st.markdown(notice_cards_html(latest, max_body=400), unsafe_allow_html=True)
else:
    st.caption("No notices right now.")
