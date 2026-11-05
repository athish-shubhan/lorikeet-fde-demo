"""A deterministic, offline stand-in for the LLM.

It does two jobs an LLM would do in production:
  1. Understanding: intent, the transaction the customer means, and the
     judgement inputs (dispute type, has the merchant been contacted?).
     This is plain keyword/regex NLU, so it is brittle on purpose: it shows
     where the deterministic layer has to catch an imperfect model.
  2. Tool policy: which tools to call, in what order.

It is deliberately *eager*. When the customer asks for a refund it calls
refund_fee, and it doesn't pre-check policy itself. That mirrors the
failure mode the workflows exist for (see README, "Design").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import Final, ToolCall

INTENTS: list[tuple[str, re.Pattern]] = [
    # order matters: first match wins
    ("other_customer", re.compile(
        r"\b(husband|wife|partner|son|daughter|mum|mom|dad|mother|father|brother|sister|friend)'?s?\b.{0,40}\b(account|balance|transactions?)\b"
        r"|\bcustomer (number|id) (is )?C\d{3}\b", re.I)),
    ("human", re.compile(r"\b(real person|a human|speak to (someone|a person|an agent)|talk to (someone|a person|a human)|call me)\b", re.I)),
    ("dispute_status", re.compile(r"\b(status|update|what'?s happening|any news|progress)\b.{0,60}\b(dispute|claim|case)\b", re.I)),
    ("scam", re.compile(r"\b(scam\w*|scammer|pretend\w*|impersonat\w*|said (they|he|she) (was|were) from)\b", re.I)),
    ("fee_refund", re.compile(
        r"\b(refund\w*|waive\w*|reverse\w*|remove\w*|take (it |that )?off|credit\w* back|get (it |that )?back)\b.{0,60}\bfees?\b"
        r"|\bfees?\b.{0,80}\b(refund\w*|waive\w*|reverse\w*|remove\w*|take (it |that )?off|back)\b"
        r"|\brefund \$\d", re.I)),
    ("dispute", re.compile(
        r"\b(didn'?t make|did not make|never made|not mine|don'?t recogni[sz]e|someone (used|has|got)|stolen|"
        r"never (arrived|came|received)|hasn'?t arrived|not received|didn'?t (arrive|receive|get)|faulty|broken|not as described|"
        r"cancel+ed|charged (me )?twice|double[- ]charged|duplicate|dispute|chargeback|not authori[sz]ed|unauthori[sz]ed)\b", re.I)),
    ("info", re.compile(r"^\s*(what|how much|how long|how do|when)\b", re.I)),
]

DISPUTE_TYPE_RULES: list[tuple[str, re.Pattern]] = [
    ("scam", re.compile(r"\bscam|pretend|impersonat|said (they|he|she) (was|were) from", re.I)),
    ("unauthorised", re.compile(r"didn'?t make|did not make|never made|not mine|don'?t recogni|someone (used|has|got)|stolen|unauthori|not authori", re.I)),
    ("duplicate", re.compile(r"charged (me )?twice|double[- ]charged|duplicate", re.I)),
    ("cancelled", re.compile(r"cancel", re.I)),
    ("not_received", re.compile(r"never (arrived|came|received)|hasn'?t arrived|not received|didn'?t (arrive|receive|get)", re.I)),
    ("not_as_described", re.compile(r"faulty|broken|not as described|damaged|doesn'?t work", re.I)),
]

CONTACTED = re.compile(
    r"\b(i'?ve |i have |i |already )?(contacted|emailed|called|rang|messaged|spoke to|spoken to|reached out to|asked|complained to)\b"
    r".{0,25}\b(them|the (seller|merchant|store|shop|host|company|retailer)|\w+ (support|team))\b"
    r"|\b(they|the (seller|merchant|store|shop|host)) (refused|won'?t|wouldn'?t|ignored|didn'?t reply|haven'?t replied)", re.I)
NOT_CONTACTED = re.compile(r"\b(haven'?t|have not|didn'?t|did not|not yet)\b.{0,15}\b(contact|email|call|message|spoke|spoken|reach)", re.I)

FEE_HINTS = {
    "overseas_fee": re.compile(r"overseas|international|foreign|currency conversion", re.I),
    "late_fee": re.compile(r"\blate\b", re.I),
    "dishonour_fee": re.compile(r"dishono|bounced|failed payment", re.I),
}
AMOUNT = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")
CUSTOMER_REF = re.compile(r"\bC\d{3}\b")


@dataclass
class Understanding:
    intent: str
    amounts: list[float] = field(default_factory=list)
    dispute_type: str | None = None
    merchant_contacted: bool = False
    fee_code: str | None = None
    other_customer: str | None = None


def understand(text: str, session_customer: str) -> Understanding:
    intent = next((name for name, pat in INTENTS if pat.search(text)), "unknown")
    u = Understanding(intent=intent)
    u.amounts = [float(a.replace(",", "")) for a in AMOUNT.findall(text)]
    u.dispute_type = next((t for t, pat in DISPUTE_TYPE_RULES if pat.search(text)), None)
    u.merchant_contacted = bool(CONTACTED.search(text)) and not NOT_CONTACTED.search(text)
    u.fee_code = next((c for c, pat in FEE_HINTS.items() if pat.search(text)), None)
    refs = [r for r in CUSTOMER_REF.findall(text) if r != session_customer]
    u.other_customer = refs[0] if refs else None
    return u


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2}


def match_txn(text: str, txns: list[dict], u: Understanding, kinds: set[str]) -> dict | None:
    """Pick the transaction the customer most likely means. Ties go to the most recent."""
    words = _tokens(text)
    best, best_score = None, 0.0
    for t in txns:  # newest first
        if t["kind"] not in kinds:
            continue
        score = 0.0
        score += 2 * len(words & _tokens(t["merchant"] or t["description"]))
        if any(abs(a - t["amount"]) < 0.005 for a in u.amounts):
            score += 3
        if t["kind"] == "fee" and u.fee_code and t["fee_code"] == u.fee_code:
            score += 3
        if t["kind"] == "transfer" and u.intent == "scam":
            score += 2
        # Judgement on ties: in a duplicate charge the extra one is the newest; otherwise
        # prefer a settled charge (the money has actually left the account).
        tie_wins = (score == best_score and best is not None and u.dispute_type != "duplicate"
                    and t["status"] == "settled" and best["status"] != "settled")
        if score > best_score or tie_wins:
            best, best_score = t, score
    if best is None and len([t for t in txns if t["kind"] in kinds]) == 1:
        best = next(t for t in txns if t["kind"] in kinds)
    return best


QUEUE_NAMES = {"care": "Customer Care", "hardship": "Financial Hardship", "complaints": "Complaints",
               "general": "Support", "team_lead": "Team Lead", "disputes": "Disputes", "scams": "Scams",
               "verification": "Verification"}


def escalation_reply(queue: str, ref: str, message: str = "") -> str:
    lead = {
        "care": "Thank you for telling me, and I'm sorry you're dealing with this. Your safety matters most. ",
        "hardship": "I'm sorry things are tough right now. ",
        "complaints": "I'm sorry we've let you down. ",
        "scams": "Thank you for letting us know quickly. That really helps. ",
    }.get(queue, "")
    why = f"{message[0].upper()}{message[1:].rstrip('.')}. " if message and "pass" not in message else ""
    return (f"{lead}{why}I've passed this to our {QUEUE_NAMES[queue]} team with a full summary, so you won't need to "
            f"repeat yourself, and they'll be in touch. [{ref}]")


class ScriptedModel:
    name = "scripted"

    def start(self, ticket: str, customer_id: str) -> None:
        self.ticket, self.customer_id = ticket, customer_id
        self.u = understand(ticket, customer_id)
        self._gen = self._plan()
        self._primed = False

    def next_step(self, last_result: dict | None) -> ToolCall | Final:
        if not self._primed:
            self._primed = True
            return next(self._gen)
        return self._gen.send(last_result)

    # -- plans (generators: yield ToolCall, receive tool result) -------------------
    def _plan(self):
        u, t = self.u, self.ticket
        if u.intent in ("human",):
            yield ToolCall("escalate_to_human", {"queue": "general", "summary": t, "policy_ref": "ESC-4"})
            yield Final("done", escalation_reply("general", "ESC-4"))
            return
        if u.intent == "unknown":
            yield ToolCall("escalate_to_human", {"queue": "general", "summary": t, "policy_ref": "ESC-5"})
            yield Final("done", "I want to make sure I get this right, so I'm passing it to a specialist. "
                        + escalation_reply("general", "ESC-5"))
            return
        if u.intent == "other_customer":
            res = yield ToolCall("lookup_account", {"customer_id": u.other_customer or "C002"})
            if res["status"] == "ok":  # baseline only: the eager model happily reads it out
                bal = res["account"]["balance"]
                yield Final("inform", f"That account's balance is ${bal:,.2f}. [PRV-1]")
            else:
                yield Final("decline", "I'm sorry, I can only discuss your own account, so I can't share details of "
                            "anyone else's. They're welcome to contact us directly. [PRV-1]")
            return
        if u.intent == "info":
            res = yield ToolCall("search_policy", {"query": t})
            hits = res.get("results", [])
            if not hits:
                yield ToolCall("escalate_to_human", {"queue": "general", "summary": t, "policy_ref": "ESC-5"})
                yield Final("done", escalation_reply("general", "ESC-5"))
                return
            top = hits[0]
            yield Final("inform", f"Here's what our policy says: {top['text']} [{top['policy_ref']}]")
            return

        # Everything below needs the account and transactions.
        yield ToolCall("lookup_account", {"customer_id": self.customer_id})
        txres = yield ToolCall("get_transactions", {})
        if txres["status"] == "escalation_required":
            yield from self._escalate_from(txres)
            return
        txns = txres["transactions"]

        if u.intent == "dispute_status":
            disputed = [x for x in txns if x.get("dispute")]
            target = match_txn(t, disputed, u, {"card"}) if disputed else None
            if target is None:
                yield ToolCall("escalate_to_human", {"queue": "general", "summary": t, "policy_ref": "ESC-5"})
                yield Final("done", escalation_reply("general", "ESC-5"))
                return
            d = target["dispute"]
            yield Final("inform", f"Your dispute {d['id']} on the {target['merchant']} charge (${target['amount']:.2f}) is "
                        f"{d['status']}. You don't need to do anything, and we'll update you. [DSP-6] [INF-2]")
            return

        if u.intent == "fee_refund":
            target = match_txn(t, txns, u, {"fee"})
            amount = u.amounts[0] if u.amounts else (target["amount"] if target else None)
            res = yield ToolCall("refund_fee", {"txn_id": target["txn_id"] if target else None, "amount": amount,
                                                "reason": "customer request"})
            yield from self._handle_write(res, verb="refund that fee")
            return

        if u.intent in ("dispute", "scam"):
            kinds = {"card", "transfer"} if u.intent == "scam" else {"card"}
            target = match_txn(t, txns, u, kinds)
            dtype = u.dispute_type or ("scam" if u.intent == "scam" else "not_as_described")
            res = yield ToolCall("open_dispute", {"txn_id": target["txn_id"] if target else None,
                                                  "dispute_type": dtype, "merchant_contacted": u.merchant_contacted})
            yield from self._handle_write(res, verb="open a dispute")
            return

        yield ToolCall("escalate_to_human", {"queue": "general", "summary": t, "policy_ref": "ESC-5"})
        yield Final("done", escalation_reply("general", "ESC-5"))

    def _escalate_from(self, res: dict):
        ref = res["policy_refs"][0]
        yield ToolCall("escalate_to_human", {"queue": res["queue"], "summary": self.ticket, "policy_ref": ref,
                                             "priority": res["queue"] in ("scams", "care")})
        yield Final("done", escalation_reply(res["queue"], ref, res.get("message", "")))

    def _handle_write(self, res: dict, verb: str):
        refs = " ".join(f"[{r}]" for r in res.get("policy_refs", []))
        status = res.get("status")
        if status == "ok":
            if "refunded" in res:
                yield Final("done", f"Done. I've refunded ${res['refunded']:.2f} to your account as a one-off goodwill "
                            f"gesture. Just so you know, we can do this once in any 12-month period. {refs}")
            else:
                lock = "I've locked your card to keep you safe, and a replacement is on its way. " if res.get("card_locked") else ""
                yield Final("done", f"{lock}I've opened dispute {res['dispute_id']}. We'll acknowledge it within 1 "
                            f"business day, and most disputes are resolved within 30 days. {refs}")
        elif status == "escalation_required":
            yield from self._escalate_from(res)
        elif status in ("conditions_not_met",):
            kind = "inform" if res.get("failed_gate") == "no_existing_dispute" else "decline"
            msg = res.get("message", "")
            if kind == "inform":
                yield Final("inform", f"Good news: {msg}. {refs}")
            else:
                yield Final("decline", f"I'm sorry, I can't {verb} here: {msg}. {refs}")
        else:
            yield ToolCall("escalate_to_human", {"queue": "general", "summary": self.ticket, "policy_ref": "ESC-5"})
            yield Final("done", escalation_reply("general", "ESC-5"))
