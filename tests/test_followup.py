"""Offline tests for multi-turn chat: follow-up questions are rewritten into standalone ones."""

import json

import pytest

from smart_campus_assistant import fakes, followup, prompts, router, simple_rag, student_data
from smart_campus_assistant import graph as graph_module
from tests.test_personal import STUDENTS


@pytest.fixture
def fake_mode():
    fakes.enable_fake_mode()
    fakes.CALL_LOG.clear()
    yield fakes
    fakes.disable_fake_mode()


@pytest.fixture
def students_file(tmp_path, monkeypatch):
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"college": "XYZ College of Engineering", "as_of": "2026-11-03",
                                "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: path)
    student_data.clear_cache()
    yield path
    student_data.clear_cache()


def turn(question: str, answer: str = "Some answer [1].") -> dict:
    return {"question": question, "answer": answer}


# ---------------------------------------------------------------- helpers


def test_clean_history_keeps_last_valid_turns_and_bounds_size():
    history = [turn(f"q{i}") for i in range(5)] + ["junk", {"question": "", "answer": "x"}, {"question": "y"}]
    history.append(turn("long " * 500, "a " * 1000))
    cleaned = followup.clean_history(history)
    assert len(cleaned) == followup.MAX_HISTORY_TURNS
    assert [t["question"] for t in cleaned[:2]] == ["q3", "q4"]
    assert len(cleaned[-1]["question"]) <= followup.MAX_HISTORY_QUESTION_CHARS
    assert len(cleaned[-1]["answer"]) <= followup.MAX_HISTORY_ANSWER_CHARS
    assert followup.clean_history(None) == [] and followup.clean_history("nope") == []


@pytest.mark.parametrize("question", [
    "aur ECE ka?", "What about the hostel?", "and for ME?", "Is that per semester?",
    "uski last date kya hai?", "fine?", "kitna hai wo?", "How do I pay it?",
    "How much is the hostel one?", "Is that one compulsory for us?",
])
def test_follow_ups_are_detected(question):
    assert followup.looks_like_follow_up(question)


@pytest.mark.parametrize("question", [
    "What is the library fine per day for a late book?",
    "Hostel mein mess ka charge kitna hai per month?",
    "When do the CSE end semester exams start?",
    "What is the tuition fee for this semester?",
    "Which exams are scheduled for that year?",
    "",
])
def test_standalone_questions_are_not_follow_ups(question):
    assert not followup.looks_like_follow_up(question)


@pytest.mark.parametrize("text, expected", [
    ("ECE ka tuition fee kitna hai?", "ECE ka tuition fee kitna hai?"),
    ('Standalone question: "What is the ECE fee?"\nextra', "What is the ECE fee?"),
    ("\n\n  question: hostel fee?  ", "hostel fee?"),
    ("   ", ""),
    ("Sure! Here is the standalone question:\nECE ka tuition fee kitna hai?", "ECE ka tuition fee kitna hai?"),
    ("Here's the rewritten question:\n\n- What is the ECE fee?", "What is the ECE fee?"),
    ('**Standalone question:** "What is the ECE fee?"', "What is the ECE fee?"),
    ("ECE fee kitni hai?\n\n(I filled in the topic from the conversation.)", "ECE fee kitni hai?"),
    ("Okay.", ""),
])
def test_parse_rewrite(text, expected):
    assert followup.parse_rewrite(text) == expected


def test_rewrite_prompt_carries_turns_and_follow_up():
    messages = prompts.rewrite_messages("aur ECE ka?", [turn("What is the CSE fee?", "Rs 95,000 [1].")])
    assert messages[0].content.startswith("Task: rewrite")
    human = messages[1].content
    assert "User: What is the CSE fee?" in human and "Assistant: Rs 95,000 [1]." in human
    assert human.rstrip().endswith("Follow-up: aur ECE ka?\nStandalone question:")


# ---------------------------------------------------------------- rewrite_follow_up


def test_no_history_or_standalone_question_makes_no_call(fake_mode):
    q = "What is the library fine per day for a late book?"
    assert followup.rewrite_follow_up("aur ECE ka?", []) == ("aur ECE ka?", None)
    assert followup.rewrite_follow_up(q, [turn("What is the CSE fee?")]) == (q, None)
    assert fakes.CALL_LOG == []


def test_follow_up_is_rewritten_with_one_cheap_call(fake_mode):
    question, record = followup.rewrite_follow_up("aur hostel ka?", [turn("What is the CSE tuition fee?")])
    assert question == "hostel ka? (about: What is the CSE tuition fee?)"
    assert record["node"] == "contextualize"
    assert fakes.CALL_LOG == [("router", "rewrite")]


def test_fake_rewrite_swaps_a_bare_branch_follow_up(fake_mode):
    """Like the rewrite prompt's own example; keeping both branches would make it a comparison."""
    question, _ = followup.rewrite_follow_up("aur ECE ka?", [turn("What is the CSE tuition fee?")])
    assert question == "What is the ECE tuition fee?"
    result = graph_module.run_question("aur ECE ka?", history=[turn("What is the CSE tuition fee?")])
    assert result["route"] == "simple" and result["question"] == "What is the ECE tuition fee?"
    # A follow-up with a topic of its own keeps the old question as context instead.
    question, _ = followup.rewrite_follow_up("aur ECE ka hostel?", [turn("What is the CSE tuition fee?")])
    assert question == "ECE ka hostel? (about: What is the CSE tuition fee?)"


@pytest.mark.parametrize("question", [
    "Is there a gym in the hostel?",
    "Are there any scholarships for girls?",
    "Is the caution deposit a one-time payment?",
    "Can I borrow more than 4 books?",
    "What is the fee for one semester of hostel?",
])
def test_existence_numbers_and_comparisons_are_not_references(question):
    assert not followup.looks_like_follow_up(question)


@pytest.mark.parametrize("question", ["Is there a late fee for it?", "Which one is cheaper?", "Tell me more"])
def test_real_references_next_to_those_words_still_count(question):
    assert followup.looks_like_follow_up(question)


@pytest.mark.parametrize("failure", ["error", "blank"])
def test_rewrite_failure_keeps_the_question(monkeypatch, failure):
    def call_llm(role, node, messages):
        if failure == "error":
            raise RuntimeError("model down")
        return "   ", {"node": node, "model_id": "m", "input_tokens": 1, "output_tokens": 1, "latency_ms": 1.0}

    monkeypatch.setattr(simple_rag, "call_llm", call_llm)
    question, record = followup.rewrite_follow_up("aur ECE ka?", [turn("What is the CSE fee?")])
    assert question == "aur ECE ka?"
    assert (record is None) == (failure == "error")


# ---------------------------------------------------------------- graph


def test_follow_up_in_graph_is_answered_as_standalone_question(fake_mode):
    result = graph_module.run_question(
        "aur lost book ka kya rule hai?", history=[turn("What is the library fine per day?", "Rs 5 per day [1].")]
    )
    assert result["original_question"] == "aur lost book ka kya rule hai?"
    assert result["question"].startswith("lost book ka kya rule hai?")
    assert "library fine" in result["question"]
    assert [r["node"] for r in result["usage"]][:2] == ["contextualize", "router"]
    assert result["answer"]


def test_question_without_history_is_unchanged(fake_mode):
    result = graph_module.run_question("aur ECE ka?")
    assert "original_question" not in result
    assert result["question"] == "aur ECE ka?"
    assert ("router", "rewrite") not in fakes.CALL_LOG


def test_standalone_question_with_history_costs_no_extra_call(fake_mode):
    result = graph_module.run_question("What is the library fine per day for a late book?",
                                       history=[turn("Meri fees ka status kya hai?")])
    assert "original_question" not in result
    assert ("router", "rewrite") not in fakes.CALL_LOG
    assert result["route"] == "simple"


def test_follow_up_about_a_friend_is_still_refused(fake_mode, students_file):
    from smart_campus_assistant.personal import REFUSAL_MESSAGE

    result = graph_module.run_question(
        "aur mere dost ki?", student_id="XYZ24CSE001",
        history=[turn("Meri fees ka status kya hai?", "As per your record, Rs 35,000 is due.")],
    )
    assert result["route"] == "personal"
    assert result["answer"] == REFUSAL_MESSAGE


def test_personal_follow_up_reads_own_record(fake_mode, students_file):
    result = graph_module.run_question(
        "aur meri attendance?", student_id="XYZ24CSE001",
        history=[turn("Meri fees ka status kya hai?", "As per your record, Rs 35,000 is due.")],
    )
    assert result["route"] == "personal"
    assert "your record" in result["answer"].lower()


def test_blank_follow_up_still_needs_no_model(fake_mode):
    result = graph_module.run_question("   ", history=[turn("What is the CSE fee?")])
    assert result["answer"] == router.EMPTY_QUESTION_MESSAGE
    assert fakes.CALL_LOG == []


def test_forced_route_still_rewrites_follow_up(fake_mode):
    result = graph_module.run_question("what about ME?", force_route="simple",
                                       history=[turn("What is the CSE tuition fee?")])
    assert result["route"] == "simple"
    assert result["original_question"] == "what about ME?"


def test_rewrite_that_only_changes_case_is_not_shown(monkeypatch):
    record = {"node": "contextualize", "model_id": "m", "input_tokens": 1, "output_tokens": 1, "latency_ms": 1.0}
    monkeypatch.setattr(simple_rag, "call_llm", lambda *a: ("What is the fee for it", record))
    question, used = followup.rewrite_follow_up("what is the fee for it?", [turn("q")])
    assert question == "what is the fee for it?" and used is record  # the call still counts as usage


def test_rewrite_with_chatter_still_reaches_the_router(fake_mode, monkeypatch):
    record = {"node": "contextualize", "model_id": "m", "input_tokens": 1, "output_tokens": 1, "latency_ms": 1.0}
    monkeypatch.setattr(simple_rag, "call_llm", lambda *a: (
        "Sure! Here is the standalone question:\nWhat is the library fine for a lost book?", record))
    result = graph_module.run_question("aur lost book ka?", history=[turn("What is the library fine per day?")])
    assert result["question"] == "What is the library fine for a lost book?"
