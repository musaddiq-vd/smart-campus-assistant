"""Simple path: one retrieval and one answer call. Also holds the shared LLM-call helpers."""

from __future__ import annotations

import re

from langchain_core.messages import BaseMessage

from smart_campus_assistant import models, prompts, usage
from smart_campus_assistant.config import BRANCHES, COLLEGE_ALIASES, COLLEGES
from smart_campus_assistant.state import AgentState, UsageRecord

# Words that name a branch in questions (code or common short forms).
BRANCH_ALIASES: dict[str, tuple[str, ...]] = {
    "CSE": ("cse", "computer science", "cs"),
    "ECE": ("ece", "electronics"),
    "ME": ("mechanical", "mech"),
    "CE": ("civil",),
}


def message_text(message: BaseMessage) -> str:
    """Plain text of a chat message (Bedrock Converse may return a list of content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
    return "".join(parts).strip()


def call_llm(role: models.Role, node: str, messages: list[BaseMessage]) -> tuple[str, UsageRecord]:
    """Invoke the chat model for ``role`` and return (text, usage record for ``node``)."""
    model = models.get_chat_model(role)
    with usage.timer() as t:
        message = model.invoke(messages)
    record = usage.usage_from_message(node, models.model_id_for(role), message, t["ms"])
    return message_text(message), record


def mentioned_colleges(text: str) -> list[str]:
    """College keys mentioned in ``text`` (at most one now: the single fictional college)."""
    lowered = f" {text.lower()} "
    return [key for key in COLLEGES if any(alias in lowered for alias in COLLEGE_ALIASES.get(key, ()))]


def mentioned_branches(text: str) -> list[str]:
    """Branch codes mentioned in ``text``, in BRANCHES order (whole-word match)."""
    lowered = text.lower()
    return [
        code
        for code in BRANCHES
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in BRANCH_ALIASES.get(code, (code.lower(),)))
    ]


def simple_answer(state: AgentState) -> dict:
    """Retrieve once and answer once. Citation i matches context block [i+1]."""
    from smart_campus_assistant import retriever  # lazy: lets fake mode stub the module

    question = state["question"]
    docs = retriever.search(question)  # one college: no filter needed
    answer, record = call_llm(
        "answer", "simple_answer", prompts.simple_answer_messages(question, retriever.format_docs(docs))
    )
    return {
        "docs": docs,
        "answer": answer,
        "citations": [retriever.to_citation(d) for d in docs],
        "usage": [record],
    }
