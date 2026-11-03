"""Deep path: planner -> researcher loop (with gap check) -> writer.

LLM calls are bounded: 1 plan + (1 note per sub-question + 1 gap check) per loop + 1 report,
with at most MAX_SUB_QUESTIONS sub-questions in total.
"""

from __future__ import annotations

import json
import re

from smart_campus_assistant import prompts, student_data
from smart_campus_assistant.config import get_settings
from smart_campus_assistant.simple_rag import call_llm
from smart_campus_assistant.state import AgentState, Citation, Note

MIN_PLAN, MAX_PLAN = 3, 5
MAX_GAP_QUESTIONS = 2
MAX_SUB_QUESTIONS = 6  # hard cap on plan size across all loops (keeps deep cost bounded)
CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


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


def _cited_numbers(text: str) -> list[int]:
    """Citation numbers in order of first appearance."""
    out: list[int] = []
    for group in CITE_RE.findall(text):
        for n in group.split(","):
            n = int(n.strip())
            if n not in out:
                out.append(n)
    return out


def _renumber(text: str, mapping: dict[int, int]) -> str:
    """Rewrite [a, b] markers with ``mapping``; unknown numbers are dropped."""

    def repl(m: re.Match) -> str:
        new = [mapping[int(n.strip())] for n in m.group(1).split(",") if int(n.strip()) in mapping]
        return "[" + ", ".join(str(n) for n in dict.fromkeys(new)) + "]" if new else ""

    return CITE_RE.sub(repl, text)


def _citation_key(c: Citation) -> tuple:
    return (c.get("source"), c.get("page"), c.get("snippet", "")[:80])


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
    plan = parse_list(text, MAX_PLAN)
    if len(plan) < MIN_PLAN and question not in plan:
        plan.append(question)  # unparseable or tiny plan: research the question itself too
    return {"plan": plan, "research_loops": 0, "usage": [record]}


def research(state: AgentState) -> dict:
    """Research every pending sub-question, then (if loops remain) check for gaps."""
    from smart_campus_assistant import retriever  # lazy: lets fake mode stub the module

    question = state["question"]
    loops_done = state.get("research_loops", 0)
    records, notes, all_docs = [], [], list(state.get("docs") or [])

    for sub_q in _pending(state):
        docs = retriever.search(sub_q)  # one college: no filter needed
        summary, record = call_llm(
            "answer", "researcher", prompts.note_messages(question, sub_q, retriever.format_docs(docs))
        )
        records.append(record)
        cited = [n for n in _cited_numbers(summary) if 1 <= n <= len(docs)]
        used = [docs[n - 1] for n in cited] if cited else docs
        # Note citations are renumbered locally to match their order in the list.
        summary = _renumber(summary, {n: i + 1 for i, n in enumerate(cited)}) if cited else summary
        notes.append(Note(sub_question=sub_q, summary=summary, citations=[retriever.to_citation(d) for d in used]))
        all_docs.extend(used)

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


def write_report(state: AgentState) -> dict:
    """Write the final report; citations are de-duplicated and renumbered 1..n in order of use."""
    notes = state.get("notes", [])
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
        summary = _renumber(note["summary"], mapping) if _cited_numbers(note["summary"]) else note["summary"]
        numbered_notes.append(Note(sub_question=note["sub_question"], summary=summary, citations=note["citations"]))

    sources_text = "\n".join(
        f"[{i}] {c.get('college')} | {c.get('source')} p.{c.get('page')}" for i, c in enumerate(global_cites, 1)
    ) or "(none)"
    report, record = call_llm(
        "answer", "writer", prompts.writer_messages(
            state["question"], _notes_text(numbered_notes), sources_text, _student_text(state)
        )
    )

    # Keep only citations the report uses, renumbered in order of first use.
    used = [n for n in _cited_numbers(report) if 1 <= n <= len(global_cites)]
    if used:
        report = _renumber(report, {n: i + 1 for i, n in enumerate(used)})
        citations = [global_cites[n - 1] for n in used]
    else:
        citations = global_cites
    return {"answer": report, "citations": citations, "usage": [record]}
