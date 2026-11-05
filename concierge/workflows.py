"""Customer policy as explicit, deterministic workflow graphs.

This is the "pocket of determinism" layer. The agent can *ask* for a refund or a
dispute at any time, but the operation only runs if every gate passes against
real system state. The agent's claims are never trusted. Each gate cites one
policy line, so every decision is auditable.

There are two kinds of gate:
  * rule:      checked against system data (dates, amounts, account flags)
  * judgement: checked against something the agent extracted from the
               conversation (e.g. "has the customer contacted the merchant?").
               These are recorded as agent-attested in the audit trail, because
               this is where an LLM's judgement enters the decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .data import TODAY, Account, Txn

REFUNDABLE_FEES = {"overseas_fee", "late_fee", "dishonour_fee"}
FEE_LIMIT = 50.00
DISPUTE_LIMIT = 5_000.00
MERCHANT_DISPUTE_TYPES = {"not_received", "not_as_described", "cancelled", "duplicate"}
DISPUTE_TYPES = MERCHANT_DISPUTE_TYPES | {"unauthorised", "scam"}


@dataclass
class Ctx:
    account: Account
    txn: Txn | None
    args: dict


@dataclass
class Gate:
    name: str
    policy_ref: str
    check: Callable[[Ctx], bool]
    on_fail: str  # "decline" | "inform" | "escalate:<queue>"
    message: Callable[[Ctx], str]
    kind: str = "rule"  # "rule" | "judgement"


@dataclass
class WorkflowResult:
    status: str  # "approved" | "conditions_not_met" | "escalation_required"
    policy_refs: list[str]
    message: str = ""
    queue: str | None = None
    failed_gate: str | None = None
    trace: list[dict] = field(default_factory=list)

    def as_tool_result(self) -> dict:
        out = {"status": self.status, "policy_refs": self.policy_refs, "message": self.message}
        if self.queue:
            out["queue"] = self.queue
        if self.failed_gate:
            out["failed_gate"] = self.failed_gate
        return out


@dataclass
class Workflow:
    name: str
    gates: list[Gate]
    approve_refs: list[str]

    def run(self, ctx: Ctx) -> WorkflowResult:
        trace = []
        for g in self.gates:
            ok = bool(g.check(ctx))
            trace.append({"gate": g.name, "policy_ref": g.policy_ref, "kind": g.kind, "passed": ok})
            if not ok:
                if g.on_fail.startswith("escalate:"):
                    status, queue = "escalation_required", g.on_fail.split(":", 1)[1]
                else:
                    status, queue = "conditions_not_met", None
                return WorkflowResult(status, [g.policy_ref], g.message(ctx), queue, g.name, trace)
        return WorkflowResult("approved", list(self.approve_refs), "", None, None, trace)


def _days_since(iso: str) -> int:
    return (TODAY - date.fromisoformat(iso)).days


def _verified(ctx: Ctx) -> bool:
    return ctx.account.identity_verified and not ctx.account.security_hold


VERIFY_GATE = Gate(
    "identity_verified_and_no_hold", "ESC-3", _verified, "escalate:verification",
    lambda c: "I need to pass this to our Verification team before I can help with transactions on this account.",
)

TXN_EXISTS = Gate(
    "transaction_belongs_to_customer", "ESC-5",
    lambda c: c.txn is not None and c.txn.customer_id == c.account.customer_id,
    "escalate:general",
    lambda c: "I couldn't match that to a transaction on your account, so I'm passing it to a specialist.",
)

# --------------------------------------------------------------------- fee refund
FEE_REFUND = Workflow(
    "fee_refund",
    gates=[
        VERIFY_GATE,
        Gate("is_refundable_fee", "FEE-1",
             lambda c: c.txn is not None and c.txn.customer_id == c.account.customer_id
             and c.txn.kind == "fee" and c.txn.fee_code in REFUNDABLE_FEES,
             "decline",
             lambda c: "only overseas transaction, late payment and dishonour fees can be refunded as a goodwill gesture, "
                       "and I couldn't find an eligible fee matching your request"),
        Gate("amount_equals_fee", "FEE-1",
             lambda c: c.args.get("amount") in (None, c.txn.amount),
             "decline",
             lambda c: f"a goodwill refund can only be for the fee charged (${c.txn.amount:.2f})"),
        Gate("account_in_good_standing", "FEE-4", lambda c: not c.account.in_collections, "decline",
             lambda c: "goodwill refunds aren't available while an account is in collections"),
        Gate("fee_within_60_days", "FEE-5", lambda c: _days_since(c.txn.date) <= 60, "decline",
             lambda c: f"goodwill refunds are only available for fees charged in the last 60 days, and this fee was charged on {c.txn.date}"),
        Gate("one_refund_per_12_months", "FEE-2",
             lambda c: c.account.last_goodwill_refund is None or _days_since(c.account.last_goodwill_refund) > 365,
             "decline",
             lambda c: "we can only offer one goodwill fee refund in any 12-month period, and one has already been applied to your account"),
        Gate("within_concierge_limit", "FEE-3", lambda c: c.txn.amount <= FEE_LIMIT, "escalate:team_lead",
             lambda c: f"refunds over ${FEE_LIMIT:.0f} need a Team Lead to review"),
    ],
    approve_refs=["FEE-1", "FEE-6"],
)

# ------------------------------------------------------------------- card dispute
DISPUTE = Workflow(
    "card_dispute",
    gates=[
        VERIFY_GATE,
        TXN_EXISTS,
        Gate("not_a_self_initiated_transfer", "DSP-7", lambda c: c.txn.kind != "transfer", "escalate:scams",
             lambda c: "transfers you sent yourself can't be charged back like a card payment, but our Scams team can act quickly to try to recover funds"),
        Gate("is_card_transaction", "ESC-5", lambda c: c.txn.kind == "card", "escalate:general",
             lambda c: "that isn't a card transaction, so I'm passing it to a specialist"),
        Gate("no_existing_dispute", "DSP-6", lambda c: c.txn.dispute is None, "inform",
             lambda c: f"there's already a dispute ({c.txn.dispute['id']}) on this transaction. Its status: {c.txn.dispute['status']}"),
        Gate("transaction_settled", "DSP-4", lambda c: c.txn.status == "settled", "decline",
             lambda c: "this transaction is still pending, and pending transactions can't be disputed. Pending charges often drop off on their own. "
                       "If it settles, message us and we can look again (usually 3-5 business days)"),
        Gate("within_120_days", "DSP-2", lambda c: _days_since(c.txn.date) <= 120, "decline",
             lambda c: f"card disputes must be raised within 120 days, and this transaction was on {c.txn.date}"),
        Gate("valid_dispute_type", "ESC-5", lambda c: c.args.get("dispute_type") in DISPUTE_TYPES, "escalate:general",
             lambda c: "I want to make sure this is handled correctly, so I'm passing it to a specialist", kind="judgement"),
        Gate("scam_routed_to_scams", "DSP-7", lambda c: c.args.get("dispute_type") != "scam", "escalate:scams",
             lambda c: "our Scams team handles this, as a priority", kind="judgement"),
        Gate("merchant_contacted_first", "DSP-3",
             lambda c: c.args.get("dispute_type") not in MERCHANT_DISPUTE_TYPES or c.args.get("merchant_contacted") is True,
             "decline",
             lambda c: f"for this kind of dispute you'll need to contact {c.txn.merchant} first. Most merchants resolve it directly. "
                       "If they don't, come back to us and we'll open a dispute",
             kind="judgement"),
        Gate("under_dispute_limit", "DSP-5", lambda c: c.txn.amount <= DISPUTE_LIMIT, "escalate:disputes",
             lambda c: f"disputes over ${DISPUTE_LIMIT:,.0f} are handled by our Disputes team"),
    ],
    approve_refs=["INF-2"],
)
