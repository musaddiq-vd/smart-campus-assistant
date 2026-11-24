"""The shared UI patterns: HTML builders, status levels, theme CSS and one feedback message per action."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from smart_campus_assistant import db, notices  # noqa: E402
from smart_campus_assistant import support_requests as sr  # noqa: E402
from tests.test_ui_smoke import ACCOUNTS, RECORDS, all_text, as_admin, backend, start  # noqa: E402,F401
from ui import components as c  # noqa: E402
from ui import theme  # noqa: E402

XSS = '<script>alert("x")</script>'


# ---------------------------------------------------------------- HTML builders

def test_empty_state_escapes_and_has_a_compact_variant():
    big = c.empty_state_html("📭", XSS, XSS)
    assert "<script>" not in big and 'class="sca-empty"' in big and "<h3>" in big
    small = c.empty_state_html("📭", "No books issued", "Borrowed books show here.", compact=True)
    assert 'class="sca-empty compact"' in small and "No books issued" in small and "<h3>" not in small
    assert 'class="text"' not in c.empty_state_html("📭", "Only a title", "", compact=True)


def test_callout_facts_and_demo_code_escape_everything():
    note = c.callout_html(XSS, icon="🧪", kind="nonsense", title=XSS)
    assert "<script>" not in note and 'class="sca-callout info"' in note  # unknown kind falls back
    facts = c.facts_html([("Name", XSS), ("Email", ""), ("Student ID", None), ("Role", "student")],
                         extra_class="sca-req-facts")
    assert "<script>" not in facts and facts.count('class="fact"') == 2
    assert facts.startswith('<div class="sca-facts sca-req-facts">')
    code = c.demo_code_html("<b>123456</b>")
    assert 'class="sca-demo-code"' in code and "<b>" not in code and "&lt;b&gt;123456" in code


def test_chip_kinds_are_whitelisted():
    assert c.chip_html("3 waiting", "warn") == (
        '<span class="sca-chip warn"><span class="dot" aria-hidden="true"></span>3 waiting</span>'
    )
    assert c.chip_html("Total") == '<span class="sca-chip ">Total</span>'
    odd = c.chip_html("x", '" onmouseover="alert(1)')
    assert "onmouseover" not in odd and 'class="sca-chip info"' in odd


def test_kpi_row_and_card_escape_the_accent():
    card = c.kpi_card_html("Fees due", "₹50,000", "Due soon", accent='red;" onclick="x')
    assert 'onclick="x' not in card and "&quot;" in card
    assert c.kpi_row_html([card, card]).startswith('<div class="sca-kpis">')


def test_notice_card_category_class_and_draft_date():
    card = c.notice_card_html({"id": "Draft", "title": "T", "body": "B", "category": "exams", "published_at": None})
    assert 'class="sca-notice cat-exams"' in card and "not published yet" in card and "—" not in card
    odd = c.notice_card_html({"title": "T", "category": '"><i>', "published_at": "2026-11-02"})
    assert 'class="sca-notice cat-general"' in odd and "<i>" not in odd and "02 Nov 2026" in odd


# ---------------------------------------------------------------- one status rule everywhere

@pytest.mark.parametrize(("status", "due", "level"), [
    ("paid", 0, "ok"), ("partially_paid", 50000, "warn"), ("unpaid", 140000, "warn"),
    ("overdue", 140000, "bad"), ("", 0, "ok"), ("due", "oops", "ok"),
])
def test_fee_level(status, due, level):
    assert c.fee_level(status, due) == level


def test_attendance_level_and_text():
    assert [c.attendance_level(p) for p in (82, 75, 74.9, None)] == ["ok", "ok", "bad", "info"]
    assert [c.attendance_text(p) for p in (82, 82.0, 61.5, None)] == ["82%", "82%", "61.5%", "—"]


def test_sidebar_rows_agree_with_the_dashboard_kpis():
    aarav = RECORDS["XYZ24CSE001"]  # 50,000 due (not late), 1 overdue book, 82% attendance
    chips = {label: (value, level) for label, value, level in c.status_chips(aarav)}
    assert chips["Fees"] == ("Due · ₹50,000", "warn")
    assert chips["Books"] == ("2 issued · 1 overdue", "bad")  # an overdue book is a problem even without a fine
    assert chips["Attendance"] == ("82%", "ok")
    kpis = c.student_kpis_html(aarav)
    assert f"--accent:{theme.WARN_COLOR}" in kpis  # the Fees KPI is amber too (not green)
    assert "82%" in kpis and "Meets the 75% minimum" in kpis
    diya = {label: (value, level) for label, value, level in c.status_chips(RECORDS["XYZ26ECE009"])}
    assert diya["Fees"] == ("Overdue · ₹1,40,000", "bad")
    assert diya["Attendance"] == ("61.5% · below 75%", "bad")  # said in words, not only in colour


def test_user_card_rows_have_icons_and_escape():
    card = c.user_card_html({"name": XSS, "email": XSS, "role": "student", "student_id": "XYZ24CSE001"},
                            RECORDS["XYZ24CSE001"])
    assert "<script>" not in card and card.count('class="ic"') == 4  # semester, fees, books, attendance
    admin = c.user_card_html({"name": "XYZ Admin Office", "email": "admin@xyz.edu.example", "role": "admin"})
    assert 'class="avatar admin"' in admin and "sca-stats" not in admin


# ---------------------------------------------------------------- theme CSS

def test_theme_css_works_in_both_themes_and_on_phones():
    css = theme._CSS
    # Text and surfaces come from CSS variables / currentColor, never a fixed light-theme colour.
    assert not re.search(r"(?<![-\w])(color|background|background-color)\s*:\s*#", css)
    assert not re.search(r"background(-color)?\s*:\s*(white|#fff)", css, re.IGNORECASE)
    for token in ("--sca-card-bg", "--sca-muted", "--sca-ok-fg", "--sca-warn-fg", "--sca-bad-fg", "--sca-info-fg",
                  "--sca-space-4", "--sca-fs-base", "--sca-radius"):
        assert f"{token}:" in css
    assert "@media (max-width: 640px)" in css and "@media (max-width: 420px)" in css
    assert ":focus-visible" in css and "prefers-reduced-motion" in css
    # Every class the pages and the other agents' pages use still exists.
    for cls in (".sca-hero", ".sca-kpi", ".sca-empty", ".sca-empty.compact", ".sca-chip", ".sca-notice",
                ".sca-user", ".sca-demo-code", ".sca-callout", ".sca-facts", ".pay-receipt", ".rec-audit",
                ".sca-cite", ".sca-flow", ".sca-hint", ".sca-profile", ".sca-timeline", ".st-key-login_card"):
        assert cls in css, cls
    assert c.NOTICE_CSS == ""  # notice styles moved into the theme


def test_status_colours_are_the_palette():
    assert (c.LEVEL_COLORS["ok"], c.LEVEL_COLORS["warn"], c.LEVEL_COLORS["bad"]) == (
        theme.SIMPLE_COLOR, theme.PERSONAL_COLOR, theme.BASELINE_COLOR)


# ---------------------------------------------------------------- one feedback message per action

@pytest.fixture
def notices_file(tmp_path, monkeypatch):
    path = tmp_path / "notices.json"
    path.write_text('{"notices": []}', encoding="utf-8")
    monkeypatch.setenv("SCA_NOTICES_FILE", str(path))
    monkeypatch.setattr(notices, "index_notice", lambda n: (True, "Indexed for search."))
    monkeypatch.setattr(notices, "unindex_notice", lambda i: (True, "Removed from search."))
    return path


def _page(name: str, user: dict) -> AppTest:
    at = AppTest.from_file(str(ROOT / "ui" / name), default_timeout=30)
    at.session_state["user"] = dict(user)
    return at.run()


def _feedback(at: AppTest) -> list[str]:
    return [str(e.value) for kind in ("success", "info", "warning", "error") for e in getattr(at, kind)]


def test_notices_page_shows_one_message_per_action_and_no_toast(backend, notices_file):  # noqa: F811
    at = _page("admin_notices.py", ACCOUNTS["admin@xyz.edu.example"][1])
    assert not at.exception
    assert "Nothing to preview yet" in all_text(at) and "No notices yet" in all_text(at)
    at.text_input(key="notice_title").input("Mid-sem exam rescheduled")
    at.text_area(key="notice_body").input("The ME mid-sem exam is moved to 2026-11-20.")
    at.button(key="notice_publish").click().run()
    assert not at.exception
    assert len(_feedback(at)) == 1 and "Published" in at.success[0].value
    assert not at.toast  # before: a toast AND an inline alert for the same publish
    at.run()  # the message is shown once only
    assert _feedback(at) == []
    nid = notices.list_notices()[0]["id"]
    at.button(key=f"notice_del_{nid}").click().run()
    assert len(at.warning) == 1  # the confirm prompt
    at.button(key=f"notice_yes_{nid}").click().run()
    assert len(_feedback(at)) == 1 and "Deleted" in at.success[0].value and not at.toast


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    target = tmp_path / "pages.db"
    monkeypatch.setattr(db, "db_path", lambda path=None: Path(path) if path else target)
    return target


def test_request_pages_show_one_message_and_no_toast(app_db):
    student = {"email": "aarav@xyz.edu.example", "role": "student", "name": "Aarav Sharma",
               "student_id": "XYZ24CSE001"}
    at = _page("student_requests.py", student)
    at.text_input(key="req_new_subject").set_value("Hostel fan broken")
    at.text_area(key="req_new_body").set_value("The fan in room A-214 has not worked since Monday.")
    next(b for b in at.button if b.label == "Send request").click().run()
    assert not at.exception
    assert len(_feedback(at)) == 1 and "sent to the admin office" in at.success[0].value and not at.toast
    request = sr.list_requests(student_id="XYZ24CSE001", path=app_db)[0]
    admin = _page("admin_requests.py", ACCOUNTS["admin@xyz.edu.example"][1])
    admin = admin.button(key=f"req_admin_open_{request['id']}").click().run()
    next(b for b in admin.button if b.label == "Mark resolved").click().run()
    assert len(_feedback(admin)) == 1 and "resolved" in admin.success[0].value and not admin.toast


def test_flash_helpers_dedupe_and_accept_one_pair(monkeypatch):
    shown = []
    state: dict = {}
    monkeypatch.setattr(c.st, "session_state", state)
    monkeypatch.setattr(c, "alert", lambda kind, text, icon=None: shown.append((kind, text)))
    c.flash("k", "success", "Saved.")
    c.flash("k", "success", "Saved.")
    c.flash("k", "weird", "Note")
    assert c.show_flashes("k") == 2 and shown == [("success", "Saved."), ("info", "Note")]
    assert "k" not in state and c.show_flashes("k") == 0
    state["old"] = ("error", "Failed.")  # one (kind, text) pair, as some pages stored it before
    assert c.show_flashes("old") == 1 and shown[-1] == ("error", "Failed.")


# ---------------------------------------------------------------- pages

def test_login_hides_the_demo_file_hint_outside_demo_mode(backend, monkeypatch):  # noqa: F811
    assert "demo_credentials.csv" in all_text(start())
    monkeypatch.setenv("SCA_DEMO_MODE", "0")
    at = start()
    assert not at.exception and "demo_credentials.csv" not in all_text(at)


def test_admin_pages_are_titled_by_what_they_show(backend):  # noqa: F811
    at = as_admin()
    assert "<h1>Overview</h1>" in all_text(at)
    at.switch_page("ui/admin_students.py").run()
    at.text_input(key="students_query").input("nobody-matches-this").run()
    assert not at.exception and "No students match" in all_text(at)
    at.text_input(key="students_query").input("diya").run()
    assert list(at.dataframe[0].value["CGPA"]) == ["—"]  # the same dash as every other missing value
