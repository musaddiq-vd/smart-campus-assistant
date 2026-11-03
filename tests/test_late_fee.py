"""Pure money helpers in student_data: fee late fee and library fines (rules from the college docs)."""

from datetime import date

import pytest

from smart_campus_assistant import student_data as sd


@pytest.mark.parametrize(
    ("due_date", "as_of", "days"),
    [
        ("2026-10-31", "2026-10-30", 0),  # before the due date
        ("2026-10-31", "2026-10-31", 0),  # paying on the due date is on time
        ("2026-10-31", "2026-11-01", 1),  # the late fee starts the day after
        ("2026-10-31", "2026-11-03", 3),
        ("2026-10-31", date(2026, 12, 1), 31),  # date objects work too
        (None, "2026-11-03", 0),
        ("not a date", "2026-11-03", 0),
    ],
)
def test_days_overdue(due_date, as_of, days):
    assert sd.days_overdue(due_date, as_of) == days


def test_fee_late_fee_example_from_fee_structure():
    # fee_structure.md: unpaid on 2026-11-03 after a 2026-10-31 due date -> Rs 300 (3 days).
    assert sd.fee_late_fee(59600, "2026-10-31", "2026-11-03") == {
        "days_overdue": 3, "late_fee": 300, "total_payable": 59900,
    }


def test_fee_late_fee_is_capped_per_semester():
    late = sd.fee_late_fee(10000, "2026-10-31", "2027-01-31")  # 92 days x 100 = 9,200 -> cap 5,000
    assert late == {"days_overdue": 92, "late_fee": 5000, "total_payable": 15000}
    assert sd.fee_late_fee(10000, "2026-10-31", "2026-12-20")["late_fee"] == 5000  # exactly 50 days


def test_fee_late_fee_zero_when_not_overdue_or_nothing_due():
    assert sd.fee_late_fee(59600, "2026-11-30", "2026-11-03") == {
        "days_overdue": 0, "late_fee": 0, "total_payable": 59600,
    }
    assert sd.fee_late_fee(0, "2026-10-31", "2026-11-03")["late_fee"] == 0
    assert sd.fee_late_fee(None, "2026-10-31", "2026-11-03")["total_payable"] == 0


def test_library_book_fine_per_day_and_cap():
    assert sd.library_book_fine("2026-10-12", "2026-11-03") == 110  # 22 days x Rs 5
    assert sd.library_book_fine("2026-11-03", "2026-11-03") == 0
    assert sd.library_book_fine("2026-01-01", "2026-11-03") == 500  # capped per book


def test_accrued_charge_without_cap():
    assert sd.accrued_charge("2026-11-01", "2026-11-11", per_day=7) == (10, 70)


def test_context_shows_capped_late_fee():
    record = {
        "student_id": "XYZ23CSE099", "name": "Test Student", "branch": "CSE", "year": 4, "semester": 7,
        "as_of": "2027-02-01",
        "fees": {"semester": "Odd 2026-27", "total": 95000, "paid": 0, "due": 95000,
                 "due_date": "2026-10-31", "status": "overdue"},
    }
    text = sd.student_context(record)
    assert "Late fee so far: Rs 5,000 (cap of Rs 5,000 per semester reached)" in text
    assert "total payable now: Rs 1,00,000" in text
    assert "grows by" not in text
