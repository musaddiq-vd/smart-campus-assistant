"""Smoke tests for the Streamlit UI (no AWS: auth, graph, fakes and student data are stubbed via sys.modules)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from ui import components  # noqa: E402

APP = str(ROOT / "app.py")
FORBIDDEN = ("Cost", "tokens", "Tokens", "LLM calls", "Latency", "Details ·", "Answer mode", "FORCED")

RECORDS = {
    "XYZ24CSE001": {
        "student_id": "XYZ24CSE001", "name": "Aarav Sharma", "branch": "CSE", "year": 3, "semester": 5,
        "email": "aarav@xyz.edu.example", "as_of": "2026-11-03",
        "hostel": {"block": "A", "room": "A-214"},
        "fees": {"semester": "Odd 2026-27", "total": 150000, "paid": 100000, "due": 50000, "due_date": "2026-11-30",
                 "status": "partially_paid",
                 "transactions": [{"date": "2026-08-10", "amount": 100000, "mode": "UPI", "receipt_no": "R-1"}]},
        "library": {"books_issued": [
            {"book_id": "B1", "title": "Operating Systems", "author": "N. R.", "issue_date": "2026-10-20", "due_date": "2026-11-11", "status": "issued"},
            {"book_id": "B2", "title": "DBMS", "author": "A. D.", "issue_date": "2026-10-01", "due_date": "2026-10-15", "status": "overdue"},
        ], "fine_due": 40},
        "attendance": {"overall_pct": 82, "courses": [{"code": "CS501", "name": "OS", "pct": 84.0}, {"code": "CS502", "name": "DBMS", "pct": 72.0}]},
        "results": {"cgpa": 8.4, "last_sgpa": 8.6, "backlogs": 0},
        "exams": {"upcoming": [{"course_code": "CS502", "course_name": "DBMS", "type": "End-Sem", "date": "2026-12-03", "time": "10:00", "room": "LH-1"},
                               {"course_code": "CS501", "course_name": "OS", "type": "End-Sem", "date": "2026-12-01", "time": "10:00", "room": "LH-1"}]},
    },
    "XYZ26ECE009": {
        "student_id": "XYZ26ECE009", "name": "Diya Patel", "branch": "ECE", "year": 1, "semester": 1,
        "email": "diya@xyz.edu.example", "as_of": "2026-11-03", "hostel": None,
        "fees": {"total": 140000, "paid": 0, "due": 140000, "due_date": "2026-10-31", "status": "overdue", "transactions": []},
        "library": {"books_issued": [], "fine_due": 0},
        "attendance": {"overall_pct": 61.5, "courses": []},
        "results": {"cgpa": None, "last_sgpa": None, "backlogs": 0},
        "exams": {"upcoming": []},
    },
}
ACCOUNTS = {
    "aarav@xyz.edu.example": ("student-pass", {"email": "aarav@xyz.edu.example", "role": "student", "name": "Aarav Sharma", "student_id": "XYZ24CSE001"}),
    "admin@xyz.edu.example": ("admin-pass", {"email": "admin@xyz.edu.example", "role": "admin", "name": "XYZ Admin Office", "student_id": None}),
}


# ---------- stubs ----------

class FakeGraph:
    """Yields the same update shapes as the real compiled graph."""

    def __init__(self):
        self.calls: list[dict] = []

    def stream(self, inputs, config=None, stream_mode="updates"):
        self.calls.append(dict(inputs))
        q = inputs["question"].lower()
        route = "deep" if "compare" in q else "personal" if (" my " in f" {q} " or "meri" in q) else "simple"
        usage = [{"input_tokens": 400, "output_tokens": 80, "latency_ms": 120.0}]
        cite = {"source": "xyz/fees.md", "college": "xyz", "page": 1, "snippet": "Tuition fee per semester..."}
        yield {"router": {"route": route, "route_reason": "Single fact lookup.", "usage": usage}}
        if route == "simple":
            yield {"simple_answer": {"answer": "The library fine is **Rs 5** per day [1].", "citations": [cite], "docs": [1, 2, 3, 4, 5], "usage": usage}}
        elif route == "personal":
            if inputs.get("student_id") in RECORDS:
                yield {"personal_answer": {"answer": "Your fee due is **Rs 50,000** by 30 Nov [1].", "citations": [cite], "docs": [1, 2, 3], "usage": usage}}
            else:
                yield {"personal_answer": {"answer": "Please log in to see your own record.", "citations": [], "docs": [], "usage": []}}
        else:
            yield {"planner": {"plan": ["a", "b", "c", "d"], "research_loops": 0, "usage": usage}}
            for loop in (1, 2):
                yield {"researcher": {"notes": [{"summary": "y"}], "research_loops": loop, "docs": [1], "usage": usage}}
            yield {"writer": {"answer": "## Report\nCSE places better [1].", "citations": [cite], "usage": usage}}


class BrokenGraph:
    def stream(self, *args, **kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover


def make_auth_module(max_failures: int = 3) -> types.ModuleType:
    mod = types.ModuleType("smart_campus_assistant.auth")

    def authenticate(email, password, role=None):
        entry = ACCOUNTS.get(str(email).strip().lower())
        if not entry or entry[0] != password:
            return None
        if role is not None and entry[1]["role"] != role:
            return None
        return dict(entry[1])

    class LoginThrottle:  # new class per test, so the cached instance never leaks between tests
        def __init__(self):
            self.failures: dict[str, int] = {}

        def check(self, key):
            return 42.0 if self.failures.get(key, 0) >= max_failures else 0.0

        def record_failure(self, key):
            self.failures[key] = self.failures.get(key, 0) + 1

        def record_success(self, key):
            self.failures.pop(key, None)

    mod.authenticate = authenticate
    mod.LoginThrottle = LoginThrottle
    mod.load_users = lambda: {k: v[1] for k, v in ACCOUNTS.items()}
    return mod


ADMIN_NOTICES = (ROOT / "ui" / "admin_notices.py").exists()
ADMIN_PAGES = ["Overview", "Students", *(["Notices"] if ADMIN_NOTICES else []), "Ask", "Account"]
STUDENT_PAGES = ["Ask", "My dashboard", "Account"]


@pytest.fixture
def backend(monkeypatch, tmp_path):
    """Stub every backend module the UI imports so tests never touch AWS or real files."""
    monkeypatch.setenv("SCA_DEMO_MODE", "1")
    monkeypatch.setenv("SCA_CHATS_DIR", str(tmp_path / "chats"))  # chat history goes to a temp folder
    graph_mod = types.ModuleType("smart_campus_assistant.graph")
    graph_mod.graph = FakeGraph()
    fakes_mod = types.ModuleType("smart_campus_assistant.fakes")
    fakes_mod.enable_fake_mode = lambda: None
    fakes_mod.disable_fake_mode = lambda: None
    students_mod = types.ModuleType("smart_campus_assistant.student_data")
    students_mod.list_students = lambda: [
        {k: r[k] for k in ("student_id", "name", "branch", "year")} for r in RECORDS.values()
    ]
    students_mod.get_student = lambda sid: RECORDS.get(str(sid or "").upper())
    auth_mod = make_auth_module()
    monkeypatch.setitem(sys.modules, "smart_campus_assistant.graph", graph_mod)
    monkeypatch.setitem(sys.modules, "smart_campus_assistant.fakes", fakes_mod)
    monkeypatch.setitem(sys.modules, "smart_campus_assistant.student_data", students_mod)
    monkeypatch.setitem(sys.modules, "smart_campus_assistant.auth", auth_mod)

    # Record the pages app.py registers for each run.
    registered: list[list[str]] = []
    real_navigation = st.navigation

    def spy(pages, *args, **kwargs):
        registered.append([p.title for p in pages])
        return real_navigation(pages, *args, **kwargs)

    monkeypatch.setattr(st, "navigation", spy)
    return types.SimpleNamespace(graph=graph_mod, auth=auth_mod, pages=registered)


# ---------- helpers ----------

def all_text(at: AppTest) -> str:
    parts = [str(m.value) for m in at.markdown]
    parts += [str(h.value) for h in at.html]
    parts += [str(x.value) for x in (*at.caption, *at.error, *at.warning, *at.info, *at.success)]
    parts += [str(b.label) for b in at.button]
    parts += [str(e.label) for e in at.expander]
    return "\n".join(parts)


def start() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def sign_in(at: AppTest, email: str, password: str, role: str = "Student") -> AppTest:
    if role != "Student":
        at.button_group(key="login_role").set_value(role).run()
    at.text_input(key="login_email").input(email)
    at.text_input(key="login_password").input(password)
    at.button[0].click().run()  # the form's "Sign in" button
    return at


def as_student() -> AppTest:
    return sign_in(start(), "aarav@xyz.edu.example", "student-pass")


def as_admin() -> AppTest:
    return sign_in(start(), "Admin@XYZ.edu.example", "admin-pass", role="Admin")


# ---------- pure helpers ----------

def test_flow_strip_has_names_but_no_timings():
    html = components.flow_strip_html("personal", [{"node": "router", "ms": 600}, {"node": "personal_answer", "n_docs": 3, "ms": 2100}])
    assert all(p in html for p in ("Router", "Your record", "3 docs", "Answer"))
    assert " ms" not in html and "total" not in html and "2.1 s" not in html
    deep = components.flow_strip_html("deep", [{"node": "router"}, {"node": "planner", "n_plan": 4},
                                               {"node": "researcher"}, {"node": "researcher"}, {"node": "writer"}])
    assert "Plan (4)" in deep and "Research ×2" in deep and "Report" in deep


def test_student_helpers():
    flags = components.student_flags(RECORDS["XYZ24CSE001"])
    assert flags["overdue_books"] == 1 and flags["fees_due"] == 50000 and not flags["low_attendance"]
    assert components.student_flags(RECORDS["XYZ26ECE009"])["low_attendance"] is True
    assert "Not available yet" in components.student_kpis_html(RECORDS["XYZ26ECE009"])
    assert "<script>" not in components.user_card_html({"name": "<script>", "email": "x", "role": "admin"})
    assert components.format_rupees(123456) == "₹1,23,456"


# ---------- login ----------

def test_logged_out_shows_only_login(backend):
    at = start()
    assert not at.exception
    assert backend.pages[-1] == ["Sign in"]
    assert at.text_input(key="login_email").value == ""
    assert at.text_input(key="login_password").value == ""
    text = all_text(at)
    assert "Sign in" in text and "demo_credentials.csv" in text
    assert len(at.sidebar.button) == 0


def test_wrong_password_shows_generic_error_and_clears_input(backend):
    at = sign_in(start(), "aarav@xyz.edu.example", "nope")
    assert not at.exception
    assert at.error and "Invalid email or password" in at.error[0].value
    assert at.session_state["login_password"] == ""
    assert "user" not in at.session_state
    # Right password but wrong role is the same generic error.
    at = sign_in(start(), "aarav@xyz.edu.example", "student-pass", role="Admin")
    assert "Invalid email or password" in at.error[0].value and "user" not in at.session_state


def test_lockout_after_repeated_failures(backend):
    at = start()
    for _ in range(3):
        sign_in(at, "aarav@xyz.edu.example", "bad")
    assert "Too many failed attempts" in at.error[0].value and "42 seconds" in at.error[0].value
    sign_in(at, "AARAV@xyz.edu.example", "student-pass")  # locked even with the right password (email key lower-cased)
    assert "user" not in at.session_state and "Too many failed attempts" in at.error[0].value


def test_setup_message_when_auth_missing(backend, monkeypatch):
    monkeypatch.setitem(sys.modules, "smart_campus_assistant.auth", None)  # import fails
    at = start()
    assert not at.exception
    assert any("seed-demo" in c.value for c in at.code)
    assert at.warning and "not set up" in at.warning[0].value


def test_setup_message_when_users_file_missing(backend):
    backend.auth.load_users = lambda: {}
    at = start()
    assert not at.exception
    assert any("seed-demo" in c.value for c in at.code)


# ---------- student ----------

def test_student_login_pages_and_sidebar(backend):
    at = as_student()
    assert not at.exception
    assert backend.pages[-1] == STUDENT_PAGES
    assert "login_password" not in at.session_state or at.session_state["login_password"] == ""
    user = at.session_state["user"]
    assert user["student_id"] == "XYZ24CSE001" and "password" not in str(user).lower()
    side = "\n".join(str(m.value) for m in at.sidebar.markdown)
    assert "Aarav Sharma" in side and "aarav@xyz.edu.example" in side and "XYZ24CSE001" in side and "Student" in side
    assert "Diya" not in all_text(at) + side


def test_student_chat_uses_own_id_and_shows_no_metrics(backend):
    at = as_student()
    labels = [b.label for b in at.button]
    assert "Meri fees ka status kya hai?" in labels
    at.chat_input[0].set_value("Meri fees ka status kya hai?").run()
    assert not at.exception
    assert backend.graph.graph.calls[-1]["student_id"] == "XYZ24CSE001"
    text = all_text(at)
    assert "PERSONAL" in text and "Single fact lookup." in text
    assert all(p in text for p in ("Router", "Your record", "3 docs", "Answer"))
    assert "Sources · 1" in text
    for word in FORBIDDEN:
        assert word not in text, word
    assert len(at.button_group) == 0 and len(at.segmented_control) == 0


def test_student_session_cannot_be_spoofed(backend):
    at = as_student()
    at.session_state["student_id"] = "XYZ26ECE009"  # stray keys are ignored; only user["student_id"] counts
    at.chat_input[0].set_value("When is my next exam?").run()
    assert backend.graph.graph.calls[-1]["student_id"] == "XYZ24CSE001"


def test_student_dashboard(backend):
    at = as_student()
    at.switch_page("ui/student_home.py").run()
    assert not at.exception
    text = all_text(at)
    assert "Aarav Sharma" in text and "₹50,000" in text and "Overdue" in text and "8.40" in text
    assert "Block A" in text and "Room A-214" in text
    assert text.index("CS501 · OS") < text.index("CS502 · DBMS")  # exams sorted by date
    assert len(at.dataframe) == 1  # fee transactions
    assert "Diya" not in text


def test_student_cannot_reach_admin_pages(backend):
    at = as_student()
    for page in ("ui/admin_overview.py", "ui/admin_students.py"):
        # The pages are not registered for students, so there is nothing to switch to.
        with pytest.raises(ValueError, match="Could not find a navigation page"):
            at.switch_page(page)
    assert backend.pages[-1] == STUDENT_PAGES
    # Second line of defence: the page scripts themselves refuse a student session.
    for page in ("admin_overview.py", "admin_students.py"):
        direct = AppTest.from_file(str(ROOT / "ui" / page), default_timeout=30)
        direct.session_state["user"] = dict(ACCOUNTS["aarav@xyz.edu.example"][1])
        direct.run()
        text = all_text(direct)
        assert "do not have access" in text and "Diya" not in text and "Admin ·" not in text


def test_log_out_returns_to_login(backend):
    at = as_student()
    at.sidebar.button(key="logout").click().run()
    assert not at.exception
    assert "user" not in at.session_state
    assert backend.pages[-1] == ["Sign in"]
    assert at.text_input(key="login_email").value == ""


# ---------- admin ----------

def test_admin_pages_and_overview(backend):
    at = as_admin()
    assert not at.exception
    assert backend.pages[-1] == ADMIN_PAGES
    text = all_text(at)
    assert "Admin · overview" in text
    assert "₹1,00,000" in text  # collected
    assert "₹1,90,000" in text  # outstanding
    assert "Action needed" in text
    actions = at.dataframe[-1].value
    assert set(actions["Issue"]) == {"Fees overdue", "Overdue books", "Low attendance"}
    for word in FORBIDDEN:
        assert word not in text, word


def test_admin_students_page_filters_and_detail(backend):
    at = as_admin()
    at.switch_page("ui/admin_students.py").run()
    assert not at.exception
    assert len(at.dataframe[0].value) == 2
    at.text_input(key="students_query").input("diya").run()
    assert list(at.dataframe[0].value["Student ID"]) == ["XYZ26ECE009"]
    at.text_input(key="students_query").input("").run()
    at.toggle(key="students_low").set_value(True).run()
    assert list(at.dataframe[0].value["Student ID"]) == ["XYZ26ECE009"]
    at.selectbox(key="students_pick").select("XYZ26ECE009").run()
    assert not at.exception
    text = all_text(at)
    assert "Diya Patel" in text and "Not available yet" in text and "Day scholar" in text


def test_admin_chat_hint_and_no_student_id(backend):
    at = as_admin()
    at.switch_page("ui/chat.py").run()
    assert not at.exception
    assert "Students" in all_text(at) and "Meri fees ka status kya hai?" not in [b.label for b in at.button]
    at.chat_input[0].set_value("Which books are issued in my name?").run()
    assert backend.graph.graph.calls[-1]["student_id"] is None
    html = all_text(at)
    assert "Personal records" in html and "No student record" in html


def test_chat_backend_error_is_friendly(backend):
    backend.graph.graph = BrokenGraph()
    at = as_student()
    at.chat_input[0].set_value("anything").run()
    assert not at.exception
    assert at.error and "Something went wrong" in at.error[0].value


# ---------- saved chat history ----------

def test_chat_history_survives_refresh_and_clear(backend):
    at = as_student()
    at.chat_input[0].set_value("What is the library fine per day?").run()
    assert not at.exception
    # A refresh is a fresh Streamlit session: history comes back from the chat store.
    fresh = AppTest.from_file(APP, default_timeout=30)
    fresh.session_state["user"] = dict(ACCOUNTS["aarav@xyz.edu.example"][1])
    fresh.run()
    text = all_text(fresh)
    assert "What is the library fine per day?" in text and "Rs 5" in text and "SIMPLE" in text
    # Another account never sees it.
    other = AppTest.from_file(APP, default_timeout=30)
    other.session_state["user"] = dict(ACCOUNTS["admin@xyz.edu.example"][1])
    other.run()
    other.switch_page("ui/chat.py").run()
    assert "library fine" not in all_text(other)
    # Clear chat empties the page and the store.
    fresh.button(key="clear_chat").click().run()
    again = AppTest.from_file(APP, default_timeout=30)
    again.session_state["user"] = dict(ACCOUNTS["aarav@xyz.edu.example"][1])
    again.run()
    assert "library fine per day?" not in [str(m.value) for m in again.markdown]
