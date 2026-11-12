"""Page "Pay fees" (students only): simulated payment of the signed-in student's own semester dues.

This is a demo: no money moves and no payment credential (card number, UPI ID, bank
details) is ever asked for. The payment mode is only a label on the receipt.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    as_of_date,
    days_text,
    empty_state,
    fee_status_label,
    format_rupees,
    get_college_name,
    get_student_safe,
    hero_html,
    kpi_card_html,
    nice_date,
    require_role,
    section_header,
)
from ui.theme import BASELINE_COLOR, PERSONAL_COLOR, SIMPLE_COLOR, inject_css  # noqa: E402

PAY_FAILED = "The payment could not be recorded. Please try again."
FULL, CUSTOM = "Full due", "Custom amount"
PAY_CSS = """
<style>
.pay-demo {
  border: 1px dashed var(--sca-card-border, rgba(128,128,128,.45)); border-radius: 12px;
  padding: .6rem .9rem; font-size: .88rem; margin: .4rem 0 .8rem 0;
}
.pay-receipt {
  border: 1px solid var(--sca-card-border, rgba(128,128,128,.25)); border-radius: var(--sca-radius, 14px);
  border-left: 4px solid #2E9E6B; padding: .8rem 1rem; margin: .4rem 0 .6rem 0;
}
.pay-receipt .title { font-weight: 650; font-size: 1rem; }
.pay-receipt .line { font-size: .9rem; margin-top: .2rem; }
.pay-receipt .line .k { color: var(--sca-muted, gray); margin-right: .35rem; }
.pay-row { font-size: .92rem; font-weight: 600; }
.pay-row .sub { font-size: .78rem; font-weight: 400; color: var(--sca-muted, gray); }
</style>
"""


def _rs(amount) -> str:
    """Plain-text rupees for the receipt file, e.g. ``Rs 1,23,456``."""
    return format_rupees(amount).replace("₹", "Rs ")


def receipt_text(record: dict, txn: dict) -> str:
    """Plain-text receipt of one payment (offered as a download)."""
    fees = record.get("fees") or {}
    lines = [
        get_college_name(),
        "FEE PAYMENT RECEIPT (SIMULATED DEMO PAYMENT)",
        "-" * 46,
        f"Receipt no     : {txn.get('receipt_no', '')}",
        f"Payment date   : {nice_date(txn.get('date'))}",
        f"Student        : {record.get('name', '')} ({record.get('student_id', '')})",
        f"Branch / sem   : {record.get('branch', '')} / Semester {record.get('semester', '')}",
        f"Fee semester   : {fees.get('semester', '')}",
        f"Amount paid    : {_rs(txn.get('amount'))}",
        f"Payment mode   : {txn.get('mode', '')}",
        "-" * 46,
        f"Semester total : {_rs(fees.get('total', 0))}",
        f"Paid so far    : {_rs(fees.get('paid', 0))}",
        f"Balance due    : {_rs(fees.get('due', 0))} (as of {nice_date(as_of_date(record).isoformat())})",
        "-" * 46,
        "This receipt comes from a demo application with synthetic data.",
        "The payment was simulated: no money was transferred.",
    ]
    return "\n".join(lines) + "\n"


def _receipt_download(record: dict, txn: dict, key: str, label: str = "Download receipt") -> None:
    receipt_no = str(txn.get("receipt_no") or "receipt")
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in receipt_no)
    st.download_button(label, data=receipt_text(record, txn).encode("utf-8"), file_name=f"{safe_name}.txt",
                       mime="text/plain", icon=":material/download:", key=key)


def _pay(student_id: str, due: int, gen: int) -> None:
    """Form callback: record the simulated payment for the signed-in student only."""
    account = st.session_state.get("user") or {}
    choice = st.session_state.get(f"pay_choice_{gen}")
    amount = due if choice != CUSTOM else st.session_state.get(f"pay_amount_{gen}")
    try:
        from smart_campus_assistant import record_updates

        updated = record_updates.record_fee_payment(
            student_id, amount, st.session_state.get(f"pay_mode_{gen}"),
            actor_email=str(account.get("email") or ""), actor_role=str(account.get("role") or ""),
            actor_student_id=account.get("student_id"),
        )
    except (ValueError, PermissionError) as exc:  # RecordError messages are written for the UI
        st.session_state["pay_flash"] = ("error", str(exc) or PAY_FAILED)
        return
    except Exception:  # noqa: BLE001
        st.session_state["pay_flash"] = ("error", PAY_FAILED)
        return
    txn = ((updated.get("fees") or {}).get("transactions") or [{}])[-1]
    st.session_state["pay_last_receipt"] = txn.get("receipt_no")
    st.session_state["pay_flash"] = ("success", f"Payment of {format_rupees(txn.get('amount'))} recorded.")
    st.session_state["pay_gen"] = gen + 1  # fresh form widgets on the next run


inject_css(max_width="72rem")
user = require_role("student")
st.markdown(PAY_CSS, unsafe_allow_html=True)
st.markdown(
    hero_html("Fees", "Pay fees", "See what is due this semester, make a demo payment and download your receipts."),
    unsafe_allow_html=True,
)

# Only ever the signed-in student's own ID, taken from the authenticated session.
student_id = str(user.get("student_id") or "").upper()
record = get_student_safe(student_id)
if not record or not isinstance(record.get("fees"), dict):
    empty_state("🗂️", "Your fee record is not available",
                "We could not find a fee record for your account. Please contact the accounts office.")
    st.stop()

flash = st.session_state.pop("pay_flash", None)
if flash:
    icon = ":material/check_circle:" if flash[0] == "success" else ":material/error:"
    getattr(st, flash[0])(flash[1], icon=icon)

fees = record["fees"]
today = as_of_date(record)
try:
    due = max(0, int(round(float(fees.get("due") or 0))))
except (TypeError, ValueError):
    due = 0
transactions = [t for t in fees.get("transactions") or [] if isinstance(t, dict)]

try:
    from smart_campus_assistant import record_updates as _records
    from smart_campus_assistant.student_data import fee_late_fee

    late = fee_late_fee(due, fees.get("due_date"), today.isoformat())
    modes = list(_records.PAYMENT_MODES)
except Exception:  # noqa: BLE001
    st.error("Payments are not available right now. Please try again later.", icon=":material/error:")
    st.stop()

status = str(fees.get("status") or "").lower()
overdue = status == "overdue" or late["days_overdue"] > 0
if due:
    due_sub = f"{fee_status_label(status)} · {fees.get('semester', '')}".strip(" ·")
    date_value, date_sub = nice_date(fees.get("due_date")), days_text(fees.get("due_date"), today)
    late_sub = (f"{late['days_overdue']} day{'s' if late['days_overdue'] != 1 else ''} after the due date"
                if late["late_fee"] else "Not overdue")
else:
    due_sub, date_value, date_sub, late_sub = "Fully paid", "—", "Nothing due", "Nothing due"
cards = [
    kpi_card_html("Amount due", format_rupees(due), due_sub, BASELINE_COLOR if (due and overdue) else SIMPLE_COLOR),
    kpi_card_html("Due date", date_value, date_sub, BASELINE_COLOR if (due and overdue) else PERSONAL_COLOR),
    kpi_card_html("Late fee so far", format_rupees(late["late_fee"]), late_sub,
                  BASELINE_COLOR if late["late_fee"] else SIMPLE_COLOR),
]
st.markdown(f'<div class="sca-kpis">{"".join(cards)}</div>', unsafe_allow_html=True)

# Receipt of the payment just made (kept for this session until the next payment).
last_no = st.session_state.get("pay_last_receipt")
last = next((t for t in transactions if last_no and t.get("receipt_no") == last_no), None)
if last:
    rows = [("Receipt no", last.get("receipt_no")), ("Date", nice_date(last.get("date"))),
            ("Amount", format_rupees(last.get("amount"))), ("Mode", last.get("mode")),
            ("Balance due", format_rupees(due))]
    lines = "".join(
        f'<div class="line"><span class="k">{html.escape(k)}</span>{html.escape(str(v or ""))}</div>' for k, v in rows
    )
    st.markdown(f'<div class="pay-receipt"><div class="title">✅ Payment receipt (simulated)</div>{lines}</div>',
                unsafe_allow_html=True)
    _receipt_download(record, last, key="pay_dl_last")

section_header("Make a payment")
if not due:
    empty_state("🎉", "Nothing to pay", "Your fees for this semester are fully paid. Your receipts are listed below.")
else:
    st.markdown(
        '<div class="pay-demo">🧪 <b>Simulated demo payment.</b> No money is transferred and no card number, UPI ID '
        "or bank detail is asked for or stored. The payment mode is only a label on the receipt.</div>",
        unsafe_allow_html=True,
    )
    gen = int(st.session_state.get("pay_gen", 0))
    with st.form(f"pay_form_{gen}"):
        st.radio("How much do you want to pay?", [FULL, CUSTOM], key=f"pay_choice_{gen}", horizontal=True,
                 format_func=lambda c: f"{FULL} ({format_rupees(due)})" if c == FULL else CUSTOM)
        c1, c2 = st.columns(2)
        c1.number_input("Custom amount (₹)", min_value=1, max_value=due, value=min(due, 1000), step=1,
                        key=f"pay_amount_{gen}",
                        help='Used only when "Custom amount" is selected. Whole rupees, up to the amount due.')
        c2.selectbox("Payment mode", modes, key=f"pay_mode_{gen}")
        st.form_submit_button("Pay now (demo)", type="primary", icon=":material/payments:",
                              on_click=_pay, args=(student_id, due, gen))
    if late["late_fee"]:
        st.caption(f"The late fee of {format_rupees(late['late_fee'])} is shown for information and is settled at "
                   "the accounts office. A demo payment here reduces the semester due only.")

section_header("Past payments", f"{len(transactions)} payment{'s' if len(transactions) != 1 else ''} this semester")
if not transactions:
    st.caption("No payments recorded this semester.")
for i, txn in enumerate(reversed(transactions)):
    info, action = st.columns([4, 1], vertical_alignment="center")
    sub = " · ".join(str(x) for x in (nice_date(txn.get("date")), txn.get("mode"), txn.get("receipt_no")) if x)
    info.markdown(
        f'<div class="pay-row">{html.escape(format_rupees(txn.get("amount")))}'
        f'<div class="sub">{html.escape(sub)}</div></div>',
        unsafe_allow_html=True,
    )
    with action:
        _receipt_download(record, txn, key=f"pay_dl_{i}", label="Receipt")
