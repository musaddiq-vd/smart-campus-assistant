"""Personal path: answer from the logged-in student's OWN record plus related policy docs.

Privacy rules enforced here (before any LLM call):
- no or unknown student_id -> ask the user to log in (no LLM call);
- question about another student (other ID, other student's name, "my friend's fees") -> refuse (no LLM call);
- only the logged-in student's record ever goes into the prompt.
"""

from __future__ import annotations

import re

from smart_campus_assistant import prompts, student_data
from smart_campus_assistant.config import BRANCHES, COLLEGE_NAME
from smart_campus_assistant.simple_rag import call_llm, mentioned_branches
from smart_campus_assistant.state import AgentState

POLICY_DOCS_K = 4

LOGIN_MESSAGE = (
    f"To answer questions about your own fees, books, attendance, results or exams, please log in "
    f"with your valid {COLLEGE_NAME} student ID (for example XYZ24CSE001). "
    "General questions about the college work without logging in."
)

REFUSAL_MESSAGE = (
    "Sorry, I can only share your own record. Details of other students are private, "
    "so I cannot answer that. You can ask about your own fees, books, attendance, results or exams."
)

# Third-person / someone-else phrasing in English and Hinglish.
OTHER_PERSON = re.compile(
    r"\b(his|her|him|friend'?s?|dost\w*|classmate'?s?|roommate'?s?|"
    r"another student|other students?|someone else'?s?|everyone'?s|all students'?)\b"
)
# These pronouns mean "his / her" but just as often "its / their" for a thing:
# "the hostel rules and their fees", "supplementary exam kab hai aur uski fees kitni hai".
AMBIGUOUS_PRONOUN = re.compile(r"\b(their|uska|uski|uske|unka|unki|unke)\b")
# College things such a pronoun can point back to.
THING_NOUNS = re.compile(
    r"\b(exams?|papers?|hostels?|mess|library|books?|courses?|subjects?|branch(?:es)?|scholarships?|placements?|"
    r"semesters?|labs?|rules?|college|departments?|clubs?|companies|company|programmes?|programs?)\b"
)
RECORD_TOPICS = re.compile(
    r"\b(fees?|dues?|books?|fine|attendance|results?|cgpa|sgpa|marks|backlogs?|exams?|hall ticket|room|record)\b"
)


def branch_scoped_query(question: str, record: dict | None) -> str:
    """Add the student's own branch to the search query when the question names none.

    "Which subjects will I study in Semester 5?" otherwise matches every branch's
    timetable equally, and the student's own curriculum drops out of the top results.
    """
    branch = (record or {}).get("branch")
    if not branch or branch not in BRANCHES or mentioned_branches(question):
        return question
    return f"{question} ({branch} {BRANCHES[branch]})"


def mentions_other_person(text: str, clear: re.Pattern[str] = OTHER_PERSON) -> bool:
    """True if lower-cased ``text`` points at another person ("her fees", "mere dost ki attendance").

    An ambiguous pronoun counts only when no college thing is named before it. Treating it as a
    thing is always safe: no path ever puts another student's record into a prompt, so the only
    cost of a wrong guess is a general answer instead of a refusal.
    """
    if clear.search(text):
        return True
    pronoun = AMBIGUOUS_PRONOUN.search(text)
    return bool(pronoun) and not THING_NOUNS.search(text[: pronoun.start()])


def asks_about_other_student(question: str, own_record: dict) -> bool:
    """True if the question targets any student other than the logged-in one."""
    own_id = student_data.normalize_id(own_record.get("student_id"))
    if any(student_data.normalize_id(m) != own_id for m in student_data.STUDENT_ID_RE.findall(question)):
        return True
    if student_data.students_named_in(question, exclude_id=own_id):
        return True
    text = question.lower()
    return bool(mentions_other_person(text) and RECORD_TOPICS.search(text))


def personal_answer(state: AgentState) -> dict:
    """One answer-model call over the student's own record and up to POLICY_DOCS_K policy docs."""
    from smart_campus_assistant import retriever  # lazy: lets fake mode stub the module

    question = state["question"]
    record = student_data.get_student(state.get("student_id"))
    if record is None:
        return {"answer": LOGIN_MESSAGE, "citations": [], "docs": []}
    if asks_about_other_student(question, record):
        return {"answer": REFUSAL_MESSAGE, "citations": [], "docs": []}

    docs = retriever.search(branch_scoped_query(question, record), k=POLICY_DOCS_K)
    answer, usage_record = call_llm(
        "answer",
        "personal_answer",
        prompts.personal_answer_messages(question, student_data.student_context(record), retriever.format_docs(docs)),
    )
    return {
        "docs": docs,
        "answer": answer,
        "citations": [retriever.to_citation(d) for d in docs],  # doc citations only; the record is not cited
        "usage": [usage_record],
    }
