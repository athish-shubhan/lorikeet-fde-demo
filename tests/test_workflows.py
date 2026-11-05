"""Each policy gate, tested directly against system state."""

import pytest

from concierge.data import MockBank
from concierge.workflows import DISPUTE, FEE_REFUND, Ctx


@pytest.fixture
def bank():
    return MockBank.fresh()


def run_fee(bank, cust, txn, **args):
    return FEE_REFUND.run(Ctx(bank.accounts[cust], bank.txns.get(txn), args))


def run_dispute(bank, cust, txn, dtype, contacted):
    return DISPUTE.run(Ctx(bank.accounts[cust], bank.txns.get(txn), {"dispute_type": dtype, "merchant_contacted": contacted}))


@pytest.mark.parametrize("cust,txn,status,ref", [
    ("C001", "T1001", "approved", "FEE-1"),
    ("C002", "T2001", "conditions_not_met", "FEE-2"),    # refund within 12 months
    ("C009", "T9001", "escalation_required", "FEE-3"),   # $80 > $50 limit
    ("C003", "T3001", "conditions_not_met", "FEE-4"),    # in collections
    ("C010", "T10001", "conditions_not_met", "FEE-5"),   # 75 days old
    ("C001", "T1005", "conditions_not_met", "FEE-1"),    # a purchase, not a fee
    ("C004", "T4001", "escalation_required", "ESC-3"),   # security hold
])
def test_fee_refund_gates(bank, cust, txn, status, ref):
    r = run_fee(bank, cust, txn)
    assert r.status == status
    assert ref in r.policy_refs


def test_fee_refund_rejects_inflated_amount(bank):
    r = run_fee(bank, "C001", "T1001", amount=500.0)
    assert r.status == "conditions_not_met" and r.failed_gate == "amount_equals_fee"


def test_fee_refund_rejects_other_customers_txn(bank):
    r = run_fee(bank, "C001", "T2001")
    assert r.status == "conditions_not_met"


@pytest.mark.parametrize("cust,txn,dtype,contacted,status,ref", [
    ("C001", "T1003", "unauthorised", False, "approved", None),
    ("C001", "T1004", "not_received", True, "approved", None),
    ("C001", "T1004", "not_received", False, "conditions_not_met", "DSP-3"),
    ("C007", "T7001", "not_received", True, "conditions_not_met", "DSP-2"),
    ("C006", "T6002", "duplicate", True, "conditions_not_met", "DSP-4"),
    ("C006", "T6001", "not_as_described", True, "escalation_required", "DSP-5"),
    ("C008", "T8001", "unauthorised", False, "conditions_not_met", "DSP-6"),
    ("C007", "T7002", "scam", False, "escalation_required", "DSP-7"),
    ("C001", "T1006", "scam", False, "escalation_required", "DSP-7"),
    ("C005", "T5001", "unauthorised", False, "escalation_required", "ESC-3"),
    ("C001", None, "unauthorised", False, "escalation_required", "ESC-5"),
])
def test_dispute_gates(bank, cust, txn, dtype, contacted, status, ref):
    r = run_dispute(bank, cust, txn, dtype, contacted)
    assert r.status == status
    if ref:
        assert r.policy_refs == [ref]


def test_judgement_gates_are_marked_in_trace(bank):
    r = run_dispute(bank, "C001", "T1004", "not_received", True)
    kinds = {g["gate"]: g["kind"] for g in r.trace}
    assert kinds["merchant_contacted_first"] == "judgement"
    assert kinds["within_120_days"] == "rule"
