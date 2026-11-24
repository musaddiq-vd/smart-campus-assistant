"""Page "Insights" (admin only): what students ask, what the documents could not answer, how the answers
were rated, and what the model costs (latency, tokens, cost and failed questions)."""

from __future__ import annotations

import importlib
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
    callout_html,
    confirm_prompt,
    empty_state,
    flash,
    format_cost,
    format_latency,
    hero,
    kpi_card_html,
    kpi_row_html,
    local_stamp,
    nice_date,
    require_role,
    section_header,
    show_flashes,
)
from ui.theme import (  # noqa: E402
    BASELINE_COLOR,
    DEEP_COLOR,
    PERSONAL_COLOR,
    ROUTER_COLOR,
    SIMPLE_COLOR,
    WIDE_WIDTH,
    inject_css,
)

# How the three answer paths are named for admins.
ROUTE_LABELS = {"simple": "Quick answer", "personal": "Own record", "deep": "Research"}
ROUTE_LABEL_COLORS = {"Quick answer": SIMPLE_COLOR, "Own record": PERSONAL_COLOR, "Research": DEEP_COLOR}
NOT_ROUTED = "Not routed"  # a question that failed before the router chose a path

INSIGHTS_CSS = """
<style>
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


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


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


def weakest_topic(by_topic: dict) -> tuple[str, int]:
    """(label, thumbs down) of the topic with the most "not helpful" ratings; ("—", 0) when there are none."""
    downs = {t: int(c.get("total") or 0) - int(c.get("helpful") or 0) for t, c in (by_topic or {}).items()}
    downs = {t: n for t, n in downs.items() if n > 0}
    if not downs:
        return "—", 0
    topic = max(downs, key=lambda t: downs[t])
    return topic_label(topic), downs[topic]


def rating_table(split: dict, column: str, label) -> pd.DataFrame:
    """Ratings per kind of answer or topic: ratings, helpful share, not-helpful count."""
    rows = []
    for key, counts in (split or {}).items():
        total, helpful = int(counts.get("total") or 0), int(counts.get("helpful") or 0)
        if total:
            rows.append({column: label(key), "Ratings": total, "Helpful": percent_text(helpful, total),
                         "Not helpful": total - helpful})
    return pd.DataFrame(rows)


def unhelpful_table(rows: list[dict]) -> pd.DataFrame:
    """Recent "not helpful" answers with the student's comment (newest first)."""
    return pd.DataFrame([
        {"Rated": local_stamp(r.get("rated_at")), "Kind": route_label(r.get("route")),
         "Topic": topic_label(r.get("topic")), "Question": r.get("question", ""), "Comment": r.get("comment") or "—"}
        for r in rows
    ])


def usage_row(stats: dict) -> dict:
    """Display columns of one usage window (see run_metrics.window_stats)."""
    return {
        "Questions": int(stats.get("questions") or 0),
        "Failed": int(stats.get("failed") or 0),
        "Avg time": format_latency(stats.get("avg_latency_ms")),
        "p95 time": format_latency(stats.get("p95_latency_ms")),
        "Tokens": f"{int(stats.get('total_tokens') or 0):,}",
        "Cost": format_cost(stats.get("cost_usd") or 0),
    }


def usage_by_day_table(by_day: list[dict]) -> pd.DataFrame:
    """The last 14 days, newest first."""
    return pd.DataFrame([{"Day": day_label(d.get("day", "")), **usage_row(d)} for d in reversed(by_day or [])])


def usage_by_route_table(by_route: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Kind": route_label(r) if r else NOT_ROUTED, **usage_row(s)} for r, s in (by_route or {}).items()])


def failures_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {"Asked": local_stamp(r.get("asked_at")), "Error": r.get("error", ""),
         "Kind": route_label(r.get("route")) if r.get("route") else NOT_ROUTED, "Question": r.get("question", "")}
        for r in rows
    ])


def errors_text(errors: dict, labels: dict) -> str:
    """"2 AWS not configured, 1 Backend error" (most frequent first)."""
    return ", ".join(f"{n:,} {labels.get(kind, kind)}" for kind, n in (errors or {}).items() if n)


# ---------- optional data sources ----------

def _optional_module(name: str):
    """A backend module for an optional section, or None: one missing table must not hide the others."""
    try:
        return importlib.import_module(f"smart_campus_assistant.{name}")
    except Exception:  # noqa: BLE001
        return None


def read_feedback(module) -> tuple[dict | None, list[dict]]:
    """(summary, recent "not helpful" ratings); (None, []) when the ratings cannot be read."""
    if module is None:
        return None, []
    try:
        return module.summary(), module.recent_unhelpful(limit=20)
    except Exception:  # noqa: BLE001
        return None, []


def read_usage(module) -> tuple[dict | None, list[dict]]:
    """(summary, recent failed questions); (None, []) when the usage rows cannot be read."""
    if module is None:
        return None, []
    try:
        return module.summary(), module.recent_failures(limit=20)
    except Exception:  # noqa: BLE001
        return None, []


def _csv(module) -> str:
    """CSV text of one table, or "" (download disabled) when it cannot be read."""
    if module is None:
        return ""
    try:
        return module.rows_to_csv(module.export_rows())
    except Exception:  # noqa: BLE001
        return ""


# ---------- callbacks ----------

def _flash(kind: str, text: str) -> None:
    flash("ins_flash", kind, text)  # the shared pattern: one inline message after the action


def ask_clear() -> None:
    st.session_state["ins_confirm_clear"] = True


def cancel_clear() -> None:
    st.session_state.pop("ins_confirm_clear", None)


def confirm_clear() -> None:
    """Delete the question log, the answer ratings and the model usage rows (all anonymous Insights data)."""
    st.session_state.pop("ins_confirm_clear", None)
    try:
        removed = question_log.clear()
        ratings = feedback_mod.clear() if feedback_mod is not None else 0
        runs = metrics_mod.clear() if metrics_mod is not None else 0
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not clear the log: {type(exc).__name__}.")
        return
    _flash("success", f"Cleared the log ({plural(removed, 'question')}, {plural(ratings, 'rating')} and "
                      f"{plural(runs, 'usage record')} removed).")


# ---------- sections ----------

def render_question_sections(stats: dict, gaps: list[dict], popular: list[dict]) -> None:
    total = int(stats.get("total") or 0)
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
        kpi_card_html("Most asked topic", topic_name, f"Asked {times_text(topic_count)}" if topic_count else "",
                      DEEP_COLOR),
    ]
    st.markdown(kpi_row_html(cards), unsafe_allow_html=True)

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
            color=alt.Color("Result:N", scale=alt.Scale(domain=["Answered", "Not answered"],
                                                        range=[SIMPLE_COLOR, BASELINE_COLOR]),
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
                color=alt.Color("Kind:N", scale=alt.Scale(domain=kinds, range=[ROUTE_LABEL_COLORS[k] for k in kinds]),
                                legend=None),
                tooltip=["Kind", "Questions"],
            )
            st.altair_chart(route_chart.properties(height=260), width="stretch")

    section_header("Questions the documents could not answer",
                   f"{len(gaps)} different question{'' if len(gaps) == 1 else 's'}, most frequent first")
    if gaps:
        hint = ("Publish a notice that covers one of these questions (page Notices): the assistant can use it "
                "right away and answer the next student who asks.")
        st.markdown(callout_html(hint, icon="💡"), unsafe_allow_html=True)
        st.dataframe(question_table(gaps), hide_index=True, width="stretch", key="ins_gaps_table")
    elif missed:
        # The KPI above counts them, but personal questions are never listed (their text is not stored).
        st.info(f"No gaps in the college documents. The {plural(missed, 'unanswered question')} counted above "
                f"{'was a personal one' if missed == 1 else 'were personal ones'} (a student's own record, or a "
                "refused question about another student), so the text is not kept.", icon=":material/lock:")
    else:
        st.success("Every question so far was answered from the documents.", icon=":material/check_circle:")

    section_header("Most asked questions", "Similar wording is counted as one question; personal questions are not listed")
    if popular:
        st.dataframe(question_table(popular, with_status=True), hide_index=True, width="stretch", key="ins_top_table")
    else:
        st.caption("Only personal questions have been asked so far; their text is never stored.")


def render_feedback_section(stats: dict | None, recent: list[dict]) -> None:
    section_header("Answer feedback", "Thumbs up or down that students and staff gave the assistant's answers")
    if stats is None:
        st.caption("The answer ratings could not be read.")
        return
    total = int(stats.get("total") or 0)
    if not total:
        st.caption("No answer has been rated yet. Every answer on the Ask page has a thumbs up and down.")
        return
    helpful, unhelpful = int(stats.get("helpful") or 0), int(stats.get("not_helpful") or 0)
    commented = int(stats.get("with_comment") or 0)
    topic, downs = weakest_topic(stats.get("by_topic") or {})
    cards = [
        kpi_card_html("Helpful", percent_text(helpful, total), f"{helpful:,} of {plural(total, 'rated answer')}",
                      SIMPLE_COLOR),
        kpi_card_html("Not helpful", f"{unhelpful:,}", f"{commented:,} with a comment",
                      BASELINE_COLOR if unhelpful else SIMPLE_COLOR),
        kpi_card_html("Weakest topic", topic, f"{downs:,} thumbs down" if downs else "No thumbs down so far",
                      DEEP_COLOR),
    ]
    st.markdown(kpi_row_html(cards), unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.caption("By kind of answer")
        st.dataframe(rating_table(stats.get("by_route"), "Kind", route_label), hide_index=True, width="stretch",
                     key="ins_fb_route_table")
    with right:
        st.caption("By topic")
        st.dataframe(rating_table(stats.get("by_topic"), "Topic", topic_label), hide_index=True, width="stretch",
                     key="ins_fb_topic_table")
    st.caption('Recent "not helpful" answers, newest first')
    if recent:
        st.dataframe(unhelpful_table(recent), hide_index=True, width="stretch", key="ins_fb_recent_table")
    else:
        st.success("No answer has been rated not helpful.", icon=":material/check_circle:")


def render_usage_section(stats: dict | None, failures: list[dict], labels: dict) -> None:
    section_header("Model usage", "Every question asked on the Ask page: time to answer, tokens and cost, by day in the "
                                  "college's time zone")
    if stats is None:
        st.caption("The model usage could not be read.")
        return
    if not int(stats.get("total") or 0):
        st.caption("No questions recorded yet.")
        return
    today, recent = stats.get("today") or {}, stats.get("last_14_days") or {}
    cards = [
        kpi_card_html("Questions today", f"{int(today.get('questions') or 0):,}",
                      f"{int(recent.get('questions') or 0):,} in the last 14 days", ROUTER_COLOR),
        kpi_card_html("Average answer time", format_latency(recent.get("avg_latency_ms")),
                      f"p95 {format_latency(recent.get('p95_latency_ms'))} · last 14 days", SIMPLE_COLOR),
        kpi_card_html("Tokens today", f"{int(today.get('total_tokens') or 0):,}",
                      f"{int(recent.get('total_tokens') or 0):,} in the last 14 days", DEEP_COLOR),
        kpi_card_html("Cost today", format_cost(today.get("cost_usd") or 0),
                      f"{format_cost(recent.get('cost_usd') or 0)} in the last 14 days", PERSONAL_COLOR),
    ]
    st.markdown(kpi_row_html(cards), unsafe_allow_html=True)
    st.caption("Times are from the question to the finished answer, over answered questions. "
               "p95: 95% of answers were ready within this time. Cost uses Amazon Bedrock on-demand prices.")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.caption("Last 14 days, newest first")
        st.dataframe(usage_by_day_table(stats.get("by_day")), hide_index=True, width="stretch", key="ins_usage_days")
    with right:
        st.caption("By kind of answer, last 14 days")
        by_route = usage_by_route_table(stats.get("by_route"))
        if len(by_route):
            st.dataframe(by_route, hide_index=True, width="stretch", key="ins_usage_routes")
        else:
            st.caption("No questions in the last 14 days.")

    failed = int(recent.get("failed") or 0)
    section_header("Failed questions", (f"{plural(failed, 'failure')} in the last 14 days: "
                                        f"{errors_text(stats.get('errors'), labels)}") if failed
                   else "No failures in the last 14 days")
    if failures:
        st.dataframe(failures_table(failures), hide_index=True, width="stretch", key="ins_failures_table")
    else:
        st.success("No question has failed so far.", icon=":material/check_circle:")


def render_export_and_reset(total: int, ratings: int, runs: int) -> None:
    section_header("Export and reset", "Anonymous data only: question, topic, kind of answer, branch, ratings and usage")
    with st.container(horizontal=True, gap="small"):
        for label, module, file_name, key in (
            ("Questions CSV", question_log, "helpdesk_questions.csv", "ins_download"),
            ("Feedback CSV", feedback_mod, "helpdesk_answer_feedback.csv", "ins_download_feedback"),
            ("Model usage CSV", metrics_mod, "helpdesk_model_usage.csv", "ins_download_usage"),
        ):
            csv_text = _csv(module)
            st.download_button(label, data=csv_text.encode("utf-8-sig"), file_name=file_name, mime="text/csv",
                               icon=":material/download:", disabled=not csv_text, key=key)
        st.button("Clear log", icon=":material/delete_sweep:", on_click=ask_clear, key="ins_clear",
                  disabled=bool(st.session_state.get("ins_confirm_clear")))
    if st.session_state.get("ins_confirm_clear"):
        confirm_prompt(f"Delete all {plural(total, 'logged question')}, {plural(ratings, 'answer rating')} and "
                       f"{plural(runs, 'usage record')}? This cannot be undone. Download the CSV files first if you "
                       "want to keep a copy.",
                       yes_label="Yes, clear", yes_key="ins_clear_yes", on_yes=confirm_clear,
                       no_key="ins_clear_no", on_no=cancel_clear)


PRIVACY_NOTE = ("Privacy: names, emails and student IDs are never stored. Questions about a student's own record are "
                "counted by topic only; their text is not kept. Ratings and comments are anonymous (comments are "
                "masked like questions), and model usage keeps the question text of failed questions only.")


# ---------- page ----------

inject_css(max_width=WIDE_WIDTH)
st.html(INSIGHTS_CSS)
require_role("admin")

try:
    from smart_campus_assistant import question_log
except Exception as exc:  # noqa: BLE001
    st.error(f"The question log could not be loaded: {type(exc).__name__}.")
    st.stop()
feedback_mod = _optional_module("feedback")
metrics_mod = _optional_module("run_metrics")

hero("Admin · insights", "Helpdesk insights",
     "What students ask the assistant, which questions the college documents could not answer, how the "
     "answers were rated and what the model costs.")
show_flashes("ins_flash")

try:
    stats = question_log.summary()
    gaps = question_log.unanswered(limit=50)
    popular = question_log.top_questions(limit=20)
except Exception as exc:  # noqa: BLE001
    st.error(f"The question log could not be read: {type(exc).__name__}.")
    st.stop()
fb_stats, fb_recent = read_feedback(feedback_mod)
usage_stats, failures = read_usage(metrics_mod)

total = int(stats.get("total") or 0)
n_ratings = int((fb_stats or {}).get("total") or 0)
n_runs = int((usage_stats or {}).get("total") or 0)
if not (total or n_ratings or n_runs):
    empty_state("📊", "No questions yet",
                "Once students ask the assistant something, this page shows the common topics, the questions the "
                "college documents could not answer, how the answers were rated and what the model costs.")
    st.markdown(f'<div class="sca-ins-note">{escape(PRIVACY_NOTE)}</div>', unsafe_allow_html=True)
    st.stop()

if total:
    render_question_sections(stats, gaps, popular)
else:
    # A failure such as missing AWS credentials leaves usage rows but no answered question.
    st.info("No question has been answered yet. The model usage below shows the questions that were asked.",
            icon=":material/info:")
render_feedback_section(fb_stats, fb_recent)
render_usage_section(usage_stats, failures, getattr(metrics_mod, "ERROR_TYPES", {}))
render_export_and_reset(total, n_ratings, n_runs)

st.markdown(f'<div class="sca-ins-note">{escape(PRIVACY_NOTE)}</div>', unsafe_allow_html=True)
