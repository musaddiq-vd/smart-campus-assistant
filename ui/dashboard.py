"""Page "Cost & Accuracy": eval results for the router vs an always-deep baseline."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    chip_html,
    empty_state,
    format_cost,
    format_latency,
    format_pct,
    hero_html,
    kpi_card_html,
    section_header,
)
from ui.theme import (  # noqa: E402
    BASELINE_COLOR,
    MODE_COLORS,
    MODE_LABELS,
    PERSONAL_COLOR,
    ROUTE_COLORS,
    ROUTER_COLOR,
    SIMPLE_COLOR,
    inject_css,
)

try:
    from smart_campus_assistant.config import ROUTES as _ROUTES

    ROUTES = [r for r in _ROUTES if r in ROUTE_COLORS] or ["simple", "deep", "personal"]
except Exception:
    ROUTES = ["simple", "deep", "personal"]
MODES = ["router", "always_deep"]
ROUTE_SCALE = alt.Scale(domain=ROUTES, range=[ROUTE_COLORS[r] for r in ROUTES])
MODE_SCALE = alt.Scale(domain=[MODE_LABELS[m] for m in MODES], range=[MODE_COLORS[m] for m in MODES])
GENERATE_CMD = "python eval/run_eval.py --fake"


# ---------- data ----------

def results_dir() -> Path:
    """Session override (tests) > SCA_RESULTS_DIR env var > settings > eval/results."""
    override = st.session_state.get("results_dir") or os.getenv("SCA_RESULTS_DIR")
    if override:
        return Path(override)
    try:
        from smart_campus_assistant.config import get_settings

        return Path(get_settings().results_dir)
    except Exception:
        return ROOT / "eval" / "results"


def _to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    if pd.api.types.is_numeric_dtype(series):  # e.g. keyword_hit written as 1.0 / 0.0
        return series.fillna(0) > 0
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "1.0", "yes", "y"])


@st.cache_data(show_spinner=False)
def load_results(csv_path: str, summary_path: str, _mtime: float) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(csv_path)
    for col in ("llm_calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd", "latency_ms", "answer_chars", "citations"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ("route_correct", "keyword_hit", "fake"):
        if col in df:
            df[col] = _to_bool(df[col])
    for col in ("expected_route", "predicted_route"):
        if col in df:
            df[col] = df[col].fillna("").astype(str).str.strip().str.lower().replace({"nan": "", "none": ""})
    if "student_id" in df:
        df["student_id"] = df["student_id"].fillna("").astype(str).replace({"nan": "", "None": ""})
    df["error"] = df["error"].astype(object).fillna("").astype(str).replace({"nan": "", "None": ""}) if "error" in df else ""
    df["mode_label"] = df["mode"].map(MODE_LABELS).fillna(df["mode"])
    summary = {}
    if Path(summary_path).is_file():
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            summary = {}
    return df, summary


def kpis(df: pd.DataFrame) -> dict:
    router = df[df["mode"] == "router"]
    deep = df[df["mode"] == "always_deep"]
    out = {
        "n": router["question_id"].nunique() if len(router) else df["question_id"].nunique(),
        "accuracy": router["route_correct"].mean() if len(router) else None,
        "correct": int(router["route_correct"].sum()) if len(router) else 0,
        "router_cost": router["cost_usd"].sum() if len(router) else None,
        "deep_cost": deep["cost_usd"].sum() if len(deep) else None,
        "kw_router": router["keyword_hit"].mean() if len(router) and "keyword_hit" in router else None,
        "kw_deep": deep["keyword_hit"].mean() if len(deep) and "keyword_hit" in deep else None,
        "lat": router.groupby("predicted_route")["latency_ms"].mean().to_dict() if len(router) else {},
        "errors": int((df["error"] != "").sum()),
    }
    rc, dc = out["router_cost"], out["deep_cost"]
    out["savings"] = (1 - rc / dc) if rc is not None and dc else None
    return out


# ---------- charts ----------

def ink() -> dict:
    """Text / surface colors for the active Streamlit theme (Vega does not follow it for custom marks)."""
    try:
        dark = (st.context.theme.type or "light") == "dark"
    except Exception:
        dark = False
    if dark:
        return {"text": "#E8E9F0", "muted": "#9AA0B4", "surface": "#0F1117", "cell_low": "#262A47"}
    return {"text": "#1A1C2E", "muted": "#6B7085", "surface": "#FFFFFF", "cell_low": "#EEF0FF"}


def _chart(chart: alt.Chart) -> None:
    # Extra left padding: Vega measures labels before the web font loads, which can clip them.
    chart = chart.properties(padding={"left": 14, "right": 8, "top": 4, "bottom": 4})
    st.altair_chart(chart.configure_view(strokeWidth=0), width="stretch")


def cost_comparison_chart(df: pd.DataFrame) -> alt.Chart:
    agg = (
        df.groupby(["mode", "mode_label"], as_index=False)
        .agg(total=("cost_usd", "sum"), avg=("cost_usd", "mean"), tokens=("total_tokens", "mean"), calls=("llm_calls", "mean"))
    )
    agg["avg_label"] = agg["avg"].map(format_cost)
    agg["label"] = agg["total"].map(format_cost) + " · " + agg["avg_label"] + "/q"
    order = [MODE_LABELS[m] for m in MODES]
    base = alt.Chart(agg).encode(
        y=alt.Y("mode_label:N", title=None, sort=order, axis=alt.Axis(labelPadding=8, labelLimit=160)),
        x=alt.X(
            "total:Q", title="Total cost for the eval set (USD)",
            axis=alt.Axis(format="$.4f", tickCount=4, grid=True),
            scale=alt.Scale(domainMax=float(agg["total"].max()) * 1.75 or 1),  # room for the labels
        ),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=40).encode(
        color=alt.Color("mode_label:N", scale=MODE_SCALE, legend=None),
        tooltip=[
            alt.Tooltip("mode_label:N", title="Mode"),
            alt.Tooltip("label:N", title="Total cost"),
            alt.Tooltip("avg_label:N", title="Avg per question"),
            alt.Tooltip("tokens:Q", title="Avg tokens", format=",.0f"),
            alt.Tooltip("calls:Q", title="Avg LLM calls", format=".1f"),
        ],
    )
    text = base.mark_text(align="left", dx=6, fontWeight=600, color=ink()["text"]).encode(text="label:N")
    return (bars + text).properties(height=230)


def cost_per_question_chart(router: pd.DataFrame) -> alt.Chart:
    data = router.copy()
    rng = np.random.default_rng(7)  # stable jitter
    data["jitter"] = rng.uniform(-0.3, 0.3, len(data))
    data["cost_label"] = data["cost_usd"].map(format_cost)
    points = alt.Chart(data).mark_circle(size=90, opacity=0.85, stroke=ink()["surface"], strokeWidth=1).encode(
        x=alt.X("cost_usd:Q", title="Cost per question (USD)", axis=alt.Axis(format="$.5f", tickCount=3, labelOverlap="greedy")),
        y=alt.Y("predicted_route:N", title=None, sort=ROUTES, axis=alt.Axis(labelPadding=8, labelLimit=160)),
        yOffset=alt.YOffset("jitter:Q", scale=alt.Scale(domain=[-1, 1])),
        color=alt.Color("predicted_route:N", scale=ROUTE_SCALE, legend=None),
        tooltip=[
            alt.Tooltip("question:N", title="Question"),
            alt.Tooltip("predicted_route:N", title="Route"),
            alt.Tooltip("cost_label:N", title="Cost"),
            alt.Tooltip("total_tokens:Q", title="Tokens", format=","),
            alt.Tooltip("latency_ms:Q", title="Latency (ms)", format=",.0f"),
        ],
    )
    means = data.groupby("predicted_route", as_index=False)["cost_usd"].mean()
    means["label"] = "avg " + means["cost_usd"].map(format_cost)
    rule = alt.Chart(means).mark_tick(thickness=2, size=44, color=ink()["muted"]).encode(
        x="cost_usd:Q", y=alt.Y("predicted_route:N", sort=ROUTES)
    )
    label = alt.Chart(means).mark_text(dy=-26, fontSize=11, color=ink()["muted"]).encode(
        x="cost_usd:Q", y=alt.Y("predicted_route:N", sort=ROUTES), text="label:N"
    )
    return (points + rule + label).properties(height=70 * len(ROUTES) + 30)


def confusion_chart(router: pd.DataFrame) -> alt.Chart:
    cells = pd.DataFrame([(e, p) for e in ROUTES for p in ROUTES], columns=["expected", "predicted"])
    counts = router.groupby(["expected_route", "predicted_route"]).size().rename("count").reset_index()
    counts.columns = ["expected", "predicted", "count"]
    cells = cells.merge(counts, how="left", on=["expected", "predicted"]).fillna({"count": 0})
    cells["count"] = cells["count"].astype(int)
    cells["outcome"] = np.where(cells["expected"] == cells["predicted"], "Correct", "Misrouted")
    vmax = max(int(cells["count"].max()), 1)
    base = alt.Chart(cells).encode(
        x=alt.X("predicted:N", title="Predicted route", sort=ROUTES, axis=alt.Axis(labelAngle=0, orient="top")),
        y=alt.Y("expected:N", title="Expected route", sort=ROUTES, axis=alt.Axis(labelPadding=8, labelLimit=160)),
    )
    rect = base.mark_rect(cornerRadius=8, stroke=ink()["surface"], strokeWidth=3).encode(
        color=alt.Color("count:Q", scale=alt.Scale(domain=[0, vmax], range=[ink()["cell_low"], ROUTER_COLOR]), legend=None),
        tooltip=["expected:N", "predicted:N", "count:Q", "outcome:N"],
    )
    text = base.mark_text(fontSize=22, fontWeight=700).encode(
        text="count:Q",
        color=alt.condition(alt.datum.count > vmax / 2, alt.value("white"), alt.value(ink()["text"])),
    )
    return (rect + text).properties(height=70 * len(ROUTES) + 30)


def latency_chart(df: pd.DataFrame) -> alt.Chart:
    data = df.copy()
    seconds = data["latency_ms"].max() >= 2000  # fake runs finish in milliseconds
    data["latency"] = data["latency_ms"] / 1000 if seconds else data["latency_ms"]
    data["group"] = np.where(
        data["mode"] == "router", "Router · " + data["predicted_route"].astype(str), "Always deep"
    )
    order = [f"Router · {r}" for r in ROUTES] + ["Always deep"]
    colors = [ROUTE_COLORS[r] for r in ROUTES] + [BASELINE_COLOR]
    return (
        alt.Chart(data)
        .mark_boxplot(size=26, extent="min-max", median={"color": "white", "strokeWidth": 2}, rule={"color": ink()["muted"]}, ticks=False)
        .encode(
            x=alt.X("latency:Q", title=f"End-to-end latency ({'seconds' if seconds else 'ms'})"),
            y=alt.Y("group:N", title=None, sort=order, axis=alt.Axis(labelPadding=8, labelLimit=160)),
            color=alt.Color("group:N", scale=alt.Scale(domain=order, range=colors), legend=None),
        )
        .properties(height=60 * len(order) + 30)
    )


def route_breakdown(router: pd.DataFrame) -> pd.DataFrame:
    """Per predicted route (router mode): count, avg calls, tokens, cost and latency."""
    rows = []
    for route in ROUTES:
        part = router[router["predicted_route"] == route]
        rows.append({
            "route": route,
            "questions": len(part),
            "accuracy": part["route_correct"].mean() if len(part) else None,
            "llm_calls": part["llm_calls"].mean() if len(part) and "llm_calls" in part else None,
            "total_tokens": part["total_tokens"].mean() if len(part) and "total_tokens" in part else None,
            "cost_usd": part["cost_usd"].mean() if len(part) else None,
            "latency_ms": part["latency_ms"].mean() if len(part) else None,
        })
    return pd.DataFrame(rows)


# ---------- page ----------

def render_empty(path: Path) -> None:
    empty_state(
        "📊",
        "No evaluation results yet",
        "Run the evaluation to compare the router against an always-deep baseline. "
        "The offline run uses the fake LLM, takes a few seconds and costs nothing.",
    )
    try:
        shown = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        shown = str(path)
    st.write("")
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.code(GENERATE_CMD, language="bash")
        st.caption(f"Results are read from `{shown}/`. Drop `--fake` to evaluate against Amazon Bedrock (a few cents).")


def render_dashboard(df: pd.DataFrame, summary: dict) -> None:
    k = kpis(df)
    router = df[df["mode"] == "router"]

    # KPI row
    lat = k["lat"]
    lat_value = format_latency(router["latency_ms"].mean()) if len(router) else "—"
    lat_sub = " · ".join(f"{r} {format_latency(lat[r])}" for r in ROUTES if r in lat) or "router mode"
    cards = [
        kpi_card_html(
            "Router accuracy", format_pct(k["accuracy"]),
            f"{k['correct']} of {len(router)} routed as labelled", ROUTER_COLOR,
        ),
        kpi_card_html(
            "Cost saved", format_pct(k["savings"]),
            f"vs always-deep · {format_cost(k['router_cost'])} vs {format_cost(k['deep_cost'])}", SIMPLE_COLOR,
        ),
        kpi_card_html(
            "Avg latency", lat_value,
            lat_sub, PERSONAL_COLOR,
        ),
        kpi_card_html(
            "Keyword hit rate", format_pct(k["kw_router"]),
            f"Always-deep: {format_pct(k['kw_deep'])}" if k["kw_deep"] is not None else "Expected facts found in answers",
            BASELINE_COLOR,
        ),
    ]
    st.markdown(f'<div class="sca-kpis">{"".join(cards)}</div>', unsafe_allow_html=True)

    # Charts
    left, right = st.columns(2, gap="large")
    with left:
        section_header("Total cost: router vs always-deep", "Same questions, two strategies. Lower is better.")
        _chart(cost_comparison_chart(df))
    with right:
        section_header("Cost per question by route", "Router mode. Each dot is one question; tick = average.")
        if len(router):
            _chart(cost_per_question_chart(router))
        else:
            st.info("No router-mode rows in the results.")

    left, right = st.columns(2, gap="large")
    with left:
        section_header("Routing confusion matrix", "Rows: labelled route. Columns: router's choice.")
        if len(router):
            _chart(confusion_chart(router))
    with right:
        section_header("Latency distribution", "Box = middle 50%, line = median, whiskers = min to max.")
        _chart(latency_chart(df))

    if len(router):
        section_header("Per-route averages", "Router mode, grouped by the route it picked.")
        st.dataframe(
            route_breakdown(router),
            hide_index=True,
            width="stretch",
            column_config={
                "route": st.column_config.TextColumn("Route"),
                "questions": st.column_config.NumberColumn("Questions", format="%d"),
                "accuracy": st.column_config.ProgressColumn("Correct", help="Share of the questions sent to this route that were labelled with it", format="percent", min_value=0, max_value=1),
                "llm_calls": st.column_config.NumberColumn("Avg calls", format="%.1f"),
                "total_tokens": st.column_config.NumberColumn("Avg tokens", format="%.0f"),
                "cost_usd": st.column_config.NumberColumn("Avg cost", format="$%.6f"),
                "latency_ms": st.column_config.NumberColumn("Avg latency", format="%.0f ms"),
            },
        )

    # Table
    section_header("All questions", "Filter to inspect misroutes, errors or a single strategy.")
    f1, f2 = st.columns(2, vertical_alignment="bottom")
    f4, f3 = st.columns([3, 1], vertical_alignment="bottom")
    modes = f1.pills("Mode", MODES, selection_mode="multi", default=MODES, format_func=lambda m: MODE_LABELS.get(m, m)) or []
    routes = f2.pills("Predicted route", ROUTES, selection_mode="multi", default=ROUTES) or []
    only_miss = f3.toggle("Misrouted only")
    search = f4.text_input("Search questions", placeholder="e.g. hostel, XYZ24CSE001", label_visibility="visible")

    view = df[df["mode"].isin(modes) & df["predicted_route"].isin(routes)]
    if only_miss:
        view = view[~view["route_correct"]]
    if search:
        hit = view["question"].str.contains(search, case=False, na=False, regex=False)
        if "student_id" in view:
            hit |= view["student_id"].str.contains(search, case=False, na=False, regex=False)
        view = view[hit]

    columns = ["question_id", "question", "student_id", "mode_label", "expected_route", "predicted_route", "route_correct",
               "llm_calls", "total_tokens", "cost_usd", "latency_ms", "keyword_hit", "citations", "error"]
    st.dataframe(
        view[[c for c in columns if c in view]],
        hide_index=True,
        width="stretch",
        column_config={
            "question_id": st.column_config.TextColumn("ID", width="small"),
            "question": st.column_config.TextColumn("Question", width="large"),
            "student_id": st.column_config.TextColumn("Student", help="Logged-in student for this question; empty = guest"),
            "mode_label": st.column_config.TextColumn("Mode"),
            "expected_route": st.column_config.TextColumn("Expected"),
            "predicted_route": st.column_config.TextColumn("Predicted"),
            "route_correct": st.column_config.CheckboxColumn("Correct"),
            "llm_calls": st.column_config.NumberColumn("Calls", format="%d"),
            "total_tokens": st.column_config.NumberColumn("Tokens", format="%d"),
            "cost_usd": st.column_config.NumberColumn("Cost", format="$%.6f"),
            "latency_ms": st.column_config.NumberColumn("Latency", format="%.0f ms"),
            "keyword_hit": st.column_config.CheckboxColumn("Keyword hit"),
            "citations": st.column_config.NumberColumn("Cites", format="%d"),
            "error": st.column_config.TextColumn("Error"),
        },
    )
    st.caption(f"{len(view)} of {len(df)} rows")


inject_css()
path = results_dir()
csv_path, summary_path = path / "latest.csv", path / "latest_summary.json"

chips = []
data = None
if csv_path.is_file():
    try:
        data = load_results(str(csv_path), str(summary_path), csv_path.stat().st_mtime)
    except Exception as exc:  # unreadable or wrong columns
        data = None
        load_error = exc
    else:
        load_error = None
else:
    load_error = None

if data is not None:
    df, summary = data
    is_fake = bool(summary.get("fake", df["fake"].any() if "fake" in df else False))
    n = summary.get("n_questions") or df["question_id"].nunique()
    chips = [chip_html(f"{n} questions")]
    if summary.get("generated_at"):
        chips.append(chip_html(f"Generated {str(summary['generated_at'])[:16].replace('T', ' ')}"))
    chips.append(chip_html("Fake LLM run", "demo") if is_fake else chip_html("Live Bedrock run", "live"))

st.markdown(
    hero_html(
        "Evaluation",
        "Cost & Accuracy",
        "How well the router picks one of three routes (simple, deep, personal), and how much it saves "
        "against running deep research on every question.",
        chips,
    ),
    unsafe_allow_html=True,
)

if data is None:
    if load_error is not None:
        st.warning(f"Could not read `{csv_path.name}`: {load_error}", icon=":material/warning:")
    render_empty(path)
else:
    required = {"question_id", "question", "mode", "expected_route", "predicted_route", "route_correct", "cost_usd", "latency_ms"}
    missing = required - set(data[0].columns)
    if missing:
        st.warning(f"`latest.csv` is missing columns: {', '.join(sorted(missing))}. Re-run the eval.", icon=":material/warning:")
        render_empty(path)
    else:
        render_dashboard(*data)
