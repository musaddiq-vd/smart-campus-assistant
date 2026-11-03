"""Reusable render helpers and number formatting.

``*_html`` functions return HTML strings (pure, easy to test); the matching
functions without the suffix render them with ``st.markdown``.
"""

from __future__ import annotations

import math
import os
from datetime import date
from html import escape
from pathlib import Path
from typing import Iterable, Mapping

import altair as alt
import pandas as pd
import streamlit as st

from ui.theme import BASELINE_COLOR, DEEP_COLOR, PERSONAL_COLOR, ROUTE_ICONS, ROUTER_COLOR, SIMPLE_COLOR

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
    """Demo (fake) mode is automatic: ``SCA_DEMO_MODE=1`` forces it, otherwise ON only without AWS credentials."""
    flag = os.getenv("SCA_DEMO_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return not has_aws_credentials()


def _is_placeholder(value: str | None) -> bool:
    return not value or value.strip().lower().startswith("your_")


def has_aws_credentials() -> bool:
    """Local check only (no network): env vars, the project .env, or an AWS profile on disk."""
    env = dict(os.environ)
    try:
        from dotenv import dotenv_values

        env = {**{k: v for k, v in dotenv_values(PROJECT_ROOT / ".env").items() if v}, **env}
    except Exception:
        pass
    if not _is_placeholder(env.get("AWS_ACCESS_KEY_ID")) and not _is_placeholder(env.get("AWS_SECRET_ACCESS_KEY")):
        return True
    if env.get("AWS_PROFILE"):
        return True
    aws_dir = Path(env.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws" / "credentials"))
    return aws_dir.is_file() and "aws_access_key_id" in aws_dir.read_text(errors="ignore").lower()


# ---------- HTML builders ----------

def _md(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)


def hero_html(eyebrow: str, title: str, subtitle: str, chips: list[str] | None = None) -> str:
    chips_html = f'<div class="chips">{"".join(chips)}</div>' if chips else ""
    return (
        f'<div class="sca-hero"><div class="eyebrow">{escape(eyebrow)}</div>'
        f"<h1>{escape(title)}</h1><p>{escape(subtitle)}</p>{chips_html}</div>"
    )


def chip_html(text: str, kind: str = "") -> str:
    dot = '<span class="dot"></span>' if kind else ""
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


def citation_card_html(citation: Mapping, number: int | None = None) -> str:
    source = str(citation.get("source") or "unknown source")
    college = citation.get("college") or (source.split("/")[0] if "/" in source else None)
    page = citation.get("page")
    snippet = str(citation.get("snippet") or "").strip()
    num = f'<span class="num">[{number}]</span>' if number is not None else ""
    page_html = f'<span class="page">Page {escape(str(page))}</span>' if page not in (None, "") else ""
    snippet_html = f'<div class="snippet">{escape(snippet)}</div>' if snippet else ""
    return (
        f'<div class="sca-cite"><div class="head"><span>{num} <span class="college">{escape(college_name(college))}</span></span>'
        f'{page_html}</div><div class="file">{escape(source.split("/")[-1])}</div>{snippet_html}</div>'
    )


def citation_cards_html(citations: Iterable[Mapping]) -> str:
    cards = "".join(citation_card_html(c, i) for i, c in enumerate(citations, start=1))
    return f'<div class="sca-cites">{cards}</div>'


def citation_card(citation: Mapping, number: int | None = None) -> None:
    _md(citation_card_html(citation, number))


def kpi_card_html(label: str, value: str, sub: str = "", accent: str = ROUTER_COLOR, unit: str = "") -> str:
    unit_html = f'<span class="unit">{escape(unit)}</span>' if unit else ""
    return (
        f'<div class="sca-kpi" style="--accent:{accent}"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}{unit_html}</div><div class="sub">{escape(sub)}</div></div>'
    )


def kpi_card(label: str, value: str, sub: str = "", accent: str = ROUTER_COLOR, unit: str = "") -> None:
    _md(kpi_card_html(label, value, sub, accent, unit))


def empty_state_html(icon: str, title: str, text: str) -> str:
    return (
        f'<div class="sca-empty"><div class="icon">{escape(icon)}</div>'
        f"<h3>{escape(title)}</h3><p>{escape(text)}</p></div>"
    )


def empty_state(icon: str, title: str, text: str) -> None:
    _md(empty_state_html(icon, title, text))


def section_header(title: str, caption: str = "") -> None:
    cap = f"<p>{escape(caption)}</p>" if caption else ""
    _md(f'<div class="sca-section"><h3>{escape(title)}</h3>{cap}</div>')


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


def status_chips(record: Mapping | None) -> list[tuple[str, str, str]]:
    """(label, value, level) for fees, books and attendance; level is ok / warn / bad."""
    if not record:
        return []
    chips = []
    fees = record.get("fees") or {}
    if fees:
        status = str(fees.get("status") or "").lower()
        due = fees.get("due") or 0
        if status == "overdue":
            chips.append(("Fees", f"Overdue {format_rupees(due)}", "bad"))
        elif status == "paid" or (not status and not due):
            chips.append(("Fees", "Paid", "ok"))
        else:
            chips.append(("Fees", f"Due {format_rupees(due)}" if due else (status.title() or "—"), "warn"))
    library = record.get("library") or {}
    if library:
        books = library.get("books_issued") or []
        n = len(books) if isinstance(books, (list, tuple)) else int(books or 0)
        level = "bad" if (library.get("fine_due") or 0) else "ok"
        chips.append(("Books", f"{n} issued", level))
    attendance = record.get("attendance") or {}
    if attendance.get("overall_pct") is not None:
        pct = float(attendance["overall_pct"])
        level = "ok" if pct >= 75 else ("warn" if pct >= 65 else "bad")
        chips.append(("Attendance", f"{pct:.0f}%", level))
    return chips


def profile_card_html(student: Mapping, record: Mapping | None = None) -> str:
    record = record or {}
    name = str(record.get("name") or student.get("name") or "Student")
    sid = str(student.get("student_id") or record.get("student_id") or "")
    branch = record.get("branch") or student.get("branch")
    year = record.get("year") or student.get("year")
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "S"
    meta = " · ".join(str(x) for x in (sid, branch, f"Year {year}" if year else None) if x)
    icons = {"Fees": "💳", "Books": "📚", "Attendance": "📅"}
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
        for label, value, level in status_chips(record):
            rows.append(f'<div class="row {level}"><span class="k">{escape(label)}</span><span class="v">{escape(value)}</span></div>')
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
FEE_STATUS_LABELS = {"paid": "Paid", "partially_paid": "Partially paid", "overdue": "Overdue", "due": "Due"}


def all_records_safe() -> list[dict]:
    """Full record of every student (admin pages only); empty when the data is missing."""
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
    """The data's "today" (records carry ``as_of``); falls back to the real date."""
    return _to_date((record or {}).get("as_of")) or date.today()


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
        "fee_status": str(fees.get("status") or "").lower(),
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
    if status == "overdue":
        fee_accent = BASELINE_COLOR
    else:
        fee_accent = PERSONAL_COLOR if flags["fees_due"] else SIMPLE_COLOR
    if flags["fees_due"]:
        due = fees.get("due_date")
        fee_sub = f"{fee_status_label(status)} · due {nice_date(due)} ({days_text(due, today)})"
    else:
        fee_sub = f"{fee_status_label(status or 'paid')} · nothing due"
    overdue = flags["overdue_books"]
    book_sub = f"Fine {format_rupees(flags['fine'])}" + (f" · {overdue} overdue" if overdue else "")
    book_accent = BASELINE_COLOR if (flags["fine"] or overdue) else SIMPLE_COLOR
    pct = flags["attendance"]
    if pct is None:
        att_value, att_sub, att_accent = "—", "No attendance yet", ROUTER_COLOR
    else:
        att_value = f"{pct:.1f}%"
        att_sub = "Below the 75% minimum" if flags["low_attendance"] else "Above the 75% minimum"
        att_accent = BASELINE_COLOR if flags["low_attendance"] else SIMPLE_COLOR
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
    return f'<div class="sca-kpis">{"".join(cards)}</div>'


def _list_html(items: list[str]) -> str:
    return f'<div class="sca-list">{"".join(items)}</div>'


def _fees_section(record: Mapping) -> None:
    fees = record.get("fees") or {}
    section_header(
        "Fees",
        f"{fees.get('semester', '')} · total {format_rupees(fees.get('total', 0))}, "
        f"paid {format_rupees(fees.get('paid', 0))}, due {format_rupees(fees.get('due', 0))}".strip(" ·"),
    )
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
        st.caption("No payments recorded this semester.")


def _books_section(record: Mapping) -> None:
    today = as_of_date(record)
    library = record.get("library") or {}
    books = [b for b in library.get("books_issued") or [] if isinstance(b, Mapping)]
    section_header("Library books", f"{len(books)} issued · fine due {format_rupees(library.get('fine_due', 0))}")
    if not books:
        st.caption("No books issued.")
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
    caption = f"Overall {overall}% · minimum {LOW_ATTENDANCE:.0f}%" if overall is not None else "No attendance recorded yet"
    section_header("Attendance", caption)
    if not courses:
        return
    df = pd.DataFrame(
        [
            {
                "Course": f"{c.get('code', '')} {c.get('name', '')}".strip(),
                "Attendance (%)": float(c.get("pct") or 0),
                "Status": "Below 75%" if float(c.get("pct") or 0) < LOW_ATTENDANCE else "OK",
            }
            for c in courses
        ]
    )
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("Attendance (%):Q", scale=alt.Scale(domain=[0, 100]), title=None),
        y=alt.Y("Course:N", sort=None, title=None, axis=alt.Axis(labelLimit=200)),
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
    st.altair_chart((bars + rule).properties(height=max(140, 36 * len(df))), width="stretch")


def _exams_section(record: Mapping) -> None:
    today = as_of_date(record)
    upcoming = sorted(
        (e for e in (record.get("exams") or {}).get("upcoming") or [] if isinstance(e, Mapping)),
        key=lambda e: (str(e.get("date")), str(e.get("time"))),
    )
    section_header("Upcoming exams", f"{len(upcoming)} scheduled" if upcoming else "None scheduled")
    if not upcoming:
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
    if hostel:
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

NOTICE_CSS = """
<style>
.sca-notices { display: flex; flex-direction: column; gap: .6rem; margin: .5rem 0 1rem 0; }
.sca-notice {
  border: 1px solid var(--sca-card-border, rgba(128,128,128,.25)); border-radius: var(--sca-radius, 14px);
  background: var(--sca-card-bg, transparent); padding: .8rem 1rem;
  border-left: 4px solid var(--sca-brand, #5B5BD6);
}
.sca-notice .head { display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; }
.sca-notice .title { font-weight: 650; font-size: 1rem; }
.sca-notice .meta { font-size: .78rem; color: var(--sca-muted, gray); margin-top: .15rem; }
.sca-notice .body { font-size: .9rem; margin-top: .4rem; white-space: pre-wrap; }
</style>
"""


def notice_card_html(notice: Mapping, show_body: bool = True, max_body: int | None = None) -> str:
    """One notice as a card (all fields escaped)."""
    category = str(notice.get("category") or "general")
    icon = NOTICE_ICONS.get(category, "📢")
    branches = list(notice.get("branches") or [])
    for_text = "All branches" if not branches or "ALL" in branches else ", ".join(str(b) for b in branches)
    meta = f"{notice.get('id', '')} · {nice_date(notice.get('published_at'))} · {category.title()} · {for_text}"
    body = str(notice.get("body") or "")
    if max_body and len(body) > max_body:
        body = body[:max_body].rsplit(" ", 1)[0] + "…"
    body_html = f'<div class="body">{escape(body)}</div>' if show_body and body else ""
    return (
        f'<div class="sca-notice"><div class="head"><span>{icon}</span>'
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
