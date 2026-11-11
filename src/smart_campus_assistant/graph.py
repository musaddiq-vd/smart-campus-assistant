"""LangGraph wiring:

START -> router -> simple_answer -> END
                -> personal_answer -> END
                -> planner -> researcher* -> writer -> END
                -> END (blank question: the router already answered)

Node names ("router", "simple_answer", "personal_answer", "planner", "researcher", "writer")
are a contract used by the UI to show live progress.
"""

from __future__ import annotations

import time
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from smart_campus_assistant import usage
from smart_campus_assistant.deep_research import plan_research, research, should_continue, write_report
from smart_campus_assistant.personal import personal_answer
from smart_campus_assistant.router import route_question
from smart_campus_assistant.simple_rag import simple_answer
from smart_campus_assistant.state import AgentState, InputState

NODE_NAMES = ("router", "simple_answer", "personal_answer", "planner", "researcher", "writer")


class GraphContext(TypedDict, total=False):
    """Run-time options; ``force_route`` skips the router LLM (used by the always-deep baseline)."""

    force_route: Optional[Literal["simple", "deep", "personal"]]


def _pick_path(state: AgentState) -> str:
    if state.get("answer"):  # the router answered a blank question itself
        return END
    route = state.get("route")
    if route == "simple":
        return "simple_answer"
    if route == "personal":
        return "personal_answer"
    return "planner"


def build_graph():
    builder = StateGraph(AgentState, context_schema=GraphContext, input_schema=InputState)
    builder.add_node("router", route_question)
    builder.add_node("simple_answer", simple_answer)
    builder.add_node("personal_answer", personal_answer)
    builder.add_node("planner", plan_research)
    builder.add_node("researcher", research)
    builder.add_node("writer", write_report)

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", _pick_path, ["simple_answer", "personal_answer", "planner", END])
    builder.add_edge("simple_answer", END)
    builder.add_edge("personal_answer", END)
    builder.add_edge("planner", "researcher")
    builder.add_conditional_edges("researcher", should_continue, {"research": "researcher", "write": "writer"})
    builder.add_edge("writer", END)
    return builder.compile()


graph = build_graph()


def run_question(question: str, force_route: str | None = None, student_id: str | None = None) -> dict:
    """Run one question; returns the final state plus ``summary`` (usage totals) and ``total_latency_ms``.

    ``student_id`` is the logged-in student (None for guests).
    """
    config = {"configurable": {"force_route": force_route}} if force_route else None
    start = time.perf_counter()
    state = graph.invoke({"question": question, "student_id": student_id}, config=config)
    total_ms = (time.perf_counter() - start) * 1000
    result = dict(state)
    result["summary"] = usage.summarize_usage(result.get("usage", []))
    result["total_latency_ms"] = round(total_ms, 1)
    return result
