"""Prompt templates for every LLM call in the graph.

Each builder returns a list of chat messages. Every system prompt starts with a
"Task: <name>" line, which keeps the prompts self-describing and lets the offline
fake model in ``fakes.py`` tell the prompt types apart.
"""

from __future__ import annotations

import re

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

NUMBERS_RULE = (
    "Totals and costs: when the question asks for a total or a cost, or says \"including\", \"with\" or "
    "\"plus\", name each component with its amount and then the total, and show the sum, for example "
    "\"Rs 8,000 fee A + Rs 4,000 fee B = Rs 12,000 per semester\". Prefer totals that the documents state "
    "(semester bill, yearly cost); when you add or multiply yourself, use the full amounts from the right "
    "table row and column and check the arithmetic. Keep the period (per semester, per year, one-time)."
)

PROGRESS_RULE = (
    "Semesters and subjects: for the student's own progress use the record's \"Programme\" line (total "
    "semesters, current semester, semesters left) and \"Current semester subjects\" line, and copy them "
    "exactly. For later semesters use the branch curriculum in the documents; it lists only some semesters, "
    "so name the semesters it lists and say plainly which ones it does not."
)

STEPS_RULE = (
    "For \"how can I / how to\" questions, list every option or step the context gives, with its exact "
    "conditions (dates, grades, limits). Copy rule wording and thresholds exactly, e.g. \"at least 75%\", "
    "\"not eligible until ... is cleared\"."
)

# Questions that ask for a sum of parts, or for steps (English and Hinglish).
TOTAL_QUESTION_RE = re.compile(
    r"\b(total|including|inclusive|incl\.?|plus|along with|together with|cost|kharcha|kul|saath|milake)\b",
    flags=re.IGNORECASE,
)
STEPS_QUESTION_RE = re.compile(r"\b(how (?:can|do|to|should)|steps?|process|procedure|kaise)\b", flags=re.IGNORECASE)

TOTAL_HINT = (
    "Answer format for this question: list each component with its amount and period, then the total "
    "with the sum written out (a + b = total). If the parts cover different periods, give the per-semester "
    "total, then the per-year total, then add one-time charges, and state each of these totals."
)
STEPS_HINT = "Answer format for this question: list every option or step with its exact conditions."


def answer_hints(question: str) -> str:
    """Format hints added to the user message for total / how-to questions ("" otherwise).

    Nova Lite follows a short hint next to the question more reliably than a rule in a long system prompt.
    """
    hints = [h for rx, h in ((TOTAL_QUESTION_RE, TOTAL_HINT), (STEPS_QUESTION_RE, STEPS_HINT)) if rx.search(question)]
    return "\n\n" + "\n".join(hints) if hints else ""


# ---------------------------------------------------------------- router

ROUTER_SYSTEM = f"""Task: route
You are a cheap routing classifier for the student helpdesk of {COLLEGE_NAME}
(branches: {BRANCH_LIST}).
Label the user's question as "simple", "deep" or "personal".
- simple: a single fact or rule about the college (a fee amount, a hostel rule, a library fine rate, an exam date).
- deep: comparison (e.g. between branches), multi-step reasoning, planning, or advice that needs several documents.
- personal: about the asking student's OWN data (my fees / dues, books issued to me, my fine, my attendance,
  my results / CGPA, my next exam, my hall ticket, my hostel room, my current semester or subjects, how many
  semesters I have left). Also use personal when the question asks
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
    (
        "Mere kitne semester baki hain aur is semester ke subjects kaunse hain?",
        '{"route": "personal", "reason": "Own semester progress and subjects."}',
    ),
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
{NUMBERS_RULE}
{STEPS_RULE}
{LANGUAGE_RULE}
Be short and direct: 1-4 sentences or a small list. Include amounts, units and dates exactly as written."""


def simple_answer_messages(question: str, context: str) -> list[BaseMessage]:
    return [
        SystemMessage(content=SIMPLE_ANSWER_SYSTEM),
        HumanMessage(
            content=f"Context:\n{context or '(no documents found)'}\n\nQuestion: {question}{answer_hints(question)}"
        ),
    ]


# ---------------------------------------------------------------- personal answer

PERSONAL_ANSWER_SYSTEM = f"""Task: personal
You are the student helpdesk of {COLLEGE_NAME}. The logged-in student asks about their own data.
1. Answer from the student record first. Call it "your record" (for example "As per your record, ...").
2. Use the numbered college documents only for policies and rules (late fee rule, library fine per day,
   minimum attendance, exam rules). Cite each document fact inline as [1], [2]. Never cite the record with a number.
3. Be precise: copy amounts, dates, percentages and counts exactly. Mention days left or overdue when relevant.
   The record already holds computed money facts (days overdue, "Late fee so far", "total payable now",
   the fine of each overdue book and the total "fine due"). State them exactly as written; never redo date
   math or recompute them. For unpaid fees always give the fee status (as written in the record), the due amount,
   the due date, the late fee so far and the total payable now. For library fines give the total fine due and the fine of each overdue book.
4. {PROGRESS_RULE}
5. Never invent data. If the record or documents do not contain the answer, say so.
6. The record belongs to this student only. Do not talk about any other student.
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
Split the question into 2 to 4 focused sub-questions (the full question is searched separately)
that can each be answered by searching the college documents (fees, exam notifications, rules,
hostel, library, branches, scholarships, placements, academic calendar).
Split by topic: each part of the question (each "and" or "including" part) gets its own sub-question.
Split by branch ONLY when the question names or compares branches, with one sub-question per named
branch. Rules that apply to all students need no branch: never add branches, years or topics that the
question (or the student record) does not mention.
When the question asks how something affects a named status, class, eligibility or benefit, add a
sub-question for the criteria of each one, using measurable terms (CGPA, attendance %, amount, date),
e.g. "What CGPA is needed for <name>?".
For cost or total questions, also ask for the totals the documents publish (total semester bill,
total yearly cost, one-time deposits), not only single components such as tuition.
If a student record is given, use it only to make the sub-questions specific (branch, year, dues);
do not ask about the record itself.
Return ONLY a JSON list of strings, e.g. ["What is the late fee rule for unpaid fees?", "..."]"""


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
Every bullet must be stated in the context itself. Never infer or guess a number (a limit, CGPA,
count or amount) that the context does not state for exactly that thing; for a part that is not
stated, write "Not stated in the provided documents".
For a figure from a table, name its row and column (for example "Open / General, hostel resident,
per year: Rs X") and say what a total includes when the document says so.
Copy amounts with their period (per semester, per year, one-time), published totals, thresholds and
rule wording exactly as written (e.g. "at least 75%", "not eligible until ... is cleared").
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
Information is missing when a part of the question has no exact answer yet, or when a note names a rule,
class, category or limit without its criteria (for example "is not eligible for X" without the conditions
for X). Phrase new sub-questions with the words the documents likely use (for example "What CGPA is
needed for <class name>?").
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
If two notes give different figures for the same thing, use the one that quotes the rule with its exact
wording and drop the other; never state both.
Answer every part of the question and nothing else: leave out notes that do not help answer it,
and do not add branches, groups or topics that the question does not mention.
{NUMBERS_RULE} For multi-step totals write the calculation as numbered steps, period by period
(per-semester total, then per-year total, then one-time charges), and state each intermediate total
as well as the final total. Prefer a total stated in the notes over your own sum when both exist.
A published total already includes its parts (for example a hostel-resident bill already includes the
hostel fee): never add those parts again, and use only amounts that appear in the notes.
Keep eligibility conditions and thresholds in the notes' own words (for example keep "no unpaid dues",
do not reword it as "should pay dues"), and keep the citation numbers on every figure.
{STEPS_RULE}
If the asking student's record is given, use it for their personal facts (call it "your record",
no citation number) and copy its numbers and dates exactly. Never mention any other student.
A fact found in the record counts as found: never say the documents lack something the record states.
{PROGRESS_RULE}
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
                f"Research notes:\n{notes_text}\n\nSources:\n{sources_text}{answer_hints(question)}"
            )
        ),
    ]
