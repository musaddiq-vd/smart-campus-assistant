"""Personal path: answer from the logged-in student's OWN record plus related policy docs.

Privacy rules enforced here (before any LLM call):
- no or unknown student_id -> ask the user to log in (no LLM call);
- question about another student (other ID, other student's name, "my friend's fees") -> refuse (no LLM call);
- only the logged-in student's record ever goes into the prompt.
"""

from __future__ import annotations

import re

from smart_campus_assistant import prompts, student_data
from smart_campus_assistant.config import COLLEGE_NAME
from smart_campus_assistant.simple_rag import call_llm
from smart_campus_assistant.state import AgentState

POLICY_DOCS_K = 3

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
    r"\b(his|her|their|him|uska|uski|uske|unka|unki|unke|friend'?s?|dost\w*|classmate'?s?|roommate'?s?|"
    r"another student|other students?|someone else'?s?|everyone'?s|all students'?)\b"
)
RECORD_TOPICS = re.compile(
    r"\b(fees?|dues?|books?|fine|attendance|results?|cgpa|sgpa|marks|backlogs?|exams?|hall ticket|room|record)\b"
)


def asks_about_other_student(question: str, own_record: dict) -> bool:
    """True if the question targets any student other than the logged-in one."""
    own_id = student_data.normalize_id(own_record.get("student_id"))
    if any(student_data.normalize_id(m) != own_id for m in student_data.STUDENT_ID_RE.findall(question)):
        return True
    if student_data.students_named_in(question, exclude_id=own_id):
        return True
    text = question.lower()
    return bool(OTHER_PERSON.search(text) and RECORD_TOPICS.search(text))


def personal_answer(state: AgentState) -> dict:
    """One answer-model call over the student's own record and up to 3 policy docs."""
    from smart_campus_assistant import retriever  # lazy: lets fake mode stub the module

    question = state["question"]
    record = student_data.get_student(state.get("student_id"))
    if record is None:
        return {"answer": LOGIN_MESSAGE, "citations": [], "docs": []}
    if asks_about_other_student(question, record):
        return {"answer": REFUSAL_MESSAGE, "citations": [], "docs": []}

    docs = retriever.search(question, k=POLICY_DOCS_K)
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
