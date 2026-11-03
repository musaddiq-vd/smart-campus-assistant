"""Graph state shared by every node."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.documents import Document


class Citation(TypedDict):
    source: str  # relative path, e.g. "iit_bombay/fee_new_entrants_2026.pdf"
    college: str  # folder key, e.g. "iit_bombay"
    page: Optional[int]  # 1-based page number
    snippet: str  # short quote from the chunk


class Note(TypedDict):
    sub_question: str
    summary: str
    citations: list[Citation]


class UsageRecord(TypedDict):
    node: str  # graph node that made the call, e.g. "router"
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class InputState(TypedDict, total=False):
    question: str  # required in practice
    student_id: Optional[str]  # logged-in student, e.g. "XYZ24CSE001"; None for guests


class AgentState(InputState, total=False):
    route: Literal["simple", "deep", "personal"]
    route_reason: str
    docs: list[Document]
    plan: list[str]  # sub-questions made by the planner
    notes: Annotated[list[Note], operator.add]  # appended by the researcher
    research_loops: int
    answer: str  # final markdown answer
    citations: list[Citation]
    usage: Annotated[list[UsageRecord], operator.add]  # one record per LLM call
