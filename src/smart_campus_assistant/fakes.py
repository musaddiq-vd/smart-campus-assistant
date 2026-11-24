"""Offline fake mode for tests, ``eval --fake`` and the UI demo mode.

``enable_fake_mode()`` patches ``models.get_chat_model`` with a deterministic, extractive
fake chat model and ``retriever.search`` / ``format_docs`` / ``to_citation`` /
``get_vector_store`` with offline versions. Search runs over the real college documents
and notices (see ``local_search.py``); the small built-in ``FAKE_DOCS`` set is only a
fallback for when those files are missing. ``disable_fake_mode()`` restores the originals.
No AWS calls are made.

Student records are NOT faked: the real local ``data/students.json`` is synthetic and offline.
Only if that file is missing or empty does fake mode fall back to 2 built-in demo students.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
import types
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from smart_campus_assistant import fee_calculator, grounding, models, student_data
from smart_campus_assistant.config import COLLEGE_KEY, COLLEGES
from smart_campus_assistant.state import Citation

RETRIEVER_MODULE = "smart_campus_assistant.retriever"

# (role, task) for every fake LLM call since the last enable_fake_mode(); handy in tests.
CALL_LOG: list[tuple[str, str]] = []

# ---------------------------------------------------------------- fake documents


def _doc(doc_type: str, source: str, page: int, text: str) -> Document:
    return Document(
        page_content=text,
        metadata={
            "college": COLLEGE_KEY,
            "doc_type": doc_type,
            "source": source,
            "page": page,
            "chunk_id": f"{source}#s{page}",
        },
    )


FAKE_DOCS: list[Document] = [
    _doc("fees", "fee_structure.md", 1,
         "B.Tech tuition fee 2026-27 is Rs 95,000 per semester for CSE and ECE and Rs 85,000 per semester "
         "for ME and CE. Fees are paid each semester through the student portal by UPI, card or NEFT."),
    _doc("fees", "fee_structure.md", 3,
         "Late fee rule: a late fee of Rs 100 per day is charged after the semester fee due date, up to Rs 5,000. "
         "Students with dues pending more than 30 days do not get their exam hall ticket."),
    _doc("exams", "exam_notification_odd_2026.md", 1,
         "End-semester exams for the Odd semester 2026-27 run from 2026-11-24 to 2026-12-10. Hall tickets are "
         "released on 2026-11-17 to students with no fee dues and at least 75% attendance."),
    _doc("rules", "academic_rules.md", 2,
         "Attendance rule: a minimum of 75% attendance in every course is required to sit the end-semester exam. "
         "Between 65% and 75% the student may apply for condonation with a medical certificate; below 65% is debarred."),
    _doc("library", "library_rules.md", 1,
         "Library rules: UG students may borrow up to 4 books for 14 days. The library fine is Rs 5 per day per "
         "book after the due date. A lost book must be replaced or paid at twice its price."),
    _doc("hostel", "hostel_rules.md", 1,
         "Hostel fee is Rs 60,000 per year for a shared room; mess charges are Rs 3,500 per month. "
         "Hostel gates close at 10:30 pm and ragging in any form leads to expulsion."),
    _doc("placements", "placements_2025_26.md", 1,
         "CSE placements 2025-26: 92% of eligible students placed, median package Rs 9.5 LPA, highest Rs 32 LPA."),
    _doc("placements", "placements_2025_26.md", 2,
         "ECE placements 2025-26: 85% of eligible students placed, median package Rs 7.2 LPA, highest Rs 24 LPA."),
    _doc("scholarships", "scholarships.md", 1,
         "Merit scholarship of Rs 20,000 per semester for students with CGPA 8.5 or above and no backlogs. "
         "Need-based scholarship covers up to 50% of tuition for family income below Rs 3 lakh."),
    _doc("branches", "branches.md", 1,
         "XYZ College of Engineering offers B.Tech in CSE (intake 120), ECE (intake 90), ME (intake 60) and "
         "CE (intake 60). Lateral entry is allowed in the second year for diploma holders."),
]

_STOPWORDS = {"the", "a", "an", "of", "at", "in", "is", "for", "and", "to", "what", "how", "much", "kya",
              "hai", "ka", "ki", "ke", "mein", "me", "kitna", "kitni", "which", "does", "do", "with",
              "my", "mera", "meri", "mere", "i", "xyz", "college", "engineering"}

# Topic words -> doc_type, so "attendance" finds the rules doc and "fine" the library doc.
_TOPIC_HINTS = {
    "attendance": "rules", "condonation": "rules", "fine": "library", "book": "library", "books": "library",
    "late": "fees", "due": "fees", "dues": "fees", "fees": "fees", "fee": "fees", "exam": "exams",
    "exams": "exams", "hall": "exams", "placement": "placements", "placements": "placements",
    "package": "placements", "scholarship": "scholarships", "hostel": "hostel", "mess": "hostel",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS}


def _branch_hits(text: str) -> list[str]:
    from smart_campus_assistant.simple_rag import mentioned_branches

    return mentioned_branches(text)


def fake_search(query: str, k: int | None = None, college: str | None = None) -> list[Document]:
    """Offline search: the real documents and notices when present, else the built-in FAKE_DOCS."""
    try:
        from smart_campus_assistant import local_search

        if local_search.chunk_count():
            return local_search.search(query, k=k, college=college)
    except Exception:  # noqa: BLE001  (a broken data folder must not break the demo)
        pass
    return builtin_search(query, k=k, college=college)


def builtin_search(query: str, k: int | None = None, college: str | None = None) -> list[Document]:
    """Keyword-overlap search over FAKE_DOCS; documents that share no word with the query are left out."""
    k = k or 3
    pool = [d for d in FAKE_DOCS if college is None or d.metadata["college"] == college]
    q = _tokens(query)
    hinted = {_TOPIC_HINTS[t] for t in q if t in _TOPIC_HINTS}
    branches = {b.lower() for b in _branch_hits(query)}

    def score(doc: Document) -> float:
        meta = doc.metadata
        s = len(q & _tokens(doc.page_content + " " + meta["doc_type"]))
        if meta["doc_type"] in hinted:
            s += 2
        s += 2 * len(branches & _tokens(doc.page_content))
        return s

    ranked = sorted(((score(d), i, d) for i, d in enumerate(pool)), key=lambda row: (-row[0], row[1]))
    return [d for s, _, d in ranked if s > 0][:k]


class OfflineVectorStore:
    """Stand-in for the vector store in fake mode.

    Offline search reads the documents and ``notices.json`` directly, so adding or deleting
    chunks needs no work here: a saved notice is searchable at once and a deleted one is gone.
    """

    def add_documents(self, documents: list[Document], ids: list[str] | None = None, **kwargs: Any) -> list[str]:
        return list(ids or [d.id or "" for d in documents])

    def delete(self, ids: list[str] | None = None, **kwargs: Any) -> bool:
        return True

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        return fake_search(query, k=k)


def fake_get_vector_store() -> OfflineVectorStore:
    return OfflineVectorStore()


def fake_format_docs(docs: list[Document]) -> str:
    """Same layout as retriever.format_docs (a block without a page, like the computed totals, has no "p.")."""
    if not docs:
        return "No relevant documents found."
    blocks = []
    for i, doc in enumerate(docs, 1):
        m = doc.metadata
        name = COLLEGES.get(m.get("college", ""), m.get("college", "unknown"))
        page = f" p.{m['page']}" if m.get("page") is not None else ""
        blocks.append(f"[{i}] ({name} | {m.get('doc_type')} | {m.get('source')}{page})\n{doc.page_content}")
    return "\n\n".join(blocks)


def fake_to_citation(doc: Document) -> Citation:
    from smart_campus_assistant.ranking import section_of  # same heading the live citations carry

    m = doc.metadata
    section = section_of(doc)
    # Like retriever.to_citation: the heading line is shown as the section, so the quote starts after it.
    first, sep, rest = doc.page_content.partition("\n")
    body = rest if sep and section and first.strip() == section.strip() and rest.strip() else doc.page_content
    text = " ".join(body.split())
    return Citation(
        source=m.get("source", "unknown"),
        college=m.get("college", "unknown"),
        page=m.get("page"),
        snippet=text if len(text) <= 160 else text[:160].rsplit(" ", 1)[0] + "...",
        section=section,
    )


# ---------------------------------------------------------------- demo students (only if the JSON is missing)

DEMO_AS_OF = "2026-11-03"
DEMO_STUDENTS: list[dict] = [
    {
        "student_id": "XYZ24CSE001", "name": "Aarav Sharma", "branch": "CSE", "year": 3, "semester": 5,
        "email": "aarav.sharma@xyz.example.edu", "hostel": {"block": "A", "room": "A-214"},
        "fees": {"semester": "Odd 2026-27", "total": 95000, "paid": 60000, "due": 35000, "due_date": "2026-11-30",
                 "status": "partially_paid", "scholarship": None,
                 "transactions": [{"date": "2026-07-10", "amount": 60000, "mode": "UPI", "receipt_no": "XYZR-24001"}]},
        "library": {"books_issued": [{"book_id": "LIB-1021", "title": "Operating System Concepts",
                                      "author": "Silberschatz", "issue_date": "2026-10-10",
                                      "due_date": "2026-10-24", "status": "overdue"}], "fine_due": 50},
        "attendance": {"overall_pct": 81.0, "courses": [{"code": "CS301", "name": "Operating Systems", "pct": 72.0},
                                                        {"code": "CS302", "name": "Computer Networks", "pct": 88.0}]},
        "results": {"cgpa": 8.12, "last_sgpa": 8.4, "backlogs": 0},
        "exams": {"upcoming": [{"course_code": "CS301", "course_name": "Operating Systems", "type": "End-sem",
                                "date": "2026-11-24", "time": "10:00", "room": "LH-2"}]},
    },
    {
        "student_id": "XYZ23ECE014", "name": "Priya Nair", "branch": "ECE", "year": 4, "semester": 7,
        "email": "priya.nair@xyz.example.edu", "hostel": None,
        "fees": {"semester": "Odd 2026-27", "total": 95000, "paid": 95000, "due": 0, "due_date": "2026-08-31",
                 "status": "paid", "scholarship": {"name": "Merit scholarship", "amount": 20000},
                 "transactions": [{"date": "2026-08-01", "amount": 75000, "mode": "NEFT", "receipt_no": "XYZR-23114"}]},
        "library": {"books_issued": [], "fine_due": 0},
        "attendance": {"overall_pct": 90.5, "courses": [{"code": "EC701", "name": "VLSI Design", "pct": 91.0}]},
        "results": {"cgpa": 8.9, "last_sgpa": 9.1, "backlogs": 0},
        "exams": {"upcoming": [{"course_code": "EC701", "course_name": "VLSI Design", "type": "End-sem",
                                "date": "2026-11-26", "time": "14:00", "room": "EC-101"}]},
    },
]


def demo_students() -> dict[str, dict]:
    return {s["student_id"]: dict(s, as_of=DEMO_AS_OF) for s in DEMO_STUDENTS}


# ---------------------------------------------------------------- fake chat model


def _heuristic_route(question: str) -> dict:
    from smart_campus_assistant.router import heuristic_route  # avoid an import cycle at load time

    route, reason = heuristic_route(question)
    return {"route": route, "reason": reason.replace("Heuristic: ", "")}


def _field(text: str, label: str) -> str:
    m = re.search(rf"^{label}:\s*(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


_BLOCK_RE = re.compile(r"^\[(\d+)\] \((.*?)\)\n(.*?)(?=^\[\d+\] \(|\Z)", flags=re.MULTILINE | re.DOTALL)
_TABLE_RULE_RE = re.compile(r"^\|?[\s:|-]+\|?$")


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _block_statements(body: str) -> tuple[str, list[str]]:
    """(heading path, statements) of one context block.

    Chunks from the real documents start with their heading path on the first line. A table row
    becomes "Row label - Column: value; ..." so a single row still reads as a fact; prose is
    split into sentences.
    """
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    if not lines:
        return "", []
    heading = lines[0] if len(lines) > 1 else ""
    statements: list[str] = []
    header: list[str] | None = None
    previous_row: list[str] | None = None
    for line in lines[1:] if heading else lines:
        if line.startswith("|"):
            if _TABLE_RULE_RE.match(line):
                header = previous_row
                if header is not None and statements and statements[-1] == " | ".join(c for c in header if c):
                    statements.pop()  # that row was the header, not a fact
                continue
            cells = _table_cells(line)
            if header and len(header) == len(cells) and len(cells) > 1:
                pairs = "; ".join(f"{h}: {c}" for h, c in zip(header[1:], cells[1:], strict=True))
                statements.append(f"{cells[0]} - {pairs}")
            else:  # no header in this chunk (it was cut off by chunking)
                statements.append(" | ".join(c for c in cells if c))
            previous_row = cells
            continue
        header = previous_row = None
        text = re.sub(r"^[-*]\s+", "", line)
        statements.extend(p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text) if p.strip())
    # Notice chunks carry a "Category: ... For: ..." line that is metadata, not a fact.
    return heading, [st for st in statements if len(st) > 3 and not st.startswith(("Category:", "For:"))]


def _context_facts(
    text: str,
    limit: int = 2,
    question: str = "",
    fallback: bool = True,
    min_hits: int = 1,
    min_coverage: float = 0.0,
) -> list[tuple[int, str]]:
    """The statements of the numbered context blocks that best match the question: [(n, statement)].

    ``min_coverage`` is the share of the question's topic words a statement (with its heading) must
    contain. It stops one generic shared word ("campus") from passing an unrelated fact off as an answer.
    When nothing matches, the first statement of each block is used (or nothing with ``fallback=False``).
    """
    from smart_campus_assistant.local_search import tokenize

    wanted = set(tokenize(question))
    scored: list[tuple[float, int, int, str]] = []
    firsts: list[tuple[int, str]] = []
    for m in _BLOCK_RE.finditer(text):
        n = int(m.group(1))
        heading, statements = _block_statements(m.group(3))
        if not statements:
            continue
        firsts.append((n, statements[0].rstrip(".")))
        heading_terms = wanted & set(tokenize(heading))
        heading_hits = len(heading_terms)
        for pos, statement in enumerate(statements):
            body_terms = wanted & set(tokenize(statement))
            hits = len(body_terms)
            covered = len(body_terms | heading_terms) / len(wanted) if wanted else 0.0
            if hits >= min_hits and covered >= min_coverage:
                # Body matches count most; the heading and an earlier block break ties.
                scored.append((hits + 0.5 * heading_hits - 0.01 * n, n, pos, statement.rstrip(".")))
    if not scored:
        return firsts[:limit] if fallback else []
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    best = scored[0][0]
    picked = [(n, st) for score, n, _, st in scored if score >= 0.6 * best][:limit]
    return sorted(picked, key=lambda fact: fact[0])


# Question words -> record line prefix used by student_data.student_context.
_RECORD_SECTIONS = [
    (("book", "books", "fine", "library", "kitab"), "Library"),
    (("attendance", "attend"), "Attendance"),
    (("result", "results", "cgpa", "sgpa", "backlog", "marks"), "Results"),
    (("exam", "exams", "hall ticket", "paper"), "Upcoming exams"),
    (("hostel", "room"), "Hostel"),
    (("fee", "fees", "due", "dues", "paid", "payment"), "Fees"),
]


def _record_line(question: str, record_text: str) -> str:
    """The record line (plus its first detail line) matching the question topic."""
    q = question.lower()
    prefix = next((p for words, p in _RECORD_SECTIONS if any(w in q for w in words)), "Fees")
    lines = record_text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            detail = lines[i + 1].strip() if i + 1 < len(lines) and lines[i + 1].startswith("  ") else ""
            return f"{line.rstrip(':')}{'; ' + detail.lstrip('- ') if detail else ''}"
    return lines[0] if lines else "(empty record)"


# Words that link the parts of a comparison; dropped to leave the topic ("placements", "fees").
_COMPARE_WORDS = re.compile(
    r"\b(compare|comparison|between|versus|vs\.?|and|aur|or|ya|which|branch|branches|is|are|better|best|the|of|"
    r"for|in|what|how|do|does)\b",
    flags=re.IGNORECASE,
)
_PART_SPLIT = re.compile(r"\s*(?:,|;|\?|\band\b|\baur\b|\bincluding\b|\bplus\b)\s*", flags=re.IGNORECASE)


def _is_personal(question: str) -> bool:
    from smart_campus_assistant.router import is_personal  # avoid an import cycle at load time

    return is_personal(question)


def _plan_for(question: str) -> list[str]:
    """Sub-questions for the offline planner: one per named branch, else one per part of the question."""
    from smart_campus_assistant.simple_rag import BRANCH_ALIASES

    codes = _branch_hits(question)
    if len(codes) >= 2:
        topic = question
        for aliases in BRANCH_ALIASES.values():
            for alias in aliases:
                topic = re.sub(rf"\b{re.escape(alias)}\b", " ", topic, flags=re.IGNORECASE)
        topic = " ".join(_COMPARE_WORDS.sub(" ", topic).replace("?", " ").split()) or "details"
        return [f"{code} {topic}" for code in codes[:4]]
    parts = [part.strip() for part in _PART_SPLIT.split(question) if len(part.strip()) > 12]
    return [f"{part}?" for part in parts[:4]] if len(parts) >= 2 else []


# Openers that only continue the last topic; the fake rewrite drops them before adding that topic.
_FOLLOW_UP_OPENER = re.compile(r"^\s*(and|or|but|so|then|what about|how about|same for|aur|ya|toh|lekin)\b[\s,]*",
                               flags=re.IGNORECASE)


def _swap_branch(rest: str, previous: str) -> str | None:
    """"ECE ka?" after "What is the CSE tuition fee?" -> "What is the ECE tuition fee?" (None if it does not apply).

    Only for a follow-up that names one other branch and nothing else, the case the rewrite prompt's own
    example shows. Appending the old question instead would leave two branches in it and send a one-fact
    question down the deep (comparison) path.
    """
    from smart_campus_assistant.local_search import tokenize
    from smart_campus_assistant.simple_rag import BRANCH_ALIASES

    new, old = _branch_hits(rest), _branch_hits(previous)
    if len(new) != 1 or len(old) != 1 or new == old:
        return None
    if set(tokenize(rest)) - set(tokenize(" ".join(BRANCH_ALIASES.get(new[0], ())))):
        return None  # the follow-up adds a topic of its own ("ECE ka hostel?")
    swapped = previous
    for alias in sorted(BRANCH_ALIASES.get(old[0], ()), key=len, reverse=True):
        swapped = re.sub(rf"\b{re.escape(alias)}\b", new[0], swapped, flags=re.IGNORECASE)
    return swapped


def _fake_rewrite(human: str) -> str:
    """Offline stand-in for the follow-up rewrite: the follow-up plus the topic of the last question.

    "aur hostel?" after "What is the CSE tuition fee?" becomes "hostel? (about: What is the CSE tuition fee?)";
    "aur ECE ka?" swaps the branch: "What is the ECE tuition fee?".
    Who the question is about stays exactly as the follow-up says, like the real rewrite.
    """
    follow_up = human.rsplit("Follow-up:", 1)[-1].split("\nStandalone question:", 1)[0].strip()
    asked = re.findall(r"^User:\s*(.+)$", human, flags=re.MULTILINE)
    if not asked or not follow_up:
        return follow_up
    from smart_campus_assistant.followup import FOLLOW_UP_START, REFERENCE_WORDS

    if not (FOLLOW_UP_START.search(follow_up) or REFERENCE_WORDS.search(follow_up)):
        return follow_up  # short but complete ("Hostel fee kitni hai?"): a new topic, kept as asked
    rest = _FOLLOW_UP_OPENER.sub("", follow_up).strip() or follow_up
    return _swap_branch(rest, asked[-1].strip()) or f"{rest} (about: {asked[-1].strip()})"


class FakeCampusChatModel(BaseChatModel):
    """Deterministic chat model that answers each prompt type sensibly and reports token usage."""

    role: str = "answer"

    @property
    def _llm_type(self) -> str:
        return "fake-campus"

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        system = str(messages[0].content) if messages else ""
        human = str(messages[-1].content) if messages else ""
        task_match = re.match(r"Task:\s*(\w+)", system)
        task = task_match.group(1) if task_match else "answer"
        CALL_LOG.append((self.role, task))
        text = self._respond(task, human)
        prompt_chars = sum(len(str(m.content)) for m in messages)
        in_tok, out_tok = max(1, prompt_chars // 4), max(1, len(text) // 4)
        message = AIMessage(
            content=text,
            usage_metadata={"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _respond(self, task: str, human: str) -> str:
        if task == "rewrite":
            return _fake_rewrite(human)
        if task == "route":
            # The last "Question:" line is the user's question (earlier ones are few-shot examples).
            questions = re.findall(r"^Question:\s*(.+)$", human, flags=re.MULTILINE)
            return json.dumps(_heuristic_route(questions[-1] if questions else human))
        if task == "plan":
            return json.dumps(_plan_for(_field(human, "Question")))
        if task == "gap":
            return "none"
        if task == "personal":
            question = _asked_question(human)
            record_text = human.split("Your record:", 1)[-1].split("College documents:", 1)[0].strip()
            docs_text = human.split("College documents:", 1)[-1].rsplit("Question:", 1)[0]
            answer = f"As per your record: {_record_line(question, record_text)}."
            for n, fact in _totals_facts(docs_text, limit=1, question=question):
                answer += f" Fee totals for your category: {fact} [{n}]."
            # A policy sentence is quoted only when it clearly matches (two topic words), else none at all.
            facts = _context_facts(_without_computed(docs_text), limit=1, question=question, fallback=False, min_hits=2)
            if facts:
                n, fact = facts[0]
                answer += f" College rule: {fact} [{n}]."
            return answer
        if task == "note":
            facts = _context_facts(human.split("Context:", 1)[-1], limit=3, question=_field(human, "Sub-question"))
            if not facts:
                return "Not found in the provided documents."
            return "\n".join(f"- {fact} [{n}]" for n, fact in facts)
        if task == "report":
            return _fake_report(human)
        # task == "answer" (simple path): computed totals first for a total question, like the prompt asks.
        question = _asked_question(human)
        context = human.split("Context:", 1)[-1].rsplit("Question:", 1)[0]
        facts = _totals_facts(context, question=question) or _context_facts(
            context, question=question, fallback=False, min_coverage=0.5
        )
        if not facts:
            return grounding.NOT_FOUND_MESSAGE
        if len(facts) == 1:
            n, fact = facts[0]
            return f"According to the college documents: {fact} [{n}]."
        return "According to the college documents:\n" + "\n".join(f"- {fact} [{n}]" for n, fact in facts)


def _asked_question(human: str) -> str:
    """The question of an answer prompt, without the answer-format hints after it."""
    return human.rsplit("Question:", 1)[-1].split("\n\nAnswer format", 1)[0].strip()


def _is_computed_label(label: str) -> bool:
    return fee_calculator.DOC_TYPE in label


def _without_computed(context: str) -> str:
    """The numbered context without its computed-totals block (numbers unchanged)."""
    return "\n\n".join(m.group(0).strip() for m in _BLOCK_RE.finditer(context) if not _is_computed_label(m.group(2)))


def _totals_facts(context: str, question: str, limit: int = 2) -> list[tuple[int, str]]:
    """Best lines of the "computed fee totals" block for a total question ([] when there is no such block)."""
    if not fee_calculator.wants_fee_totals(question):
        return []
    computed = "\n\n".join(m.group(0).strip() for m in _BLOCK_RE.finditer(context) if _is_computed_label(m.group(2)))
    return _context_facts(computed, limit=limit, question=question) if computed else []


def _fake_report(human: str) -> str:
    """Offline writer: the cited note bullets (computed totals first), plus the record line for own questions."""
    notes = human.split("Research notes:")[-1].split("Sources:")[0]
    computed: list[str] = []
    others: list[str] = []
    for section in re.split(r"^### ", notes, flags=re.MULTILINE):
        target = computed if section.lower().startswith(fee_calculator.DOC_TYPE) else others
        target.extend(line for line in section.splitlines() if line.startswith("- ") and re.search(r"\[\d+\]", line))
    bullets = list(dict.fromkeys(computed))[:3] + list(dict.fromkeys(others))[:6]
    question = _field(human, "Question")
    record_note = ""
    # The student's record is added only when the question is about the student.
    if "Your record" in human and _is_personal(question):
        record_text = human.split("own data):", 1)[-1].split("Research notes:", 1)[0].strip()
        record_note = f"**Your record:** {_record_line(question, record_text)}."
    if not bullets:
        if record_note:
            return f"{record_note}\n\nThe college documents do not contain more on this question."
        return grounding.NOT_FOUND_MESSAGE
    return (
        "**Short answer:** here is what the college documents say.\n\n"
        + "\n".join(bullets)
        + (f"\n\n{record_note}" if record_note else "")
        + "\n\n**Bottom line:** these points are quoted from the cited college documents; open the sources below for the full text."
    )


# ---------------------------------------------------------------- enable / disable

_ORIGINALS: dict[str, Any] = {}
_STUB_CREATED = False


def fake_get_chat_model(role: str = "answer") -> BaseChatModel:
    return FakeCampusChatModel(role=role)


def _with_demo_fallback(original):
    """Wrap load_students: real (synthetic) JSON first, built-in demo students only if it is empty."""

    def load_students(path=None, db_path=None):
        return original(path, db_path) or demo_students()

    load_students.__wrapped__ = original  # type: ignore[attr-defined]
    return load_students


def _retriever_module() -> types.ModuleType:
    """Import the real retriever module, or register a stub if it cannot be imported."""
    global _STUB_CREATED
    try:
        return importlib.import_module(RETRIEVER_MODULE)
    except Exception:
        stub = types.ModuleType(RETRIEVER_MODULE)
        sys.modules[RETRIEVER_MODULE] = stub
        import smart_campus_assistant

        smart_campus_assistant.retriever = stub  # type: ignore[attr-defined]
        _STUB_CREATED = True
        return stub


_RETRIEVER_FAKES = (
    ("search", fake_search),
    ("format_docs", fake_format_docs),
    ("to_citation", fake_to_citation),
    ("get_vector_store", fake_get_vector_store),
)


def is_fake_mode() -> bool:
    return bool(_ORIGINALS)


def enable_fake_mode() -> None:
    """Patch models and retriever with offline fakes (idempotent)."""
    CALL_LOG.clear()
    if _ORIGINALS:
        return
    retriever = _retriever_module()
    _ORIGINALS["models.get_chat_model"] = models.get_chat_model
    models.get_chat_model = fake_get_chat_model  # type: ignore[assignment]
    for name, fake in _RETRIEVER_FAKES:
        _ORIGINALS[f"retriever.{name}"] = getattr(retriever, name, None)
        setattr(retriever, name, fake)
    _ORIGINALS["student_data.load_students"] = student_data.load_students
    student_data.load_students = _with_demo_fallback(student_data.load_students)  # type: ignore[assignment]


def disable_fake_mode() -> None:
    """Restore the originals patched by enable_fake_mode()."""
    global _STUB_CREATED
    if not _ORIGINALS:
        return
    models.get_chat_model = _ORIGINALS.pop("models.get_chat_model")  # type: ignore[assignment]
    student_data.load_students = _ORIGINALS.pop("student_data.load_students")  # type: ignore[assignment]
    retriever = sys.modules.get(RETRIEVER_MODULE)
    for name, _ in _RETRIEVER_FAKES:
        original = _ORIGINALS.pop(f"retriever.{name}", None)
        if retriever is None:
            continue
        if original is None:
            if hasattr(retriever, name):
                delattr(retriever, name)
        else:
            setattr(retriever, name, original)
    if _STUB_CREATED:
        sys.modules.pop(RETRIEVER_MODULE, None)
        import smart_campus_assistant

        if hasattr(smart_campus_assistant, "retriever"):
            delattr(smart_campus_assistant, "retriever")
        _STUB_CREATED = False
    _ORIGINALS.clear()
