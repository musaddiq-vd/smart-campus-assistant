"""Shared colors and CSS for every page.

Custom HTML blocks tint themselves from ``currentColor`` with ``color-mix`` so the same
CSS reads well on both Streamlit's light and dark themes.
"""

from __future__ import annotations

import streamlit as st

# One palette for the whole app (validated for colorblind separation in light and dark).
SIMPLE_COLOR = "#0E9F6E"  # simple route: green
DEEP_COLOR = "#7C3AED"  # deep route: violet
PERSONAL_COLOR = "#E5A50A"  # personal route: amber (separable from green, violet and orange under CVD)
ROUTER_COLOR = "#5B5BD6"  # "router" eval mode: indigo (also the brand color)
BASELINE_COLOR = "#D95926"  # "always_deep" eval mode: orange

ROUTE_COLORS = {"simple": SIMPLE_COLOR, "deep": DEEP_COLOR, "personal": PERSONAL_COLOR}
ROUTE_ICONS = {"simple": "⚡", "deep": "🔬", "personal": "👤"}
MODE_COLORS = {"router": ROUTER_COLOR, "always_deep": BASELINE_COLOR}
MODE_LABELS = {"router": "Router (ours)", "always_deep": "Always deep"}

_CSS = f"""
<style>
:root {{
  --sca-brand: {ROUTER_COLOR};
  --sca-simple: {SIMPLE_COLOR};
  --sca-deep: {DEEP_COLOR};
  --sca-personal: {PERSONAL_COLOR};
  --sca-baseline: {BASELINE_COLOR};
  --sca-radius: 14px;
  --sca-card-bg: color-mix(in srgb, currentColor 3.5%, transparent);
  --sca-card-border: color-mix(in srgb, currentColor 12%, transparent);
  --sca-muted: color-mix(in srgb, currentColor 62%, transparent);
  --sca-shadow: 0 1px 2px rgba(15, 23, 42, .06), 0 4px 14px rgba(15, 23, 42, .05);
}}

/* Page frame */
[data-testid="stMainBlockContainer"] {{ padding-bottom: 6rem; }}
h1, h2, h3 {{ letter-spacing: -0.02em; }}

/* Hero header */
.sca-hero {{ margin: 0 0 1.1rem 0; }}
.sca-hero .eyebrow {{
  font-size: .74rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--sca-brand);
}}
.sca-hero h1 {{ font-size: 2rem; font-weight: 700; margin: .15rem 0 .35rem 0; padding: 0; line-height: 1.2; }}
.sca-hero p {{ color: var(--sca-muted); margin: 0; font-size: .98rem; max-width: 46rem; }}
.sca-hero .chips {{ margin-top: .7rem; display: flex; flex-wrap: wrap; gap: .4rem; }}

/* Small pill chips */
.sca-chip {{
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .18rem .62rem; border-radius: 999px; font-size: .78rem; font-weight: 500;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); white-space: nowrap;
}}
.sca-chip .dot {{ width: .5rem; height: .5rem; border-radius: 50%; background: currentColor; }}
.sca-chip.live .dot {{ background: var(--sca-simple); }}
.sca-chip.demo .dot {{ background: var(--sca-baseline); }}

/* Route badge */
.sca-route {{ display: flex; align-items: center; flex-wrap: wrap; gap: .55rem; margin: .1rem 0 .7rem 0; }}
.sca-badge {{
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .2rem .62rem; border-radius: 8px;
  font-size: .72rem; font-weight: 700; letter-spacing: .07em;
}}
.sca-badge.simple {{
  color: color-mix(in srgb, var(--sca-simple) 78%, currentColor);
  background: color-mix(in srgb, var(--sca-simple) 15%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-simple) 35%, transparent);
}}
.sca-badge.deep {{
  color: color-mix(in srgb, var(--sca-deep) 72%, currentColor);
  background: color-mix(in srgb, var(--sca-deep) 15%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-deep) 38%, transparent);
}}
.sca-badge.personal {{
  color: color-mix(in srgb, var(--sca-personal) 55%, currentColor);
  background: color-mix(in srgb, var(--sca-personal) 17%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-personal) 45%, transparent);
}}
.sca-badge.forced {{
  color: var(--sca-muted); background: var(--sca-card-bg); border: 1px dashed var(--sca-card-border);
  font-weight: 600; letter-spacing: .04em;
}}
.sca-route .reason {{ color: var(--sca-muted); font-size: .86rem; }}

/* Metric tiles under each answer */
.sca-metrics {{
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .55rem; margin: .9rem 0 .3rem 0;
}}
.sca-tile {{
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  border-radius: 12px; padding: .55rem .75rem;
}}
.sca-tile .label {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; color: var(--sca-muted); }}
.sca-tile .value {{
  font-size: clamp(.9rem, 1.6vw, 1.12rem); font-weight: 650; font-variant-numeric: tabular-nums;
  margin-top: .1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.sca-tile .sub {{ font-size: .74rem; color: var(--sca-muted); font-variant-numeric: tabular-nums; }}

/* Citation cards */
.sca-cites {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: .6rem; }}
.sca-cite {{
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  border-radius: 12px; padding: .7rem .85rem; min-width: 0;
}}
.sca-cite .head {{ display: flex; align-items: center; justify-content: space-between; gap: .5rem; }}
.sca-cite .num {{
  font-size: .72rem; font-weight: 700; color: var(--sca-brand);
  background: color-mix(in srgb, var(--sca-brand) 13%, transparent);
  border-radius: 6px; padding: .05rem .4rem;
}}
.sca-cite .college {{ font-weight: 650; font-size: .88rem; }}
.sca-cite .page {{ font-size: .74rem; color: var(--sca-muted); white-space: nowrap; }}
.sca-cite .file {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .74rem;
  color: var(--sca-muted); margin: .25rem 0 .4rem 0; overflow-wrap: anywhere;
}}
.sca-cite .snippet {{
  font-size: .84rem; line-height: 1.45; border-left: 3px solid var(--sca-card-border);
  padding-left: .55rem; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden;
}}

/* Progress timeline (inside st.status) */
.sca-timeline {{ list-style: none; margin: .2rem 0 .1rem 0; padding: 0; }}
.sca-timeline > li {{ position: relative; padding: 0 0 .75rem 1.55rem; }}
.sca-timeline > li:last-child {{ padding-bottom: .1rem; }}
.sca-timeline > li::before {{
  content: ""; position: absolute; left: .4rem; top: 1.05rem; bottom: -.1rem; width: 2px;
  background: var(--sca-card-border);
}}
.sca-timeline > li:last-child::before {{ display: none; }}
.sca-timeline .dot {{
  position: absolute; left: 0; top: .2rem; width: .95rem; height: .95rem; border-radius: 50%;
  background: var(--c, var(--sca-brand)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--c, var(--sca-brand)) 22%, transparent);
}}
.sca-timeline li.running .dot {{ animation: sca-pulse 1.1s ease-in-out infinite; }}
.sca-timeline li.running .title {{ color: var(--sca-muted); }}
@keyframes sca-pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .35; }} }}
.sca-timeline .row {{ display: flex; justify-content: space-between; gap: .75rem; }}
.sca-timeline .title {{ font-weight: 600; font-size: .9rem; }}
.sca-timeline .ms {{ font-size: .76rem; color: var(--sca-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }}
.sca-timeline .detail {{ font-size: .82rem; color: var(--sca-muted); margin-top: .05rem; }}
.sca-timeline .detail ol {{ margin: .25rem 0 0 1.1rem; padding: 0; list-style: decimal; }}
.sca-timeline .detail ol li {{ font-size: .82rem; margin: 0 0 .1rem 0; padding: 0; }}

/* Shown under the live flow strip while deep research runs */
.sca-wait-hint {{
  margin-top: .45rem; padding: .4rem .65rem; border-radius: 10px; font-size: .8rem;
  color: var(--sca-muted); background: color-mix(in srgb, var(--sca-deep) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-deep) 22%, transparent);
}}

/* Flow strip: one compact line of the steps that ran */
.sca-flow {{
  display: flex; flex-wrap: wrap; align-items: center; gap: .3rem .25rem; margin: .75rem 0 .2rem 0;
  font-size: .78rem; line-height: 1.2;
}}
.sca-flow .step {{
  display: inline-flex; align-items: center; gap: .3rem; padding: .18rem .55rem; border-radius: 999px;
  background: color-mix(in srgb, var(--c, var(--sca-brand)) 11%, transparent);
  border: 1px solid color-mix(in srgb, var(--c, var(--sca-brand)) 30%, transparent);
  white-space: nowrap; font-weight: 550;
}}
.sca-flow .step .t {{ color: var(--sca-muted); font-weight: 500; font-variant-numeric: tabular-nums; }}
.sca-flow .arrow {{ color: var(--sca-muted); font-size: .8rem; padding: 0 .05rem; }}
.sca-flow .total {{ color: var(--sca-muted); margin-left: .35rem; font-variant-numeric: tabular-nums; white-space: nowrap; }}

/* Login hint for guests asking a personal question */
.sca-hint {{
  display: flex; gap: .5rem; align-items: flex-start; margin: .5rem 0 .2rem 0; padding: .5rem .75rem;
  border-radius: 10px; font-size: .84rem; line-height: 1.4;
  background: color-mix(in srgb, var(--sca-personal) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-personal) 35%, transparent);
}}

/* Student profile card + status chips (sidebar) */
.sca-profile {{
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); border-radius: 12px;
  padding: .65rem .75rem; margin: .45rem 0 .3rem 0;
}}
.sca-profile .top {{ display: flex; align-items: center; gap: .6rem; }}
.sca-profile .avatar {{
  width: 2.1rem; height: 2.1rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: .85rem; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}}
.sca-profile .name {{ font-weight: 650; font-size: .92rem; line-height: 1.2; }}
.sca-profile .meta {{ font-size: .74rem; color: var(--sca-muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
.sca-status {{ display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .55rem; }}
.sca-status .s {{
  display: inline-flex; align-items: center; gap: .3rem; padding: .12rem .5rem; border-radius: 999px;
  font-size: .72rem; font-weight: 600; white-space: nowrap;
  background: color-mix(in srgb, var(--c, currentColor) 13%, transparent);
  border: 1px solid color-mix(in srgb, var(--c, currentColor) 32%, transparent);
}}
.sca-status .s .k {{ color: var(--sca-muted); font-weight: 500; }}
.sca-status .s.ok {{ --c: var(--sca-simple); }}
.sca-status .s.warn {{ --c: var(--sca-personal); }}
.sca-status .s.bad {{ --c: var(--sca-baseline); }}

/* Profile card v2: gradient header strip + status rows */
.sca-profile {{
  position: relative; overflow: hidden; padding: .8rem .85rem .7rem .85rem; border-radius: 16px;
  box-shadow: var(--sca-shadow);
}}
.sca-profile::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3.1rem;
  background: linear-gradient(120deg, color-mix(in srgb, var(--sca-brand) 22%, transparent), color-mix(in srgb, var(--sca-deep) 16%, transparent));
}}
.sca-profile .top {{ position: relative; }}
.sca-profile .avatar {{ width: 2.5rem; height: 2.5rem; font-size: .95rem; box-shadow: 0 0 0 3px var(--sca-side-bg, white); }}
.sca-stats {{ display: flex; flex-direction: column; gap: .35rem; margin-top: .75rem; }}
.sca-stats .row {{
  --c: var(--sca-simple);
  display: grid; grid-template-columns: 1.3rem 1fr auto; align-items: center; column-gap: .45rem;
  padding: .38rem .55rem; border-radius: 10px; font-size: .8rem;
  background: color-mix(in srgb, var(--c) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--c) 24%, transparent);
}}
.sca-stats .row.warn {{ --c: var(--sca-personal); }}
.sca-stats .row.bad {{ --c: var(--sca-baseline); }}
.sca-stats .ic {{ font-size: .9rem; line-height: 1; }}
.sca-stats .k {{ color: var(--sca-muted); }}
.sca-stats .v {{ font-weight: 650; font-variant-numeric: tabular-nums; color: color-mix(in srgb, var(--c) 70%, currentColor); }}
.sca-stats .bar {{
  grid-column: 1 / -1; position: relative; height: 5px; margin-top: .35rem; border-radius: 999px;
  background: color-mix(in srgb, currentColor 10%, transparent);
}}
.sca-stats .bar span {{ position: absolute; inset: 0 auto 0 0; border-radius: inherit; background: var(--c); }}
.sca-stats .bar i {{ position: absolute; left: 75%; top: -3px; bottom: -3px; width: 2px; background: currentColor; opacity: .45; }}

/* Guest card */
.sca-guest {{
  display: flex; gap: .65rem; align-items: center; padding: .7rem .8rem; margin: 0 0 .55rem 0; border-radius: 14px;
  border: 1px dashed color-mix(in srgb, var(--sca-brand) 40%, transparent);
  background: color-mix(in srgb, var(--sca-brand) 6%, transparent);
}}
.sca-guest .lock {{
  width: 2.1rem; height: 2.1rem; flex: none; border-radius: 10px; display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--sca-brand) 14%, transparent); font-size: 1rem;
}}
.sca-guest.admin {{
  border-style: solid; border-color: color-mix(in srgb, var(--sca-simple) 40%, transparent);
  background: color-mix(in srgb, var(--sca-simple) 8%, transparent); margin-top: .5rem;
}}
.sca-guest.admin .lock {{ background: color-mix(in srgb, var(--sca-simple) 16%, transparent); }}
.sca-guest .title {{ font-weight: 650; font-size: .84rem; line-height: 1.25; }}
.sca-guest .sub {{ font-size: .75rem; color: var(--sca-muted); line-height: 1.35; margin-top: .1rem; }}

.sca-side-foot {{
  margin-top: 1.4rem; padding-top: .7rem; font-size: .72rem; color: var(--sca-muted);
  border-top: 1px solid var(--sca-card-border); text-align: center;
}}

/* KPI cards (dashboard) */
.sca-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .9rem; margin: .4rem 0 .6rem 0; }}
.sca-kpi {{
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); border-radius: var(--sca-radius);
  padding: 1rem 1.1rem; box-shadow: var(--sca-shadow); height: 100%;
  border-top: 3px solid var(--accent, var(--sca-brand));
}}
.sca-kpi .label {{ font-size: .74rem; text-transform: uppercase; letter-spacing: .07em; color: var(--sca-muted); font-weight: 600; }}
.sca-kpi .value {{ font-size: 1.9rem; font-weight: 700; line-height: 1.15; margin: .3rem 0 .2rem 0; font-variant-numeric: tabular-nums; }}
.sca-kpi .sub {{ font-size: .82rem; color: var(--sca-muted); }}
.sca-kpi .value .unit {{ font-size: .95rem; font-weight: 600; color: var(--sca-muted); margin-left: .15rem; }}
.sca-kpi .split {{ display: flex; gap: 1.1rem; flex-wrap: wrap; }}
.sca-kpi .split span.tag {{ font-size: .72rem; font-weight: 700; letter-spacing: .06em; display: block; margin-top: .1rem; }}

/* Section headings (dashboard) */
.sca-section {{ margin: 1.6rem 0 .2rem 0; }}
.sca-section h3 {{ font-size: 1.08rem; font-weight: 650; margin: 0; padding: 0; }}
.sca-section p {{ font-size: .85rem; color: var(--sca-muted); margin: .1rem 0 0 0; }}

/* Empty states */
.sca-empty {{
  text-align: center; padding: 2.4rem 1.4rem 1.6rem 1.4rem; border-radius: 18px;
  border: 1px dashed var(--sca-card-border);
  background:
    radial-gradient(circle at 20% 0%, color-mix(in srgb, var(--sca-brand) 10%, transparent), transparent 55%),
    radial-gradient(circle at 85% 100%, color-mix(in srgb, var(--sca-deep) 9%, transparent), transparent 55%);
}}
.sca-empty .icon {{
  width: 3.2rem; height: 3.2rem; margin: 0 auto .8rem auto; border-radius: 14px;
  display: flex; align-items: center; justify-content: center; font-size: 1.6rem;
  background: color-mix(in srgb, var(--sca-brand) 14%, transparent);
}}
.sca-empty h3 {{ margin: 0 0 .35rem 0; padding: 0; font-size: 1.25rem; font-weight: 650; }}
.sca-empty p {{ color: var(--sca-muted); margin: 0 auto; max-width: 34rem; font-size: .93rem; }}

/* Sidebar */
/* Brand: gradient banner card */
.sca-brand {{
  display: flex; align-items: center; gap: .7rem; margin: -.6rem 0 .6rem 0; padding: .85rem .9rem;
  border-radius: 16px; color: white; position: relative; overflow: hidden;
  background: linear-gradient(135deg, var(--sca-brand) 0%, var(--sca-deep) 100%);
  box-shadow: 0 8px 22px color-mix(in srgb, var(--sca-brand) 30%, transparent);
}}
.sca-brand::after {{
  content: ""; position: absolute; width: 7rem; height: 7rem; right: -2.2rem; top: -2.6rem; border-radius: 50%;
  background: rgba(255, 255, 255, .12);
}}
.sca-brand .logo {{
  width: 2.4rem; height: 2.4rem; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center; font-size: 1.25rem;
  background: rgba(255, 255, 255, .18); border: 1px solid rgba(255, 255, 255, .28);
}}
.sca-brand .name {{ font-weight: 700; font-size: 1rem; line-height: 1.2; color: white; }}
.sca-brand .tag {{ font-size: .74rem; color: rgba(255, 255, 255, .82); margin-top: .1rem; }}
.sca-side-label {{
  font-size: .7rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  color: var(--sca-muted); margin: .9rem 0 .4rem 0;
}}
.sca-colleges {{ display: flex; flex-wrap: wrap; gap: .35rem; }}
.sca-steps {{ margin: 0; padding: 0; list-style: none; counter-reset: s; }}
.sca-steps li {{ counter-increment: s; position: relative; padding: 0 0 .55rem 1.75rem; font-size: .83rem; line-height: 1.4; }}
.sca-steps li::before {{
  content: counter(s); position: absolute; left: 0; top: .05rem; width: 1.2rem; height: 1.2rem; border-radius: 50%;
  font-size: .7rem; font-weight: 700; display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--sca-brand) 15%, transparent); color: var(--sca-brand);
}}
.sca-steps b.simple {{ color: color-mix(in srgb, var(--sca-simple) 78%, currentColor); }}
.sca-steps b.deep {{ color: color-mix(in srgb, var(--sca-deep) 72%, currentColor); }}
.sca-steps b.personal {{ color: color-mix(in srgb, var(--sca-personal) 55%, currentColor); }}

/* Signed-in user card (sidebar) */
.sca-user {{
  position: relative; overflow: hidden; padding: .8rem .85rem .75rem .85rem; margin: .2rem 0 .5rem 0;
  border-radius: 16px; background: var(--sca-card-bg); border: 1px solid var(--sca-card-border); box-shadow: var(--sca-shadow);
}}
.sca-user::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 2.9rem;
  background: linear-gradient(120deg, color-mix(in srgb, var(--sca-brand) 22%, transparent), color-mix(in srgb, var(--sca-deep) 16%, transparent));
}}
.sca-user .top {{ position: relative; display: flex; align-items: center; gap: .6rem; }}
.sca-user .avatar {{
  width: 2.5rem; height: 2.5rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: .92rem; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}}
.sca-user .avatar.admin {{ background: linear-gradient(135deg, var(--sca-simple), var(--sca-brand)); }}
.sca-user .who {{ min-width: 0; }}
.sca-user .name {{ font-weight: 650; font-size: .92rem; line-height: 1.2; }}
.sca-user .email {{ font-size: .74rem; color: var(--sca-muted); overflow-wrap: anywhere; }}
.sca-user .badges {{ margin-top: .6rem; }}
.sca-user .meta {{
  font-size: .74rem; color: var(--sca-muted); margin-top: .4rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
.sca-stats.compact {{ margin-top: .55rem; gap: .3rem; }}
.sca-stats.compact .row {{ grid-template-columns: 1fr auto; padding: .3rem .55rem; }}
.sca-stats .row.sem {{ --c: var(--sca-brand); }}
.sca-stats.compact .row.sem .bar {{ margin-top: .3rem; }}

/* Remaining semesters dropdown (sidebar) */
.sca-sems {{ display: flex; flex-direction: column; gap: .45rem; }}
.sca-sems .sem-item {{
  border: 1px solid var(--sca-card-border); border-radius: 10px; padding: .45rem .6rem; background: var(--sca-card-bg);
}}
.sca-sems .sem-item.current {{
  border-color: color-mix(in srgb, var(--sca-brand) 45%, transparent);
  background: color-mix(in srgb, var(--sca-brand) 8%, transparent);
}}
.sca-sems .head {{ display: flex; align-items: center; gap: .45rem; }}
.sca-sems .num {{
  width: 1.35rem; height: 1.35rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-size: .7rem; font-weight: 700; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}}
.sca-sems .title {{ font-weight: 650; font-size: .82rem; }}
.sca-sems .now {{
  margin-left: auto; font-size: .66rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
  color: var(--sca-brand); background: color-mix(in srgb, var(--sca-brand) 14%, transparent); padding: .05rem .4rem; border-radius: 999px;
}}
.sca-sems ul {{ margin: .3rem 0 0 1.85rem; padding: 0; }}
.sca-sems li {{ font-size: .76rem; line-height: 1.4; margin: 0; padding: 0; }}
.sca-sems .none {{ font-size: .75rem; color: var(--sca-muted); margin: .25rem 0 0 1.85rem; font-style: italic; }}
.sca-sems .note {{ font-size: .72rem; color: var(--sca-muted); margin: .25rem 0 0 1.85rem; }}

/* Role badge */
.sca-role {{
  display: inline-flex; align-items: center; gap: .3rem; padding: .14rem .55rem; border-radius: 999px;
  font-size: .7rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
  color: color-mix(in srgb, var(--sca-brand) 75%, currentColor);
  background: color-mix(in srgb, var(--sca-brand) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--sca-brand) 35%, transparent);
}}
.sca-role.admin {{
  color: color-mix(in srgb, var(--sca-simple) 75%, currentColor);
  background: color-mix(in srgb, var(--sca-simple) 14%, transparent);
  border-color: color-mix(in srgb, var(--sca-simple) 38%, transparent);
}}

/* Login screen: centered card, no sidebar */
.st-key-login_card {{
  max-width: 440px; margin: 6vh auto 0 auto; padding: 1.6rem 1.5rem 1.3rem 1.5rem; border-radius: 22px;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
  box-shadow: 0 10px 40px color-mix(in srgb, var(--sca-brand) 14%, transparent);
}}
.st-key-login_card [data-testid="stForm"] {{ border: none; padding: 0; }}
.sca-login-head {{ text-align: center; margin-bottom: .4rem; }}
.sca-login-head .logo {{
  width: 3.3rem; height: 3.3rem; margin: 0 auto .7rem auto; border-radius: 16px; font-size: 1.6rem;
  display: flex; align-items: center; justify-content: center; color: white;
  background: linear-gradient(135deg, var(--sca-brand) 0%, var(--sca-deep) 100%);
  box-shadow: 0 8px 22px color-mix(in srgb, var(--sca-brand) 35%, transparent);
}}
.sca-login-head h1 {{ font-size: 1.45rem; font-weight: 700; margin: 0; padding: 0; line-height: 1.25; }}
.sca-login-head p {{ color: var(--sca-muted); font-size: .9rem; margin: .25rem 0 0 0; }}
.sca-login-note {{ text-align: center; font-size: .78rem; color: var(--sca-muted); margin-top: .6rem; }}
.sca-login-note code {{ font-size: .76rem; }}
.st-key-login_views {{ margin: .35rem 0 .2rem 0; }}
.st-key-login_views button p {{ font-size: .82rem; white-space: nowrap; }}
.sca-login-sub {{ font-weight: 600; font-size: .95rem; margin: .3rem 0 .1rem 0; }}
.sca-demo-code {{
  margin: .4rem 0 .8rem 0; padding: .7rem .9rem; border-radius: 12px; text-align: center;
  border: 1px dashed color-mix(in srgb, var(--sca-personal) 70%, transparent);
  background: color-mix(in srgb, var(--sca-personal) 12%, transparent);
}}
.sca-demo-code .lbl {{ font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
  color: color-mix(in srgb, var(--sca-personal) 70%, currentColor); }}
.sca-demo-code .code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 1.6rem;
  font-weight: 700; letter-spacing: .35em; margin: .15rem 0; }}
.sca-demo-code .hint {{ font-size: .74rem; color: var(--sca-muted); }}
.st-key-temp_password_box {{
  padding: .8rem 1rem; border-radius: 12px;
  border: 1px solid color-mix(in srgb, var(--sca-personal) 55%, transparent);
  background: color-mix(in srgb, var(--sca-personal) 10%, transparent);
}}

/* Student dashboard header */
.sca-student-head {{
  display: flex; align-items: center; gap: 1rem; padding: 1.1rem 1.2rem; margin: 0 0 1rem 0; border-radius: 18px;
  background: linear-gradient(120deg, color-mix(in srgb, var(--sca-brand) 14%, transparent), color-mix(in srgb, var(--sca-deep) 10%, transparent));
  border: 1px solid var(--sca-card-border);
}}
.sca-student-head .avatar {{
  width: 3.4rem; height: 3.4rem; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 1.15rem; color: white; background: linear-gradient(135deg, var(--sca-brand), var(--sca-deep));
}}
.sca-student-head .body {{ min-width: 0; }}
.sca-student-head .eyebrow {{
  font-size: .72rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--sca-brand);
}}
.sca-student-head h2 {{ font-size: 1.55rem; font-weight: 700; margin: .1rem 0 .35rem 0; padding: 0; line-height: 1.2; }}
.sca-student-head .facts {{ display: flex; flex-wrap: wrap; gap: .35rem; }}
.sca-student-head .fact {{
  font-size: .78rem; padding: .15rem .55rem; border-radius: 999px;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
}}
.sca-student-head .fact .k {{ color: var(--sca-muted); }}

/* Card lists: books, exams, hostel, action items */
.sca-list {{ display: flex; flex-direction: column; gap: .45rem; margin: .5rem 0 .3rem 0; }}
.sca-list .item {{
  display: flex; align-items: center; gap: .7rem; padding: .6rem .75rem; border-radius: 12px;
  background: var(--sca-card-bg); border: 1px solid var(--sca-card-border);
}}
.sca-list .item.bad {{
  border-color: color-mix(in srgb, var(--sca-baseline) 45%, transparent);
  background: color-mix(in srgb, var(--sca-baseline) 7%, transparent);
}}
.sca-list .cal {{
  width: 2.6rem; height: 2.6rem; flex: none; border-radius: 10px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; line-height: 1; font-size: 1.1rem;
  background: color-mix(in srgb, var(--sca-brand) 12%, transparent);
}}
.sca-list .cal .d {{ font-weight: 700; font-size: 1rem; }}
.sca-list .cal .m {{ font-size: .62rem; text-transform: uppercase; letter-spacing: .06em; color: var(--sca-muted); margin-top: .1rem; }}
.sca-list .main {{ flex: 1; min-width: 0; }}
.sca-list .title {{ font-weight: 600; font-size: .9rem; line-height: 1.3; }}
.sca-list .sub {{ font-size: .78rem; color: var(--sca-muted); overflow-wrap: anywhere; }}
.sca-list .side {{ text-align: right; flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: .2rem; }}
.sca-list .date {{ font-size: .78rem; font-variant-numeric: tabular-nums; }}
.sca-list .tag {{
  display: inline-block; font-size: .7rem; font-weight: 650; padding: .1rem .5rem; border-radius: 999px; white-space: nowrap;
  background: color-mix(in srgb, var(--sca-brand) 12%, transparent);
  color: color-mix(in srgb, var(--sca-brand) 75%, currentColor);
}}
.sca-list .tag.bad {{
  background: color-mix(in srgb, var(--sca-baseline) 16%, transparent);
  color: color-mix(in srgb, var(--sca-baseline) 75%, currentColor);
}}
.sca-list .tag.warn {{
  background: color-mix(in srgb, var(--sca-personal) 18%, transparent);
  color: color-mix(in srgb, var(--sca-personal) 55%, currentColor);
}}

/* Example-question buttons look like soft chips */
.st-key-examples button {{
  border-radius: 999px !important; font-size: .86rem; text-align: left;
}}

/* Phone widths */
@media (max-width: 640px) {{
  .sca-metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .sca-flow {{ font-size: .74rem; }}
  .sca-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .55rem; }}
  .sca-kpi {{ padding: .75rem .8rem; }}
  .sca-kpi .value {{ font-size: 1.3rem; overflow-wrap: anywhere; }}
  .sca-student-head {{ padding: .85rem .9rem; gap: .75rem; }}
  .sca-student-head h2 {{ font-size: 1.25rem; }}
  .st-key-login_card {{ margin-top: 2vh; padding: 1.2rem 1rem 1rem 1rem; }}
  .sca-hero h1 {{ font-size: 1.55rem; }}
  .sca-kpi .value {{ font-size: 1.55rem; }}
  [data-testid="stMainBlockContainer"] {{ padding-left: 1rem; padding-right: 1rem; }}
}}
</style>
"""


def inject_css(max_width: str | None = None) -> None:
    """Inject the shared CSS once per page run; optionally cap the main column width."""
    extra = ""
    if max_width:
        extra = (
            "<style>[data-testid=\"stMainBlockContainer\"], [data-testid=\"stBottomBlockContainer\"] "
            f"{{ max-width: {max_width}; }}</style>"
        )
    st.html(_CSS + extra)
