"""Router node: label a question "simple", "deep" or "personal" with a cheap model."""

import json
import re

from langchain_core.runnables import RunnableConfig

from smart_campus_assistant import followup, prompts
from smart_campus_assistant.config import ROUTES
from smart_campus_assistant.personal import RECORD_TOPICS, mentions_other_person
from smart_campus_assistant.simple_rag import call_llm, mentioned_branches
from smart_campus_assistant.state import AgentState
from smart_campus_assistant.student_data import STUDENT_ID_RE, students_named_in

DEEP_PATTERNS = [
    r"\bcompar\w*",
    r"\bbest\b",
    r"\bbetter\b",
    r"\bwhich branch\b",
    # Hinglish "which branch" / "better". Bare "kaun" also means "who" ("HoD kaun hai?"), a simple lookup.
    r"\bkaun\w*\s+(?:sa\s+|si\s+|se\s+)?branch\b",
    r"\b(?:behtar|zyada (?:accha|achha|acchi|achhi))\b",
    r"\b(?:farak|fark|tulna)\b",
    r"\bvs\.?\b",
    r"\bversus\b",
    r"\bdifference\b",
    r"\brecommend\w*",
    r"\bshould i\b",
    r"\bplan\w*\b",
    r"\ball (four|4|branches)\b",
    # Several documents or steps have to be combined.
    r"\bchecklist\b",
    r"\bcombin\w*",
    r"\bdiffer\w*",
    r"\baffects?\b",
    r"\bwhat happens\b",
    r"\bpros and cons\b",
    r"\btotal\b.*\b(cost|kharcha)\b",
]

# First-person words in English and Hinglish. Bare "me" is left out on purpose:
# in Hinglish it means "in" ("hostel me fees"), and "tell me ..." is not personal.
FIRST_PERSON = re.compile(
    r"\b(my|mine|myself|i have|i've|have i|do i|did i|am i|i owe|i got|for me|to me|"
    r"mera|meri|mere|mujhe|mujhko|maine|apna|apni|apne|hamara|hamari)\b"
)

# Topics that live in a student's own record: the same list the personal node uses to refuse
# questions about someone else's record, so routing and refusing never disagree.
PERSONAL_TOPICS = RECORD_TOPICS


# Someone else's record ("uski fees", "her attendance"): routed to personal so that node can refuse.
# Ambiguous pronouns ("their", "uski") are judged by personal.mentions_other_person.
THIRD_PERSON = re.compile(r"\b(his|her|friend'?s?|dost\w*|classmate'?s?)\b")

# Longer questions are cut to this size before any model sees them (keeps cost bounded).
MAX_QUESTION_CHARS = 2000
EMPTY_QUESTION_MESSAGE = (
    'Please type a question, for example "What is the library fine per day?" or "When is my next exam?".'
)


# Advice and comparison wording. With "I / me" but no "my" it asks for advice, not about the record:
# "Mujhe 2 lakh budget mein kaunsi branch better rahegi, scholarship ke saath?" is deep (a router example).
ADVICE = re.compile(
    r"\b(compar\w*|better|best|behtar|vs\.?|versus|recommend\w*|which branch)\b|\bkaun\w*\s+(?:sa\s+|si\s+|se\s+)?branch\b"
)
OWN_POSSESSIVE = re.compile(r"\b(my|mine|mera|meri|mere|apna|apni|apne|hamara|hamari)\b")


def is_personal(question: str) -> bool:
    """A student ID, or a person (me / someone else / a known student's name) + a record topic.

    First-person advice without "my" ("which branch is better for me?") is not about the record.
    """
    text = question.lower()
    if STUDENT_ID_RE.search(question):
        return True
    if not PERSONAL_TOPICS.search(text):
        return False
    if mentions_other_person(text, THIRD_PERSON) or students_named_in(question):
        return True
    return bool(FIRST_PERSON.search(text)) and not (ADVICE.search(text) and not OWN_POSSESSIVE.search(text))


def heuristic_route(question: str) -> tuple[str, str]:
    """Keyword fallback used when the router output cannot be parsed."""
    text = question.lower()
    if is_personal(question):
        return "personal", "Heuristic: asks about the student's own record."
    if len(mentioned_branches(question)) >= 2:
        return "deep", "Heuristic: mentions multiple branches."
    for pattern in DEEP_PATTERNS:
        if re.search(pattern, text):
            return "deep", f"Heuristic: matched '{pattern}'."
    return "simple", "Heuristic: looks like a single fact lookup."


def parse_route(text: str) -> tuple[str, str] | None:
    """Pull {"route": ..., "reason": ...} out of free model text; None if not found."""
    for match in re.finditer(r"\{.*?\}", text, flags=re.DOTALL):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        route = str(data.get("route", "")).strip().lower() if isinstance(data, dict) else ""
        if route in ROUTES:
            return route, str(data.get("reason", "")).strip()
    # Loose form like: route: deep
    loose = re.search(r'route"?\s*[:=]\s*"?(simple|deep|personal)\b', text, flags=re.IGNORECASE)
    if loose:
        return loose.group(1).lower(), ""
    return None


def _forced_route(config: RunnableConfig | None) -> str | None:
    """force_route from config["configurable"] or from the graph runtime context."""
    forced = ((config or {}).get("configurable") or {}).get("force_route")
    if not forced:
        try:
            from langgraph.runtime import get_runtime

            context = get_runtime().context
            if isinstance(context, dict):
                forced = context.get("force_route")
            else:
                forced = getattr(context, "force_route", None)
        except Exception:  # not running inside a graph
            forced = None
    return forced if forced in ROUTES else None


def normalize_question(question: object) -> str:
    """Collapse whitespace and cut the question to MAX_QUESTION_CHARS ("" for None / blank input)."""
    text = " ".join(str(question or "").split())
    return text[:MAX_QUESTION_CHARS].rstrip()


def route_question(state: AgentState, config: RunnableConfig) -> dict:
    raw = state.get("question")
    question = normalize_question(raw)
    if not question:
        # Nothing to answer: reply directly with no model call (the graph ends after this node).
        return {"question": "", "route": "simple", "route_reason": "Empty question.",
                "answer": EMPTY_QUESTION_MESSAGE, "citations": []}
    # A follow-up ("aur ECE ka?") becomes a standalone question; every later node sees only that.
    standalone, rewrite_record = followup.rewrite_follow_up(question, followup.clean_history(state.get("history")))
    early_usage = [rewrite_record] if rewrite_record else []
    cleaned = {"question": standalone} if standalone != raw else {}
    if early_usage:
        cleaned["usage"] = early_usage
    if standalone != question:
        cleaned["original_question"] = question
    question = standalone
    forced = _forced_route(config)
    if forced:
        return {**cleaned, "route": forced, "route_reason": f"Forced route: {forced}."}

    try:
        text, record = call_llm("router", "router", prompts.router_messages(question))
    except Exception as exc:  # model unavailable: still answer via heuristic
        route, reason = heuristic_route(question)
        return {**cleaned, "route": route, "route_reason": f"{reason} (router error: {type(exc).__name__})"}

    parsed = parse_route(text)
    route, reason = parsed if parsed else heuristic_route(question)
    return {**cleaned, "route": route, "route_reason": reason or f"Router chose {route}.",
            "usage": [*early_usage, record]}
