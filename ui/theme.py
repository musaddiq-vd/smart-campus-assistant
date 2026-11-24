"""Shared colors, design tokens and CSS for every page.

Custom HTML blocks tint themselves from ``currentColor`` with ``color-mix`` so the same
CSS reads well on both Streamlit's light and dark themes: no text or background colour
is hard-coded for one theme. Status colours are always paired with a word or icon, so
nothing relies on colour alone.
"""

from __future__ import annotations

import streamlit as st

# One palette for the whole app (validated for colorblind separation in light and dark).
SIMPLE_COLOR = "#0E9F6E"  # simple route: green (also "ok")
DEEP_COLOR = "#7C3AED"  # deep route: violet
PERSONAL_COLOR = "#E5A50A"  # personal route: amber (also "needs attention"); separable from green, violet and orange
ROUTER_COLOR = "#5B5BD6"  # "router" eval mode: indigo (also the brand color)
BASELINE_COLOR = "#D95926"  # "always_deep" eval mode: orange (also "problem")

ROUTE_COLORS = {"simple": SIMPLE_COLOR, "deep": DEEP_COLOR, "personal": PERSONAL_COLOR}
ROUTE_ICONS = {"simple": "⚡", "deep": "🔬", "personal": "👤"}
MODE_COLORS = {"router": ROUTER_COLOR, "always_deep": BASELINE_COLOR}
MODE_LABELS = {"router": "Router (ours)", "always_deep": "Always deep"}

# Status levels used by every page (sidebar card, dashboards, admin tables, Pay fees).
OK_COLOR, WARN_COLOR, BAD_COLOR, INFO_COLOR = SIMPLE_COLOR, PERSONAL_COLOR, BASELINE_COLOR, ROUTER_COLOR

# Main column widths: forms, reading pages and data-heavy admin pages.
NARROW_WIDTH = "40rem"
CONTENT_WIDTH = "72rem"
WIDE_WIDTH = "88rem"

_TOKENS = f"""
:root {{
  --sca-brand: {ROUTER_COLOR};
  --sca-simple: {SIMPLE_COLOR};
  --sca-deep: {DEEP_COLOR};
  --sca-personal: {PERSONAL_COLOR};
  --sca-baseline: {BASELINE_COLOR};
  --sca-ok: var(--sca-simple);
  --sca-warn: var(--sca-personal);
  --sca-bad: var(--sca-baseline);
  --sca-info: var(--sca-brand);
}}
"""

# Plain string (no f-string), so the CSS braces stay readable.
_RULES = """
:root {
  /* Text tinted towards the theme text colour: AA contrast on light and dark backgrounds. */
  --sca-ok-fg: color-mix(in srgb, var(--sca-ok) 70%, currentColor);
  --sca-warn-fg: color-mix(in srgb, var(--sca-warn) 50%, currentColor);
  --sca-bad-fg: color-mix(in srgb, var(--sca-bad) 70%, currentColor);
  --sca-info-fg: color-mix(in srgb, var(--sca-brand) 75%, currentColor);

  /* Surfaces follow the text colour, so they work on both themes. */
  --sca-card-bg: color-mix(in srgb, currentColor 3.5%, transparent);
  --sca-card-bg-strong: color-mix(in srgb, currentColor 6%, transparent);
  --sca-card-border: color-mix(in srgb, currentColor 12%, transparent);
  --sca-card-border-strong: color-mix(in srgb, currentColor 20%, transparent);
  --sca-muted: color-mix(in srgb, currentColor 68%, transparent);

  --sca-shadow-sm: 0 1px 2px rgba(15, 23, 42, .05);
  --sca-shadow: 0 1px 2px rgba(15, 23, 42, .06), 0 4px 14px rgba(15, 23, 42, .05);
  --sca-shadow-lg: 0 2px 6px rgba(15, 23, 42, .06), 0 18px 48px color-mix(in srgb, var(--sca-brand) 16%, transparent);

  /* Spacing, type and radius scales. */
  --sca-space-1: .25rem; --sca-space-2: .5rem; --sca-space-3: .75rem;
  --sca-space-4: 1rem; --sca-space-5: 1.5rem; --sca-space-6: 2rem;
  --sca-fs-xs: .72rem; --sca-fs-sm: .8rem; --sca-fs-base: .9rem;
  --sca-fs-md: 1rem; --sca-fs-lg: 1.12rem; --sca-fs-xl: 1.5rem;
  --sca-radius-sm: 10px; --sca-radius: 14px; --sca-radius-lg: 18px; --sca-radius-pill: 999px;
  --sca-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* ---------- page frame ---------- */
/* Streamlit leaves 8rem above the content under the top navigation; 5rem clears the bar. */
[data-testid="stMainBlockContainer"] { padding-top: 5rem; padding-bottom: 6rem; }
h1, h2, h3 { letter-spacing: -0.02em; }

/* Visible keyboard focus on everything you can tab to (mouse clicks keep the default look). */
.stApp button:focus-visible, .stApp a:focus-visible, .stApp summary:focus-visible,
.stApp [role="tab"]:focus-visible, .stApp [role="radio"]:focus-visible, .stApp [role="checkbox"]:focus-visible,
.stApp [role="switch"]:focus-visible, .stApp [tabindex]:focus-visible {
  outline: 2px solid var(--sca-brand); outline-offset: 2px; border-radius: 8px;
}
.stApp button { transition: background-color .15s ease, border-color .15s ease, box-shadow .15s ease, color .15s ease; }
.stApp [data-testid^="stBaseButton-primary"]:hover:not(:disabled) {
  box-shadow: 0 4px 14px color-mix(in srgb, var(--sca-brand) 32%, transparent);
}
.stApp [data-testid^="stBaseButton-secondary"]:hover:not(:disabled) {
  background: color-mix(in srgb, var(--sca-brand) 6%, transparent);
}

/* ---------- page header (hero) ---------- */
.sca-hero { margin: 0 0 var(--sca-space-5) 0; }
.sca-hero .eyebrow {
  font-size: var(--sca-fs-xs); font-weight: 650; letter-spacing: .08em; text-transform: uppercase;
  color: var(--sca-info-fg);
}
.sca-hero h1 {
  font-size: clamp(1.5rem, 1.15rem + 1.3vw, 2rem); font-weight: 700; margin: .2rem 0 .35rem 0; padding: 0;
  line-height: 1.2;
}
.sca-hero p { color: var(--sca-muted); margin: 0; font-size: .98rem; line-height: 1.5; max-width: 46rem; }
.sca-hero .chips { margin-top: var(--sca-space-3); display: flex; flex-wrap: wrap; gap: .4rem; }

/* ---------- chips (dot + text, never colour alone) ---------- */
.sca-chip {
  display: inline-flex; align-items: center; gap: .4rem; line-height: 1.4;
  padding: .18rem .65rem; border-radius: var(--sca-radius-pill); font-size: .78rem; font-weight: 550;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); white-space: nowrap;
}
.sca-chip .dot { width: .5rem; height: .5rem; flex: none; border-radius: 50%; background: currentColor; }
.sca-chip.live .dot, .sca-chip.ok .dot { background: var(--sca-ok); }
.sca-chip.demo .dot, .sca-chip.bad .dot { background: var(--sca-bad); }
.sca-chip.warn .dot { background: var(--sca-warn); }
.sca-chip.info .dot { background: var(--sca-brand); }

/* ---------- route badge (chat) ---------- */
.sca-route { display: flex; align-items: center; flex-wrap: wrap; gap: .55rem; margin: .1rem 0 .7rem 0; }
.sca-badge {
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .2rem .62rem; border-radius: 8px;
  font-size: var(--sca-fs-xs); font-weight: 700; letter-spacing: .07em;
}
.sca-badge.simple {
  color: var(--sca-ok-fg);
  background: color-mix(in srgb, var(--sca-simple) 15%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-simple) 35%, transparent);
}
.sca-badge.deep {
  color: color-mix(in srgb, var(--sca-deep) 72%, currentColor);
  background: color-mix(in srgb, var(--sca-deep) 15%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-deep) 38%, transparent);
}
.sca-badge.personal {
  color: var(--sca-warn-fg);
  background: color-mix(in srgb, var(--sca-personal) 17%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-personal) 45%, transparent);
}
.sca-badge.forced {
  color: var(--sca-muted); background: var(--sca-card-bg); border: 1px dashed var(--sca-card-border);
  font-weight: 600; letter-spacing: .04em;
}
.sca-route .reason { color: var(--sca-muted); font-size: .86rem; }

/* Metric tiles under an answer */
.sca-metrics {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .55rem; margin: .9rem 0 .3rem 0;
}
.sca-tile {
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  border-radius: 12px; padding: .55rem .75rem;
}
.sca-tile .label { font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; color: var(--sca-muted); }
.sca-tile .value {
  font-size: clamp(.9rem, 1.6vw, 1.12rem); font-weight: 650; font-variant-numeric: tabular-nums;
  margin-top: .1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sca-tile .sub { font-size: .74rem; color: var(--sca-muted); font-variant-numeric: tabular-nums; }

/* ---------- citation cards ---------- */
.sca-cites { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: .6rem; }
.sca-cite {
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  border-radius: 12px; padding: .7rem .85rem; min-width: 0;
}
.sca-cite .head { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.sca-cite .num {
  font-size: var(--sca-fs-xs); font-weight: 700; color: var(--sca-info-fg);
  background: color-mix(in srgb, var(--sca-brand) 13%, transparent);
  border-radius: 6px; padding: .05rem .4rem;
}
.sca-cite .college { font-weight: 650; font-size: .88rem; }
.sca-cite .page { font-size: .74rem; color: var(--sca-muted); white-space: nowrap; }
.sca-cite .section { font-size: .8rem; font-weight: 600; margin: -.15rem 0 .35rem 0; overflow-wrap: anywhere; }
.sca-cite .file {
  font-family: var(--sca-mono); font-size: .74rem;
  color: var(--sca-muted); margin: .25rem 0 .4rem 0; overflow-wrap: anywhere;
}
.sca-cite .snippet {
  font-size: .84rem; line-height: 1.45; border-left: 3px solid var(--sca-card-border);
  padding-left: .55rem; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden;
}

/* ---------- progress timeline (inside st.status) ---------- */
.sca-timeline { list-style: none; margin: .2rem 0 .1rem 0; padding: 0; }
.sca-timeline > li { position: relative; padding: 0 0 .75rem 1.55rem; }
.sca-timeline > li:last-child { padding-bottom: .1rem; }
.sca-timeline > li::before {
  content: ""; position: absolute; left: .4rem; top: 1.05rem; bottom: -.1rem; width: 2px;
  background: var(--sca-card-border);
}
.sca-timeline > li:last-child::before { display: none; }
.sca-timeline .dot {
  position: absolute; left: 0; top: .2rem; width: .95rem; height: .95rem; border-radius: 50%;
  background: var(--c, var(--sca-brand));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--c, var(--sca-brand)) 22%, transparent);
}
.sca-timeline li.running .dot { animation: sca-pulse 1.1s ease-in-out infinite; }
.sca-timeline li.running .title { color: var(--sca-muted); }
@keyframes sca-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
.sca-timeline .row { display: flex; justify-content: space-between; gap: .75rem; }
.sca-timeline .title { font-weight: 600; font-size: .9rem; }
.sca-timeline .ms { font-size: .76rem; color: var(--sca-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.sca-timeline .detail { font-size: .82rem; color: var(--sca-muted); margin-top: .05rem; }
.sca-timeline .detail ol { margin: .25rem 0 0 1.1rem; padding: 0; list-style: decimal; }
.sca-timeline .detail ol li { font-size: .82rem; margin: 0 0 .1rem 0; padding: 0; }

/* Shown under the live flow strip while deep research runs */
.sca-wait-hint {
  margin-top: .45rem; padding: .4rem .65rem; border-radius: var(--sca-radius-sm); font-size: .8rem;
  color: var(--sca-muted); background: color-mix(in srgb, var(--sca-deep) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-deep) 22%, transparent);
}

/* Flow strip: one compact line of the steps that ran */
.sca-flow {
  display: flex; flex-wrap: wrap; align-items: center; gap: .3rem .25rem; margin: .75rem 0 .2rem 0;
  font-size: .78rem; line-height: 1.2;
}
.sca-flow .step {
  display: inline-flex; align-items: center; gap: .3rem; padding: .18rem .55rem; border-radius: var(--sca-radius-pill);
  background: color-mix(in srgb, var(--c, var(--sca-brand)) 11%, transparent);
  border: 1px solid color-mix(in srgb, var(--c, var(--sca-brand)) 30%, transparent);
  white-space: nowrap; font-weight: 550;
}
.sca-flow .step .t { color: var(--sca-muted); font-weight: 500; font-variant-numeric: tabular-nums; }
.sca-flow .arrow { color: var(--sca-muted); font-size: .8rem; padding: 0 .05rem; }
.sca-flow .total { color: var(--sca-muted); margin-left: .35rem; font-variant-numeric: tabular-nums; white-space: nowrap; }

/* ---------- callouts: hints and notes inside a page ---------- */
.sca-hint, .sca-callout {
  display: flex; gap: .55rem; align-items: flex-start; margin: .5rem 0 .6rem 0; padding: .6rem .8rem;
  border-radius: var(--sca-radius-sm); font-size: .86rem; line-height: 1.45;
  --c: var(--sca-brand);
  background: color-mix(in srgb, var(--c) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--c) 28%, transparent);
}
.sca-hint { --c: var(--sca-personal); }
.sca-callout.ok { --c: var(--sca-ok); }
.sca-callout.warn { --c: var(--sca-warn); }
.sca-callout.bad { --c: var(--sca-bad); }
.sca-callout .icon { flex: none; line-height: 1.45; }
.sca-callout .body { min-width: 0; }
.sca-callout .title { font-weight: 650; }

/* ---------- KPI tiles (dashboards) ---------- */
.sca-kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: var(--sca-space-3);
  margin: var(--sca-space-2) 0 var(--sca-space-4) 0;
}
.sca-kpi {
  position: relative; overflow: hidden; min-width: 0; height: 100%; box-sizing: border-box;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); border-radius: var(--sca-radius);
  padding: 1.05rem 1.1rem .95rem 1.1rem; box-shadow: var(--sca-shadow);
}
.sca-kpi::before {
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; background: var(--accent, var(--sca-brand));
}
.sca-kpi .label {
  font-size: var(--sca-fs-xs); text-transform: uppercase; letter-spacing: .07em; color: var(--sca-muted); font-weight: 650;
}
.sca-kpi .value {
  font-size: clamp(1.35rem, 1.1rem + .9vw, 1.85rem); font-weight: 700; line-height: 1.15; margin: .35rem 0 .25rem 0;
  font-variant-numeric: tabular-nums; overflow-wrap: anywhere;
}
.sca-kpi .sub { font-size: .82rem; line-height: 1.4; color: var(--sca-muted); }
.sca-kpi .value .unit { font-size: .95rem; font-weight: 600; color: var(--sca-muted); margin-left: .15rem; }
.sca-kpi .split { display: flex; gap: 1.1rem; flex-wrap: wrap; }
.sca-kpi .split span.tag { font-size: var(--sca-fs-xs); font-weight: 700; letter-spacing: .06em; display: block; margin-top: .1rem; }

/* ---------- section headings ---------- */
.sca-section { margin: 1.75rem 0 var(--sca-space-2) 0; }
.sca-section h3 { font-size: var(--sca-fs-lg); font-weight: 650; margin: 0; padding: 0; line-height: 1.3; }
.sca-section p { font-size: .85rem; line-height: 1.45; color: var(--sca-muted); margin: .15rem 0 0 0; }

/* ---------- empty states: one look for "nothing here yet" ---------- */
.sca-empty {
  text-align: center; padding: 2.4rem 1.4rem 1.7rem 1.4rem; border-radius: var(--sca-radius-lg);
  border: 1px dashed var(--sca-card-border-strong); margin: .4rem 0 .8rem 0;
  background:
    radial-gradient(circle at 20% 0%, color-mix(in srgb, var(--sca-brand) 10%, transparent), transparent 55%),
    radial-gradient(circle at 85% 100%, color-mix(in srgb, var(--sca-deep) 9%, transparent), transparent 55%);
}
.sca-empty .icon {
  width: 3.2rem; height: 3.2rem; margin: 0 auto .8rem auto; border-radius: var(--sca-radius);
  display: flex; align-items: center; justify-content: center; font-size: 1.6rem;
  background: color-mix(in srgb, var(--sca-brand) 14%, transparent);
}
.sca-empty h3 { margin: 0 0 .35rem 0; padding: 0; font-size: 1.2rem; font-weight: 650; }
.sca-empty p { color: var(--sca-muted); margin: 0 auto; max-width: 34rem; font-size: .93rem; line-height: 1.5; }
/* Compact: an empty section inside a page */
.sca-empty.compact {
  display: flex; align-items: center; gap: .7rem; text-align: left; padding: .65rem .85rem;
  border-radius: var(--sca-radius-sm); background: var(--sca-card-bg); margin: .35rem 0 .7rem 0;
}
.sca-empty.compact .icon { width: 2.1rem; height: 2.1rem; margin: 0; flex: none; border-radius: var(--sca-radius-sm); font-size: 1.05rem; }
.sca-empty.compact .body { min-width: 0; }
.sca-empty.compact .title { font-weight: 600; font-size: .9rem; line-height: 1.3; }
.sca-empty.compact .text { font-size: .82rem; line-height: 1.4; color: var(--sca-muted); }

/* ---------- sidebar ---------- */
/* Brand: gradient banner card (white text on a fixed dark gradient: readable in both themes) */
.sca-brand {
  display: flex; align-items: center; gap: .7rem; margin: -.6rem 0 .7rem 0; padding: .85rem .9rem;
  border-radius: var(--sca-radius-lg); color: white; position: relative; overflow: hidden;
  background: linear-gradient(135deg, var(--sca-brand) 0%, var(--sca-deep) 100%);
  box-shadow: 0 8px 22px color-mix(in srgb, var(--sca-brand) 30%, transparent);
}
.sca-brand::after {
  content: ""; position: absolute; width: 7rem; height: 7rem; right: -2.2rem; top: -2.6rem; border-radius: 50%;
  background: rgba(255, 255, 255, .12);
}
.sca-brand .logo {
  width: 2.4rem; height: 2.4rem; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center; font-size: 1.25rem;
  background: rgba(255, 255, 255, .18); border: 1px solid rgba(255, 255, 255, .28);
}
.sca-brand .name { font-weight: 700; font-size: 1rem; line-height: 1.2; color: white; }
.sca-brand .tag { font-size: .74rem; color: rgba(255, 255, 255, .9); margin-top: .1rem; }
.sca-side-label {
  font-size: .7rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  color: var(--sca-muted); margin: .9rem 0 .4rem 0;
}
.sca-side-foot {
  margin-top: 1.4rem; padding-top: .7rem; font-size: var(--sca-fs-xs); color: var(--sca-muted);
  border-top: 1px solid var(--sca-card-border); text-align: center;
}
.sca-colleges { display: flex; flex-wrap: wrap; gap: .35rem; }
.sca-steps { margin: 0; padding: 0; list-style: none; counter-reset: s; }
.sca-steps li { counter-increment: s; position: relative; padding: 0 0 .55rem 1.75rem; font-size: .83rem; line-height: 1.4; }
.sca-steps li::before {
  content: counter(s); position: absolute; left: 0; top: .05rem; width: 1.2rem; height: 1.2rem; border-radius: 50%;
  font-size: .7rem; font-weight: 700; display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--sca-brand) 15%, transparent); color: var(--sca-info-fg);
}
.sca-steps b.simple { color: var(--sca-ok-fg); }
.sca-steps b.deep { color: color-mix(in srgb, var(--sca-deep) 72%, currentColor); }
.sca-steps b.personal { color: var(--sca-warn-fg); }

/* Signed-in user card */
.sca-user {
  position: relative; overflow: hidden; padding: .85rem .9rem .8rem .9rem; margin: .2rem 0 .6rem 0;
  border-radius: var(--sca-radius-lg); background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  box-shadow: var(--sca-shadow);
}
.sca-user::before {
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3rem;
  background: linear-gradient(120deg, color-mix(in srgb, var(--sca-brand) 22%, transparent),
                                      color-mix(in srgb, var(--sca-deep) 16%, transparent));
}
.sca-user .top { position: relative; display: flex; align-items: center; gap: .65rem; }
.sca-user .avatar {
  width: 2.6rem; height: 2.6rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: .92rem; color: white; letter-spacing: .02em;
  background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--sca-brand) 18%, transparent);
}
/* Darker green so the white initials keep AA contrast. */
.sca-user .avatar.admin { background: linear-gradient(135deg, color-mix(in srgb, var(--sca-simple) 70%, black), var(--sca-brand)); }
.sca-user .who { min-width: 0; }
.sca-user .name { font-weight: 650; font-size: .94rem; line-height: 1.25; overflow-wrap: anywhere; }
.sca-user .email { font-size: .75rem; color: var(--sca-muted); overflow-wrap: anywhere; }
.sca-user .badges { margin-top: .65rem; display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; }
.sca-user .meta {
  font-size: .74rem; color: var(--sca-muted); margin-top: .45rem; font-family: var(--sca-mono); overflow-wrap: anywhere;
}

/* Status rows (sidebar card) */
.sca-stats { display: flex; flex-direction: column; gap: .35rem; margin-top: .75rem; }
.sca-stats .row {
  --c: var(--sca-ok);
  display: grid; grid-template-columns: 1.3rem 1fr auto; align-items: center; column-gap: .45rem;
  padding: .38rem .55rem; border-radius: var(--sca-radius-sm); font-size: .8rem;
  background: color-mix(in srgb, var(--c) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--c) 24%, transparent);
}
.sca-stats .row.warn { --c: var(--sca-warn); }
.sca-stats .row.bad { --c: var(--sca-bad); }
.sca-stats .row.sem { --c: var(--sca-brand); }
.sca-stats .ic { font-size: .9rem; line-height: 1; text-align: center; }
.sca-stats .k { color: var(--sca-muted); }
.sca-stats .v { font-weight: 650; font-variant-numeric: tabular-nums; text-align: right; color: var(--sca-ok-fg); }
.sca-stats .row.warn .v { color: var(--sca-warn-fg); }
.sca-stats .row.bad .v { color: var(--sca-bad-fg); }
.sca-stats .row.sem .v { color: var(--sca-info-fg); }
.sca-stats .bar {
  grid-column: 1 / -1; position: relative; height: 5px; margin-top: .35rem; border-radius: var(--sca-radius-pill);
  background: color-mix(in srgb, currentColor 10%, transparent);
}
.sca-stats .bar span { position: absolute; inset: 0 auto 0 0; border-radius: inherit; background: var(--c); }
.sca-stats .bar i { position: absolute; left: 75%; top: -3px; bottom: -3px; width: 2px; background: currentColor; opacity: .45; }
.sca-stats.compact { margin-top: .65rem; gap: .3rem; }
.sca-stats.compact .row { padding: .32rem .55rem; }
.sca-stats.compact .row.sem .bar { margin-top: .3rem; }

/* Older student profile card + status chips (kept for pages that still use them) */
.sca-profile {
  position: relative; overflow: hidden; padding: .8rem .85rem .7rem .85rem; margin: .45rem 0 .3rem 0;
  border-radius: var(--sca-radius-lg); background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  box-shadow: var(--sca-shadow);
}
.sca-profile::before {
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3.1rem;
  background: linear-gradient(120deg, color-mix(in srgb, var(--sca-brand) 22%, transparent),
                                      color-mix(in srgb, var(--sca-deep) 16%, transparent));
}
.sca-profile .top { position: relative; display: flex; align-items: center; gap: .6rem; }
.sca-profile .avatar {
  width: 2.5rem; height: 2.5rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: .95rem; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}
.sca-profile .name { font-weight: 650; font-size: .92rem; line-height: 1.2; }
.sca-profile .meta { font-size: .74rem; color: var(--sca-muted); font-family: var(--sca-mono); }
.sca-status { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .55rem; }
.sca-status .s {
  display: inline-flex; align-items: center; gap: .3rem; padding: .12rem .5rem; border-radius: var(--sca-radius-pill);
  font-size: var(--sca-fs-xs); font-weight: 600; white-space: nowrap;
  background: color-mix(in srgb, var(--c, currentColor) 13%, transparent);
  border: 1px solid color-mix(in srgb, var(--c, currentColor) 32%, transparent);
}
.sca-status .s .k { color: var(--sca-muted); font-weight: 500; }
.sca-status .s.ok { --c: var(--sca-ok); }
.sca-status .s.warn { --c: var(--sca-warn); }
.sca-status .s.bad { --c: var(--sca-bad); }

/* Guest card */
.sca-guest {
  display: flex; gap: .65rem; align-items: center; padding: .7rem .8rem; margin: 0 0 .55rem 0; border-radius: var(--sca-radius);
  border: 1px dashed color-mix(in srgb, var(--sca-brand) 40%, transparent);
  background: color-mix(in srgb, var(--sca-brand) 6%, transparent);
}
.sca-guest .lock {
  width: 2.1rem; height: 2.1rem; flex: none; border-radius: var(--sca-radius-sm); display: flex; align-items: center;
  justify-content: center; background: color-mix(in srgb, var(--sca-brand) 14%, transparent); font-size: 1rem;
}
.sca-guest.admin {
  border-style: solid; border-color: color-mix(in srgb, var(--sca-simple) 40%, transparent);
  background: color-mix(in srgb, var(--sca-simple) 8%, transparent); margin-top: .5rem;
}
.sca-guest.admin .lock { background: color-mix(in srgb, var(--sca-simple) 16%, transparent); }
.sca-guest .title { font-weight: 650; font-size: .84rem; line-height: 1.25; }
.sca-guest .sub { font-size: .75rem; color: var(--sca-muted); line-height: 1.35; margin-top: .1rem; }

/* Remaining semesters (sidebar dropdown) */
.sca-sems { display: flex; flex-direction: column; gap: .45rem; }
.sca-sems .sem-item {
  border: 1px solid var(--sca-card-border); border-radius: var(--sca-radius-sm); padding: .45rem .6rem; background: var(--sca-card-bg);
}
.sca-sems .sem-item.current {
  border-color: color-mix(in srgb, var(--sca-brand) 45%, transparent);
  background: color-mix(in srgb, var(--sca-brand) 8%, transparent);
}
.sca-sems .head { display: flex; align-items: center; gap: .45rem; }
.sca-sems .num {
  width: 1.35rem; height: 1.35rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-size: .7rem; font-weight: 700; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}
.sca-sems .title { font-weight: 650; font-size: .82rem; }
.sca-sems .now {
  margin-left: auto; font-size: .66rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
  color: var(--sca-info-fg); background: color-mix(in srgb, var(--sca-brand) 14%, transparent);
  padding: .05rem .4rem; border-radius: var(--sca-radius-pill);
}
.sca-sems ul { margin: .3rem 0 0 1.85rem; padding: 0; }
.sca-sems li { font-size: .76rem; line-height: 1.4; margin: 0; padding: 0; }
.sca-sems .none { font-size: .75rem; color: var(--sca-muted); margin: .25rem 0 0 1.85rem; font-style: italic; }
.sca-sems .note { font-size: var(--sca-fs-xs); color: var(--sca-muted); margin: .25rem 0 0 1.85rem; }

/* Role badge */
.sca-role {
  display: inline-flex; align-items: center; gap: .3rem; padding: .14rem .55rem; border-radius: var(--sca-radius-pill);
  font-size: .7rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
  color: var(--sca-info-fg);
  background: color-mix(in srgb, var(--sca-brand) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-brand) 35%, transparent);
}
.sca-role.admin {
  color: var(--sca-ok-fg);
  background: color-mix(in srgb, var(--sca-simple) 14%, transparent);
  border-color: color-mix(in srgb, var(--sca-simple) 38%, transparent);
}

/* ---------- sign-in card ---------- */
.st-key-login_card {
  max-width: 440px; margin: 6vh auto 2rem auto; padding: 1.75rem 1.6rem 1.35rem 1.6rem; border-radius: 22px;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); box-shadow: var(--sca-shadow-lg);
  box-sizing: border-box;
}
.st-key-login_card [data-testid="stForm"] { border: none; padding: 0; }
.sca-login-head { text-align: center; margin-bottom: .5rem; }
.sca-login-head .logo {
  width: 3.3rem; height: 3.3rem; margin: 0 auto .75rem auto; border-radius: 16px; font-size: 1.6rem;
  display: flex; align-items: center; justify-content: center; color: white;
  background: linear-gradient(135deg, var(--sca-brand) 0%, var(--sca-deep) 100%);
  box-shadow: 0 8px 22px color-mix(in srgb, var(--sca-brand) 35%, transparent);
}
.sca-login-head h1 { font-size: 1.45rem; font-weight: 700; margin: 0; padding: 0; line-height: 1.25; }
.sca-login-head p { color: var(--sca-muted); font-size: .9rem; margin: .3rem 0 0 0; }
.sca-login-note { text-align: center; font-size: .78rem; color: var(--sca-muted); margin-top: .75rem; line-height: 1.45; }
.sca-login-note code { font-size: .76rem; }
.st-key-login_views { margin: .35rem 0 .3rem 0; }
.st-key-login_views button p { font-size: .82rem; white-space: nowrap; }
.sca-login-sub { font-weight: 650; font-size: .98rem; margin: .45rem 0 .15rem 0; }
/* Step header of the 6-digit code step */
.sca-login-step { display: flex; align-items: center; gap: .55rem; margin: .45rem 0 .5rem 0; }
.sca-login-step .n {
  flex: none; display: inline-flex; align-items: center; justify-content: center; height: 1.5rem; padding: 0 .55rem;
  border-radius: var(--sca-radius-pill); font-size: var(--sca-fs-xs); font-weight: 700; letter-spacing: .04em;
  color: var(--sca-info-fg); background: color-mix(in srgb, var(--sca-brand) 14%, transparent);
}
.sca-login-step .t { font-weight: 650; font-size: .98rem; }
/* Demo code box (no email server): amber, dashed, easy to select */
.sca-demo-code {
  margin: .5rem 0 .9rem 0; padding: .8rem 1rem .75rem 1rem; border-radius: var(--sca-radius); text-align: center;
  border: 1.5px dashed color-mix(in srgb, var(--sca-warn) 65%, transparent);
  background: color-mix(in srgb, var(--sca-warn) 10%, transparent);
}
.sca-demo-code .lbl {
  font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--sca-warn-fg);
}
.sca-demo-code .code {
  font-family: var(--sca-mono); font-size: 1.75rem; font-weight: 700; letter-spacing: .38em; padding-left: .38em;
  margin: .2rem 0; font-variant-numeric: tabular-nums; user-select: all;
}
.sca-demo-code .hint { font-size: .74rem; color: var(--sca-muted); }
.st-key-temp_password_box {
  padding: .85rem 1rem; border-radius: var(--sca-radius);
  border: 1px solid color-mix(in srgb, var(--sca-warn) 55%, transparent);
  background: color-mix(in srgb, var(--sca-warn) 10%, transparent);
}

/* ---------- student dashboard ---------- */
.sca-student-head {
  display: flex; align-items: center; gap: 1rem; padding: 1.1rem 1.2rem; margin: 0 0 1rem 0; border-radius: var(--sca-radius-lg);
  background: linear-gradient(120deg, color-mix(in srgb, var(--sca-brand) 14%, transparent),
                                      color-mix(in srgb, var(--sca-deep) 10%, transparent));
  border: 1px solid var(--sca-card-border);
}
.sca-student-head .avatar {
  width: 3.4rem; height: 3.4rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 1.15rem; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}
.sca-student-head .body { min-width: 0; }
.sca-student-head .eyebrow {
  font-size: var(--sca-fs-xs); font-weight: 650; letter-spacing: .08em; text-transform: uppercase; color: var(--sca-info-fg);
}
.sca-student-head h2 { font-size: 1.55rem; font-weight: 700; margin: .1rem 0 .4rem 0; padding: 0; line-height: 1.2; }
.sca-student-head .facts { display: flex; flex-wrap: wrap; gap: .35rem; }
.sca-student-head .fact {
  font-size: .78rem; padding: .15rem .55rem; border-radius: var(--sca-radius-pill);
  background: var(--sca-card-bg-strong); border: 1px solid var(--sca-card-border);
}
.sca-student-head .fact .k { color: var(--sca-muted); }

/* Card lists: books, exams, hostel */
.sca-list { display: flex; flex-direction: column; gap: .45rem; margin: .5rem 0 .3rem 0; }
.sca-list .item {
  display: flex; align-items: center; gap: .7rem; padding: .6rem .75rem; border-radius: 12px;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
}
.sca-list .item.bad {
  border-color: color-mix(in srgb, var(--sca-bad) 45%, transparent);
  background: color-mix(in srgb, var(--sca-bad) 7%, transparent);
}
.sca-list .cal {
  width: 2.6rem; height: 2.6rem; flex: none; border-radius: var(--sca-radius-sm); display: flex; flex-direction: column;
  align-items: center; justify-content: center; line-height: 1; font-size: 1.1rem;
  background: color-mix(in srgb, var(--sca-brand) 12%, transparent);
}
.sca-list .cal .d { font-weight: 700; font-size: 1rem; }
.sca-list .cal .m { font-size: .62rem; text-transform: uppercase; letter-spacing: .06em; color: var(--sca-muted); margin-top: .1rem; }
.sca-list .main { flex: 1; min-width: 0; }
.sca-list .title { font-weight: 600; font-size: .9rem; line-height: 1.3; overflow-wrap: anywhere; }
.sca-list .sub { font-size: .78rem; color: var(--sca-muted); overflow-wrap: anywhere; }
.sca-list .side { text-align: right; flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: .2rem; }
.sca-list .date { font-size: .78rem; font-variant-numeric: tabular-nums; }
.sca-list .tag {
  display: inline-block; font-size: .7rem; font-weight: 650; padding: .1rem .5rem; border-radius: var(--sca-radius-pill);
  white-space: nowrap; background: color-mix(in srgb, var(--sca-brand) 12%, transparent); color: var(--sca-info-fg);
}
.sca-list .tag.bad { background: color-mix(in srgb, var(--sca-bad) 16%, transparent); color: var(--sca-bad-fg); }
.sca-list .tag.warn { background: color-mix(in srgb, var(--sca-warn) 18%, transparent); color: var(--sca-warn-fg); }
.sca-list .tag.ok { background: color-mix(in srgb, var(--sca-ok) 15%, transparent); color: var(--sca-ok-fg); }

/* ---------- notices ---------- */
.sca-notices { display: flex; flex-direction: column; gap: .6rem; margin: .4rem 0 1rem 0; }
/* A card with its action button right under it (container gap 8px). Streamlit pulls markdown up by 1rem
   (margin-bottom: -16px), so the card keeps a 1rem bottom margin or the button would overlap it. */
.sca-notices.tight { margin: .4rem 0 1rem 0; }
div.sca-req { margin-bottom: 1rem; }  /* beats the .15rem of support_requests.REQUESTS_CSS, injected later */
.sca-notice {
  --c: var(--sca-brand);
  border: 1px solid var(--sca-card-border); border-left: 4px solid var(--c); border-radius: var(--sca-radius);
  background: var(--sca-card-bg); padding: .85rem 1rem; box-shadow: var(--sca-shadow-sm); min-width: 0;
}
.sca-notice.cat-exams { --c: var(--sca-deep); }
.sca-notice.cat-fees { --c: var(--sca-warn); }
.sca-notice.cat-hostel { --c: var(--sca-ok); }
.sca-notice.cat-library { --c: var(--sca-bad); }
.sca-notice .head { display: flex; gap: .5rem; align-items: baseline; }
.sca-notice .head > span:first-child { flex: none; }
.sca-notice .title { font-weight: 650; font-size: 1rem; line-height: 1.35; overflow-wrap: anywhere; min-width: 0; }
.sca-notice .meta { font-size: .78rem; color: var(--sca-muted); margin-top: .15rem; overflow-wrap: anywhere; }
.sca-notice .body { font-size: .9rem; line-height: 1.5; margin-top: .45rem; white-space: pre-wrap; overflow-wrap: anywhere; }

/* ---------- facts (label / value rows) ---------- */
.sca-facts, .sca-req-facts { display: flex; flex-direction: column; gap: .35rem; margin: .4rem 0 .3rem 0; }
.sca-facts .fact, .sca-req-facts .fact {
  display: flex; justify-content: space-between; gap: .75rem; font-size: .86rem; padding: .4rem .65rem;
  border: 1px solid var(--sca-card-border); border-radius: var(--sca-radius-sm); background: var(--sca-card-bg);
}
.sca-facts .k, .sca-req-facts .k { color: var(--sca-muted); white-space: nowrap; }
.sca-facts .v, .sca-req-facts .v { font-weight: 600; text-align: right; overflow-wrap: anywhere; min-width: 0; }

/* ---------- requests (thread width on phones; the cards come from support_requests.REQUESTS_CSS) ---------- */
.sca-thread .sca-msg { line-height: 1.45; }

/* ---------- admin: record change log ---------- */
.rec-audit { display: flex; flex-direction: column; gap: .45rem; margin: .4rem 0 .6rem 0; }
.rec-audit .row {
  border: 1px solid var(--sca-card-border); border-left: 4px solid var(--sca-brand); border-radius: var(--sca-radius-sm);
  background: var(--sca-card-bg); padding: .5rem .8rem;
}
.rec-audit .what { font-size: .9rem; font-weight: 600; overflow-wrap: anywhere; }
.rec-audit .meta { font-size: .76rem; color: var(--sca-muted); margin-top: .1rem; overflow-wrap: anywhere; }

/* ---------- Pay fees ---------- */
.pay-receipt {
  border: 1px solid var(--sca-card-border); border-left: 4px solid var(--sca-ok); border-radius: var(--sca-radius);
  background: var(--sca-card-bg); padding: .85rem 1rem; margin: .4rem 0 .6rem 0; box-shadow: var(--sca-shadow-sm);
}
.pay-receipt .title { font-weight: 650; font-size: 1rem; margin-bottom: .2rem; }
.pay-receipt .line { font-size: .9rem; margin-top: .2rem; font-variant-numeric: tabular-nums; }
.pay-receipt .line .k { color: var(--sca-muted); display: inline-block; min-width: 7.5rem; }
.pay-row { font-size: .95rem; font-weight: 650; font-variant-numeric: tabular-nums; }
.pay-row .sub { font-size: .78rem; font-weight: 400; color: var(--sca-muted); }
/* Old name of the demo-payment note (now a callout) */
.pay-demo {
  border: 1px dashed var(--sca-card-border-strong); border-radius: var(--sca-radius-sm);
  padding: .6rem .9rem; font-size: .88rem; margin: .4rem 0 .8rem 0;
}

/* Example-question buttons look like soft chips */
.st-key-examples button { border-radius: var(--sca-radius-pill) !important; font-size: .86rem; text-align: left; }

/* ---------- phones ---------- */
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; padding-bottom: 4rem; }
  .sca-hero { margin-bottom: var(--sca-space-4); }
  .sca-hero p { font-size: .92rem; }
  .sca-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .sca-flow { font-size: .74rem; }
  .sca-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .55rem; }
  .sca-kpi { padding: .85rem .85rem .75rem .85rem; }
  .sca-kpi .value { font-size: 1.3rem; }
  .sca-kpi .sub { font-size: .78rem; }
  .sca-student-head { padding: .85rem .9rem; gap: .75rem; align-items: flex-start; }
  .sca-student-head .avatar { width: 2.8rem; height: 2.8rem; font-size: 1rem; }
  .sca-student-head h2 { font-size: 1.25rem; }
  .sca-list .item { flex-wrap: wrap; }
  .sca-list .main { flex: 1 1 11rem; }
  .sca-list .side { flex-direction: row; align-items: center; gap: .45rem; }
  .sca-empty { padding: 1.6rem 1rem 1.2rem 1rem; }
  .sca-notice { padding: .75rem .85rem; }
  .sca-thread .sca-msg { max-width: 94%; }
  .st-key-login_card { margin-top: 2vh; padding: 1.25rem 1rem 1rem 1rem; border-radius: var(--sca-radius-lg); }
  .sca-demo-code .code { font-size: 1.45rem; letter-spacing: .26em; padding-left: .26em; }
  .pay-receipt .line .k { min-width: 6.5rem; }
}
@media (max-width: 420px) {
  .sca-kpi { padding: .75rem .75rem .65rem .75rem; }
  .sca-kpi .value { font-size: 1.15rem; }
  .st-key-login_views button p { font-size: .74rem; }
}
@media (max-width: 340px) {
  .sca-kpis { grid-template-columns: minmax(0, 1fr); }
}

/* Less motion for people who ask for it */
@media (prefers-reduced-motion: reduce) {
  .sca-timeline li.running .dot { animation: none; }
  .stApp button { transition: none; }
}
"""

_CSS = f"<style>\n{_TOKENS}\n{_RULES}\n</style>\n"


def inject_css(max_width: str | None = None) -> None:
    """Inject the shared CSS once per page run; optionally cap the main column width."""
    extra = ""
    if max_width:
        extra = (
            "<style>[data-testid=\"stMainBlockContainer\"], [data-testid=\"stBottomBlockContainer\"] "
            f"{{ max-width: {max_width}; }}</style>"
        )
    st.html(_CSS + extra)
