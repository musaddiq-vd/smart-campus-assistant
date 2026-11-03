"""Offline tests for the eval questions and runner (no AWS, no real graph)."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# eval/ is not a package, so load run_eval.py by path.
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval_mod)

QUESTIONS = run_eval_mod.load_questions(ROOT / "eval" / "questions.json")


def _summary(llm_calls: int, cost: float) -> dict:
    return {
        "llm_calls": llm_calls,
        "input_tokens": 100 * llm_calls,
        "output_tokens": 20 * llm_calls,
        "total_tokens": 120 * llm_calls,
        "cost_usd": cost,
        "llm_latency_ms": 10.0,
    }


STUDENT_IDS = {
    s["student_id"]
    for s in json.loads((ROOT / "data" / "students.json").read_text(encoding="utf-8"))["students"]
}


def stub_runner(question: str, force_route: str | None = None, student_id: str | None = None) -> dict:
    """Routes 'compare' to deep, 'my'/'meri' to personal, else simple; deep costs 10x simple."""
    q = question.lower()
    if force_route:
        route = force_route
    elif "compare" in q:
        route = "deep"
    elif any(w in q.split() for w in ("my", "meri", "mera", "mere")):
        route = "personal"
    else:
        route = "simple"
    cost = 0.001 if route == "deep" else 0.0001
    return {
        "question": question,
        "student_id": student_id,
        "route": route,
        "route_reason": "stub",
        "answer": "The fee is Rs. 1,49,000 and tuition is 80,000.",
        "citations": [{"source": "fee_structure.md", "college": "xyz", "page": 2, "snippet": "..."}],
        "usage": [],
        "summary": _summary(5 if route == "deep" else 2, cost),
        "total_latency_ms": 42.0,
    }


# --- questions.json ---------------------------------------------------------

def test_questions_count_and_split():
    assert len(QUESTIONS) == 45
    routes = [q["expected_route"] for q in QUESTIONS]
    assert routes.count("simple") == 20
    assert routes.count("deep") == 12
    assert routes.count("personal") == 13


def test_questions_fields_and_unique_ids():
    ids = [q["id"] for q in QUESTIONS]
    assert len(set(ids)) == len(ids)
    for q in QUESTIONS:
        for key in ("id", "question", "expected_route", "student_id", "expected_keywords", "notes"):
            assert key in q, (q["id"], key)
        assert q["question"].strip()
        assert q["expected_route"] in run_eval_mod.ROUTES
        assert 1 <= len(q["expected_keywords"]) <= 4
        assert all(isinstance(k, str) and k for k in q["expected_keywords"])


def test_student_ids_on_questions():
    for q in QUESTIONS:
        if q["expected_route"] != "personal":
            assert q["student_id"] is None, q["id"]
        elif q["student_id"] is not None:
            assert q["student_id"] in STUDENT_IDS, q["id"]
    personal = [q for q in QUESTIONS if q["expected_route"] == "personal"]
    guests = [q for q in personal if q["student_id"] is None]
    # Exactly one guest question; it expects a log-in prompt.
    assert len(guests) == 1 and "student ID" in guests[0]["expected_keywords"]
    assert len(personal) - len(guests) == 12


def test_keywords_appear_in_data():
    """Every keyword is in the college docs (simple/deep/guest) or in that student's record."""
    docs = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "data" / "college").glob("*.md"))
    records = {
        s["student_id"]: json.dumps(s)
        for s in json.loads((ROOT / "data" / "students.json").read_text(encoding="utf-8"))["students"]
    }
    for q in QUESTIONS:
        source = records[q["student_id"]] if q["student_id"] else docs
        assert run_eval_mod.keyword_hit(source, q["expected_keywords"]) == 1.0, q["id"]


# --- keyword_hit ------------------------------------------------------------

def test_keyword_hit_normalizes_numbers_and_case():
    kh = run_eval_mod.keyword_hit
    assert kh("Total is Rs. 1,25,550/-", ["125550"]) == 1.0
    assert kh("Total is 125550", ["1,25,550"]) == 1.0
    assert kh("Total is 1 25 550", ["125550"]) == 1.0
    assert kh("100000 300000", ["100000", "300000"]) == 1.0
    assert kh("pay via  SB\ncollect", ["SB Collect"]) == 1.0
    assert kh("needs 75 % attendance", ["75%"]) == 1.0
    assert kh("fee 62500, deposit 6500", ["62500", "99999"]) == 0.5
    assert kh("anything", []) == 0.0
    assert kh("", ["x"]) == 0.0


def test_keyword_hit_numbers_need_digit_boundary():
    kh = run_eval_mod.keyword_hit
    assert kh("in the year 2012", ["12"]) == 0.0
    assert kh("earn 12 credits", ["12"]) == 1.0


# --- run_eval / summarize / write_results ------------------------------------

def test_run_eval_writes_exact_csv_and_summary(tmp_path):
    qs = QUESTIONS[:4] + QUESTIONS[-3:]  # simple questions + personal ones (incl. the guest)
    rows = run_eval_mod.run_eval(qs, ["router", "always_deep"], runner=stub_runner, fake=True)
    assert len(rows) == 14
    summary = run_eval_mod.summarize(rows, fake=True)
    run_path = run_eval_mod.write_results(rows, summary, tmp_path)

    assert run_path.name.startswith("run_") and run_path.suffix == ".csv"
    with open(tmp_path / "latest.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == run_eval_mod.CSV_COLUMNS
        data = list(reader)
    assert reader.fieldnames[:4] == ["question_id", "question", "student_id", "expected_route"]
    assert len(data) == 14
    by_id = {q["id"]: q for q in qs}
    for r in data:
        assert r["student_id"] == (by_id[r["question_id"]]["student_id"] or "")
    assert {r["mode"] for r in data} == {"router", "always_deep"}
    assert all(r["fake"] == "True" for r in data)
    assert all(r["predicted_route"] == "deep" for r in data if r["mode"] == "always_deep")

    saved = json.loads((tmp_path / "latest_summary.json").read_text(encoding="utf-8"))
    assert set(saved) == {
        "generated_at", "fake", "n_questions", "routes", "modes", "savings_pct", "confusion",
    }
    assert saved["routes"] == ["simple", "deep", "personal"]
    assert saved["fake"] is True and saved["n_questions"] == 7
    for m in saved["modes"].values():
        assert set(m) == {
            "route_accuracy", "total_cost_usd", "avg_cost_usd", "avg_latency_ms",
            "avg_tokens", "keyword_hit_rate", "errors",
        }
    routes = ("simple", "deep", "personal")
    assert set(saved["confusion"]) == {f"{e}->{p}" for e in routes for p in routes}
    assert len(saved["confusion"]) == 9
    assert sum(saved["confusion"].values()) == 7


def test_savings_pct_math():
    qs = [
        {"id": "a", "question": "fee?", "expected_route": "simple", "student_id": None,
         "expected_keywords": ["1,49,000"]},
        {"id": "b", "question": "compare fees", "expected_route": "deep", "student_id": None,
         "expected_keywords": ["x"]},
        {"id": "c", "question": "what is my fee status", "expected_route": "personal",
         "student_id": "XYZ24CSE001", "expected_keywords": ["80000"]},
        # Expected personal, but the stub routes it to simple.
        {"id": "d", "question": "fee status please", "expected_route": "personal",
         "student_id": "XYZ24CSE001", "expected_keywords": ["zzz"]},
    ]
    rows = run_eval_mod.run_eval(qs, ["router", "always_deep"], runner=stub_runner)
    s = run_eval_mod.summarize(rows)
    # router: 0.0001 + 0.001 + 0.0001 + 0.0001 = 0.0013; always_deep: 4 x 0.001 = 0.004 -> 67.5% saved.
    assert s["modes"]["router"]["total_cost_usd"] == pytest.approx(0.0013)
    assert s["modes"]["always_deep"]["total_cost_usd"] == pytest.approx(0.004)
    assert s["savings_pct"] == pytest.approx(67.5)
    assert s["modes"]["router"]["route_accuracy"] == 0.75
    assert s["modes"]["always_deep"]["route_accuracy"] == 0.25
    expected = {f"{e}->{p}": 0 for e in run_eval_mod.ROUTES for p in run_eval_mod.ROUTES}
    expected.update({"simple->simple": 1, "deep->deep": 1, "personal->personal": 1, "personal->simple": 1})
    assert s["confusion"] == expected
    assert s["modes"]["router"]["keyword_hit_rate"] == 0.5
    assert s["routes"] == ["simple", "deep", "personal"]


def test_student_id_passed_to_runner():
    seen = []

    def recording(question, force_route=None, student_id=None):
        seen.append((force_route, student_id))
        return stub_runner(question, force_route, student_id)

    personal = [q for q in QUESTIONS if q["expected_route"] == "personal"]
    run_eval_mod.run_eval(personal, ["router", "always_deep"], runner=recording)
    n = len(personal)
    assert [sid for _, sid in seen] == [q["student_id"] for q in personal] * 2
    # always_deep still forces "deep" for every question, including personal ones.
    assert [fr for fr, _ in seen] == [None] * n + ["deep"] * n


def test_savings_pct_none_with_single_mode():
    rows = run_eval_mod.run_eval(QUESTIONS[:2], ["router"], runner=stub_runner)
    assert run_eval_mod.summarize(rows)["savings_pct"] is None


def test_per_question_error_is_caught(tmp_path):
    def flaky(question, force_route=None, student_id=None):
        if student_id:
            raise RuntimeError("boom")
        return stub_runner(question, force_route, student_id)

    qs = QUESTIONS[:2] + QUESTIONS[-4:]  # 2 simple + 3 logged-in personal + 1 guest
    rows = run_eval_mod.run_eval(qs, ["router"], runner=flaky)
    errors = [r for r in rows if r["error"]]
    assert len(errors) == 3
    assert all("boom" in r["error"] and r["route_correct"] is False for r in errors)
    s = run_eval_mod.summarize(rows)
    assert s["modes"]["router"]["errors"] == 3
    run_eval_mod.write_results(rows, s, tmp_path)
    assert (tmp_path / "latest.csv").exists()


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        run_eval_mod.run_eval(QUESTIONS[:1], ["bogus"], runner=stub_runner)
