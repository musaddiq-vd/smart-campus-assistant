"""Page "Insights" (admin only): what students ask the assistant and what the documents could not answer."""

from __future__ import annotations

import sys
from datetime import date
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    empty_state,
    hero_html,
    kpi_card_html,
    nice_date,
    require_role,
    section_header,
)
from ui.theme import (  # noqa: E402
    BASELINE_COLOR,
    DEEP_COLOR,
    PERSONAL_COLOR,
    ROUTER_COLOR,
    SIMPLE_COLOR,
    inject_css,
)

# How the three answer paths are named for admins.
ROUTE_LABELS = {"simple": "Quick answer", "personal": "Own record", "deep": "Research"}
ROUTE_LABEL_COLORS = {"Quick answer": SIMPLE_COLOR, "Own record": PERSONAL_COLOR, "Research": DEEP_COLOR}

INSIGHTS_CSS = """
<style>
.sca-ins-hint {
  display: flex; gap: .55rem; align-items: flex-start; margin: .5rem 0 .6rem 0; padding: .55rem .8rem;
  border-radius: 10px; font-size: .86rem; line-height: 1.45;
  background: color-mix(in srgb, var(--sca-brand, #5B5BD6) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-brand, #5B5BD6) 28%, transparent);
}
.sca-ins-note { font-size: .78rem; color: var(--sca-muted, gray); margin: 1.2rem 0 .4rem 0; }
</style>
"""


# ---------- pure helpers ----------

def route_label(route: str | None) -> str:
    return ROUTE_LABELS.get(str(route or ""), "Quick answer")


def topic_label(topic: str | None) -> str:
    return str(topic or "other").replace("_", " ").title()


def percent_text(part: int, total: int) -> str:
    """Whole percentage like "83%"; a dash when there is nothing to divide by."""
    return f"{round(part * 100 / total)}%" if total else "—"


def top_topic(by_topic: dict) -> tuple[str, int]:
    """(label, count) of the most asked topic; "other" only wins when it is the only one."""
    named = {t: n for t, n in (by_topic or {}).items() if t != "other" and n}
    pool = named or {t: n for t, n in (by_topic or {}).items() if n}
    if not pool:
        return "—", 0
    topic = max(pool, key=lambda t: pool[t])
    return topic_label(topic), int(pool[topic])


def day_label(day: str) -> str:
    """"2026-11-03" -> "03 Nov"."""
    try:
        return date.fromisoformat(str(day)[:10]).strftime("%d %b")
    except ValueError:
        return str(day)


def times_text(n: int) -> str:
    return f"{n} time{'' if n == 1 else 's'}"


def question_table(groups: list[dict], with_status: bool = False) -> pd.DataFrame:
    """Grouped questions as a table: question, times asked, last asked (and how often it went unanswered)."""
    rows = []
    for g in groups:
        row = {"Question": g.get("question", ""), "Topic": topic_label(g.get("topic")),
               "Times asked": int(g.get("count") or 0), "Last asked": nice_date(g.get("last_asked"))}
        if with_status:
            misses = int(g.get("unanswered") or 0)
            row["Answered"] = "Yes" if not misses else ("No" if misses == row["Times asked"] else "Sometimes")
        rows.append(row)
    return pd.DataFrame(rows)


# ---------- callbacks ----------

def _flash(kind: str, text: str) -> None:
    st.session_state.setdefault("ins_flash", []).append((kind, text))


def ask_clear() -> None:
    st.session_state["ins_confirm_clear"] = True


def cancel_clear() -> None:
    st.session_state.pop("ins_confirm_clear", None)


def confirm_clear() -> None:
    st.session_state.pop("ins_confirm_clear", None)
    try:
        removed = question_log.clear()
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not clear the log: {type(exc).__name__}.")
        return
    _flash("success", f"Cleared the log ({removed} question{'' if removed == 1 else 's'} removed).")


# ---------- page ----------

inject_css()
st.html(INSIGHTS_CSS)
require_role("admin")

try:
    from smart_campus_assistant import question_log
except Exception as exc:  # noqa: BLE001
    st.error(f"The question log could not be loaded: {type(exc).__name__}.")
    st.stop()

st.markdown(
    hero_html("Admin · insights", "Helpdesk insights",
              "What students ask the assistant, and which questions the college documents could not answer."),
    unsafe_allow_html=True,
)

for kind, text in st.session_state.pop("ins_flash", []):
    icon = {"success": ":material/check_circle:", "error": ":material/error:"}[kind]
    getattr(st, kind)(text, icon=icon)

try:
    stats = question_log.summary()
    gaps = question_log.unanswered(limit=50)
    popular = question_log.top_questions(limit=20)
except Exception as exc:  # noqa: BLE001
    st.error(f"The question log could not be read: {type(exc).__name__}.")
    st.stop()

total = int(stats.get("total") or 0)
if not total:
    empty_state("📊", "No questions yet",
                "Once students ask the assistant something, this page shows the common topics and the "
                "questions the college documents could not answer.")
    st.markdown('<div class="sca-ins-note">Only the question, its topic and the asker\'s branch are kept. '
                "Names, emails and student IDs are never stored.</div>", unsafe_allow_html=True)
    st.stop()

answered = int(stats.get("answered") or 0)
missed = int(stats.get("unanswered") or 0)
by_topic = stats.get("by_topic") or {}
by_route = stats.get("by_route") or {}
topic_name, topic_count = top_topic(by_topic)

cards = [
    kpi_card_html("Questions asked", f"{total:,}", "Since the log was last cleared", ROUTER_COLOR),
    kpi_card_html("Answered", percent_text(answered, total), f"{answered:,} of {total:,} questions", SIMPLE_COLOR),
    kpi_card_html("Unanswered", f"{missed:,}", "The documents had no answer" if missed else "Nothing missed so far",
                  BASELINE_COLOR if missed else SIMPLE_COLOR),
    kpi_card_html("Most asked topic", topic_name, f"Asked {times_text(topic_count)}" if topic_count else "", DEEP_COLOR),
]
st.markdown(f'<div class="sca-kpis">{"".join(cards)}</div>', unsafe_allow_html=True)

section_header("Questions per day", "Last 14 days, split by whether the documents had an answer")
day_rows = []
for entry in stats.get("by_day") or []:
    day_total, day_missed = int(entry.get("total") or 0), int(entry.get("unanswered") or 0)
    label = day_label(entry.get("day", ""))
    day_rows.append({"Day": label, "Result": "Answered", "Questions": day_total - day_missed})
    day_rows.append({"Day": label, "Result": "Not answered", "Questions": day_missed})
if any(r["Questions"] for r in day_rows):
    day_order = list(dict.fromkeys(r["Day"] for r in day_rows))
    day_chart = alt.Chart(pd.DataFrame(day_rows)).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("Day:N", sort=day_order, title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        y=alt.Y("Questions:Q", title="Questions", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("Result:N", scale=alt.Scale(domain=["Answered", "Not answered"], range=[SIMPLE_COLOR, BASELINE_COLOR]),
                        legend=alt.Legend(orient="bottom", title=None)),
        order=alt.Order("Result:N", sort="ascending"),
        tooltip=["Day", "Result", "Questions"],
    )
    st.altair_chart(day_chart.properties(height=260), width="stretch")
else:
    st.caption("No questions in the last 14 days.")

left, right = st.columns([3, 2], gap="large")
with left:
    section_header("By topic", "What the questions were about")
    topic_df = pd.DataFrame([{"Topic": topic_label(t), "Questions": int(n)} for t, n in by_topic.items() if n])
    if len(topic_df):
        topic_chart = alt.Chart(topic_df).mark_bar(cornerRadiusEnd=4, color=ROUTER_COLOR).encode(
            x=alt.X("Questions:Q", title=None, axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("Topic:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
            tooltip=["Topic", "Questions"],
        )
        st.altair_chart(topic_chart.properties(height=max(140, 30 * len(topic_df) + 40)), width="stretch")
with right:
    section_header("By kind of answer", "Quick answer, the student's own record, or research across documents")
    route_df = pd.DataFrame([{"Kind": route_label(r), "Questions": int(n)} for r, n in by_route.items() if n])
    if len(route_df):
        kinds = [k for k in ROUTE_LABEL_COLORS if k in set(route_df["Kind"])]
        route_chart = alt.Chart(route_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Kind:N", sort=kinds, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Questions:Q", title=None, axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Kind:N", scale=alt.Scale(domain=kinds, range=[ROUTE_LABEL_COLORS[k] for k in kinds]), legend=None),
            tooltip=["Kind", "Questions"],
        )
        st.altair_chart(route_chart.properties(height=260), width="stretch")

section_header("Questions the documents could not answer",
               f"{len(gaps)} different question{'' if len(gaps) == 1 else 's'}, most frequent first")
if gaps:
    hint = ("Publish a notice that covers one of these questions (page Notices): the assistant can use it "
            "right away and answer the next student who asks.")
    st.markdown(f'<div class="sca-ins-hint"><span>💡</span><span>{escape(hint)}</span></div>', unsafe_allow_html=True)
    st.dataframe(question_table(gaps), hide_index=True, width="stretch", key="ins_gaps_table")
else:
    st.success("Every question so far was answered from the documents.", icon=":material/check_circle:")

section_header("Most asked questions", "Similar wording is counted as one question; personal questions are not listed")
if popular:
    st.dataframe(question_table(popular, with_status=True), hide_index=True, width="stretch", key="ins_top_table")
else:
    st.caption("Only personal questions have been asked so far; their text is never stored.")

section_header("Export and reset", "The log keeps the question, topic, kind of answer and branch only")
export_col, clear_col, _ = st.columns([1, 1, 3])
with export_col:
    try:
        csv_text = question_log.rows_to_csv(question_log.export_rows())
    except Exception:  # noqa: BLE001
        csv_text = ""
    st.download_button("Download CSV", data=csv_text.encode("utf-8-sig"), file_name="helpdesk_questions.csv",
                       mime="text/csv", icon=":material/download:", disabled=not csv_text, key="ins_download")
with clear_col:
    st.button("Clear log", icon=":material/delete_sweep:", on_click=ask_clear, key="ins_clear",
              disabled=bool(st.session_state.get("ins_confirm_clear")))
if st.session_state.get("ins_confirm_clear"):
    st.warning(f"Delete all {total:,} logged questions? This cannot be undone. Download the CSV first "
               "if you want to keep a copy.", icon=":material/warning:")
    yes, no, _ = st.columns([1, 1, 4])
    with yes:
        st.button("Yes, clear", type="primary", on_click=confirm_clear, key="ins_clear_yes")
    with no:
        st.button("Cancel", on_click=cancel_clear, key="ins_clear_no")

st.markdown('<div class="sca-ins-note">Privacy: names, emails and student IDs are never stored. Questions about a '
            "student's own record are counted by topic only; their text is not kept.</div>", unsafe_allow_html=True)
