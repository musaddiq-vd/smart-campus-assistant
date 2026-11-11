"""Page "Ask": chat with a compact flow strip (step names), route badge, answer and sources."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    admin_hint_html,
    chip_html,
    citation_cards_html,
    flow_strip_html,
    get_college_name,
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
    """A failure we can explain to the user in plain words."""

    def __init__(self, title: str, body: str, details: str = ""):
        super().__init__(title)
        self.title, self.body, self.details = title, body, details


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
            ) from exc
    try:
        from smart_campus_assistant.graph import graph
    except Exception as exc:  # ImportError or an error while building the graph
        raise BackendError(
            "The agent backend is not available",
            "`smart_campus_assistant.graph` could not be loaded. Check that the backend is installed "
            "(`pip install -e .`) and that all its modules import cleanly.",
            traceback.format_exc(),
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


def run_question(question: str, demo: bool, live, student_id: str | None = None) -> dict:
    """Stream the graph, updating the live flow strip; returns a result dict for history."""
    graph = load_graph(demo)
    result = {"question": question, "citations": [], "steps": [], "student_id": student_id}
    steps = result["steps"]
    live.html(live_html(None, [], "Routing…"))

    for chunk in graph.stream({"question": question, "student_id": student_id}, stream_mode="updates"):
        for node, update in (chunk or {}).items():
            if node.startswith("__"):
                continue
            update = update or {}
            step = step_detail(node, update, student_id)
            steps.append(step)
            if step.get("guest"):
                result["no_record"] = True
            for key in ("route", "route_reason", "answer", "citations"):
                if key in update:
                    result[key] = update[key]

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
    st.markdown(result.get("answer") or "_No answer was returned._")
    if is_admin and result.get("no_record"):
        st.html(admin_hint_html())
    if result.get("steps"):
        st.html(flow_strip_html(result.get("route"), result["steps"]))
    citations = dedupe(result.get("citations") or [])
    if citations:
        with st.expander(f"Sources · {len(citations)}", icon=":material/menu_book:"):
            st.html(citation_cards_html(citations))


def dedupe(citations: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in citations:
        key = (c.get("source"), c.get("page"), (c.get("snippet") or "")[:60])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def render_error(err: BackendError) -> None:
    st.error(f"**{err.title}**\n\n{err.body}", icon=":material/error:")
    if err.details:
        with st.expander("Technical details"):
            st.code(err.details, language="text")


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
    keys = ("answer", "route", "route_reason", "citations", "steps", "no_record")
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
    if result.get("no_record"):
        answer["no_record"] = True
    try:
        store.append_messages(email, [{"role": "user", "content": question}, answer])
    except Exception:  # noqa: BLE001
        pass


def clear_chat() -> None:
    st.session_state.messages = []
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
    st.markdown(
        f'<div class="sca-empty" style="margin-top:.4rem"><div class="icon">💬</div><h3>{title}</h3><p>{text}</p></div>',
        unsafe_allow_html=True,
    )
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
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/school:"):
        try:
            if not demo and not has_aws_credentials():
                raise BackendError(
                    "AWS is not configured",
                    "No AWS credentials were found (environment, `.env` or `~/.aws`). Add them, or start the app "
                    "with `SCA_DEMO_MODE=1` to try the full flow offline.",
                )
            live = st.empty()
            result = run_question(question, demo, live, student_id)
            live.empty()
            render_result(result, is_admin)
            st.session_state.messages.append({"role": "assistant", "result": result})
            save_turn(user.get("email", ""), question, result)
        except Exception as exc:  # show a friendly box instead of a stack trace
            err = explain_error(exc)
            render_error(err)
            st.session_state.messages.append(
                {"role": "assistant", "error": {"title": err.title, "body": err.body, "details": err.details}}
            )
