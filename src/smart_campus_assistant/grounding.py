"""Answer checks shared by the simple, personal and deep paths (pure functions, no model calls).

Two promises to the UI and to the admin Insights log:

- Citation numbers: every ``[n]`` left in an answer points at entry ``n`` of the returned
  ``citations`` list. A number the model made up (``[7]`` with four sources) is dropped, sources
  the answer never cites are left out, and the rest are renumbered 1..k in order of first use.
  Sources the chat page would merge as duplicates (same ``citation_key``) get one number, so the
  page's own de-duplication can never shift the numbering.
- Not found: an answer that cites no source and states nothing from the student's own record is
  not grounded in anything, so it is replaced by one standard "information not available" message.
  The same wording is used on every path, and it keeps the phrase "do not contain" that
  ``question_log.is_answered`` looks for, so such questions count as unanswered.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from smart_campus_assistant.state import Citation

NEXT_STEP = "If you need this answered, raise a request on the My requests page or contact the admin office."
NOT_FOUND_MESSAGE = (
    f"Information not available: the college documents do not contain the answer to this question. {NEXT_STEP}"
)
RECORD_NOT_FOUND_MESSAGE = (
    "Information not available: your record and the college documents do not contain the answer to this "
    f"question. {NEXT_STEP}"
)

# [3], [1, 2], [1-3]. Citation numbers have one or two digits, so "[2026-11-30]" or "[1021]" never counts.
CITE_RE = re.compile(r"\[(\d{1,2}(?:\s*[,\-–]\s*\d{1,2})*)\]")
_MARKER_RE = re.compile(r"(?P<lead>[ \t]*)\[(?P<nums>\d{1,2}(?:\s*[,\-–]\s*\d{1,2})*)\]")
_REPEATED_MARKER_RE = re.compile(r"(\[\d+(?:, \d+)*\])(?:[ \t]*\1)+")
_MAX_RANGE = 20  # "[1-30]" is not a range of sources

# The student's own record is named "your record" in every prompt; Hinglish answers keep the word "record".
_RECORD_WORD = re.compile(r"\brecord\b", flags=re.IGNORECASE)
# Values that can only come from a record: dates, course / room / book codes, amounts and counts.
_VALUE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b[A-Z]{1,4}-?\d{2,4}\b|\d[\d,]*(?:\.\d+)?")
_YEAR_RE = re.compile(r"(?:19|20)\d\d")


def _numbers(group: str) -> list[int]:
    """Numbers of one marker body: "1, 3" -> [1, 3]; "2-4" -> [2, 3, 4]."""
    out: list[int] = []
    for part in group.split(","):
        bounds = [int(b) for b in re.split(r"\s*[\-–]\s*", part.strip()) if b]
        if len(bounds) == 2 and 0 <= bounds[1] - bounds[0] <= _MAX_RANGE:
            out.extend(range(bounds[0], bounds[1] + 1))
        else:
            out.extend(bounds)
    return out


def cited_numbers(text: str) -> list[int]:
    """Citation numbers in ``text`` in order of first appearance (each once)."""
    out: list[int] = []
    for group in CITE_RE.findall(text or ""):
        for n in _numbers(group):
            if n not in out:
                out.append(n)
    return out


def renumber(text: str, mapping: Mapping[int, int]) -> str:
    """Rewrite every [a, b] marker with ``mapping``; numbers not in it are dropped with their marker.

    A marker that loses all its numbers is removed together with the space before it, so
    "Rs 5 per day [7]." becomes "Rs 5 per day.".
    """

    def repl(m: re.Match) -> str:
        new = list(dict.fromkeys(mapping[n] for n in _numbers(m.group("nums")) if n in mapping))
        return f"{m.group('lead')}[{', '.join(str(n) for n in new)}]" if new else ""

    return _REPEATED_MARKER_RE.sub(r"\1", _MARKER_RE.sub(repl, text or ""))


def citation_key(citation: Mapping) -> tuple:
    """Identity of a source as the chat page de-duplicates it (ui/chat.py ``dedupe``: first 60 snippet chars)."""
    return (citation.get("source"), citation.get("page"), (citation.get("snippet") or "")[:60])


def finalize_citations(answer: str, citations: Sequence[Citation]) -> tuple[str, list[Citation]]:
    """(answer, citations) where each [n] in the answer is entry n of the list and every entry is cited.

    ``citations[i]`` is what the model saw as context block [i + 1]. Out-of-range numbers are
    dropped; duplicate sources share the number of their first copy.
    """
    keys = [citation_key(c) for c in citations]
    first: dict[tuple, int] = {}
    for i, key in enumerate(keys, start=1):
        first.setdefault(key, i)
    canonical = {n: first[keys[n - 1]] for n in range(1, len(citations) + 1)}
    order: list[int] = []
    for n in cited_numbers(answer):
        if n in canonical and canonical[n] not in order:
            order.append(canonical[n])
    position = {c: i for i, c in enumerate(order, start=1)}
    mapping = {n: position[c] for n, c in canonical.items() if c in position}
    return renumber(answer, mapping), [citations[c - 1] for c in order]


def _values(text: str) -> set[str]:
    """Record-like values in ``text``: dates, codes and numbers of 3+ digits ("59,600" == "59600").

    Bare years and short numbers ("2026", "27 days") are left out: they appear in almost any answer.
    """
    out = set()
    for raw in _VALUE_RE.findall(text or ""):
        value = raw.replace(",", "").rstrip(".").upper()
        if value.replace(".", "").isdigit() and (len(value.replace(".", "")) < 3 or _YEAR_RE.fullmatch(value)):
            continue
        out.add(value)
    return out


def uses_record(answer: str, record_text: str | None) -> bool:
    """True if the answer states something from the student's own record (it names it, or quotes a value)."""
    if not record_text or not answer:
        return False
    return bool(_RECORD_WORD.search(answer)) or bool(_values(answer) & _values(record_text))


def check_answer(
    answer: str,
    citations: Sequence[Citation],
    record_text: str | None = None,
    not_found: str = NOT_FOUND_MESSAGE,
) -> tuple[str, list[Citation]]:
    """The answer with valid citation numbers, or ``not_found`` when it is grounded in nothing.

    Grounded means: it cites at least one existing source, or (with ``record_text``) it uses the
    student's own record. Everything else, including an empty answer, gets the standard message.
    """
    text, used = finalize_citations((answer or "").strip(), citations)
    if used or uses_record(text, record_text):
        return text.strip(), used
    return not_found, []


def is_not_found(answer: str) -> bool:
    """True for one of the standard "information not available" messages."""
    return (answer or "").strip() in (NOT_FOUND_MESSAGE, RECORD_NOT_FOUND_MESSAGE)
