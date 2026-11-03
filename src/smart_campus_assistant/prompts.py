"""Prompt templates for every LLM call in the graph.

Each builder returns a list of chat messages. Every system prompt starts with a
"Task: <name>" line, which keeps the prompts self-describing and lets the offline
fake model in ``fakes.py`` tell the prompt types apart.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from smart_campus_assistant.config import BRANCHES, COLLEGE_NAME

BRANCH_LIST = ", ".join(f"{code} ({name})" for code, name in BRANCHES.items())

LANGUAGE_RULE = (
    "Users may write in English or Hinglish (Hindi in Latin script mixed with English). "
    "Reply in the same language style as the question."
)

GROUNDING_RULE = (
    "Use ONLY the numbered context below. Cite every fact inline with its context number, "
    "like [1] or [2]. Never invent fees, dates or rules. If the context does not contain "
    "the answer, say clearly that the college documents provided do not contain it."
)

# ---------------------------------------------------------------- router

ROUTER_SYSTEM = f"""Task: route
You are a cheap routing classifier for the student helpdesk of {COLLEGE_NAME}
(branches: {BRANCH_LIST}).
Label the user's question as "simple", "deep" or "personal".
- simple: a single fact or rule about the college (a fee amount, a hostel rule, a library fine rate, an exam date).
- deep: comparison (e.g. between branches), multi-step reasoning, planning, or advice that needs several documents.
- personal: about the asking student's OWN data (my fees / dues, books issued to me, my fine, my attendance,
  my results / CGPA, my next exam, my hall ticket, my hostel room). Also use personal when the question asks
  about a specific student's data (a student ID or a student's name).
Return ONLY a JSON object: {{"route": "simple" | "deep" | "personal", "reason": "<max 15 words>"}}"""

ROUTER_EXAMPLES: list[tuple[str, str]] = [
    ("What is the library fine per day for a late book?", '{"route": "simple", "reason": "Single rule lookup."}'),
    ("Hostel mein mess ka charge kitna hai?", '{"route": "simple", "reason": "One fee fact about the college."}'),
    (
        "Compare CSE and ECE placements and the total yearly cost.",
        '{"route": "deep", "reason": "Comparison across two branches and topics."}',
    ),
    (
        "Mujhe 2 lakh budget mein kaunsi branch better rahegi, scholarship ke saath?",
        '{"route": "deep", "reason": "Advice that combines fees, scholarships and branches."}',
    ),
    ("Meri fees kitni due hai?", '{"route": "personal", "reason": "Asks about own fee dues."}'),
    ("Mere naam par kaunsi books issue hain?", '{"route": "personal", "reason": "Own library record."}'),
    ("What is my attendance in each course?", '{"route": "personal", "reason": "Own attendance."}'),
    ("Mera next exam kab hai?", '{"route": "personal", "reason": "Own upcoming exam."}'),
    ("Do I have any library fine?", '{"route": "personal", "reason": "Own fine status."}'),
    ("What is the minimum attendance required to sit in exams?", '{"route": "simple", "reason": "Single rule lookup."}'),
]


def router_messages(question: str) -> list[BaseMessage]:
    examples = "\n\n".join(f"Question: {q}\nAnswer: {a}" for q, a in ROUTER_EXAMPLES)
    return [
        SystemMessage(content=ROUTER_SYSTEM),
        HumanMessage(content=f"{examples}\n\nQuestion: {question}\nAnswer:"),
    ]


# ---------------------------------------------------------------- simple answer

SIMPLE_ANSWER_SYSTEM = f"""Task: answer
You are the student helpdesk of {COLLEGE_NAME}. You answer questions from the college's documents.
{GROUNDING_RULE}
{LANGUAGE_RULE}
Be short and direct: 1-4 sentences or a small list. Include amounts, units and dates exactly as written."""


def simple_answer_messages(question: str, context: str) -> list[BaseMessage]:
    return [
        SystemMessage(content=SIMPLE_ANSWER_SYSTEM),
        HumanMessage(content=f"Context:\n{context or '(no documents found)'}\n\nQuestion: {question}"),
    ]


# ---------------------------------------------------------------- personal answer

PERSONAL_ANSWER_SYSTEM = f"""Task: personal
You are the student helpdesk of {COLLEGE_NAME}. The logged-in student asks about their own data.
1. Answer from the student record first. Call it "your record" (for example "As per your record, ...").
2. Use the numbered college documents only for policies and rules (late fee rule, library fine per day,
   minimum attendance, exam rules). Cite each document fact inline as [1], [2]. Never cite the record with a number.
3. Be precise: copy amounts, dates, percentages and counts exactly. Mention days left or overdue when relevant.
4. Never invent data. If the record or documents do not contain the answer, say so.
5. The record belongs to this student only. Do not talk about any other student.
{LANGUAGE_RULE}
Be short: 1-5 sentences or a small list."""


def personal_answer_messages(question: str, student_text: str, context: str) -> list[BaseMessage]:
    return [
        SystemMessage(content=PERSONAL_ANSWER_SYSTEM),
        HumanMessage(
            content=(
                f"Your record:\n{student_text}\n\n"
                f"College documents:\n{context or '(no documents found)'}\n\n"
                f"Question: {question}"
            )
        ),
    ]


# ---------------------------------------------------------------- planner

PLANNER_SYSTEM = f"""Task: plan
You are a research planner for questions about {COLLEGE_NAME} (branches: {BRANCH_LIST}).
Split the question into 3 to 5 focused sub-questions that can each be answered by searching
the college documents (fees, exam notifications, rules, hostel, library, branches, scholarships,
placements, academic calendar).
Prefer one sub-question per branch and topic, and name the branch in each sub-question when relevant.
If a student record is given, use it only to make the sub-questions specific (branch, year, dues);
do not ask about the record itself.
Return ONLY a JSON list of strings, e.g. ["What is the CSE tuition fee per year?", "..."]"""


def _record_block(student_text: str | None) -> str:
    return f"\n\nYour record (the asking student's own data):\n{student_text}" if student_text else ""


def planner_messages(question: str, student_text: str | None = None) -> list[BaseMessage]:
    return [
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=f"Question: {question}{_record_block(student_text)}"),
    ]


# ---------------------------------------------------------------- researcher note

NOTE_SYSTEM = f"""Task: note
You are a researcher writing a short evidence note for one sub-question.
{GROUNDING_RULE}
Write 2-5 bullet points in English with exact figures and inline citations [n].
If nothing relevant is in the context, write exactly: "Not found in the provided documents." """


def note_messages(main_question: str, sub_question: str, context: str) -> list[BaseMessage]:
    return [
        SystemMessage(content=NOTE_SYSTEM),
        HumanMessage(
            content=(
                f"Main question: {main_question}\nSub-question: {sub_question}\n\n"
                f"Context:\n{context or '(no documents found)'}"
            )
        ),
    ]


# ---------------------------------------------------------------- gap check

GAP_SYSTEM = """Task: gap
You check whether research notes are enough to answer the main question.
If important information is missing, return a JSON list with at most 2 NEW sub-questions
(do not repeat the ones already researched). If the notes are enough, or the documents
clearly do not cover the gap, return exactly: none"""


def gap_messages(question: str, notes_text: str) -> list[BaseMessage]:
    return [
        SystemMessage(content=GAP_SYSTEM),
        HumanMessage(content=f"Main question: {question}\n\nNotes so far:\n{notes_text}"),
    ]


# ---------------------------------------------------------------- writer

WRITER_SYSTEM = f"""Task: report
You write the final answer for a student of {COLLEGE_NAME} from research notes.
Use ONLY facts from the notes and keep their citation numbers exactly as given, like [1] or [3].
Never add facts that are not in the notes. If the notes say something was not found,
state clearly that the college documents provided do not contain it.
If the asking student's record is given, use it for their personal facts (call it "your record",
no citation number) and copy its numbers and dates exactly. Never mention any other student.
{LANGUAGE_RULE}
Format: markdown with a one-line direct answer first, then short sections or a comparison
table where helpful, then a brief "Bottom line". Do not add a sources list; it is added separately."""


def writer_messages(
    question: str, notes_text: str, sources_text: str, student_text: str | None = None
) -> list[BaseMessage]:
    return [
        SystemMessage(content=WRITER_SYSTEM),
        HumanMessage(
            content=(
                f"Question: {question}{_record_block(student_text)}\n\n"
                f"Research notes:\n{notes_text}\n\nSources:\n{sources_text}"
            )
        ),
    ]
