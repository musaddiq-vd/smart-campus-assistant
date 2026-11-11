"""Offline tests for the router, simple path, deep path and graph wiring (no AWS calls).

Personal-route behaviour is tested in test_personal.py.
"""

import json
from types import SimpleNamespace

import pytest

from smart_campus_assistant import deep_research, fakes, models, router
from smart_campus_assistant import graph as graph_module


@pytest.fixture
def fake_mode():
    fakes.enable_fake_mode()
    yield fakes
    fakes.disable_fake_mode()


def _set_max_loops(monkeypatch, n: int) -> None:
    monkeypatch.setattr(deep_research, "get_settings", lambda: SimpleNamespace(max_research_loops=n, research_workers=4))


# ---------------------------------------------------------------- router


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"route": "deep", "reason": "Comparison."}', ("deep", "Comparison.")),
        ('Sure! Here you go:\n```json\n{"route": "simple", "reason": "One fact"}\n```', ("simple", "One fact")),
        ('{"oops": 1} then {"route": "DEEP", "reason": "x"}', ("deep", "x")),
        ("route: simple", ("simple", "")),
        ('{"route": "personal", "reason": "Own fees."}', ("personal", "Own fees.")),
        ("route = personal", ("personal", "")),
        ("I am not sure.", None),
        ('{"route": "maybe"}', None),
    ],
)
def test_parse_route(text, expected):
    assert router.parse_route(text) == expected


@pytest.mark.parametrize(
    "question, route",
    [
        ("What is the hostel fee at XYZ College?", "simple"),
        ("Hostel mein mess charge kitna hai?", "simple"),
        ("What is the library fine per day?", "simple"),
        ("Library me fine kitna lagta hai?", "simple"),  # Hinglish "me" = "in", not personal
        ("Tell me the minimum attendance rule", "simple"),
        ("Compare CSE and ECE placements", "deep"),
        ("CSE vs ME fees", "deep"),
        ("Placements of CSE and ECE", "deep"),  # two branches
        ("Which branch should I pick for placements?", "deep"),
        ("Meri fees kitni due hai?", "personal"),
        ("Mere naam par kaunsi books issued hain?", "personal"),
        ("What is my attendance?", "personal"),
        ("Mera next exam kab hai?", "personal"),
        ("Do I have any fine?", "personal"),
        ("Mujhe hall ticket milega?", "personal"),
        ("Show my CGPA and backlogs", "personal"),
        ("What is the fee status of XYZ24ECE002?", "personal"),  # another ID: personal node refuses
    ],
)
def test_heuristic_route(question, route):
    assert router.heuristic_route(question)[0] == route


def test_router_falls_back_to_heuristic_on_bad_output(fake_mode, monkeypatch):
    class Garbage(fakes.FakeCampusChatModel):
        def _respond(self, task, human):
            return "no json here"

    monkeypatch.setattr(models, "get_chat_model", lambda role: Garbage(role=role))
    out = router.route_question({"question": "Compare CSE vs ECE placements"}, {"configurable": {}})
    assert out["route"] == "deep"
    assert out["route_reason"].startswith("Heuristic")
    assert len(out["usage"]) == 1  # the failed call still costs tokens


def test_force_route_skips_llm(fake_mode):
    out = router.route_question({"question": "What is the hostel fee?"}, {"configurable": {"force_route": "deep"}})
    assert out["route"] == "deep"
    assert "usage" not in out
    assert fakes.CALL_LOG == []


@pytest.mark.parametrize("forced", ["simple", "deep", "personal"])
def test_force_route_all_routes(fake_mode, forced):
    out = router.route_question({"question": "What is the hostel fee?"}, {"configurable": {"force_route": forced}})
    assert out == {"route": forced, "route_reason": f"Forced route: {forced}."}


def test_router_llm_picks_personal(fake_mode):
    out = router.route_question({"question": "Meri fees kitni due hai?"}, {"configurable": {}})
    assert out["route"] == "personal"
    assert fakes.CALL_LOG == [("router", "route")]


def test_force_route_via_graph_config_and_context(fake_mode):
    state = graph_module.graph.invoke(
        {"question": "What is the hostel fee?"}, config={"configurable": {"force_route": "deep"}}
    )
    assert state["route"] == "deep"
    assert all(rec["node"] != "router" for rec in state["usage"])

    state = graph_module.graph.invoke({"question": "Compare CSE vs ECE placements"}, context={"force_route": "simple"})
    assert state["route"] == "simple"
    assert [rec["node"] for rec in state["usage"]] == ["simple_answer"]


# ---------------------------------------------------------------- simple path


def test_simple_path_two_llm_calls_with_citations(fake_mode):
    result = graph_module.run_question("What is the hostel fee?")
    assert result["route"] == "simple"
    assert fakes.CALL_LOG == [("router", "route"), ("answer", "answer")]
    assert [r["node"] for r in result["usage"]] == ["router", "simple_answer"]
    assert "[1]" in result["answer"]
    assert result["citations"] and result["citations"][0]["college"] == "xyz"
    assert {"source", "college", "page", "snippet"} <= set(result["citations"][0])
    assert "plan" not in result


# ---------------------------------------------------------------- deep path


def test_deep_path_runs_planner_researcher_writer(fake_mode):
    nodes = [
        name
        for chunk in graph_module.graph.stream({"question": "Compare fees of CSE vs ECE"}, stream_mode="updates")
        for name in chunk
    ]
    assert nodes == ["router", "planner", "researcher", "writer"]


def test_deep_report_citations_renumbered(fake_mode):
    result = graph_module.run_question("Compare fees of CSE vs ECE")
    assert result["route"] == "deep"
    assert 3 <= len(result["plan"]) <= 5
    assert len(result["notes"]) == len(result["plan"])
    cites = result["citations"]
    assert cites
    keys = [(c["source"], c["page"], c["snippet"]) for c in cites]
    assert len(keys) == len(set(keys))  # de-duplicated
    used = {int(n) for n in deep_research._cited_numbers(result["answer"])}
    assert used == set(range(1, len(cites) + 1))  # 1..n, all used
    nodes = [r["node"] for r in result["usage"]]
    assert nodes[0] == "router" and nodes[1] == "planner" and nodes[-1] == "writer"
    assert len(result["usage"]) == len(fakes.CALL_LOG)  # a record for every LLM call


def test_research_respects_max_loops(fake_mode, monkeypatch):
    counter = {"n": 0}

    class AlwaysGaps(fakes.FakeCampusChatModel):
        def _respond(self, task, human):
            if task == "gap":
                counter["n"] += 1
                return json.dumps([f"Extra question {counter['n']} about the CSE hostel?"])
            return super()._respond(task, human)

    monkeypatch.setattr(models, "get_chat_model", lambda role: AlwaysGaps(role=role))
    _set_max_loops(monkeypatch, 1)
    result = graph_module.run_question("Compare CSE vs ECE placements", force_route="deep")
    assert result["research_loops"] == 2  # first pass + 1 extra loop
    assert counter["n"] == 1  # no gap check after the last allowed loop
    researched = {n["sub_question"] for n in result["notes"]}
    assert "Extra question 1 about the CSE hostel?" in researched


def test_zero_extra_loops(fake_mode, monkeypatch):
    _set_max_loops(monkeypatch, 0)
    result = graph_module.run_question("Compare CSE vs ECE placements")
    assert result["research_loops"] == 1
    assert ("answer", "gap") not in fakes.CALL_LOG


def test_parse_list_formats():
    assert deep_research.parse_list('["a?", "b?", "c?"]') == ["a?", "b?", "c?"]
    assert deep_research.parse_list("Plan:\n1. a?\n2) b?\n- c?\n") == ["a?", "b?", "c?"]
    assert len(deep_research.parse_list(json.dumps([f"q{i}" for i in range(9)]))) == 5


def test_planner_fallback_when_unparseable(fake_mode, monkeypatch):
    class NoPlan(fakes.FakeCampusChatModel):
        def _respond(self, task, human):
            return "hmm" if task == "plan" else super()._respond(task, human)

    monkeypatch.setattr(models, "get_chat_model", lambda role: NoPlan(role=role))
    out = deep_research.plan_research({"question": "Compare CSE vs ECE placements"})
    assert out["plan"] == ["Compare CSE vs ECE placements"]
    assert out["research_loops"] == 0


# ---------------------------------------------------------------- usage, summary, wiring


def test_usage_records_have_tokens(fake_mode):
    result = graph_module.run_question("Compare CSE vs ECE hostel and fees")
    for rec in result["usage"]:
        assert rec["input_tokens"] > 0 and rec["output_tokens"] > 0
        assert rec["model_id"] in (models.model_id_for("router"), models.model_id_for("answer"))
    router_rec = result["usage"][0]
    assert router_rec["model_id"] == models.model_id_for("router")


def test_run_question_summary(fake_mode):
    result = graph_module.run_question("What is the tuition fee for CSE?")
    summary = result["summary"]
    assert summary["llm_calls"] == 2
    assert summary["total_tokens"] == summary["input_tokens"] + summary["output_tokens"] > 0
    assert summary["cost_usd"] > 0
    assert result["total_latency_ms"] >= 0


def test_deep_costs_more_than_simple(fake_mode):
    q = "What is the tuition fee for CSE?"
    simple = graph_module.run_question(q)["summary"]
    deep = graph_module.run_question(q, force_route="deep")["summary"]
    assert deep["llm_calls"] > simple["llm_calls"]
    assert deep["cost_usd"] > simple["cost_usd"]


def test_node_names():
    nodes = set(graph_module.graph.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == set(graph_module.NODE_NAMES) == {
        "router", "simple_answer", "personal_answer", "planner", "researcher", "writer"
    }


def test_fake_mode_restores_originals():
    from smart_campus_assistant import retriever

    original_model, original_search = models.get_chat_model, retriever.search
    fakes.enable_fake_mode()
    assert models.get_chat_model is not original_model
    assert retriever.search is fakes.fake_search
    fakes.disable_fake_mode()
    assert models.get_chat_model is original_model
    assert retriever.search is original_search


# ---------------------------------------------------------------- answer-format hints


@pytest.mark.parametrize(
    "question, total, steps",
    [
        ("What is the hostel fee per semester, including mess charges?", True, False),
        ("Hostel ke saath ek saal ka total kharcha kitna hoga?", True, False),
        ("How can a student clear backlogs?", False, True),
        ("What is the library fine per day?", False, False),
    ],
)
def test_answer_hints(question, total, steps):
    from smart_campus_assistant import prompts

    hints = prompts.answer_hints(question)
    assert (prompts.TOTAL_HINT in hints) is total
    assert (prompts.STEPS_HINT in hints) is steps


def test_hints_reach_simple_and_writer_messages():
    from smart_campus_assistant import prompts

    q = "What is the total yearly cost, including hostel?"
    assert prompts.TOTAL_HINT in prompts.simple_answer_messages(q, "[1] (x)\nfact")[-1].content
    writer = prompts.writer_messages(q, "### sub\n- fact [1]", "[1] src")[-1].content
    assert writer.index("Sources:") < writer.index(prompts.TOTAL_HINT)  # after the notes and sources


def test_planner_does_not_invite_extra_branches():
    from smart_campus_assistant import prompts

    assert "never add branches" in prompts.PLANNER_SYSTEM
    assert "Late fee so far" in prompts.PERSONAL_ANSWER_SYSTEM


def test_plan_starts_with_the_question_itself(fake_mode):
    out = deep_research.plan_research({"question": "Compare CSE vs ECE placements"})
    assert out["plan"][0] == "Compare CSE vs ECE placements"
    assert 2 <= len(out["plan"]) <= deep_research.MAX_PLAN
    assert len({q.lower() for q in out["plan"]}) == len(out["plan"])  # no duplicates
