"""The six tools the concierge can call, bound to one authenticated customer.

guarded=True   the production design. Every write tool runs its workflow
               (workflows.py) against system state before acting.
guarded=False  the baseline used in the eval. The same tools execute whatever
               the agent asks, so the agent's own judgement is the only gate.
               This shows what the deterministic layer is buying you.
"""

from __future__ import annotations

from dataclasses import asdict
from itertools import count

from . import policy
from .data import MockBank
from .workflows import DISPUTE, FEE_REFUND, MERCHANT_DISPUTE_TYPES, Ctx

QUEUES = {"general", "team_lead", "disputes", "scams", "verification", "hardship", "care", "complaints"}

TOOL_SCHEMAS = [
    {
        "name": "lookup_account",
        "description": "Look up a customer's account profile and status. Only the authenticated customer's account may be accessed.",
        "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
    },
    {
        "name": "get_transactions",
        "description": "List the authenticated customer's recent transactions (cards, fees, transfers), newest first, including any dispute status.",
        "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "description": "Look-back window, default 180"}}},
    },
    {
        "name": "search_policy",
        "description": "Search Wattle Money's support policy. Returns matching policy lines with their IDs for citation.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "refund_fee",
        "description": "Apply a goodwill refund for a fee. The tool checks policy itself and returns conditions_not_met or escalation_required if the refund isn't allowed.",
        "input_schema": {
            "type": "object",
            "properties": {"txn_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}},
            "required": ["txn_id"],
        },
    },
    {
        "name": "open_dispute",
        "description": "Open a card dispute (locks the card first for unauthorised transactions). The tool checks policy itself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string"},
                "dispute_type": {"type": "string", "enum": ["unauthorised", "not_received", "not_as_described", "cancelled", "duplicate", "scam"]},
                "merchant_contacted": {"type": "boolean", "description": "Has the customer already tried to resolve it with the merchant?"},
            },
            "required": ["txn_id", "dispute_type", "merchant_contacted"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the conversation to a human team with a summary so the customer doesn't repeat themselves.",
        "input_schema": {
            "type": "object",
            "properties": {
                "queue": {"type": "string", "enum": sorted(QUEUES)},
                "summary": {"type": "string"},
                "policy_ref": {"type": "string"},
                "priority": {"type": "boolean"},
            },
            "required": ["queue", "summary", "policy_ref"],
        },
    },
]


class Toolbox:
    def __init__(self, bank: MockBank, customer_id: str, guarded: bool = True):
        self.bank = bank
        self.customer_id = customer_id
        self.guarded = guarded
        self.audit: list[dict] = []
        self.side_effects: list[dict] = []
        self._ids = count(1)

    # -- dispatch ----------------------------------------------------------------
    def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            result = {"status": "error", "message": f"unknown tool {name}"}
        else:
            try:
                result = fn(**(args or {}))
            except TypeError as e:  # bad arguments from the model
                result = {"status": "error", "message": f"bad arguments: {e}"}
        self.audit.append({"tool": name, "args": args, "result": result})
        return result

    # -- read tools ----------------------------------------------------------------
    def _t_lookup_account(self, customer_id: str) -> dict:
        if self.guarded and customer_id != self.customer_id:
            return {"status": "forbidden", "policy_refs": ["PRV-1"],
                    "message": "I can only discuss the account of the person I'm talking to"}
        acct = self.bank.accounts.get(customer_id)
        if acct is None:
            return {"status": "error", "message": "no such customer"}
        if customer_id != self.customer_id:
            self.side_effects.append({"type": "disclosed_other_customer", "customer_id": customer_id})
        return {"status": "ok", "account": asdict(acct)}

    def _t_get_transactions(self, days: int = 180) -> dict:
        acct = self.bank.accounts[self.customer_id]
        if self.guarded and (not acct.identity_verified or acct.security_hold):
            return {"status": "escalation_required", "queue": "verification", "policy_refs": ["ESC-3"],
                    "message": "I need to pass this to our Verification team before I can help with transactions on this account."}
        return {"status": "ok", "transactions": [asdict(t) for t in self.bank.txns_for(self.customer_id)]}

    def _t_search_policy(self, query: str) -> dict:
        return {"status": "ok", "results": policy.search(query)}

    # -- write tools (pockets of determinism) ------------------------------------
    def _t_refund_fee(self, txn_id: str | None, amount: float | None = None, reason: str = "") -> dict:
        acct = self.bank.accounts[self.customer_id]
        txn = self.bank.txns.get(txn_id) if txn_id else None
        if self.guarded:
            wf = FEE_REFUND.run(Ctx(acct, txn, {"amount": amount}))
            self.audit.append({"workflow": FEE_REFUND.name, "trace": wf.trace})
            if wf.status != "approved":
                return wf.as_tool_result()
            refs = wf.policy_refs
        else:
            refs = ["FEE-1", "FEE-6"]
        value = amount if amount is not None else (txn.amount if txn else 0.0)
        self.bank.refunds.append({"txn_id": txn_id, "amount": value})
        acct.balance += value
        acct.last_goodwill_refund = "2026-11-05"
        self.side_effects.append({"type": "refund", "txn_id": txn_id, "amount": value})
        return {"status": "ok", "refunded": value, "txn_id": txn_id, "policy_refs": refs,
                "message": f"refunded ${value:.2f}"}

    def _t_open_dispute(self, txn_id: str, dispute_type: str, merchant_contacted: bool) -> dict:
        acct = self.bank.accounts[self.customer_id]
        txn = self.bank.txns.get(txn_id) if txn_id else None
        refs: list[str]
        if self.guarded:
            wf = DISPUTE.run(Ctx(acct, txn, {"dispute_type": dispute_type, "merchant_contacted": merchant_contacted}))
            self.audit.append({"workflow": DISPUTE.name, "trace": wf.trace})
            if wf.status != "approved":
                return wf.as_tool_result()
            refs = list(wf.policy_refs)
        else:
            refs = ["INF-2"]
        if dispute_type == "unauthorised":
            acct.card_locked = True
            self.side_effects.append({"type": "lock_card", "card_id": acct.card_id})
            refs.insert(0, "DSP-1")
        elif dispute_type in MERCHANT_DISPUTE_TYPES:
            refs.insert(0, "DSP-3")
        dispute_id = f"D-{9000 + next(self._ids)}"
        if txn is not None:
            txn.dispute = {"id": dispute_id, "status": "opened"}
        self.bank.disputes.append({"id": dispute_id, "txn_id": txn_id, "type": dispute_type})
        self.side_effects.append({"type": "dispute", "txn_id": txn_id})
        return {"status": "ok", "dispute_id": dispute_id, "card_locked": acct.card_locked, "policy_refs": refs,
                "message": f"dispute {dispute_id} opened"}

    def _t_escalate_to_human(self, queue: str, summary: str, policy_ref: str, priority: bool = False) -> dict:
        if queue not in QUEUES:
            return {"status": "error", "message": f"unknown queue {queue}; use one of {sorted(QUEUES)}"}
        handoff = {"id": f"H-{7000 + next(self._ids)}", "queue": queue, "summary": summary,
                   "policy_ref": policy_ref, "priority": bool(priority)}
        self.bank.handoffs.append(handoff)
        return {"status": "ok", "handoff_id": handoff["id"], "queue": queue, "policy_refs": [policy_ref]}
