"""Offline tests for student records, the personal route and its privacy rules (no AWS calls).

A tmp students.json is used so the tests do not depend on data/students.json.
"""

import json

import pytest
from langchain_core.messages import BaseMessage

from smart_campus_assistant import fakes, graph as graph_module, models, personal, student_data


def _student(sid, name, branch, **over):
    rec = {
        "student_id": sid, "name": name, "branch": branch, "year": 3, "semester": 5,
        "email": f"{name.split()[0].lower()}@xyz.example.edu", "hostel": {"block": "A", "room": "A-214"},
        "fees": {"semester": "Odd 2026-27", "total": 95000, "paid": 60000, "due": 35000, "due_date": "2026-11-30",
                 "status": "partially_paid", "scholarship": None,
                 "transactions": [{"date": "2026-07-10", "amount": 60000, "mode": "UPI", "receipt_no": "R-1"}]},
        "library": {"books_issued": [{"book_id": "B1", "title": "Operating System Concepts", "author": "Silberschatz",
                                      "issue_date": "2026-10-10", "due_date": "2026-11-10", "status": "issued"}],
                    "fine_due": 0},
        "attendance": {"overall_pct": 82.5, "courses": [{"code": "CS301", "name": "Operating Systems", "pct": 88.0}]},
        "results": {"cgpa": 8.12, "last_sgpa": 8.4, "backlogs": 0},
        "exams": {"upcoming": [{"course_code": "CS301", "course_name": "Operating Systems", "type": "End-sem",
                                "date": "2026-11-24", "time": "10:00", "room": "LH-2"}]},
    }
    rec.update(over)
    return rec


OVERDUE = _student(
    "XYZ23ECE007", "Kabir Mehta", "ECE",
    hostel=None,
    fees={"semester": "Odd 2026-27", "total": 95000, "paid": 40000, "due": 55000, "due_date": "2026-10-31",
          "status": "overdue", "scholarship": {"name": "Merit scholarship", "amount": 20000}, "transactions": []},
    library={"books_issued": [{"book_id": "B9", "title": "Signals and Systems", "author": "Oppenheim",
                               "issue_date": "2026-10-01", "due_date": "2026-10-15", "status": "overdue"}],
             "fine_due": 95},
    attendance={"overall_pct": 71.0, "courses": [{"code": "EC301", "name": "Digital Communication", "pct": 64.0},
                                                 {"code": "EC302", "name": "VLSI", "pct": 80.0}]},
)
STUDENTS = [_student("XYZ24CSE001", "Ananya Rao", "CSE"), OVERDUE]


@pytest.fixture
def students_file(tmp_path, monkeypatch):
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"college": "XYZ College of Engineering", "as_of": "2026-11-03",
                                "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: path)
    student_data.clear_cache()
    yield path
    student_data.clear_cache()


@pytest.fixture
def fake_mode(students_file):
    fakes.enable_fake_mode()
    yield fakes
    fakes.disable_fake_mode()


class PromptSpy(fakes.FakeCampusChatModel):
    """Fake model that records every prompt it receives."""

    seen: list = []

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        PromptSpy.seen.append((self.role, "\n".join(str(m.content) for m in messages)))
        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def spy(fake_mode, monkeypatch):
    PromptSpy.seen = []
    monkeypatch.setattr(models, "get_chat_model", lambda role: PromptSpy(role=role))
    return PromptSpy.seen


# ---------------------------------------------------------------- student_data


def test_load_and_lookup(students_file):
    assert set(student_data.load_students()) == {"XYZ24CSE001", "XYZ23ECE007"}
    assert student_data.get_student(" xyz24cse001 ")["name"] == "Ananya Rao"
    assert student_data.get_student("XYZ 24 CSE 001")["name"] == "Ananya Rao"
    assert student_data.get_student("XYZ99CSE999") is None
    assert student_data.get_student(None) is None


def test_missing_file_is_empty(tmp_path):
    student_data.clear_cache()
    assert student_data.load_students(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert student_data.load_students(bad) == {}


def test_list_students_only_public_fields(students_file):
    rows = student_data.list_students()
    assert [r["student_id"] for r in rows] == ["XYZ23ECE007", "XYZ24CSE001"]
    for row in rows:
        assert set(row) == {"student_id", "name", "branch", "year"}


def test_student_context_normal(students_file):
    text = student_data.student_context(student_data.get_student("XYZ24CSE001"))
    assert "Ananya Rao" in text and "XYZ24CSE001" in text
    assert "due Rs 35,000 by 2026-11-30 (27 days left)" in text
    assert "Hostel: Block A, Room A-214" in text
    assert "Operating System Concepts" in text and "(7 days left)" in text
    assert "CGPA 8.12" in text and "backlogs 0" in text
    assert "2026-11-24 10:00: CS301" in text
    assert "BELOW 75%" not in text and "OVERDUE" not in text
    assert "Late fee so far: Rs 0 (not overdue); total payable now: Rs 35,000" in text


def test_student_context_flags_overdue_and_low_attendance(students_file):
    text = student_data.student_context(student_data.get_student("XYZ23ECE007"))
    assert "(OVERDUE by 3 days)" in text and "status: OVERDUE" in text
    assert "Scholarship: Merit scholarship Rs 20,000" in text
    assert "Signals and Systems" in text and "(OVERDUE by 19 days, fine Rs 95)" in text
    assert "fine due Rs 95" in text
    # Computed money facts, so the model does no date math (3 days x Rs 100 after 2026-10-31).
    assert "Late fee so far: Rs 300 (3 days x Rs 100 per day, cap Rs 5,000 per semester)" in text
    assert "total payable now: Rs 55,300 (due Rs 55,000 + late fee Rs 300)" in text
    assert "overall 71.0% (BELOW 75%)" in text
    assert "EC301 Digital Communication: 64.0% (BELOW 75%)" in text
    assert "EC302 VLSI: 80.0%" in text and "80.0% (BELOW" not in text
    assert "day scholar" in text
    assert "Ananya" not in text  # one student only


# ---------------------------------------------------------------- personal route


def test_personal_path_two_calls_own_data_only(spy):
    result = graph_module.run_question("Meri fees kitni due hai?", student_id="xyz24cse001")
    assert result["route"] == "personal"
    assert [r["node"] for r in result["usage"]] == ["router", "personal_answer"]
    assert fakes.CALL_LOG == [("router", "route"), ("answer", "personal")]
    prompt = spy[-1][1]
    assert "Ananya Rao" in prompt and "Rs 35,000" in prompt
    assert "Kabir" not in prompt and "XYZ23ECE007" not in prompt  # never another student's data
    assert "your record" in result["answer"].lower() and "35,000" in result["answer"]
    assert result["citations"] and all(c["source"].endswith(".md") for c in result["citations"])
    assert len(result["docs"]) <= personal.POLICY_DOCS_K


def test_personal_stream_updates(fake_mode):
    chunks = list(graph_module.graph.stream(
        {"question": "Do I have any fine?", "student_id": "XYZ23ECE007"}, stream_mode="updates"
    ))
    assert [name for chunk in chunks for name in chunk] == ["router", "personal_answer"]
    update = chunks[-1]["personal_answer"]
    assert {"answer", "citations", "docs", "usage"} <= set(update)
    assert "Rs 95" in update["answer"]


@pytest.mark.parametrize("sid", [None, "", "XYZ99CSE999"])
def test_guest_or_unknown_id_gets_login_message(fake_mode, sid):
    result = graph_module.run_question("What is my attendance?", student_id=sid)
    assert result["route"] == "personal"
    assert result["answer"] == personal.LOGIN_MESSAGE
    assert ("answer", "personal") not in fakes.CALL_LOG
    assert [r["node"] for r in result["usage"]] == ["router"]
    assert result["citations"] == []


@pytest.mark.parametrize(
    "question",
    [
        "What is the fee status of XYZ23ECE007?",
        "Kabir Mehta ki attendance kitni hai?",
        "How many books has Kabir issued?",
        "Mere dost ki fees kitni due hai?",
        "What is my friend's CGPA?",
    ],
)
def test_other_student_is_refused(fake_mode, question):
    result = graph_module.run_question(question, student_id="XYZ24CSE001")
    assert result["route"] == "personal"
    assert result["answer"] == personal.REFUSAL_MESSAGE
    assert ("answer", "personal") not in fakes.CALL_LOG


def test_own_id_and_name_are_allowed(students_file):
    own = student_data.get_student("XYZ24CSE001")
    assert not personal.asks_about_other_student("Is XYZ24CSE001 fee paid? I am Ananya Rao.", own)
    assert not personal.asks_about_other_student("Meri attendance kitni hai?", own)


def test_force_route_personal(fake_mode):
    result = graph_module.run_question("Library fine?", force_route="personal", student_id="XYZ23ECE007")
    assert result["route"] == "personal"
    assert [r["node"] for r in result["usage"]] == ["personal_answer"]


# ---------------------------------------------------------------- deep path with a logged-in student


def test_deep_path_includes_own_student_context(spy):
    result = graph_module.run_question(
        "Compare CSE vs ECE placements and my fee dues", force_route="deep", student_id="XYZ24CSE001"
    )
    planner_prompt = next(p for role, p in spy if "Task: plan" in p)
    writer_prompt = next(p for role, p in spy if "Task: report" in p)
    for prompt in (planner_prompt, writer_prompt):
        assert "Ananya Rao" in prompt and "Rs 35,000" in prompt
        assert "Kabir" not in prompt and "XYZ23ECE007" not in prompt
    assert "Your record" in result["answer"]


def test_deep_path_guest_has_no_student_context(spy):
    graph_module.run_question("Compare CSE vs ECE placements", force_route="deep")
    assert all("Student record" not in p for _, p in spy)


# ---------------------------------------------------------------- fake mode student fallback


def test_fake_mode_demo_students_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(student_data, "_default_path", lambda: tmp_path / "missing.json")
    student_data.clear_cache()
    original = student_data.load_students
    fakes.enable_fake_mode()
    try:
        assert len(student_data.list_students()) == 2
        assert student_data.get_student("XYZ24CSE001")["name"] == fakes.DEMO_STUDENTS[0]["name"]
    finally:
        fakes.disable_fake_mode()
    assert student_data.load_students is original
    assert student_data.list_students() == []


def test_fake_mode_prefers_real_file(fake_mode):
    assert {r["student_id"] for r in student_data.list_students()} == {"XYZ24CSE001", "XYZ23ECE007"}
