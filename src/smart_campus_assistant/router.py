"""Router node: label a question "simple", "deep" or "personal" with a cheap model."""

import json
import re

from langchain_core.runnables import RunnableConfig

from smart_campus_assistant import prompts
from smart_campus_assistant.config import ROUTES
from smart_campus_assistant.simple_rag import call_llm, mentioned_branches
from smart_campus_assistant.state import AgentState
from smart_campus_assistant.student_data import STUDENT_ID_RE, students_named_in

DEEP_PATTERNS = [
    r"\bcompar\w*",
    r"\bbest\b",
    r"\bbetter\b",
    r"\bwhich branch\b",
    r"\bkaun\w*\b",
    r"\bvs\.?\b",
    r"\bversus\b",
    r"\bdifference\b",
    r"\brecommend\w*",
    r"\bshould i\b",
    r"\bplan\w*\b",
    r"\ball (four|4|branches)\b",
]

# First-person words in English and Hinglish. Bare "me" is left out on purpose:
# in Hinglish it means "in" ("hostel me fees"), and "tell me ..." is not personal.
FIRST_PERSON = re.compile(
    r"\b(my|mine|myself|i have|i've|have i|do i|did i|am i|i owe|i got|for me|to me|"
    r"mera|meri|mere|mujhe|mujhko|maine|apna|apni|apne|hamara|hamari)\b"
)

# Topics that live in a student's own record.
PERSONAL_TOPICS = re.compile(
    r"\b(fee|fees|dues?|pending|paid|payment|receipt|scholarship|books?|issued?|fine|fines|library|"
    r"attendance|result|results|cgpa|sgpa|gpa|marks|backlogs?|exams?|hall ticket|admit card|"
    r"hostel room|room|roll|semesters?|semister|sem|subjects?|subjests?|courses?)\b"
)


# Someone else's record ("uski fees", "her attendance"): routed to personal so that node can refuse.
THIRD_PERSON = re.compile(r"\b(his|her|their|uska|uski|uske|unka|unki|unke|friend'?s?|dost\w*|classmate'?s?)\b")


def is_personal(question: str) -> bool:
    """A student ID, or a person (me / someone else / a known student's name) + a record topic."""
    text = question.lower()
    if STUDENT_ID_RE.search(question):
        return True
    if not PERSONAL_TOPICS.search(text):
        return False
    return bool(FIRST_PERSON.search(text) or THIRD_PERSON.search(text) or students_named_in(question))


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


def route_question(state: AgentState, config: RunnableConfig) -> dict:
    question = state["question"]
    forced = _forced_route(config)
    if forced:
        return {"route": forced, "route_reason": f"Forced route: {forced}."}

    try:
        text, record = call_llm("router", "router", prompts.router_messages(question))
    except Exception as exc:  # model unavailable: still answer via heuristic
        route, reason = heuristic_route(question)
        return {"route": route, "route_reason": f"{reason} (router error: {type(exc).__name__})"}

    parsed = parse_route(text)
    route, reason = parsed if parsed else heuristic_route(question)
    return {"route": route, "route_reason": reason or f"Router chose {route}.", "usage": [record]}
