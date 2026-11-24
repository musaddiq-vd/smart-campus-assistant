"""Deep path: planner -> researcher loop (with gap check) -> writer.

LLM calls are bounded: 1 plan + (1 note per sub-question + 1 gap check) per loop + 1 report,
with at most MAX_SUB_QUESTIONS sub-questions in total.

The sub-questions of one loop are independent, so they are researched concurrently
(``RESEARCH_WORKERS`` threads); results are kept in plan order, so the output is the same
as a sequential run, only faster.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from langchain_core.documents import Document

from smart_campus_assistant import fee_calculator, grounding, prompts, student_data
from smart_campus_assistant.config import get_settings
from smart_campus_assistant.personal import branch_scoped_query
from smart_campus_assistant.simple_rag import call_llm
from smart_campus_assistant.state import AgentState, Citation, Note, UsageRecord

MAX_PLAN = 5  # plan size: the question itself + up to 4 planner sub-questions
MAX_GAP_QUESTIONS = 2
MAX_SUB_QUESTIONS = 6  # hard cap on plan size across all loops (keeps deep cost bounded)
CITE_RE = grounding.CITE_RE


# ---------------------------------------------------------------- parsing helpers


def _item_text(item: object) -> str:
    """Models sometimes return [{"sub-question": "..."}] instead of plain strings."""
    if isinstance(item, dict):
        values = [v for v in item.values() if isinstance(v, str) and v.strip()]
        return values[0].strip() if values else ""
    return str(item).strip()


def parse_list(text: str, limit: int = MAX_PLAN) -> list[str]:
    """Parse a JSON list of strings, or numbered / bulleted lines, into at most ``limit`` items."""
    items: list[str] = []
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                items = [_item_text(x) for x in data]
                items = [x for x in items if x]
        except json.JSONDecodeError:
            items = []
    if not items:
        for line in text.splitlines():
            m = re.match(r"^\s*(?:\d+[.)]|[-*•])\s+(.+)$", line)
            if m:
                items.append(m.group(1).strip().strip('"'))
    seen: set[str] = set()
    unique = []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            unique.append(item)
    return unique[:limit]


_cited_numbers = grounding.cited_numbers  # citation numbers in order of first appearance
_renumber = grounding.renumber  # rewrite [a, b] markers with a mapping; unknown numbers are dropped
_citation_key = grounding.citation_key  # same identity as the chat page's source de-duplication


def _pending(state: AgentState) -> list[str]:
    done = {n["sub_question"] for n in state.get("notes", [])}
    return [q for q in state.get("plan", []) if q not in done]


def _student_text(state: AgentState) -> str | None:
    """Own record of the logged-in student (None for guests / unknown IDs). Never other students."""
    record = student_data.get_student(state.get("student_id"))
    return student_data.student_context(record) if record else None


def _notes_text(notes: list[Note]) -> str:
    return "\n\n".join(f"### {n['sub_question']}\n{n['summary']}" for n in notes) or "(no notes)"


# ---------------------------------------------------------------- nodes


def plan_research(state: AgentState) -> dict:
    question = state["question"]
    text, record = call_llm("answer", "planner", prompts.planner_messages(question, _student_text(state)))
    sub_questions = [q for q in parse_list(text, MAX_PLAN) if q.lower() != question.lower()]
    # The question itself is always researched first: documents often state the combined answer
    # (e.g. a published total) that the planner's narrower sub-questions miss.
    plan = [question] + sub_questions[: MAX_PLAN - 1]
    return {"plan": plan, "research_loops": 0, "usage": [record]}


def _research_one(question: str, sub_q: str, own_record: dict | None) -> tuple[Note, list[Document], UsageRecord]:
    """Search and write the evidence note for ONE sub-question (safe to run in a worker thread)."""
    from smart_campus_assistant import retriever  # lazy: lets fake mode stub the module

    docs = retriever.search(branch_scoped_query(sub_q, own_record))  # one college: no filter needed
    summary, record = call_llm(
        "answer", "researcher", prompts.note_messages(question, sub_q, retriever.format_docs(docs))
    )
    cited = [n for n in _cited_numbers(summary) if 1 <= n <= len(docs)]
    used = [docs[n - 1] for n in cited]
    # Note citations are renumbered locally to match their order in the list; numbers that point at no
    # document are dropped. A note that cites nothing (e.g. "Not found ...") brings no citations along.
    summary = _renumber(summary, {n: i + 1 for i, n in enumerate(cited)})
    note = Note(sub_question=sub_q, summary=summary, citations=[retriever.to_citation(d) for d in used])
    return note, used or docs, record


def research(state: AgentState) -> dict:
    """Research every pending sub-question (concurrently), then (if loops remain) check for gaps."""
    question = state["question"]
    loops_done = state.get("research_loops", 0)
    records, notes, all_docs = [], [], list(state.get("docs") or [])
    # A signed-in student's own branch keeps sub-questions like "Semester 5 subjects" on their branch.
    own_record = student_data.get_student(state["student_id"]) if state.get("student_id") else None

    pending = _pending(state)
    workers = min(max(1, get_settings().research_workers), len(pending))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research") as pool:
            # map() yields in plan order, whatever order the calls finish in.
            results = list(pool.map(lambda sub_q: _research_one(question, sub_q, own_record), pending))
    else:
        results = [_research_one(question, sub_q, own_record) for sub_q in pending]
    for note, used, record in results:
        notes.append(note)
        all_docs.extend(used)
        records.append(record)

    update: dict = {"notes": notes, "research_loops": loops_done + 1, "docs": all_docs, "usage": records}

    plan = list(state.get("plan", []))
    if loops_done < get_settings().max_research_loops and len(plan) < MAX_SUB_QUESTIONS:
        all_notes = list(state.get("notes", [])) + notes
        text, record = call_llm("answer", "researcher", prompts.gap_messages(question, _notes_text(all_notes)))
        records.append(record)
        if text.strip().lower().strip(".\"'") != "none":
            known = {q.lower() for q in plan}
            new = [q for q in parse_list(text, MAX_GAP_QUESTIONS) if q.lower() not in known and q.lower() != "none"]
            room = MAX_SUB_QUESTIONS - len(plan)
            if new[:room]:
                update["plan"] = plan + new[:room]
    return update


def should_continue(state: AgentState) -> str:
    """Loop while sub-questions are pending and extra loops remain."""
    if _pending(state) and state.get("research_loops", 0) <= get_settings().max_research_loops:
        return "research"
    return "write"


def _own_record_for_totals(state: AgentState) -> dict | None:
    """The signed-in student's own record when the question is about them ("my yearly cost"), else None."""
    from smart_campus_assistant.router import FIRST_PERSON  # lazy: the router imports the graph's other nodes

    if not state.get("student_id") or not FIRST_PERSON.search(state["question"].lower()):
        return None
    return student_data.get_student(state["student_id"])


def fee_totals_note(state: AgentState) -> Note | None:
    """The fee totals computed in code, as a research note citing them (None if the question asks for none).

    The writer gets them first, so it copies the per-semester, per-year and one-time totals
    instead of adding up table amounts across notes.
    """
    from smart_campus_assistant import retriever  # lazy: lets fake mode stub the module

    doc = fee_calculator.fee_totals_document(state["question"], _own_record_for_totals(state))
    if doc is None:
        return None
    heading, *lines = doc.page_content.splitlines()
    summary = "\n".join(f"{line.rstrip('.')} [1]." for line in lines if line.startswith("- "))
    return Note(sub_question=heading, summary=summary, citations=[retriever.to_citation(doc)])


def _source_line(number: int, citation: Citation) -> str:
    page = citation.get("page")
    return f"[{number}] {citation.get('college')} | {citation.get('source')}" + (f" p.{page}" if page is not None else "")


def write_report(state: AgentState) -> dict:
    """Write the final report; citations are de-duplicated and renumbered 1..n in order of use.

    A report that cites nothing and uses nothing from the student's own record becomes the
    standard not-found message (grounding.check_answer).
    """
    totals = fee_totals_note(state)
    notes = ([totals] if totals else []) + list(state.get("notes", []))
    global_cites: list[Citation] = []
    index: dict[tuple, int] = {}
    numbered_notes: list[Note] = []
    for note in notes:
        mapping = {}
        for i, cite in enumerate(note["citations"], start=1):
            key = _citation_key(cite)
            if key not in index:
                global_cites.append(cite)
                index[key] = len(global_cites)
            mapping[i] = index[key]
        summary = _renumber(note["summary"], mapping)  # also drops numbers the note has no source for
        numbered_notes.append(Note(sub_question=note["sub_question"], summary=summary, citations=note["citations"]))

    sources_text = "\n".join(_source_line(i, c) for i, c in enumerate(global_cites, 1)) or "(none)"
    student_text = _student_text(state)
    report, record = call_llm(
        "answer", "writer", prompts.writer_messages(
            state["question"], _notes_text(numbered_notes), sources_text, student_text
        )
    )
    # Keep only citations the report uses, renumbered in order of first use; made-up numbers are dropped.
    report, citations = grounding.check_answer(report, global_cites, record_text=student_text)
    return {"answer": report, "citations": citations, "usage": [record]}
