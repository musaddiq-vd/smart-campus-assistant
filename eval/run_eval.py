"""Evaluate the 3-route router (simple / deep / personal) against an always-deep baseline.

Each question may carry a `student_id` (the logged-in student); it is passed to the runner.

Usage:
    python eval/run_eval.py [--fake] [--limit N] [--modes router,always_deep]

Writes eval/results/run_<timestamp>.csv, eval/results/latest.csv and
eval/results/latest_summary.json, then prints a summary table.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.json"
RESULTS_DIR = EVAL_DIR / "results"

# The dashboard depends on these exact columns, in this order.
CSV_COLUMNS = [
    "question_id",
    "question",
    "student_id",
    "expected_route",
    "mode",
    "predicted_route",
    "route_correct",
    "llm_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "latency_ms",
    "keyword_hit",
    "answer_chars",
    "citations",
    "error",
    "fake",
]

# Mode name -> force_route passed to run_question.
MODES: dict[str, str | None] = {"router": None, "always_deep": "deep"}

ROUTES = ("simple", "deep", "personal")

Runner = Callable[..., dict]


def load_questions(path: Path | str = QUESTIONS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize(text: str) -> str:
    """Lowercase, drop commas, and join digit groups so "1,25,550" == "1 25 550" == "125550"."""
    text = (text or "").lower().replace(",", "")
    text = re.sub(r"\s+", " ", text)
    # "1 25 550" -> "125550": join only short digit groups, so table cells like "100000 300000" stay apart.
    text = re.sub(r"(?<=\d) (?=\d{2,3}(?!\d))", "", text)
    return re.sub(r"(?<=\d) %", "%", text)  # "75 %" -> "75%"


def keyword_hit(answer: str, keywords: Iterable[str]) -> float:
    """Fraction (0-1) of keywords found in the answer, case-insensitive."""
    keywords = [k for k in keywords if k]
    if not keywords:
        return 0.0
    text = _normalize(answer)
    hits = 0
    for kw in keywords:
        norm = _normalize(kw)
        if norm.isdigit():
            # Pure numbers must not be part of a longer number ("12" not in "2012").
            found = re.search(rf"(?<!\d){re.escape(norm)}(?!\d)", text) is not None
        else:
            found = norm in text
        hits += found
    return round(hits / len(keywords), 4)


def default_runner(fake: bool = False) -> Runner:
    """Return the real graph runner; imported lazily so this module loads without it."""
    if fake:
        from smart_campus_assistant import fakes

        fakes.enable_fake_mode()
    from smart_campus_assistant.graph import run_question

    return run_question


def _run_one(q: dict, mode: str, runner: Runner, fake: bool) -> dict:
    row = {
        "question_id": q["id"],
        "question": q["question"],
        "student_id": q.get("student_id") or "",
        "expected_route": q["expected_route"],
        "mode": mode,
        "predicted_route": "",
        "route_correct": False,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0.0,
        "keyword_hit": 0.0,
        "answer_chars": 0,
        "citations": 0,
        "error": "",
        "fake": bool(fake),
    }
    start = time.perf_counter()
    try:
        result = runner(q["question"], force_route=MODES[mode], student_id=q.get("student_id")) or {}
    except Exception as exc:  # one bad question must not stop the run
        row["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        traceback.print_exc()
        return row

    summary = result.get("summary") or {}
    answer = result.get("answer") or ""
    predicted = result.get("route") or ""
    row.update(
        predicted_route=predicted,
        route_correct=predicted == q["expected_route"],
        llm_calls=int(summary.get("llm_calls", 0)),
        input_tokens=int(summary.get("input_tokens", 0)),
        output_tokens=int(summary.get("output_tokens", 0)),
        total_tokens=int(summary.get("total_tokens", 0)),
        cost_usd=float(summary.get("cost_usd", 0.0)),
        latency_ms=round(
            float(result.get("total_latency_ms") or (time.perf_counter() - start) * 1000), 1
        ),
        keyword_hit=keyword_hit(answer, q.get("expected_keywords", [])),
        answer_chars=len(answer),
        citations=len(result.get("citations") or []),
    )
    return row


def run_eval(
    questions: list[dict],
    modes: Iterable[str] = ("router", "always_deep"),
    runner: Runner | None = None,
    fake: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """Run every question in every mode and return one row per (question, mode)."""
    modes = list(modes)
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        raise ValueError(f"Unknown modes: {unknown}. Use {list(MODES)}")
    runner = runner or default_runner(fake)
    rows = []
    for mode in modes:
        for i, q in enumerate(questions, 1):
            row = _run_one(q, mode, runner, fake)
            rows.append(row)
            if verbose:
                status = "ERR" if row["error"] else ("ok " if row["route_correct"] else "MIS")
                print(
                    f"[{mode:<11}] {i:>2}/{len(questions)} {q['id']} {status} "
                    f"route={row['predicted_route'] or '-':<8} cost=${row['cost_usd']:.6f}"
                )
    return rows


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(rows: list[dict], fake: bool = False) -> dict:
    """Aggregate rows into the summary JSON structure."""
    by_mode: dict[str, list[dict]] = {}
    for r in rows:
        by_mode.setdefault(r["mode"], []).append(r)

    modes = {}
    for mode, mrows in by_mode.items():
        ok = [r for r in mrows if not r["error"]]
        modes[mode] = {
            # Errors count as wrong routes.
            "route_accuracy": round(_mean([1.0 if r["route_correct"] else 0.0 for r in mrows]), 4),
            "total_cost_usd": round(sum(r["cost_usd"] for r in mrows), 8),
            "avg_cost_usd": round(_mean([r["cost_usd"] for r in ok]), 8),
            "avg_latency_ms": round(_mean([r["latency_ms"] for r in ok]), 1),
            "avg_tokens": round(_mean([r["total_tokens"] for r in ok]), 1),
            "keyword_hit_rate": round(_mean([r["keyword_hit"] for r in mrows]), 4),
            "errors": len(mrows) - len(ok),
        }

    savings_pct = None
    if "router" in modes and "always_deep" in modes:
        base = modes["always_deep"]["total_cost_usd"]
        if base > 0:
            savings_pct = round((base - modes["router"]["total_cost_usd"]) / base * 100, 2)

    # 3x3: expected route -> predicted route, router mode only.
    confusion = {f"{e}->{p}": 0 for e in ROUTES for p in ROUTES}
    for r in by_mode.get("router", []):
        key = f"{r['expected_route']}->{r['predicted_route']}"
        if key in confusion:
            confusion[key] += 1

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fake": bool(fake),
        "n_questions": len({r["question_id"] for r in rows}),
        "routes": list(ROUTES),
        "modes": modes,
        "savings_pct": savings_pct,
        "confusion": confusion,
    }


def write_results(rows: list[dict], summary: dict, results_dir: Path | str = RESULTS_DIR) -> Path:
    """Write the timestamped CSV, latest.csv and latest_summary.json. Returns the run CSV path."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    run_path = results_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(run_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    shutil.copyfile(run_path, results_dir / "latest.csv")
    with open(results_dir / "latest_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return run_path


def print_summary(summary: dict) -> None:
    header = f"{'mode':<12}{'route_acc':>10}{'total_$':>12}{'avg_$':>12}{'avg_ms':>10}{'avg_tok':>10}{'kw_hit':>8}{'errors':>8}"
    print("\n" + header)
    print("-" * len(header))
    for mode, m in summary["modes"].items():
        print(
            f"{mode:<12}{m['route_accuracy']:>10.1%}{m['total_cost_usd']:>12.6f}{m['avg_cost_usd']:>12.6f}"
            f"{m['avg_latency_ms']:>10.0f}{m['avg_tokens']:>10.0f}{m['keyword_hit_rate']:>8.2f}{m['errors']:>8}"
        )
    if summary["savings_pct"] is not None:
        print(f"\nRouter saves {summary['savings_pct']:.1f}% cost vs always_deep")
    if "router" in summary["modes"]:
        c = summary["confusion"]
        print("\nRouter confusion (rows = expected, columns = predicted):")
        print("  " + " " * 10 + "".join(f"{p:>10}" for p in ROUTES))
        for e in ROUTES:
            print(f"  {e:<10}" + "".join(f"{c[f'{e}->{p}']:>10}" for p in ROUTES))
    print(f"\nfake={summary['fake']}  n_questions={summary['n_questions']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Router vs always-deep evaluation")
    parser.add_argument("--fake", action="store_true", help="offline fake LLM and retriever")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--modes", default="router,always_deep", help="comma list: router,always_deep")
    parser.add_argument("--questions", default=str(QUESTIONS_PATH))
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args(argv)

    questions = load_questions(args.questions)
    if args.limit:
        questions = questions[: args.limit]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    rows = run_eval(questions, modes, runner=default_runner(args.fake), fake=args.fake, verbose=True)
    summary = summarize(rows, fake=args.fake)
    run_path = write_results(rows, summary, args.results_dir)
    print_summary(summary)
    print(f"\nWrote {run_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
