"""Multi-turn chat: turn a follow-up ("aur ECE ka?", "what about the hostel?") into a standalone question.

The rest of the graph (router, search, answer prompts, privacy checks) only ever sees one complete
question, so it works exactly as for a first question. Only the asker's own earlier turns from this
chat are used, and the rewrite runs on the cheap router model and only when the question looks like
a follow-up, so standalone questions cost nothing extra.

If the rewrite fails or returns something unusable, the original question is kept.
"""

from __future__ import annotations

import re
from typing import Iterable

from smart_campus_assistant import prompts
from smart_campus_assistant.state import HistoryTurn, UsageRecord

MAX_HISTORY_TURNS = 3  # earlier question + answer pairs given to the rewriter
MAX_HISTORY_QUESTION_CHARS = 400
MAX_HISTORY_ANSWER_CHARS = 600
MAX_REWRITE_CHARS = 2000  # same limit as router.MAX_QUESTION_CHARS
SHORT_QUESTION_WORDS = 4

# Words that point back at something said earlier (English and Hinglish). Bare "me" and "is" are left
# out on purpose ("hostel me", "what is"); "is" as Hinglish "this" is covered by "isme", "iska" etc.
# "this / that" before a time word ("this semester", "that year") names a time, not something said earlier.
# Likewise "is there / there are" (existence), "one-time / one semester" (a number) and "more than"
# (a comparison) refer back to nothing, so "Is there a gym?" stays a standalone question.
REFERENCE_WORDS = re.compile(
    r"\b(it|its|(?:this|that)(?!\s+(?:semester|sem|year|month|week|session|term)\b)|those|these|they|them|"
    r"their|(?<!\bis )(?<!\bare )(?<!\bwas )(?<!\bwere )there(?!\s+(?:is|are|was|were|will|has|have)\b)|"
    r"same|also|too|else|another|more(?!\s+than\b)|"
    r"one(?![-\s]+(?:time|times|years?|semesters?|sem|days?|months?|weeks?|books?|courses?|subjects?|exams?|of)\b)|"
    r"ones|"
    r"previous|above|earlier|former|latter|instead|"
    r"uska|uski|uske|unka|unki|unke|iska|iski|iske|inka|inki|inke|isme|ismein|usme|usmein|"
    r"wo|woh|vo|ye|yeh|wahi|vahi|yahi|waha|wahan|vaha|yaha|bhi|phir|fir)\b",
    flags=re.IGNORECASE,
)
# Openers that continue the last topic: "and for ECE?", "aur hostel?", "what about ME?".
FOLLOW_UP_START = re.compile(
    r"^\s*(and|or|but|so|then|what about|how about|same for|aur|ya|toh|to|lekin|par)\b",
    flags=re.IGNORECASE,
)


def clean_history(history: Iterable[object] | None) -> list[HistoryTurn]:
    """The last MAX_HISTORY_TURNS valid turns, each cut to a bounded size."""
    turns: list[HistoryTurn] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        question = " ".join(str(turn.get("question") or "").split())
        answer = " ".join(str(turn.get("answer") or "").split())
        if question and answer:
            turns.append(HistoryTurn(question=question[:MAX_HISTORY_QUESTION_CHARS],
                                     answer=answer[:MAX_HISTORY_ANSWER_CHARS]))
    return turns[-MAX_HISTORY_TURNS:]


def looks_like_follow_up(question: str) -> bool:
    """True if the question may lean on earlier turns (a pronoun, a continuing opener, or very short)."""
    text = question.strip()
    if not text:
        return False
    return bool(
        FOLLOW_UP_START.search(text)
        or REFERENCE_WORDS.search(text)
        or len(re.findall(r"\w+", text)) <= SHORT_QUESTION_WORDS
    )


# A label in front of the question on the same line: "Standalone question: ...".
_LABEL = re.compile(r"^(?:standalone question|rewritten question|follow-up|question)\s*:\s*", flags=re.IGNORECASE)
# Chatter models sometimes put around the answer: "Sure! Here is the standalone question:", "(I filled in ...)".
_PREAMBLE = re.compile(
    r"^(?:sure|okay|ok|certainly|of course|here(?:'s| is| are)|the standalone question|the rewritten question)\b",
    flags=re.IGNORECASE,
)


def _clean_line(line: str) -> str:
    line = re.sub(r"^\s*(?:[-*•>]|\d+[.)])\s+", "", line)  # list markers
    wrapping = " \t\"'*_`"  # quotes and markdown emphasis around the label or the question
    return _LABEL.sub("", line.strip(wrapping)).strip(wrapping)


def parse_rewrite(text: str) -> str:
    """The rewritten question from model text ("" when there is none).

    Lines that are only chatter (ending in ":" or starting "Sure", "Here is ...") or a note in
    brackets are skipped, so "Sure! Here is the question:\\nECE fee?" gives "ECE fee?".
    """
    for raw in str(text or "").strip().splitlines():
        line = _clean_line(raw)
        if not line or line.endswith(":") or line.startswith(("(", "[")):
            continue
        if _PREAMBLE.match(line) and not line.endswith("?"):
            continue
        return line[:MAX_REWRITE_CHARS].rstrip()
    return ""


def _same_question(a: str, b: str) -> bool:
    """True when two questions differ only in case, spacing or end punctuation."""
    def norm(text: str) -> str:
        return " ".join(re.sub(r"[?.!]+$", "", text.strip()).casefold().split())

    return norm(a) == norm(b)


def rewrite_follow_up(question: str, history: list[HistoryTurn]) -> tuple[str, UsageRecord | None]:
    """(standalone question, usage record or None). Returns the question unchanged when no rewrite is needed."""
    if not history or not looks_like_follow_up(question):
        return question, None
    from smart_campus_assistant.simple_rag import call_llm  # lazy: avoids an import cycle with router

    try:
        text, record = call_llm("router", "contextualize", prompts.rewrite_messages(question, history))
    except Exception:  # noqa: BLE001  (model unavailable: answer the question as asked)
        return question, None
    rewritten = parse_rewrite(text)
    if not rewritten or _same_question(rewritten, question):
        return question, record  # the call was made (and costs tokens) but nothing changed
    return rewritten, record
