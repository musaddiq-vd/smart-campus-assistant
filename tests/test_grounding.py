"""Citation numbering and the shared "information not available" message on every answer path (offline)."""

import json

import pytest

from smart_campus_assistant import deep_research, fakes, grounding, models, personal, question_log, student_data
from smart_campus_assistant import graph as graph_module
from tests.test_personal import STUDENTS


def cite(source: str, snippet: str, page: int | None = 1) -> dict:
    return {"source": source, "college": "xyz", "page": page, "snippet": snippet}


A, B, C = cite("a.md", "alpha"), cite("b.md", "beta"), cite("c.md", "gamma")


@pytest.fixture
def fake_mode():
    fakes.enable_fake_mode()
    yield fakes
    fakes.disable_fake_mode()


@pytest.fixture
def students_file(tmp_path, monkeypatch):
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"as_of": "2026-11-03", "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: path)
    student_data.clear_cache()
    yield path
    student_data.clear_cache()


def answering(monkeypatch, replies: dict[str, str]) -> None:
    """Fake model whose answer for the given tasks is fixed (other tasks behave as usual)."""

    class Fixed(fakes.FakeCampusChatModel):
        def _respond(self, task, human):
            return replies[task] if task in replies else super()._respond(task, human)

    monkeypatch.setattr(models, "get_chat_model", lambda role: Fixed(role=role))


def assert_numbers_point_at_citations(result: dict) -> None:
    numbers = grounding.cited_numbers(result["answer"])
    assert all(1 <= n <= len(result["citations"]) for n in numbers), (numbers, len(result["citations"]))
    assert numbers == list(range(1, len(result["citations"]) + 1))  # every source is cited, in order of use
    keys = [grounding.citation_key(c) for c in result["citations"]]
    assert len(keys) == len(set(keys))  # the chat page's de-duplication cannot shift the numbers


# ---------------------------------------------------------------- pure helpers


@pytest.mark.parametrize("text, expected", [
    ("Fee [2]. Rule [1, 3]. Again [2].", [2, 1, 3]),
    ("Range [2-4] and [5–6].", [2, 3, 4, 5, 6]),
    ("Due on [2026-11-30], book [1021], ok [1].", [1]),  # dates and IDs in brackets are not citations
    ("No citations here.", []),
])
def test_cited_numbers(text, expected):
    assert grounding.cited_numbers(text) == expected


def test_renumber_drops_unknown_numbers_with_their_space():
    assert grounding.renumber("Rs 5 per day [7]. Cap Rs 500 [2, 7].", {2: 1}) == "Rs 5 per day. Cap Rs 500 [1]."
    assert grounding.renumber("A [1][2] b [3]", {1: 1, 2: 1, 3: 2}) == "A [1] b [2]"


def test_finalize_keeps_cited_sources_in_order_of_first_use():
    text, used = grounding.finalize_citations("X [3]. Y [1]. Z [3].", [A, B, C])
    assert text == "X [1]. Y [2]. Z [1]." and used == [C, A]


def test_finalize_drops_numbers_without_a_source():
    text, used = grounding.finalize_citations("Fine is Rs 5 [1]. Hostel [9]. Both [1, 12].", [A, B])
    assert text == "Fine is Rs 5 [1]. Hostel. Both [1]." and used == [A]


def test_finalize_merges_sources_the_page_would_merge():
    head = "Placements Report 2026 > Branch-wise placement statistics > Computer Science"  # > 60 chars
    first, second = cite("p.md", head + " part one"), cite("p.md", head + " part two")  # the page shows one card
    text, used = grounding.finalize_citations("One [2]. Two [1]. Three [3].", [first, second, B])
    assert text == "One [1]. Two [1]. Three [2]." and used == [first, B]


@pytest.mark.parametrize("answer", ["", "  ", "The hostel fee is Rs 54,000.", "Fee [5]."])
def test_uncited_answer_becomes_the_standard_message(answer):
    assert grounding.check_answer(answer, [A, B]) == (grounding.NOT_FOUND_MESSAGE, [])


def test_record_facts_keep_an_uncited_personal_answer():
    record = "Fees (Odd 2026-27): total Rs 1,49,000, due Rs 59,600 by 2026-11-30"
    assert grounding.check_answer("As per your record, you owe Rs 59,600.", [A], record)[0].startswith("As per")
    assert grounding.check_answer("You owe 59600 by 2026-11-30.", [], record)[1] == []
    assert grounding.check_answer("Fees are due in 2026.", [], record, grounding.RECORD_NOT_FOUND_MESSAGE)[0] == (
        grounding.RECORD_NOT_FOUND_MESSAGE
    )


def test_standard_messages_count_as_unanswered_and_point_to_requests():
    for message in (grounding.NOT_FOUND_MESSAGE, grounding.RECORD_NOT_FOUND_MESSAGE):
        assert message.startswith("Information not available")
        assert "My requests" in message and "admin office" in message
        assert question_log.is_answered(message) is False
        assert grounding.is_not_found(message)


def test_prompts_ask_for_the_same_wording():
    from smart_campus_assistant import prompts

    assert grounding.NOT_FOUND_MESSAGE in prompts.SIMPLE_ANSWER_SYSTEM
    assert grounding.NOT_FOUND_MESSAGE in prompts.WRITER_SYSTEM
    assert grounding.RECORD_NOT_FOUND_MESSAGE in prompts.PERSONAL_ANSWER_SYSTEM
    assert grounding.NOT_FOUND_MESSAGE not in prompts.NOTE_SYSTEM  # notes keep their own internal marker


# ---------------------------------------------------------------- simple path


def test_simple_path_drops_a_made_up_citation(fake_mode, monkeypatch):
    answering(monkeypatch, {"answer": "The library fine is Rs 5 per day [2] and lost books cost double [9]."})
    result = graph_module.run_question("What is the library fine per day?")
    assert "[9]" not in result["answer"] and result["answer"].count("[1]") == 1
    assert len(result["citations"]) == 1 and len(result["docs"]) > 1
    assert_numbers_point_at_citations(result)


def test_simple_path_uncited_answer_is_not_found(fake_mode, monkeypatch):
    answering(monkeypatch, {"answer": "Yes, the college runs a bus service for Rs 12,000 a year."})
    result = graph_module.run_question("What is the library fine per day?")
    assert result["answer"] == grounding.NOT_FOUND_MESSAGE and result["citations"] == []


def test_simple_path_with_nothing_retrieved_makes_no_answer_call(fake_mode):
    result = graph_module.run_question("What is zzqqxx?")
    assert result["answer"] == grounding.NOT_FOUND_MESSAGE and result["citations"] == []
    assert ("answer", "answer") not in fakes.CALL_LOG


def test_fake_not_found_uses_the_standard_message(fake_mode):
    result = graph_module.run_question("What is the fee for the college bus service?")
    assert result["route"] == "simple"
    assert result["answer"] == grounding.NOT_FOUND_MESSAGE and result["citations"] == []


# ---------------------------------------------------------------- personal path


def test_personal_record_only_answer_has_no_document_citations(fake_mode, students_file, monkeypatch):
    answering(monkeypatch, {"personal": "As per your record, your CGPA is 8.12 [7]."})
    result = graph_module.run_question("What is my CGPA?", student_id="XYZ24CSE001")
    assert result["answer"] == "As per your record, your CGPA is 8.12."
    assert result["citations"] == []


def test_personal_answer_grounded_in_nothing_gets_the_record_message(fake_mode, students_file, monkeypatch):
    answering(monkeypatch, {"personal": "Your blood group is O+."})
    result = graph_module.run_question("What is my blood group in my record?", student_id="XYZ24CSE001",
                                       force_route="personal")
    assert result["answer"] == grounding.RECORD_NOT_FOUND_MESSAGE and result["citations"] == []


def test_personal_login_and_refusal_messages_are_unchanged(fake_mode, students_file):
    assert graph_module.run_question("What is my CGPA?")["answer"] == personal.LOGIN_MESSAGE
    refused = graph_module.run_question("What is my friend's CGPA?", student_id="XYZ24CSE001")
    assert refused["answer"] == personal.REFUSAL_MESSAGE


# ---------------------------------------------------------------- deep path


def test_deep_report_drops_numbers_without_a_source(fake_mode, monkeypatch):
    answering(monkeypatch, {"report": "CSE placed 92.9% [1]; ECE placed 84.5% [2]; ME placed 99% [42]."})
    result = graph_module.run_question("Compare CSE and ECE placements", force_route="deep")
    assert "[42]" not in result["answer"]
    assert_numbers_point_at_citations(result)


def test_deep_uncited_report_is_not_found(fake_mode, monkeypatch):
    answering(monkeypatch, {"report": "CSE is clearly the best branch for everyone."})
    result = graph_module.run_question("Compare CSE and ECE placements", force_route="deep")
    assert result["answer"] == grounding.NOT_FOUND_MESSAGE and result["citations"] == []


def test_note_without_citations_brings_no_sources(fake_mode, monkeypatch):
    answering(monkeypatch, {"note": "Not found in the provided documents."})
    result = graph_module.run_question("Compare CSE and ECE placements", force_route="deep")
    assert all(note["citations"] == [] for note in result["notes"])
    assert result["answer"] == grounding.NOT_FOUND_MESSAGE and result["citations"] == []


def test_note_numbers_without_a_source_are_dropped(monkeypatch):
    from langchain_core.documents import Document

    from smart_campus_assistant import retriever

    docs = [Document(page_content="CSE placed 92.9%.", metadata={"source": "p.md", "page": 1, "college": "xyz"})]
    monkeypatch.setattr(retriever, "search", lambda q, k=None, college=None: docs)
    monkeypatch.setattr(deep_research, "call_llm", lambda *a: ("- CSE 92.9% [1]\n- ECE 84.5% [3]", {}))
    note, used, _ = deep_research._research_one("q", "sub", None)
    assert note["summary"] == "- CSE 92.9% [1]\n- ECE 84.5%" and len(note["citations"]) == 1


# ---------------------------------------------------------------- every path, real documents


@pytest.mark.parametrize("question, route", [
    ("What is the library fine per day for an overdue book?", None),
    ("What is the hostel fee per semester, including mess charges?", None),
    ("Supplementary exam kab hai aur uski fees kitni hai?", None),
    ("Compare CSE and ECE placements in 2026", None),
    ("What is the total first-year cost for an Open category hostel resident, including the caution deposit?", None),
    ("What is the late fee rule?", "deep"),
])
def test_every_number_points_at_a_returned_citation(fake_mode, question, route):
    result = graph_module.run_question(question, force_route=route)
    assert result["citations"], result["answer"]
    assert_numbers_point_at_citations(result)


def test_personal_numbers_point_at_returned_citations(fake_mode, students_file):
    result = graph_module.run_question("Do I have any library fine?", student_id="XYZ23ECE007")
    assert_numbers_point_at_citations(result)
