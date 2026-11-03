"""Page "My dashboard": the signed-in student's own record (fees, books, attendance, exams, hostel)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import empty_state, get_student_safe, render_student_dashboard, require_role  # noqa: E402
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
