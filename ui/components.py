"""Reusable render helpers and number formatting.

``*_html`` functions return HTML strings (pure, easy to test); the matching
functions without the suffix render them with ``st.markdown``.

Shared UI patterns, so every page looks and behaves the same:
- page header: ``hero`` / ``hero_html``; section titles: ``section_header``;
- KPI tiles: ``kpi_card_html`` inside ``kpi_row_html``;
- "nothing here yet": ``empty_state`` (``compact=True`` inside a section);
- the outcome of an action: ``flash`` in the callback, ``show_flashes`` on the page (one inline alert);
- destructive actions: ``confirm_prompt`` ("Yes, ..." + "Cancel");
- status words and colours: ``fee_level`` / ``attendance_level`` with ``LEVEL_COLORS``;
- dates ``nice_date`` ("03 Nov 2026"), timestamps ``local_stamp`` ("03 Nov 2026, 15:45 IST"), money ``format_rupees``.
"""

from __future__ import annotations

import math
from datetime import date
from html import escape
from pathlib import Path
from typing import Iterable, Mapping

import altair as alt
import pandas as pd
import streamlit as st

from ui.theme import (
    BAD_COLOR,
    BASELINE_COLOR,
    DEEP_COLOR,
    INFO_COLOR,
    OK_COLOR,
    PERSONAL_COLOR,
    ROUTE_ICONS,
    ROUTER_COLOR,
    SIMPLE_COLOR,
    WARN_COLOR,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Fallback when the backend package cannot be imported.
DEFAULT_COLLEGE_NAME = "XYZ College of Engineering"
DEFAULT_COLLEGES = {"xyz": DEFAULT_COLLEGE_NAME}
DEFAULT_BRANCHES = {
    "CSE": "Computer Science and Engineering",
    "ECE": "Electronics and Communication Engineering",
    "ME": "Mechanical Engineering",
    "CE": "Civil Engineering",
}
ROUTE_NAMES = ("simple", "deep", "personal")


# ---------- formatting ----------

def format_cost(usd: float | None) -> str:
    """Dollar amount with ~2 significant digits for tiny values, e.g. ``$0.00012``."""
    if usd is None or (isinstance(usd, float) and math.isnan(usd)):
        return "—"
    usd = float(usd)
    if usd == 0:
        return "$0"
    if abs(usd) >= 1:
        return f"${usd:,.2f}"
    if abs(usd) < 1e-8:
        return "<$0.00000001"
    decimals = min(8, -math.floor(math.log10(abs(usd))) + 1)
    return f"${usd:.{decimals}f}"


def format_latency(ms: float | None) -> str:
    """``850 ms`` below one second, otherwise ``2.4 s``."""
    if ms is None or (isinstance(ms, float) and math.isnan(ms)):
        return "—"
    ms = float(ms)
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.1f} s"


def format_pct(fraction: float | None, digits: int = 1) -> str:
    """Fraction (0..1) as a percentage string."""
    if fraction is None or (isinstance(fraction, float) and math.isnan(fraction)):
        return "—"
    return f"{fraction * 100:.{digits}f}%"


def college_name(key: str | None) -> str:
    if not key:
        return "Unknown college"
    return get_colleges().get(key, key.replace("_", " ").title())


# ---------- environment ----------

def get_colleges() -> dict[str, str]:
    try:
        from smart_campus_assistant.config import COLLEGES

        return dict(COLLEGES)
    except Exception:
        return dict(DEFAULT_COLLEGES)


def get_college_name() -> str:
    try:
        from smart_campus_assistant.config import COLLEGE_NAME

        return str(COLLEGE_NAME)
    except Exception:
        return DEFAULT_COLLEGE_NAME


def get_branches() -> dict[str, str]:
    try:
        from smart_campus_assistant.config import BRANCHES

        return dict(BRANCHES)
    except Exception:
        return dict(DEFAULT_BRANCHES)


def list_students_safe() -> list[dict]:
    """Students for the login picker; empty when the backend or its data file is missing."""
    try:
        from smart_campus_assistant.student_data import list_students

        return [dict(x) for x in (list_students() or []) if x.get("student_id")]
    except Exception:
        return []


def get_student_safe(student_id: str | None) -> dict | None:
    if not student_id:
        return None
    try:
        from smart_campus_assistant.student_data import get_student

        record = get_student(student_id)
        return dict(record) if record else None
    except Exception:
        return None


def student_label(student: Mapping) -> str:
    """``XYZ24CSE001 · Name (CSE, Year 3)``."""
    sid = student.get("student_id", "?")
    name = student.get("name") or "Student"
    bits = [str(b) for b in (student.get("branch"), f"Year {student['year']}" if student.get("year") else None) if b]
    return f"{sid} · {name}" + (f" ({', '.join(bits)})" if bits else "")


def is_demo_mode() -> bool:
    """Demo (offline) mode; the one rule lives in the backend config so auth and the UI agree."""
    from smart_campus_assistant.config import is_demo_mode as backend_is_demo_mode

    return backend_is_demo_mode()


def has_aws_credentials() -> bool:
    """Local check only (no network); see ``smart_campus_assistant.config.has_aws_credentials``."""
    from smart_campus_assistant.config import has_aws_credentials as backend_has_aws_credentials

    return backend_has_aws_credentials()


# ---------- HTML builders ----------

def _md(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)


def hero_html(eyebrow: str, title: str, subtitle: str, chips: list[str] | None = None) -> str:
    """Page header: eyebrow ("Admin · notices"), title, one-line subtitle and optional chips (already HTML)."""
    chips_html = f'<div class="chips">{"".join(chips)}</div>' if chips else ""
    return (
        f'<div class="sca-hero"><div class="eyebrow">{escape(eyebrow)}</div>'
        f"<h1>{escape(title)}</h1><p>{escape(subtitle)}</p>{chips_html}</div>"
    )


def hero(eyebrow: str, title: str, subtitle: str, chips: list[str] | None = None) -> None:
    _md(hero_html(eyebrow, title, subtitle, chips))


# Chip kinds: ok / warn / bad / info are status dots; live / demo are the older names of ok / bad.
CHIP_KINDS = ("", "ok", "warn", "bad", "info", "live", "demo")


def chip_html(text: str, kind: str = "") -> str:
    """Small pill. A kind adds a coloured dot; the text always says the status too (never colour alone)."""
    kind = kind if kind in CHIP_KINDS else "info"
    dot = '<span class="dot" aria-hidden="true"></span>' if kind else ""
    return f'<span class="sca-chip {kind}">{dot}{escape(text)}</span>'


def route_badge_html(route: str | None, reason: str | None = None, forced: bool = False) -> str:
    route = route if route in ROUTE_NAMES else "simple"
    icon = ROUTE_ICONS.get(route, "⚡")
    parts = [f'<span class="sca-badge {route}">{icon} {route.upper()}</span>']
    if forced:
        parts.append('<span class="sca-badge forced">FORCED</span>')
    if reason:
        parts.append(f'<span class="reason">{escape(reason)}</span>')
    return f'<div class="sca-route">{"".join(parts)}</div>'


def route_badge(route: str | None, reason: str | None = None, forced: bool = False) -> None:
    _md(route_badge_html(route, reason, forced))


def citation_card_html(citation: Mapping, number: int | str | None = None) -> str:
    """One source card: [n], the document, its section heading and a short quote (all escaped).

    ``number`` may be text such as "1, 3" when one card stands for two numbers in the answer.
    """
    source = str(citation.get("source") or "unknown source")
    college = citation.get("college") or (source.split("/")[0] if "/" in source else None)
    page = citation.get("page")
    section = str(citation.get("section") or "").strip()
    snippet = str(citation.get("snippet") or "").strip()
    num = f'<span class="num">[{escape(str(number))}]</span>' if number not in (None, "") else ""
    page_html = f'<span class="page">Section {escape(str(page))}</span>' if page not in (None, "") else ""
    section_html = f'<div class="section">{escape(section)}</div>' if section else ""
    snippet_html = f'<div class="snippet">{escape(snippet)}</div>' if snippet else ""
    return (
        f'<div class="sca-cite"><div class="head"><span>{num} <span class="college">{escape(college_name(college))}</span></span>'
        f'{page_html}</div><div class="file">{escape(source.split("/")[-1])}</div>{section_html}{snippet_html}</div>'
    )


def citation_cards_html(citations: Iterable[Mapping]) -> str:
    cards = "".join(citation_card_html(c, i) for i, c in enumerate(citations, start=1))
    return f'<div class="sca-cites">{cards}</div>'


def citation_card(citation: Mapping, number: int | None = None) -> None:
    _md(citation_card_html(citation, number))


def kpi_card_html(label: str, value: str, sub: str = "", accent: str = ROUTER_COLOR, unit: str = "") -> str:
    """One KPI tile; ``accent`` colours the top stripe (the sub text carries the meaning, not the colour)."""
    unit_html = f'<span class="unit">{escape(unit)}</span>' if unit else ""
    return (
        f'<div class="sca-kpi" style="--accent:{escape(str(accent))}"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}{unit_html}</div><div class="sub">{escape(sub)}</div></div>'
    )


def kpi_card(label: str, value: str, sub: str = "", accent: str = ROUTER_COLOR, unit: str = "") -> None:
    _md(kpi_card_html(label, value, sub, accent, unit))


def kpi_row_html(cards: Iterable[str]) -> str:
    """KPI tiles in one responsive grid (2 per row on phones, 1 on very narrow ones)."""
    return f'<div class="sca-kpis">{"".join(cards)}</div>'


def empty_state_html(icon: str, title: str, text: str, compact: bool = False) -> str:
    """"Nothing here yet" in one look: a big card for a page, ``compact`` for one section of a page."""
    if compact:
        text_html = f'<div class="text">{escape(text)}</div>' if text else ""
        return (
            f'<div class="sca-empty compact" role="status"><div class="icon" aria-hidden="true">{escape(icon)}</div>'
            f'<div class="body"><div class="title">{escape(title)}</div>{text_html}</div></div>'
        )
    return (
        f'<div class="sca-empty" role="status"><div class="icon" aria-hidden="true">{escape(icon)}</div>'
        f"<h3>{escape(title)}</h3><p>{escape(text)}</p></div>"
    )


def empty_state(icon: str, title: str, text: str, compact: bool = False) -> None:
    _md(empty_state_html(icon, title, text, compact))


def section_header(title: str, caption: str = "") -> None:
    cap = f"<p>{escape(caption)}</p>" if caption else ""
    _md(f'<div class="sca-section"><h3>{escape(title)}</h3>{cap}</div>')


CALLOUT_KINDS = ("info", "ok", "warn", "bad")


def callout_html(text: str, icon: str = "ℹ️", kind: str = "info", title: str = "") -> str:
    """A tinted note inside a page (demo payment, tips); plain text only, everything escaped."""
    kind = kind if kind in CALLOUT_KINDS else "info"
    title_html = f'<span class="title">{escape(title)}</span> ' if title else ""
    return (
        f'<div class="sca-callout {kind}"><span class="icon" aria-hidden="true">{escape(icon)}</span>'
        f'<div class="body">{title_html}{escape(text)}</div></div>'
    )


def facts_html(facts: Iterable[tuple[str, object]], extra_class: str = "") -> str:
    """Label / value rows (empty values are skipped), e.g. the student of a request."""
    rows = "".join(
        f'<div class="fact"><span class="k">{escape(str(label))}</span><span class="v">{escape(str(value))}</span></div>'
        for label, value in facts if value not in (None, "")
    )
    cls = f"sca-facts {escape(extra_class)}".strip()
    return f'<div class="{cls}">{rows}</div>'


# ---------- feedback: one pattern for every page ----------
#
# The outcome of an action is shown ONCE, as an inline alert where the action happened
# (under the page header, or under a single form). Never a toast plus an alert for the same
# event: toasts vanish after a few seconds and screen readers can miss them.

FLASH_KINDS = ("success", "info", "warning", "error")
FLASH_ICONS = {
    "success": ":material/check_circle:",
    "info": ":material/info:",
    "warning": ":material/warning:",
    "error": ":material/error:",
}
LOCKOUT_ICON = ":material/lock_clock:"


def alert(kind: str, text: str, icon: str | None = None) -> None:
    """One inline alert with the standard icon for its kind (unknown kinds become "info")."""
    kind = kind if kind in FLASH_KINDS else "info"
    getattr(st, kind)(text, icon=icon or FLASH_ICONS[kind])


def flash(key: str, kind: str, text: str) -> None:
    """Queue a message (usually from a callback); ``show_flashes(key)`` shows it on the next run."""
    st.session_state.setdefault(key, []).append((kind if kind in FLASH_KINDS else "info", str(text)))


def show_flashes(key: str) -> int:
    """Show, then forget, the messages queued under ``key``; returns how many were shown.

    The same message queued twice for one action is shown once.
    """
    queued = st.session_state.pop(key, None) or []
    if isinstance(queued, tuple):  # one (kind, text) pair instead of a list
        queued = [queued]
    shown: list[tuple[str, str]] = []
    for item in queued:
        try:
            kind, text = item
        except (TypeError, ValueError):
            continue
        if (kind, text) in shown:
            continue
        shown.append((kind, text))
        alert(kind, text)
    return len(shown)


def confirm_prompt(text: str, *, yes_label: str, yes_key: str, on_yes, yes_args: tuple = (),
                   no_key: str, on_no, no_args: tuple = ()) -> None:
    """The confirm step of every destructive action: a warning, then "Yes, ..." (primary) and "Cancel"."""
    st.warning(text, icon=":material/warning:")
    with st.container(horizontal=True, gap="small"):
        st.button(yes_label, key=yes_key, type="primary", on_click=on_yes, args=yes_args)
        st.button("Cancel", key=no_key, on_click=on_no, args=no_args)


def demo_code_html(code: str) -> str:
    """The 6-digit code shown on the page in demo mode (no email server); one click selects it."""
    return (
        '<div class="sca-demo-code" role="note"><div class="lbl">Demo mode · no email server configured</div>'
        f'<div class="code">{escape(str(code))}</div>'
        '<div class="hint">In a real deployment this code arrives by email.</div></div>'
    )


# ---------- flow strip ----------

def flow_steps(route: str | None, steps: list[Mapping]) -> list[dict]:
    """Collapse the node list into short pills: icon, label, color (step names only, no timings)."""
    pills: list[dict] = []
    research_loops = 0

    def flush_research() -> None:
        nonlocal research_loops
        if research_loops:
            label = "Research" + (f" ×{research_loops}" if research_loops > 1 else "")
            pills.append({"icon": "🔎", "label": label, "color": DEEP_COLOR})
            research_loops = 0

    for step in steps:
        node = step.get("node")
        if node == "router":
            pills.append({"icon": "🧭", "label": "Router", "color": ROUTER_COLOR})
        elif node in ("simple_answer", "personal_answer"):
            color = PERSONAL_COLOR if node == "personal_answer" else SIMPLE_COLOR
            if node == "personal_answer":
                if step.get("guest"):
                    pills.append({"icon": "🔒", "label": "No student record", "color": color})
                    continue
                pills.append({"icon": "👤", "label": "Your record", "color": color})
            n_docs = int(step.get("n_docs") or 0)
            if n_docs:
                pills.append({"icon": "🔎", "label": f"{n_docs} doc{'s' if n_docs != 1 else ''}", "color": color})
            pills.append({"icon": "✍️", "label": "Answer", "color": color})
        elif node == "planner":
            n = int(step.get("n_plan") or 0)
            pills.append({"icon": "🗂️", "label": f"Plan ({n})" if n else "Plan", "color": DEEP_COLOR})
        elif node == "researcher":
            research_loops += 1
        elif node == "writer":
            flush_research()
            pills.append({"icon": "✍️", "label": "Report", "color": DEEP_COLOR})
    flush_research()  # stream ended before the writer
    return pills


def flow_strip_html(route: str | None, steps: list[Mapping]) -> str:
    """One line of step pills joined by arrows, e.g. Router → Your record → 3 docs → Answer."""
    pills = flow_steps(route, steps)
    if not pills:
        return ""
    parts = [
        f'<span class="step" style="--c:{pill["color"]}">{pill["icon"]} {escape(pill["label"])}</span>'
        for pill in pills
    ]
    arrow = '<span class="arrow">→</span>'
    return f'<div class="sca-flow" aria-label="Steps that ran">{arrow.join(parts)}</div>'


def admin_hint_html() -> str:
    """Shown to admins: the chat has no student record, the Students page does."""
    return (
        '<div class="sca-hint"><span>🗂️</span><span>Admin chat answers college questions only. '
        "Personal records (fees, books, attendance, exams) live in the <b>Students</b> page.</span></div>"
    )


# ---------- student profile ----------

def format_rupees(amount) -> str:
    """Indian digit grouping, e.g. ``₹1,23,456``."""
    try:
        value = int(round(float(amount)))
    except (TypeError, ValueError):
        return str(amount)
    digits = str(abs(value))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return ("-" if value < 0 else "") + "₹" + digits


# ---------- status levels: the same words and colours on every page ----------

LEVEL_COLORS = {"ok": OK_COLOR, "warn": WARN_COLOR, "bad": BAD_COLOR, "info": INFO_COLOR}
STAT_ICONS = {"Fees": "💳", "Books": "📚", "Attendance": "📅"}


def fee_level(status, due=0) -> str:
    """ok (paid) / warn (due, not late) / bad (overdue) for a fee status from ``effective_fee_status``."""
    status = str(status or "").lower()
    if status == "overdue":
        return "bad"
    try:
        owed = float(due or 0) > 0
    except (TypeError, ValueError):
        owed = False
    if status == "paid" or not owed:
        return "ok"
    return "warn"


def attendance_level(pct) -> str:
    """ok at or above the 75% minimum, bad below it, info when not recorded yet."""
    try:
        return "ok" if float(pct) >= LOW_ATTENDANCE else "bad"
    except (TypeError, ValueError):
        return "info"


def attendance_text(pct) -> str:
    """``82%`` / ``61.5%`` (one decimal only when it says something); a dash when not recorded."""
    try:
        value = float(pct)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def status_chips(record: Mapping | None) -> list[tuple[str, str, str]]:
    """(label, value, level) for fees, books and attendance; level is ok / warn / bad.

    The same rules as the dashboard KPIs and the admin tables, so the sidebar never disagrees with them.
    Every value says its status in words as well as in colour.
    """
    if not record:
        return []
    chips = []
    today = as_of_date(record)
    fees = record.get("fees") or {}
    if fees:
        status = effective_fee_status(fees, today)
        due = fees.get("due") or 0
        level = fee_level(status, due)
        if level == "ok":
            chips.append(("Fees", "Paid", "ok"))
        elif level == "bad":
            chips.append(("Fees", f"Overdue · {format_rupees(due)}", "bad"))
        else:
            chips.append(("Fees", f"Due · {format_rupees(due)}", "warn"))
    library = record.get("library") or {}
    if library:
        books = library.get("books_issued") or []
        n = len(books) if isinstance(books, (list, tuple)) else int(books or 0)
        late = sum(1 for b in books if isinstance(b, Mapping) and book_is_overdue(b, today)) if isinstance(books, (list, tuple)) else 0
        level = "bad" if (library.get("fine_due") or 0) or late else "ok"
        chips.append(("Books", f"{n} issued" + (f" · {late} overdue" if late else ""), level))
    attendance = record.get("attendance") or {}
    if attendance.get("overall_pct") is not None:
        pct = attendance["overall_pct"]
        level = attendance_level(pct)
        chips.append(("Attendance", attendance_text(pct) + (" · below 75%" if level == "bad" else ""), level))
    return chips


def profile_card_html(student: Mapping, record: Mapping | None = None) -> str:
    record = record or {}
    name = str(record.get("name") or student.get("name") or "Student")
    sid = str(student.get("student_id") or record.get("student_id") or "")
    branch = record.get("branch") or student.get("branch")
    year = record.get("year") or student.get("year")
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "S"
    meta = " · ".join(str(x) for x in (sid, branch, f"Year {year}" if year else None) if x)
    icons = STAT_ICONS
    rows = []
    for label, value, level in status_chips(record):
        bar = ""
        if label == "Attendance":
            # Progress bar with a marker at the 75% minimum.
            pct = max(0.0, min(100.0, float((record.get("attendance") or {}).get("overall_pct") or 0)))
            bar = f'<div class="bar"><span style="width:{pct:.0f}%"></span><i title="75% minimum"></i></div>'
        rows.append(
            f'<div class="row {level}"><span class="ic">{icons.get(label, "•")}</span>'
            f'<span class="k">{escape(label)}</span><span class="v">{escape(value)}</span>{bar}</div>'
        )
    rows_html = f'<div class="sca-stats">{"".join(rows)}</div>' if rows else ""
    return (
        f'<div class="sca-profile"><div class="top"><div class="avatar">{escape(initials)}</div><div>'
        f'<div class="name">{escape(name)}</div><div class="meta">{escape(meta)}</div></div></div>{rows_html}</div>'
    )


# ---------- accounts / roles ----------

def require_role(*roles: str) -> dict:
    """Return the signed-in user, or stop the page if nobody (or the wrong role) is signed in.

    app.py only registers pages a role may see; this is a second line of defence.
    """
    user = st.session_state.get("user")
    if not isinstance(user, dict) or user.get("role") not in roles:
        st.error("You do not have access to this page. Please sign in with the right account.", icon=":material/lock:")
        st.stop()
    return user


def role_badge_html(role: str | None) -> str:
    role = "admin" if role == "admin" else "student"
    icon = "🛡️" if role == "admin" else "🎓"
    return f'<span class="sca-role {role}">{icon} {role.title()}</span>'


TOTAL_SEMESTERS = 8


def _semester_of(record: Mapping | None) -> int | None:
    try:
        sem = int((record or {}).get("semester"))
    except (TypeError, ValueError):
        return None
    return sem if 1 <= sem <= TOTAL_SEMESTERS else None


def semester_row_html(record: Mapping | None) -> str:
    """Card row "Semester 3 of 8" with a progress bar (semesters already finished are filled)."""
    sem = _semester_of(record)
    if not sem:
        return ""
    pct = (sem - 1) / TOTAL_SEMESTERS * 100
    return (
        '<div class="row sem"><span class="ic" aria-hidden="true">🎓</span><span class="k">Semester</span>'
        f'<span class="v">{sem} of {TOTAL_SEMESTERS}</span>'
        f'<div class="bar"><span style="width:{pct:.0f}%"></span></div></div>'
    )


def semester_plan_safe(record: Mapping | None) -> list[dict]:
    """Per-semester plan from student_data, or [] if it is unavailable."""
    try:
        from smart_campus_assistant.student_data import semester_plan
    except Exception:  # noqa: BLE001
        return []
    try:
        return semester_plan(dict(record or {}))
    except Exception:  # noqa: BLE001
        return []


def remaining_semesters_html(plan: list[dict]) -> str:
    """List of the current and upcoming semesters with their published subjects."""
    items = []
    for row in plan:
        if row.get("status") == "done":
            continue
        current = row.get("status") == "current"
        tag = '<span class="now">Current</span>' if current else ""
        subjects = row.get("subjects") or []
        body = (
            "<ul>" + "".join(f"<li>{escape(str(s))}</li>" for s in subjects) + "</ul>"
            if subjects else '<div class="none">Subjects not published yet</div>'
        )
        note = f'<div class="note">ℹ️ {escape(str(row["note"]))}</div>' if row.get("note") else ""
        items.append(
            f'<div class="sem-item{" current" if current else ""}">'
            f'<div class="head"><span class="num">{int(row["semester"])}</span>'
            f'<span class="title">Semester {int(row["semester"])}</span>{tag}</div>{body}{note}</div>'
        )
    return f'<div class="sca-sems">{"".join(items)}</div>' if items else ""


def user_card_html(user: Mapping, record: Mapping | None = None) -> str:
    """Sidebar card for the signed-in account: name, email, role badge; students also get ID / branch / year."""
    record = record or {}
    is_student = user.get("role") == "student"
    name = str(user.get("name") or record.get("name") or "User")
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "U"
    meta = ""
    if is_student:
        year = record.get("year")
        bits = [str(x) for x in (user.get("student_id"), record.get("branch"), f"Year {year}" if year else None) if x]
        if bits:
            meta = f'<div class="meta">{escape(" · ".join(bits))}</div>'
    rows = []
    if is_student:
        semester_row = semester_row_html(record)
        if semester_row:
            rows.append(semester_row)
        for label, value, level in status_chips(record):
            rows.append(
                f'<div class="row {level}"><span class="ic" aria-hidden="true">{STAT_ICONS.get(label, "•")}</span>'
                f'<span class="k">{escape(label)}</span><span class="v">{escape(value)}</span></div>'
            )
    rows_html = f'<div class="sca-stats compact">{"".join(rows)}</div>' if rows else ""
    role_cls = "admin" if user.get("role") == "admin" else "student"
    return (
        f'<div class="sca-user"><div class="top"><div class="avatar {role_cls}">{escape(initials)}</div>'
        f'<div class="who"><div class="name">{escape(name)}</div>'
        f'<div class="email">{escape(str(user.get("email") or ""))}</div></div></div>'
        f'<div class="badges">{role_badge_html(user.get("role"))}</div>{meta}{rows_html}</div>'
    )


# ---------- student records (dashboard + admin) ----------

LOW_ATTENDANCE = 75.0
FEE_STATUS_LABELS = {
    "paid": "Paid", "partially_paid": "Partially paid", "unpaid": "Unpaid", "overdue": "Overdue", "due": "Due",
}


def effective_fee_status(fees: Mapping, today: date | None = None) -> str:
    """Status to display, by the backend's one rule (``student_data.fee_status``) so pages and the assistant agree."""
    try:
        from smart_campus_assistant.student_data import fee_status

        return fee_status(dict(fees or {}), today)
    except Exception:  # noqa: BLE001  (backend missing or stubbed: the stored status)
        status = str((fees or {}).get("status") or "").lower()
        if status == "partially_paid" and not (fees or {}).get("paid") and (fees or {}).get("due"):
            return "unpaid"
        return status


def all_records_safe() -> list[dict]:
    """Full record of every student (admin pages only); empty when the data is missing."""
    try:
        from smart_campus_assistant.student_data import all_records  # one pass, not one lookup per student

        return [dict(r) for r in all_records() if r.get("student_id")]
    except ImportError:  # older backend without all_records: fall back to per-student lookups
        pass
    except Exception:  # noqa: BLE001
        return []
    records = []
    for student in list_students_safe():
        record = get_student_safe(student["student_id"])
        if record:
            records.append(record)
    return records


def _to_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def as_of_date(record: Mapping | None) -> date:
    """The data's "today" (records carry ``as_of``); falls back to today's date in the college's time zone."""
    found = _to_date((record or {}).get("as_of"))
    if found:
        return found
    try:
        from smart_campus_assistant.config import local_today

        return local_today()
    except Exception:  # noqa: BLE001  (backend missing: the server's date)
        return date.today()


def fee_status_label(status) -> str:
    status = str(status or "").lower()
    return FEE_STATUS_LABELS.get(status, status.replace("_", " ").title() or "—")


def book_is_overdue(book: Mapping, today: date) -> bool:
    due = _to_date(book.get("due_date"))
    return str(book.get("status") or "").lower() == "overdue" or (due is not None and due < today)


def days_text(value, today: date) -> str:
    """``in 5 days`` / ``today`` / ``3 days ago``."""
    d = _to_date(value)
    if d is None:
        return "—"
    days = (d - today).days
    if days == 0:
        return "today"
    if days > 0:
        return f"in {days} day{'s' if days != 1 else ''}"
    return f"{-days} day{'s' if days != -1 else ''} ago"


def nice_date(value) -> str:
    d = _to_date(value)
    return d.strftime("%d %b %Y") if d else (str(value) if value else "—")


def student_flags(record: Mapping) -> dict:
    """Numbers and booleans the admin pages count and filter on."""
    today = as_of_date(record)
    fees = record.get("fees") or {}
    library = record.get("library") or {}
    attendance = (record.get("attendance") or {}).get("overall_pct")
    books = library.get("books_issued") or []
    return {
        "fee_status": effective_fee_status(fees, today),
        "fees_due": float(fees.get("due") or 0),
        "fees_paid": float(fees.get("paid") or 0),
        "overdue_books": sum(1 for b in books if isinstance(b, Mapping) and book_is_overdue(b, today)),
        "fine": float(library.get("fine_due") or 0),
        "attendance": float(attendance) if attendance is not None else None,
        "low_attendance": attendance is not None and float(attendance) < LOW_ATTENDANCE,
        "backlogs": int((record.get("results") or {}).get("backlogs") or 0),
    }


def student_header_html(record: Mapping) -> str:
    name = str(record.get("name") or "Student")
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "S"
    branch = record.get("branch") or ""
    branch_name = get_branches().get(branch, branch)
    facts = [
        ("ID", record.get("student_id")),
        ("Branch", f"{branch} · {branch_name}" if branch else None),
        ("Year", record.get("year")),
        ("Semester", record.get("semester")),
    ]
    facts_html = "".join(
        f'<span class="fact"><span class="k">{escape(k)}</span> {escape(str(v))}</span>'
        for k, v in facts
        if v not in (None, "")
    )
    as_of = record.get("as_of")
    eyebrow = "Student dashboard" + (f" · as of {nice_date(as_of)}" if as_of else "")
    return (
        f'<div class="sca-student-head"><div class="avatar">{escape(initials)}</div><div class="body">'
        f'<div class="eyebrow">{escape(eyebrow)}</div>'
        f'<h2>{escape(name)}</h2><div class="facts">{facts_html}</div></div></div>'
    )


def student_kpis_html(record: Mapping) -> str:
    today = as_of_date(record)
    fees = record.get("fees") or {}
    books = (record.get("library") or {}).get("books_issued") or []
    flags = student_flags(record)
    status = flags["fee_status"]
    fee_accent = LEVEL_COLORS[fee_level(status, flags["fees_due"])]
    if flags["fees_due"]:
        due = fees.get("due_date")
        when = "was due" if status == "overdue" else "due"
        fee_sub = f"{fee_status_label(status or 'due')} · {when} {nice_date(due)} ({days_text(due, today)})"
    else:
        fee_sub = f"{fee_status_label(status or 'paid')} · nothing due"
    overdue = flags["overdue_books"]
    book_sub = f"Fine {format_rupees(flags['fine'])}" + (f" · {overdue} overdue" if overdue else "")
    book_accent = BAD_COLOR if (flags["fine"] or overdue) else OK_COLOR
    pct = flags["attendance"]
    if pct is None:
        att_value, att_sub, att_accent = "—", "Not recorded yet", INFO_COLOR
    else:
        att_value = attendance_text(pct)
        att_sub = "Below the 75% minimum" if flags["low_attendance"] else "Meets the 75% minimum"
        att_accent = LEVEL_COLORS[attendance_level(pct)]
    results = record.get("results") or {}
    cgpa = results.get("cgpa")
    if cgpa is None:
        cg_value, cg_sub = "Not available yet", "No semester completed"
    else:
        n = flags["backlogs"]
        cg_value = f"{float(cgpa):.2f}"
        cg_sub = f"Last SGPA {results.get('last_sgpa', '—')} · {n} backlog{'s' if n != 1 else ''}"
    cards = [
        kpi_card_html("Fees due", format_rupees(flags["fees_due"]), fee_sub, fee_accent),
        kpi_card_html("Books issued", str(len(books)), book_sub, book_accent),
        kpi_card_html("Attendance", att_value, att_sub, att_accent),
        kpi_card_html("CGPA", cg_value, cg_sub, DEEP_COLOR),
    ]
    return kpi_row_html(cards)


def _list_html(items: list[str]) -> str:
    return f'<div class="sca-list">{"".join(items)}</div>'


def _fees_section(record: Mapping) -> None:
    fees = record.get("fees") or {}
    parts = [
        str(fees.get("semester") or ""),
        f"Total {format_rupees(fees.get('total', 0))}",
        f"paid {format_rupees(fees.get('paid', 0))}",
        f"due {format_rupees(fees.get('due', 0))}",
    ]
    section_header("Fees", " · ".join(p for p in parts if p))
    scholarship = fees.get("scholarship")
    if scholarship:
        st.caption(f"Scholarship: {scholarship.get('name')} · {format_rupees(scholarship.get('amount'))}")
    rows = [
        {"Date": nice_date(t.get("date")), "Amount": format_rupees(t.get("amount")), "Mode": t.get("mode", ""),
         "Receipt": t.get("receipt_no", "")}
        for t in fees.get("transactions") or []
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        empty_state("🧾", "No payments recorded this semester", "Receipts appear here after each payment.",
                    compact=True)


def _books_section(record: Mapping) -> None:
    today = as_of_date(record)
    library = record.get("library") or {}
    books = [b for b in library.get("books_issued") or [] if isinstance(b, Mapping)]
    section_header("Library books", f"{len(books)} issued · fine due {format_rupees(library.get('fine_due', 0))}")
    if not books:
        empty_state("📚", "No books issued", "Books you borrow from the library are listed here with their due dates.",
                    compact=True)
        return
    items = []
    for b in sorted(books, key=lambda b: str(b.get("due_date"))):
        late = book_is_overdue(b, today)
        if late:
            tag = '<span class="tag bad">Overdue</span>'
        else:
            tag = f'<span class="tag">Due {escape(days_text(b.get("due_date"), today))}</span>'
        sub = " · ".join(str(x) for x in (b.get("author"), b.get("book_id")) if x)
        if b.get("issue_date"):
            sub += f" · issued {nice_date(b.get('issue_date'))}"
        items.append(
            f'<div class="item{" bad" if late else ""}"><div class="cal">📘</div><div class="main">'
            f'<div class="title">{escape(str(b.get("title") or "Book"))}</div><div class="sub">{escape(sub)}</div></div>'
            f'<div class="side"><div class="date">{escape(nice_date(b.get("due_date")))}</div>{tag}</div></div>'
        )
    _md(_list_html(items))


def _attendance_section(record: Mapping) -> None:
    attendance = record.get("attendance") or {}
    courses = attendance.get("courses") or []
    overall = attendance.get("overall_pct")
    if overall is not None:
        caption = f"Overall {attendance_text(overall)} · minimum {LOW_ATTENDANCE:.0f}%"
    else:
        caption = "Not recorded yet"
    section_header("Attendance", caption)
    courses = [c for c in courses if isinstance(c, Mapping)]
    recorded = [c for c in courses if c.get("pct") is not None]
    pending = [c for c in courses if c.get("pct") is None]
    if pending:
        names = ", ".join(f"{c.get('code', '')} {c.get('name', '')}".strip() for c in pending)
        st.caption(f"Not recorded yet: {names}")
    if not recorded:
        return
    df = pd.DataFrame(
        [
            {
                "Course": f"{c.get('code', '')} {c.get('name', '')}".strip(),
                "Attendance (%)": float(c["pct"]),
                "Status": "Below 75%" if float(c["pct"]) < LOW_ATTENDANCE else "OK",
            }
            for c in recorded
        ]
    )
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("Attendance (%):Q", scale=alt.Scale(domain=[0, 100]), title=None),
        # Long course names are cut with "…" (full name in the tooltip) so the bars keep room on phones.
        y=alt.Y("Course:N", sort=None, title=None, axis=alt.Axis(labelLimit=170, labelOverlap=False)),
        color=alt.Color(
            "Status:N",
            scale=alt.Scale(domain=["OK", "Below 75%"], range=[SIMPLE_COLOR, BASELINE_COLOR]),
            legend=alt.Legend(orient="bottom", title=None),
        ),
        tooltip=["Course", "Attendance (%)", "Status"],
    )
    rule = (
        alt.Chart(pd.DataFrame({"x": [LOW_ATTENDANCE]}))
        .mark_rule(strokeDash=[4, 3], strokeWidth=2, color="#888")
        .encode(x="x:Q")
    )
    # One readable row per course; the left padding stops long course names from being clipped
    # (Vega measures labels before the web font has loaded).
    chart = (bars + rule).properties(height=max(140, 34 * len(df) + 70), padding={"left": 36, "right": 8, "top": 4, "bottom": 4})
    st.altair_chart(chart, width="stretch")


def _exams_section(record: Mapping) -> None:
    today = as_of_date(record)
    upcoming = sorted(
        (e for e in (record.get("exams") or {}).get("upcoming") or [] if isinstance(e, Mapping)),
        key=lambda e: (str(e.get("date")), str(e.get("time"))),
    )
    section_header("Upcoming exams", f"{len(upcoming)} scheduled" if upcoming else "None scheduled")
    if not upcoming:
        empty_state("🗓️", "No exams scheduled", "The exam timetable shows up here once it is published.", compact=True)
        return
    items = []
    for e in upcoming:
        d = _to_date(e.get("date"))
        cal = f'<span class="d">{d.strftime("%d")}</span><span class="m">{d.strftime("%b")}</span>' if d else "📝"
        title = " · ".join(str(x) for x in (e.get("course_code"), e.get("course_name") or e.get("subject")) if x)
        sub = " · ".join(str(x) for x in (e.get("type"), e.get("time"), f"Room {e['room']}" if e.get("room") else None) if x)
        items.append(
            f'<div class="item"><div class="cal">{cal}</div><div class="main"><div class="title">{escape(title or "Exam")}</div>'
            f'<div class="sub">{escape(sub)}</div></div>'
            f'<div class="side"><span class="tag">{escape(days_text(e.get("date"), today))}</span></div></div>'
        )
    _md(_list_html(items))


def _hostel_section(record: Mapping) -> None:
    hostel = record.get("hostel")
    section_header("Hostel")
    if hostel and str(hostel.get("room") or "").lower() == "to be allotted":
        # New admissions who chose hostel get a room later.
        title, sub, icon = "Room to be allotted", "Hostel requested; the warden allots rooms before term starts", "🏠"
    elif hostel:
        room = " · ".join(str(x) for x in (f"Block {hostel['block']}" if hostel.get("block") else None,
                                          f"Room {hostel['room']}" if hostel.get("room") else None) if x)
        title, sub, icon = room or "Hostel allotted", "Hostel room for this academic year", "🏠"
    else:
        title, sub, icon = "Day scholar", "No hostel room allotted", "🚌"
    _md(_list_html([
        f'<div class="item"><div class="cal">{icon}</div><div class="main"><div class="title">{escape(title)}</div>'
        f'<div class="sub">{escape(sub)}</div></div></div>'
    ]))


def render_student_dashboard(record: Mapping) -> None:
    """Read-only view of ONE student's record (used by "My dashboard" and the admin Students page)."""
    _md(student_header_html(record))
    _md(student_kpis_html(record))
    left, right = st.columns(2, gap="large")
    with left:
        _fees_section(record)
        _books_section(record)
        _hostel_section(record)
    with right:
        _attendance_section(record)
        _exams_section(record)


# ---------- admin notices ----------

NOTICE_ICONS = {"exams": "📝", "fees": "💳", "hostel": "🏠", "library": "📚", "general": "📢"}

# The notice card styles now live in ui.theme (with the other cards); kept so old imports still work.
NOTICE_CSS = ""


def notice_card_html(notice: Mapping, show_body: bool = True, max_body: int | None = None) -> str:
    """One notice as a card (all fields escaped); the left stripe colour follows the category."""
    category = str(notice.get("category") or "general")
    known = category if category in NOTICE_ICONS else "general"
    icon = NOTICE_ICONS[known]
    branches = list(notice.get("branches") or [])
    for_text = "All branches" if not branches or "ALL" in branches else ", ".join(str(b) for b in branches)
    published = nice_date(notice.get("published_at")) if notice.get("published_at") else "not published yet"
    meta = f"{notice.get('id', '')} · {published} · {category.title()} · {for_text}"
    body = str(notice.get("body") or "")
    if max_body and len(body) > max_body:
        body = body[:max_body].rsplit(" ", 1)[0] + "…"
    body_html = f'<div class="body">{escape(body)}</div>' if show_body and body else ""
    return (
        f'<div class="sca-notice cat-{known}"><div class="head"><span aria-hidden="true">{icon}</span>'
        f'<span class="title">{escape(str(notice.get("title") or ""))}</span></div>'
        f'<div class="meta">{escape(meta)}</div>{body_html}</div>'
    )


def notice_cards_html(notices: Iterable[Mapping], max_body: int | None = None) -> str:
    cards = "".join(notice_card_html(n, max_body=max_body) for n in notices)
    return f'{NOTICE_CSS}<div class="sca-notices">{cards}</div>'


def list_notices_safe(branch: str | None = None, limit: int | None = None) -> list[dict]:
    """Notices from the backend, or [] if it cannot be loaded."""
    try:
        from smart_campus_assistant import notices

        return notices.list_notices(branch=branch, limit=limit)
    except Exception:  # noqa: BLE001
        return []


# ---------- email notifications ----------

def notify_students(action: str, *args):
    """Queue student emails with ``notifications.<action>(*args)`` and send them in the background.

    Returns what the action returned (count or bool), or None when it failed. Emails must never
    break the admin action that triggered them, so every error is swallowed.
    """
    try:
        from smart_campus_assistant import notifications

        result = getattr(notifications, action)(*args)
        if result:
            notifications.deliver_in_background()
        return result
    except Exception:  # noqa: BLE001
        return None


def email_note(what: str) -> str:
    """" {what} queued for email." when SMTP is set up, else a plain note that it was only recorded.

    "Queued", not "emailed": the email is sent in the background and may still be retried.
    """
    try:
        from smart_campus_assistant import mailer

        configured = mailer.email_configured()
    except Exception:  # noqa: BLE001
        configured = False
    if configured:
        return f" {what} queued for email (status on the Overview page)."
    return f" {what} recorded in the outbox but not sent (email/SMTP is not configured)."


def local_stamp(stamp) -> str:
    """A stored UTC timestamp as "03 Nov 2026, 15:45 IST" in the college's time zone (the text itself otherwise)."""
    try:
        from smart_campus_assistant.config import local_time

        moment = local_time(stamp)
    except Exception:  # noqa: BLE001
        moment = None
    if moment is None:
        return str(stamp or "")
    return f"{moment.strftime('%d %b %Y, %H:%M')} {moment.tzname() or ''}".strip()
