"""Page "Ask": chat with a compact flow strip (step names), route badge, answer, sources and a helpful rating.

Each question also leaves anonymous rows for the admin Insights page: the question log, the
model usage of the run (tokens, cost, time, failure type) and the answer's rating, if given.
"""

from __future__ import annotations

import sys
import time
import traceback
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    admin_hint_html,
    chip_html,
    citation_card_html,
    empty_state,
    flow_strip_html,
    get_college_name,
    get_student_safe,
    has_aws_credentials,
    hero_html,
    is_demo_mode,
    require_role,
    route_badge,
)
from ui.theme import inject_css  # noqa: E402

COLLEGE_EXAMPLES = [
    "When do CSE end-semester exams start?",
    "What is the library fine per day?",
    "Compare CSE and ECE placements",
]
ADMIN_EXAMPLES = [
    "What is the hostel fee per semester?",
    "What is the minimum attendance to sit the exams?",
    "Compare CSE and ECE placements",
    "What scholarships does the college offer?",
]
STUDENT_EXAMPLES = [
    "Meri fees ka status kya hai?",
    "Which books are issued in my name?",
    "When is my next exam?",
    "Is my attendance enough to sit the exams?",
]

MAX_QUESTION_CHARS = 2000  # same limit as router.MAX_QUESTION_CHARS
MAX_COMMENT_CHARS = 300  # same limit as feedback.MAX_COMMENT_CHARS

# Label of the pill shown while the next node is still running.
NEXT_STEP = {
    "router": {
        "simple": ("simple_answer", "Searching docs…"),
        "deep": ("planner", "Planning…"),
        "personal": ("personal_answer", "Reading your record…"),
    },
    "planner": ("researcher", "Researching…"),
    "researcher": ("researcher", "Researching or writing…"),
}


class BackendError(Exception):
    """A failure we can explain to the user in plain words.

    ``kind`` is the error type the admin's model usage page counts (see run_metrics.ERROR_TYPES).
    """

    def __init__(self, title: str, body: str, details: str = "", kind: str = "backend_error"):
        super().__init__(title)
        self.title, self.body, self.details, self.kind = title, body, details, kind


# ---------- backend ----------

def load_graph(demo: bool):
    """Switch fake mode on/off, then import the compiled graph lazily."""
    try:
        from smart_campus_assistant import fakes

        if demo:
            fakes.enable_fake_mode()
        else:
            fakes.disable_fake_mode()
    except ImportError as exc:
        if demo:
            raise BackendError(
                "Demo mode is not available yet",
                "The offline fake backend (`smart_campus_assistant.fakes`) could not be imported.",
                repr(exc),
                kind="backend_unavailable",
            ) from exc
    try:
        from smart_campus_assistant.graph import graph
    except Exception as exc:  # ImportError or an error while building the graph
        raise BackendError(
            "The agent backend is not available",
            "`smart_campus_assistant.graph` could not be loaded. Check that the backend is installed "
            "(`pip install -e .`) and that all its modules import cleanly.",
            traceback.format_exc(),
            kind="backend_unavailable",
        ) from exc
    return graph


def explain_error(exc: Exception) -> BackendError:
    """Map common AWS / runtime failures to a friendly message."""
    if isinstance(exc, BackendError):
        return exc
    name = type(exc).__name__
    text = f"{name}: {exc}"
    aws_markers = ("NoCredentials", "Unauthorized", "AccessDenied", "ExpiredToken", "InvalidClientTokenId",
                   "UnrecognizedClient", "EndpointConnectionError", "ResourceNotFound", "ValidationException")
    if any(m in text for m in aws_markers):
        return BackendError(
            "AWS is not reachable or not configured",
            "The live backend could not call Amazon Bedrock / S3 Vectors. Check your AWS credentials, region "
            "and model access, or start the app with `SCA_DEMO_MODE=1` to run offline.",
            text,
            kind="aws_unreachable",
        )
    return BackendError("Something went wrong while answering", "The agent raised an error. Details below.", traceback.format_exc())


def step_detail(node: str, update: dict, student_id: str | None = None) -> dict:
    """Flow-strip entry for one finished node (step names and counts only, no timings)."""
    step = {"node": node}
    if node in ("simple_answer", "personal_answer"):
        docs = update.get("docs") or []
        cites = update.get("citations") or []
        step["n_docs"] = len(docs) or len(cites)
        if node == "personal_answer" and (not student_id or not docs):
            step["guest"] = True  # no student record was read
    elif node == "planner":
        step["n_plan"] = len(update.get("plan") or [])
    return step


DEEP_WAIT_HINT = "Deep research can take up to a minute. Please stay on this page until the answer appears."


def live_html(route: str | None, steps: list[dict], running: str) -> str:
    """Flow strip so far plus a pulsing pill for the step that is running."""
    done = flow_strip_html(route, steps)
    pill = f'<span class="step" style="opacity:.65">⏳ {running}</span>'
    strip = (done.replace("</div>", f'<span class="arrow">→</span>{pill}</div>', 1)
             if done else f'<div class="sca-flow">{pill}</div>')
    if route == "deep":
        # Leaving the page stops the run, so say so while the slow path works.
        strip += f'<div class="sca-wait-hint">⏱️ {DEEP_WAIT_HINT}</div>'
    return strip


def interrupted_question() -> str | None:
    """The last question if it never got an answer (the run was stopped mid-way), else None."""
    messages = st.session_state.get("messages") or []
    if messages and messages[-1].get("role") == "user":
        return messages[-1].get("content")
    return None


def retry_interrupted() -> None:
    """Drop the unanswered question and ask it again."""
    messages = st.session_state.get("messages") or []
    if messages and messages[-1].get("role") == "user":
        st.session_state.pending_question = messages.pop()["content"]


def chat_history(messages: list[dict]) -> list[dict]:
    """Earlier answered turns of this chat as ``{"question", "answer"}`` pairs, oldest first.

    Turns that ended in an error, or a question still waiting for its answer, are left out.
    The graph keeps only the last few turns.
    """
    turns: list[dict] = []
    for prev, msg in zip(messages, messages[1:], strict=False):
        if prev.get("role") != "user" or msg.get("role") != "assistant" or msg.get("error"):
            continue
        result = msg.get("result") or {}
        # A rewritten follow-up is sent in its standalone form, so a chain of follow-ups keeps its topic.
        question = result.get("understood_as") or prev.get("content")
        if question and result.get("answer"):
            turns.append({"question": question, "answer": result["answer"]})
    return turns


def new_trace() -> dict:
    """What one question used so far: its route and the usage record of every model call."""
    return {"route": "", "usage": []}


def run_question(question: str, demo: bool, live, student_id: str | None = None,
                 history: list[dict] | None = None, trace: dict | None = None) -> dict:
    """Stream the graph, updating the live flow strip; returns a result dict for history.

    ``trace`` (see ``new_trace``) is filled while the graph streams, not at the end, so a run that
    fails half-way still reports the model calls it already paid for.
    """
    trace = trace if trace is not None else new_trace()
    graph = load_graph(demo)
    # The random answer_id lets a later rating update this answer's one feedback row.
    result = {"question": question, "citations": [], "steps": [], "student_id": student_id,
              "answer_id": uuid.uuid4().hex}
    steps = result["steps"]
    live.html(live_html(None, [], "Routing…"))
    payload = {"question": question, "student_id": student_id}
    if history:
        payload["history"] = history

    for chunk in graph.stream(payload, stream_mode="updates"):
        for node, update in (chunk or {}).items():
            if node.startswith("__"):
                continue
            update = update or {}
            trace["usage"].extend(r for r in (update.get("usage") or []) if isinstance(r, dict))
            if update.get("route"):
                trace["route"] = str(update["route"])
            step = step_detail(node, update, student_id)
            steps.append(step)
            if step.get("guest"):
                result["no_record"] = True
            for key in ("route", "route_reason", "answer", "citations"):
                if key in update:
                    result[key] = update[key]
            if update.get("original_question") and update.get("question"):
                result["understood_as"] = update["question"]  # the follow-up as a standalone question
                trace["understood_as"] = update["question"]

            nxt = NEXT_STEP.get(node)
            if isinstance(nxt, dict):
                nxt = nxt.get(result.get("route", "simple"))
            if nxt and node not in ("writer", "simple_answer", "personal_answer"):
                live.html(live_html(result.get("route"), steps, nxt[1]))
            else:
                live.html(flow_strip_html(result.get("route"), steps))
    return result


# ---------- rendering ----------

def render_result(result: dict, is_admin: bool = False) -> None:
    route_badge(result.get("route"), result.get("route_reason"))
    if result.get("understood_as"):
        st.caption(f"Understood as: {result['understood_as']}", help="Your follow-up, read with the earlier messages.")
    st.markdown(result.get("answer") or "_No answer was returned._")
    if is_admin and result.get("no_record"):
        st.html(admin_hint_html())
    if result.get("steps"):
        st.html(flow_strip_html(result.get("route"), result["steps"]))
    cards = numbered_citations(result.get("citations") or [])
    if cards:
        with st.expander(f"Sources · {len(cards)}", icon=":material/menu_book:"):
            st.html(numbered_cards_html(cards))


def _citation_key(c: dict) -> tuple:
    return (c.get("source"), c.get("page"), (c.get("snippet") or "")[:60])


def dedupe(citations: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in citations:
        key = _citation_key(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def numbered_citations(citations: list[dict]) -> list[tuple[list[int], dict]]:
    """Unique citations with the [n] numbers the answer text uses for them (1-based list positions).

    A duplicate is merged into the first card with the same source, page and snippet, and that card
    keeps every number, so after de-duplication [3] in the answer text still points at the card
    labelled 3 (shown as "[1, 3]" when citation 3 repeated citation 1).
    """
    cards: dict[tuple, tuple[list[int], dict]] = {}
    for number, c in enumerate(citations, start=1):
        if not isinstance(c, dict):
            continue
        key = _citation_key(c)
        if key in cards:
            cards[key][0].append(number)
        else:
            cards[key] = ([number], c)
    return list(cards.values())


def numbered_cards_html(cards: list[tuple[list[int], dict]]) -> str:
    """Source cards labelled with their original numbers (see ``numbered_citations``)."""
    html = "".join(citation_card_html(c, ", ".join(str(n) for n in numbers)) for numbers, c in cards)
    return f'<div class="sca-cites">{html}</div>'


def render_error(err: BackendError) -> None:
    st.error(f"**{err.title}**\n\n{err.body}", icon=":material/error:")
    if err.details:
        with st.expander("Technical details"):
            st.code(err.details, language="text")


@st.cache_resource(show_spinner=False)
def _shared_budget():
    """One question budget per server process, shared by every session (keyed by account)."""
    from smart_campus_assistant.rate_limit import QuestionBudget

    return QuestionBudget()


def question_budget():
    """The per-account question budget, or None when the limit cannot be loaded (the chat still works)."""
    try:
        return _shared_budget()
    except Exception:  # noqa: BLE001
        return None


def limit_message(seconds: float) -> str:
    try:
        from smart_campus_assistant.rate_limit import wait_message
    except Exception:  # noqa: BLE001
        return "You have asked a lot of questions in the last hour. Please try again later."
    return wait_message(seconds)


def units_for_route(route: str | None) -> int:
    try:
        from smart_campus_assistant.rate_limit import units_for
    except Exception:  # noqa: BLE001
        return 1
    return units_for(route)


def ask_example(question: str) -> None:
    st.session_state.pending_question = question


# ---------- saved history (survives a refresh) ----------

def _chat_store():
    """The chat_store module, or None if it cannot be loaded (the chat still works, just not saved)."""
    try:
        from smart_campus_assistant import chat_store
    except Exception:  # noqa: BLE001
        return None
    return chat_store


def stored_to_message(rec: dict) -> dict:
    """Saved record -> in-memory chat message."""
    if rec.get("role") == "user":
        return {"role": "user", "content": rec.get("content", "")}
    keys = ("answer", "route", "route_reason", "citations", "steps", "no_record", "understood_as", "answer_id",
            "feedback")
    return {"role": "assistant", "result": {k: rec[k] for k in keys if k in rec}}


def load_saved_history(email: str) -> list[dict]:
    store = _chat_store()
    if store is None or not email:
        return []
    try:
        return [stored_to_message(r) for r in store.load_history(email)]
    except Exception:  # noqa: BLE001  (a broken history file must not break the page)
        return []


def save_turn(email: str, question: str, result: dict) -> None:
    """Persist one question + answer for this user only (chat_store whitelists the fields)."""
    store = _chat_store()
    if store is None or not email:
        return
    answer = {"role": "assistant", **{k: result.get(k) for k in ("answer", "route", "route_reason", "citations", "steps")}}
    for key in ("understood_as", "answer_id"):
        if result.get(key):
            answer[key] = result[key]
    if result.get("no_record"):
        answer["no_record"] = True
    try:
        store.append_messages(email, [{"role": "user", "content": question}, answer])
    except Exception:  # noqa: BLE001
        pass


def insights_identity(user: dict) -> tuple[str, str]:
    """(role, branch code) for the anonymous Insights tables: never the email, student ID or name."""
    role = "admin" if user.get("role") == "admin" else "student"
    branch = ""
    if role == "student":
        branch = str((get_student_safe(user.get("student_id")) or {}).get("branch") or "")
    return role, branch


def log_for_insights(user: dict, question: str, result: dict) -> None:
    """Record the question for the admin Insights page (role and branch only, no identity).

    Any failure here, including a missing question_log module, is ignored: it must never affect the chat.
    """
    try:
        from smart_campus_assistant import question_log

        role, branch = insights_identity(user)
        question_log.log_question(
            question,
            result.get("route") or "simple",
            result.get("answer") or "",
            citations=len(dedupe(result.get("citations") or [])),
            role=role,
            branch=branch,
        )
    except Exception:  # noqa: BLE001
        pass


def record_run(user: dict, question: str, trace: dict, started: float, error: str | None) -> None:
    """One anonymous model-usage row for the admin Insights page: route, tokens, cost, time, failure type.

    Ignored on any failure, like ``log_for_insights``: monitoring must never affect the chat.
    """
    try:
        from smart_campus_assistant import run_metrics

        run_metrics.record_run(
            trace.get("understood_as") or question,  # a follow-up as its standalone question, like the log
            route=trace.get("route"),
            usage_records=trace.get("usage"),
            latency_ms=(time.perf_counter() - started) * 1000,
            error=error,
            role=user.get("role") or "student",  # the role only, never who asked
        )
    except Exception:  # noqa: BLE001
        pass


# ---------- answer feedback ----------

def _feedback_module():
    """The feedback module, or None if it cannot be loaded (the rating is still kept with the chat)."""
    try:
        from smart_campus_assistant import feedback
    except Exception:  # noqa: BLE001
        return None
    return feedback


def find_answer(messages: list[dict], answer_id: str) -> tuple[str, dict] | None:
    """(question, result) of the answer with this id, or None.

    The question is what Insights store with the rating: the standalone form of a follow-up, else
    the user message just before the answer (saved results do not keep the question themselves).
    """
    for i, msg in enumerate(messages):
        result = msg.get("result") or {}
        if msg.get("role") == "assistant" and result.get("answer_id") == answer_id:
            before = messages[i - 1] if i else {}
            asked = before.get("content") if before.get("role") == "user" else ""
            return str(result.get("understood_as") or asked or result.get("question") or ""), result
    return None


def save_feedback(email: str, answer_id: str, rating: dict | None) -> None:
    """Keep the rating with this user's saved answer, so it still shows after a refresh."""
    store = _chat_store()
    if store is None or not email:
        return
    try:
        store.set_feedback(email, answer_id, rating)
    except Exception:  # noqa: BLE001
        pass


def on_rating(answer_id: str) -> None:
    """Thumbs clicked or cleared: one rating per answer, so a new click replaces the old one."""
    try:
        found = find_answer(st.session_state.get("messages") or [], answer_id)
        if found is None:
            return
        question, result = found
        value = st.session_state.get(f"fb_{answer_id}")
        rating = int(value) if value in (0, 1) else None  # st.feedback: 1 thumbs up, 0 thumbs down
        if rating is None:
            result.pop("feedback", None)
        else:
            result["feedback"] = {"rating": rating, "comment": ""}  # a comment belonged to the old rating
        st.session_state["fb_open"] = answer_id if rating == 0 else None
        user = st.session_state.get("user") or {}
        save_feedback(user.get("email", ""), answer_id, result.get("feedback"))
        module = _feedback_module()
        if module is not None:
            role, branch = insights_identity(user)
            module.record_rating(answer_id, rating, question, result.get("route") or "simple", role, branch)
    except Exception:  # noqa: BLE001  (a rating must never break the chat)
        pass


def on_comment(answer_id: str) -> None:
    """Send the optional comment of a thumbs down (cleaned and masked like the question)."""
    st.session_state["fb_open"] = None
    try:
        found = find_answer(st.session_state.get("messages") or [], answer_id)
        rating = (found[1].get("feedback") or {}) if found else {}
        if rating.get("rating") != 0:
            return
        text = st.session_state.get(f"fb_note_{answer_id}") or ""
        module = _feedback_module()
        comment = module.clean_comment(text) if module is not None else " ".join(str(text).split())[:MAX_COMMENT_CHARS]
        if not comment:
            return
        rating["comment"] = comment
        save_feedback((st.session_state.get("user") or {}).get("email", ""), answer_id, rating)
        if module is not None:
            module.record_comment(answer_id, comment)
    except Exception:  # noqa: BLE001
        pass


def render_feedback(result: dict) -> None:
    """Thumbs up / down under an answer; after a thumbs down, an optional short comment."""
    answer_id = result.get("answer_id")
    if not answer_id:  # answers saved before ratings existed cannot be rated
        return
    key = f"fb_{answer_id}"
    saved = result.get("feedback") or {}
    if key not in st.session_state and saved.get("rating") in (0, 1):
        st.session_state[key] = saved["rating"]  # after a refresh, show the rating given earlier
    st.feedback("thumbs", key=key, on_change=on_rating, args=(answer_id,))
    if saved.get("rating") != 0:
        return
    if saved.get("comment"):
        st.caption("Thanks for telling us what was wrong.")
    elif st.session_state.get("fb_open") == answer_id:
        with st.form(key=f"fb_form_{answer_id}", border=False):
            st.text_input("What was wrong? (optional)", max_chars=MAX_COMMENT_CHARS, key=f"fb_note_{answer_id}",
                          placeholder="For example: the date is wrong, or it answered a different question")
            st.form_submit_button("Send", on_click=on_comment, args=(answer_id,), key=f"fb_send_{answer_id}",
                                  icon=":material/send:")


def clear_chat() -> None:
    st.session_state.messages = []
    st.session_state.pop("fb_open", None)
    user = st.session_state.get("user") or {}
    store = _chat_store()
    if store is not None and user.get("email"):
        try:
            store.clear_history(user["email"])
        except Exception:  # noqa: BLE001
            pass


def render_empty_state(user: dict) -> None:
    college = get_college_name()
    is_admin = user.get("role") == "admin"
    if is_admin:
        title = "Ask about the college"
        text = (f"Exam notices, fees, rules, hostel, library and placements of {college}. "
                "This chat does not read student records.")
        examples = ADMIN_EXAMPLES
    else:
        title = "Ask about the college or about you"
        text = (f"Exam notices, fees, hostel, library and placement rules of {college}, plus your own fee status, "
                "books, attendance and exams. Only your record is ever read.")
        examples = STUDENT_EXAMPLES + COLLEGE_EXAMPLES[:2]
    empty_state("💬", title, text)  # the shared empty state escapes the text (the college name comes from config)
    if is_admin:
        st.html(admin_hint_html())
    st.write("")
    st.caption("Try one of these")
    with st.container(horizontal=True, key="examples", gap="small"):
        for i, q in enumerate(examples):
            st.button(q, key=f"example_{i}", on_click=ask_example, args=(q,), icon=":material/north_east:")


# ---------- page ----------

inject_css(max_width="58rem")
# The chat input is pinned to the bottom and Streamlit scrolls a chat page to its end, so spare
# bottom padding only pushes the page header out of view.
st.html('<style>[data-testid="stMainBlockContainer"] { padding-bottom: 1rem; }</style>')
user = require_role("student", "admin")
is_admin = user["role"] == "admin"
st.session_state.setdefault("messages", [])
demo = bool(st.session_state["demo_mode"]) if "demo_mode" in st.session_state else is_demo_mode()
# Identity comes only from the authenticated session; admins have no student record.
student_id = None if is_admin else (user.get("student_id") or None)

# A different account must never see the previous account's answers.
owner = f"{user['role']}:{user.get('email', '')}"
if st.session_state.get("chat_owner") != owner:
    st.session_state.messages = []
    st.session_state.pop("pending_question", None)
    st.session_state.pop("chat_loaded_for", None)
st.session_state.chat_owner = owner

# After a refresh (or a new sign-in) reload this user's saved history once.
if st.session_state.get("chat_loaded_for") != owner:
    if not st.session_state.messages:
        st.session_state.messages = load_saved_history(user.get("email", ""))
    st.session_state.chat_loaded_for = owner

chips = []
if demo:
    chips.append(chip_html("Offline demo", "demo"))
if is_admin:
    chips.append(chip_html("Admin · college questions only", "live"))
elif student_id:
    chips.append(chip_html(f"Signed in · {student_id}", "live"))
if is_admin:
    subtitle = "Ask about exams, fees, rules, hostel, library and placements."
else:
    subtitle = "Ask about exams, fees, rules, hostel and library, or about your own fees, books, attendance and exams."
head, side = st.columns([5, 1], vertical_alignment="bottom")
with head:
    st.markdown(hero_html("AI helpdesk", get_college_name(), subtitle, chips), unsafe_allow_html=True)
with side:
    if st.session_state.messages:
        st.button("Clear chat", icon=":material/delete_sweep:", on_click=clear_chat, width="stretch", key="clear_chat")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user" else ":material/school:"):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        elif msg.get("error"):
            render_error(BackendError(**msg["error"]))
        else:
            render_result(msg["result"], is_admin)
            render_feedback(msg["result"])

typed = st.chat_input("Ask about the college…" if is_admin else "Ask about exams, fees, your books…",
                      max_chars=MAX_QUESTION_CHARS)
question = typed or st.session_state.pop("pending_question", None)

# A question left without an answer means its run was stopped (e.g. the page changed
# during deep research). Offer a retry instead of leaving it hanging silently.
if not question and interrupted_question():
    with st.chat_message("assistant", avatar=":material/school:"):
        st.warning(
            "This answer was interrupted before it finished (the page changed while I was still working).",
            icon=":material/hourglass_disabled:",
        )
        st.button("Retry", icon=":material/refresh:", on_click=retry_interrupted, key="retry_interrupted")

if not st.session_state.messages and not question:
    render_empty_state(user)

if question:
    st.session_state.pop("fb_open", None)  # a new question closes an unsent comment box
    history = chat_history(st.session_state.messages)  # turns before this question
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/school:"):
        budget = question_budget()
        spent = 0  # units to count for this question (none if it never reached the model)
        trace, started = new_trace(), time.perf_counter()
        outcome = None  # "" once answered, else the error type; None means the run was stopped mid-way
        try:
            if not demo and not has_aws_credentials():
                raise BackendError(
                    "AWS is not configured",
                    "No AWS credentials were found (environment, `.env` or `~/.aws`). Add them, or start the app "
                    "with `SCA_DEMO_MODE=1` to try the full flow offline.",
                    kind="aws_not_configured",
                )
            wait = budget.wait_seconds(owner) if budget is not None else 0.0
            if wait > 0:
                raise BackendError("Question limit reached", limit_message(wait), kind="question_limit")
            live = st.empty()
            spent = 1  # from here on the model may be called, even if the run fails
            result = run_question(question, demo, live, student_id, history, trace)
            outcome = ""
            spent = units_for_route(result.get("route"))
            live.empty()
            render_result(result, is_admin)
            st.session_state.messages.append({"role": "assistant", "result": result})
            save_turn(user.get("email", ""), question, result)
            # Insights get the follow-up as a standalone question ("ECE fee?", not "aur ECE ka?").
            log_for_insights(user, result.get("understood_as") or question, result)
            render_feedback(result)
        except Exception as exc:  # show a friendly box instead of a stack trace
            err = explain_error(exc)
            outcome = err.kind
            render_error(err)
            st.session_state.messages.append(
                {"role": "assistant", "error": {"title": err.title, "body": err.body, "details": err.details}}
            )
        finally:
            if budget is not None and spent:
                budget.record(owner, spent)
            # Every question gets one usage row, failures and stopped runs included (they may have cost tokens).
            record_run(user, question, trace, started, "interrupted" if outcome is None else outcome)
