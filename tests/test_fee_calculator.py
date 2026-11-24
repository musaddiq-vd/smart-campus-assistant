"""Fee totals computed in code from fee_structure.md and handed to the answer model as a citable block."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import BaseMessage

from smart_campus_assistant import fakes, fee_calculator, models, prompts, student_data
from smart_campus_assistant import graph as graph_module
from tests.test_personal import STUDENTS

SID = "XYZ24CSE001"  # tests.test_personal.STUDENTS: hostel room A-214, fee total Rs 95,000


@pytest.fixture
def tables():
    return fee_calculator.load_fee_tables()


@pytest.fixture
def fake_mode():
    fakes.enable_fake_mode()
    yield fakes
    fakes.disable_fake_mode()


@pytest.fixture
def students_file(tmp_path, monkeypatch):
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"as_of": "2026-11-03", "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: path)
    student_data.clear_cache()
    yield path
    student_data.clear_cache()


class PromptSpy(fakes.FakeCampusChatModel):
    seen: list = []

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        PromptSpy.seen.append("\n".join(str(m.content) for m in messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def spy(fake_mode, monkeypatch):
    PromptSpy.seen = []
    monkeypatch.setattr(models, "get_chat_model", lambda role: PromptSpy(role=role))
    return PromptSpy.seen


# ---------------------------------------------------------------- parsing


def test_tables_are_parsed_from_the_document(tables):
    assert tables.year_label == "2026-27"
    assert tables.labels == {"open": "Open / General", "obc": "OBC-NCL and EWS", "sc": "SC / ST"}
    assert tables.academic_total == {"open": 95000, "obc": 75000, "sc": 35000}
    assert tables.academic["open"][0] == ("Tuition fee", 80000)
    assert tables.hostel_total == 54000 and tables.caution_deposit == 10000
    assert tables.bills["obc"] == (75000, 129000) and tables.yearly["sc"] == (70000, 178000)
    assert tables.same_for_all_branches and tables.scholarships["SC/ST Tuition Scholarship"] == 20000


def test_computed_sums_match_the_published_totals(tables):
    for key in tables.labels:
        for resident in (False, True):
            published = tables.bills[key][1 if resident else 0]
            assert tables.academic_total[key] + (tables.hostel_total if resident else 0) == published
            assert fee_calculator.yearly_cost(tables, key, resident) == 2 * published


def test_tables_are_cached_until_the_file_changes():
    assert fee_calculator.load_fee_tables() is fee_calculator.load_fee_tables()


def test_missing_or_tableless_file_gives_no_block(tmp_path, monkeypatch):
    (tmp_path / "fee_structure.md").write_text("# Fees\n\n## Tuition\n\nTuition is Rs 80,000.\n", encoding="utf-8")
    monkeypatch.setattr(fee_calculator, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    assert fee_calculator.load_fee_tables() is None
    assert fee_calculator.fee_totals_text("What is the total yearly cost?") == ""
    assert fee_calculator.load_fee_tables(tmp_path / "missing.md") is None


def test_published_totals_win_over_sums():
    text = (
        "# Fee Structure 2030-31\n\n## Academic fee per semester\n\n| Component | Open / General |\n|---|---|\n"
        "| Tuition fee | 1,000 |\n| Total academic fee per semester | 1,000 |\n\n"
        "## Total semester bill\n\n| Category | Day scholar | Hostel resident |\n|---|---|---|\n"
        "| Open / General | 1,500 | 9,000 |\n"
    )
    tables = fee_calculator.parse_fee_tables(text)
    assert fee_calculator.semester_bill(tables, "open", False) == 1500  # published, not the 1,000 sum
    assert fee_calculator.yearly_cost(tables, "open", True) == 18000  # no yearly table: 2 x the bill


def test_semester_bill_in_student_data_is_unchanged():
    expected = {("General", False): 95000, ("General", True): 149000, ("EWS", False): 75000,
                ("OBC-NCL", True): 129000, ("SC", False): 35000, ("ST", True): 89000}
    for (category, hostel), amount in expected.items():
        assert student_data.semester_bill(category, hostel) == amount


# ---------------------------------------------------------------- which questions get totals


@pytest.mark.parametrize("question, wanted", [
    ("What is the total first-year cost for an Open category student living in the hostel?", True),
    ("SC student ke liye hostel ke saath ek saal ka kharcha kitna hoga?", True),
    ("What is the hostel fee per semester, including mess charges?", True),
    ("OBC student ki yearly fees kitni hai?", True),
    ("Mera is saal ka total fees milakar kitna hoga?", True),
    ("What is the tuition fee per semester for an Open category student?", False),  # one table cell
    ("What is the total intake of CSE?", False),  # a total, but not money
    ("What is the late fee rule for paying the semester fee after the due date?", False),
    ("What is the total library fine for 3 late books?", False),
])
def test_wants_fee_totals(question, wanted):
    assert fee_calculator.wants_fee_totals(question) is wanted


@pytest.mark.parametrize("text, options", [
    ("a day scholar", [False]),
    ("living in the hostel", [True]),
    ("hostel ke saath", [True]),
    ("without hostel", [False]),
    ("hostel ke bina", [False]),
    ("compare a day scholar and a hostel resident", [False, True]),
    ("open category", []),
])
def test_hostel_options(text, options):
    assert fee_calculator.hostel_options_in(text) == options


def test_categories_named(tables):
    assert fee_calculator.categories_in("sc/st student") == ["sc"]
    assert fee_calculator.categories_in("general vs obc-ncl vs ews") == ["open", "obc"]
    assert fee_calculator.categories_in("cse student") == []


# ---------------------------------------------------------------- the block


def test_first_year_cost_with_deposit():
    text = fee_calculator.fee_totals_text(
        "What is the total first-year cost for an Open category student living in the hostel, including the "
        "caution deposit?"
    )
    heading, *lines = text.splitlines()
    assert heading.startswith("Computed fee totals") and "fee_structure.md" in heading
    assert lines[0] == (
        "- Open / General, hostel resident: per semester: academic fee Rs 95,000 + hostel and mess fee Rs 54,000 "
        "= Rs 1,49,000; per year: 2 semesters x Rs 1,49,000 = Rs 2,98,000; first year with the one-time "
        "refundable hostel caution deposit: Rs 2,98,000 + Rs 10,000 = Rs 3,08,000."
    )
    assert "OBC" not in text and "SC / ST" not in text  # only the category the question names


def test_both_options_and_no_deposit_for_day_scholars():
    text = fee_calculator.fee_totals_text("For an OBC-NCL student, compare the yearly cost of a day scholar and a "
                                          "hostel resident.")
    assert "OBC-NCL and EWS, day scholar: per semester: Rs 75,000 (no hostel fee); per year: 2 semesters x " \
           "Rs 75,000 = Rs 1,50,000." in text
    assert "per year: 2 semesters x Rs 1,29,000 = Rs 2,58,000" in text
    assert text.count("caution deposit") == 1  # residents only


def test_every_category_when_none_is_named():
    text = fee_calculator.fee_totals_text("What is the total yearly cost of studying here?")
    assert all(label in text for label in ("Open / General", "OBC-NCL and EWS", "SC / ST"))
    assert "Rs 1,90,000" in text and "Rs 1,78,000" in text


def test_four_year_programme_and_scholarship_cap():
    text = fee_calculator.fee_totals_text(
        "What is the total cost of the full four-year B.Tech for an OBC-NCL hostel resident with the need-based "
        "scholarship?"
    )
    assert "full 4-year B.Tech at 2026-27 rates: 4 x Rs 2,58,000 = Rs 10,32,000, plus the deposit once: " \
           "Rs 10,42,000" in text
    assert "XYZ Need-based Scholarship (Rs 30,000 per semester)" in text  # under the Rs 60,000 OBC tuition
    sc = fee_calculator.fee_totals_text("SC day scholar total cost with the need-based scholarship?")
    assert "capped at the Rs 20,000 tuition fee): Rs 35,000 - Rs 20,000 = Rs 15,000 per semester" in sc


def test_hostel_fee_question_leads_with_the_hostel_line():
    first = fee_calculator.fee_totals_text("What is the hostel fee per semester, including mess charges?").splitlines()[1]
    assert first.startswith("- Hostel and mess fee per semester")
    assert "Rs 30,000 + mess charges (Rs 4,000 per month x 6 months) Rs 24,000 = Rs 54,000" in first
    assert "per year: 2 x Rs 54,000 = Rs 1,08,000" in first


def test_branches_named_get_the_same_fees_line():
    text = fee_calculator.fee_totals_text("Compare CSE and ECE placements and the total yearly cost.")
    assert "Fees are the same for all 4 branches (CSE, ECE, ME, CE), so these totals are the same for CSE and ECE." in text


def test_own_category_is_read_from_the_bill(tables):
    record = {"fees": {"total": 129000}, "hostel": {"block": "B", "room": "B-1"}}
    assert fee_calculator.record_category(record, tables) == "obc"
    assert fee_calculator.record_category({"category": "SC", "fees": {"total": 1}}, tables) == "sc"
    assert fee_calculator.record_category({"fees": {"total": 12345}, "hostel": None}, tables) is None
    text = fee_calculator.fee_totals_text("Meri is saal ki total fees kitni hogi?", record)
    assert "for your fee category (OBC-NCL and EWS, hostel resident, from your bill)" in text
    assert "Rs 2,58,000" in text and "Open / General" not in text
    # The question's own words win over the record.
    assert "SC / ST, day scholar" in fee_calculator.fee_totals_text("Total yearly cost for an SC day scholar?", record)


# ---------------------------------------------------------------- the answer paths


def test_simple_path_gets_the_block_first_and_cites_it(spy):
    result = graph_module.run_question("What is the hostel fee per semester, including mess charges?")
    prompt = next(p for p in spy if "Task: answer" in p)
    assert f"[1] (XYZ College of Engineering | {fee_calculator.DOC_TYPE} | fee_structure.md)" in prompt
    assert prompts.TOTAL_HINT in prompt
    assert "Rs 54,000 [1]" in result["answer"] or "Rs 1,08,000 [1]" in result["answer"]
    assert result["citations"][0]["source"] == "fee_structure.md" and result["citations"][0]["page"] is None


def test_simple_path_without_a_total_question_gets_no_block(spy):
    graph_module.run_question("What is the library fine per day?")
    assert all(f"| {fee_calculator.DOC_TYPE} |" not in p for p in spy)  # the rules name it; no block is given


def test_personal_path_uses_the_students_own_category(spy, students_file):
    result = graph_module.run_question("Meri is saal ki total fees kitni hogi?", student_id=SID)
    assert result["route"] == "personal"
    prompt = next(p for p in spy if "Task: personal" in p)
    # Ananya: hostel resident with a Rs 95,000 bill matches no published bill, so every category is listed.
    assert f"| {fee_calculator.DOC_TYPE} |" in prompt and "Kabir" not in prompt and "XYZ23ECE007" not in prompt
    assert "Fee totals for your category" in result["answer"]


def test_personal_path_with_a_published_bill_names_the_category(fake_mode):
    # data/students.json: XYZ25CSE023 is a hostel resident with a Rs 1,29,000 bill (OBC-NCL / EWS).
    result = graph_module.run_question("What will my total fee for the full year be?", student_id="XYZ25CSE023")
    assert result["route"] == "personal"
    assert "Rs 2,58,000" in result["answer"] and "[1]" in result["answer"]
    assert result["citations"][0]["source"] == "fee_structure.md"


def test_deep_path_writer_gets_the_totals_note_first(spy):
    result = graph_module.run_question(
        "What is the total first-year cost for an Open category student living in the hostel, including the "
        "caution deposit?"
    )
    assert result["route"] == "deep"
    writer = next(p for p in spy if "Task: report" in p)
    notes = writer.split("Research notes:", 1)[1]
    assert notes.lstrip().startswith("### Computed fee totals")
    assert "Rs 3,08,000" in result["answer"]
    assert "plan" in result and all(not n["sub_question"].startswith("Computed") for n in result["notes"])


def test_deep_path_uses_own_category_only_for_own_questions(fake_mode):
    own = graph_module.run_question("What is my total yearly cost?", force_route="deep", student_id="XYZ25CSE023")
    assert "OBC-NCL and EWS, hostel resident" in own["answer"] and "Open / General" not in own["answer"]
    general = graph_module.run_question("What is the total yearly cost for an SC student?", force_route="deep",
                                        student_id="XYZ25CSE023")
    assert "SC / ST" in general["answer"] and "OBC-NCL and EWS, hostel" not in general["answer"]
