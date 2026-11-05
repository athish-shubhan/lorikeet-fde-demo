"""A deterministic, offline stand-in for the LLM (v2: classifier NLU + multi-turn).

It does what an LLM does in production:
  1. Understanding (concierge/nlu.py): intent with a confidence score, which
     transaction the customer means, and the judgement inputs (dispute type,
     has the merchant been contacted).
  2. Tool policy: which tool to call next, and when to ask the customer something.

It is deliberately *eager*. When the customer asks for a refund, it calls refund_fee
straight away and lets the tool tell it what's missing ("conditions not met: confirm
with the customer first"). That is the failure mode the deterministic layer is built
to contain, and it's why the guarded and unguarded runs differ.
"""

from __future__ import annotations

import re

from ..nlu import DISPUTE_KNN, Understanding, parse_contacted, understand
from ..workflows import is_affirmative
from .base import Final, ToolCall

FEE_WORDS = re.compile(r"\bfees?\b|dishono|late payment|paying late|overseas|international|foreign|goodwill", re.I)

QUEUE_NAMES = {"care": "Customer Care team", "hardship": "Financial Hardship team", "complaints": "Complaints team",
               "general": "Support team", "team_lead": "Team Lead", "disputes": "Disputes team", "scams": "Scams team",
               "verification": "Verification team"}

SAFETY_INTENTS = {"vulnerability": ("care", "ESC-2"), "hardship": ("hardship", "ESC-1"),
                  "complaint": ("complaints", "ESC-6"), "human": ("general", "ESC-4"),
                  "out_of_scope": ("general", "ESC-5")}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2}


def match_txn(text: str, txns: list[dict], u: Understanding, kinds: set[str]) -> dict | None:
    """Pick the transaction the customer most likely means.

    Judgement on ties: in a duplicate charge the extra one is the newest; otherwise
    prefer a settled charge (the money has actually left the account)."""
    words = _tokens(text)
    best, best_score = None, 0.0
    for t in txns:  # newest first
        if t["kind"] not in kinds:
            continue
        score = 2.0 * len(words & _tokens(t["merchant"] or t["description"]))
        if any(abs(a - t["amount"]) < 0.005 for a in u.amounts):
            score += 3
        if t["kind"] == "fee" and u.fee_code and t["fee_code"] == u.fee_code:
            score += 3
        if t["kind"] == "transfer" and u.intent == "scam":
            score += 2
        tie_wins = (score == best_score and best is not None and u.dispute_type != "duplicate"
                    and t["status"] == "settled" and best["status"] != "settled")
        if score > best_score or tie_wins:
            best, best_score = t, score
    # No evidence linking the message to any transaction -> None (the caller asks, it never guesses).
    return best


def escalation_reply(queue: str, ref: str, message: str = "") -> str:
    lead = {
        "care": "Thank you for telling me, and I'm sorry you're dealing with this. Your safety matters most. ",
        "hardship": "I'm sorry things are tough right now. You're not alone, and there are options. ",
        "complaints": "I'm sorry we've let you down. ",
        "scams": "Thank you for letting us know quickly. That really helps. ",
    }.get(queue, "")
    why = f"{message[0].upper()}{message[1:].rstrip('.')}. " if message and "pass" not in message else ""
    return (f"{lead}{why}I've passed this to our {QUEUE_NAMES[queue]} with a full summary, so you won't need to "
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

    # ------------------------------------------------------------------ helpers
    def _escalate(self, queue: str, ref: str, message: str = ""):
        yield ToolCall("escalate_to_human", {"queue": queue, "summary": self._summary(), "policy_ref": ref,
                                             "priority": queue in ("scams", "care")})
        yield Final("done", escalation_reply(queue, ref, message))

    def _summary(self) -> str:
        return f"[{self.u.intent}, confidence {self.u.confidence}] {self.ticket}"

    # --------------------------------------------------------------------- plan
    def _plan(self):
        u = self.u
        # 1. Unsure what they want: ask once (ESC-5), then re-read with the clarification.
        if u.intent == "unknown":
            reply = yield Final("ask", "Sorry, I want to make sure I get this right. Could you tell me a bit more about "
                                "what's happened, and which transaction or fee it's about? [ESC-5]", {"type": "clarify"})
            extra = (reply or {}).get("customer_reply", "")
            self.ticket = f"{self.ticket} {extra}".strip()
            self.u = u = understand(self.ticket, self.customer_id)
            if u.intent == "unknown":
                yield from self._escalate("general", "ESC-5")
                return

        if u.intent in SAFETY_INTENTS:
            yield from self._escalate(*SAFETY_INTENTS[u.intent])
            return

        if u.intent == "other_customer":
            if u.other_customer:
                res = yield ToolCall("lookup_account", {"customer_id": u.other_customer})
                if res["status"] == "ok":  # baseline only: the eager model reads it out
                    yield Final("inform", f"That account's balance is ${res['account']['balance']:,.2f}. [PRV-1]")
                    return
            yield Final("decline", "I'm sorry, I can only discuss your own account, so I can't share or check anyone "
                        "else's. They're welcome to contact us directly, and we'll help them. [PRV-1]")
            return

        if u.intent == "info":
            res = yield ToolCall("search_policy", {"query": self.ticket, "section": "INF"})
            hits = [h for h in res.get("results", []) if h["policy_ref"].startswith("INF")]
            if not hits:
                yield from self._escalate("general", "ESC-5")
                return
            yield Final("inform", f"Good question. {hits[0]['text']} [{hits[0]['policy_ref']}]")
            return

        # Everything below needs the account and transactions.
        yield ToolCall("lookup_account", {"customer_id": self.customer_id})
        txres = yield ToolCall("get_transactions", {})
        if txres["status"] == "escalation_required":
            yield from self._escalate(txres["queue"], txres["policy_refs"][0], txres.get("message", ""))
            return
        txns = txres["transactions"]

        # Judgement: "refund" with no fee mentioned, naming a merchant, is a dispute.
        if u.intent == "fee_refund" and not FEE_WORDS.search(self.ticket):
            card = match_txn(self.ticket, txns, u, {"card"})
            if card is not None and (_tokens(card["merchant"]) & _tokens(self.ticket)):
                u.intent, u.dispute_type = "dispute", DISPUTE_KNN.scores(self.ticket)[0][0]

        if u.intent == "dispute_status":
            disputed = [x for x in txns if x.get("dispute")]
            target = match_txn(self.ticket, disputed, u, {"card"}) if disputed else None
            if target is None and len(disputed) == 1:  # read-only: their only open dispute is safe to report
                target = disputed[0]
            if target is None:
                yield from self._escalate("general", "ESC-5")
                return
            d = target["dispute"]
            yield Final("inform", f"Your dispute {d['id']} on the {target['merchant']} charge (${target['amount']:.2f}) is "
                        f"{d['status']}. You don't need to do anything, and we'll keep you updated. [DSP-6] [INF-2]")
            return

        if u.intent == "fee_refund":
            target = match_txn(self.ticket, txns, u, {"fee"})
            args = {"txn_id": target["txn_id"] if target else None,
                    "amount": u.amounts[0] if u.amounts else (target["amount"] if target else None),
                    "reason": "customer request"}
            yield from self._write("refund_fee", args, target)
            return

        if u.intent in ("dispute", "scam"):
            kinds = {"card", "transfer"} if u.intent == "scam" else {"card"}
            target = match_txn(self.ticket, txns, u, kinds)
            if target is None:  # disputes are open-ended: never guess the transaction, ask
                reply = yield Final("ask", "Which transaction is this about? The merchant name and amount will help me "
                                    "find it. [ESC-5]", {"type": "which_txn"})
                extra = (reply or {}).get("customer_reply", "")
                hint = understand(extra, self.customer_id)
                u.amounts = u.amounts + hint.amounts
                target = match_txn(f"{self.ticket} {extra}", txns, u, kinds)
                if target is None:
                    yield from self._escalate("general", "ESC-5")
                    return
            args = {"txn_id": target["txn_id"] if target else None,
                    "dispute_type": u.dispute_type or "not_as_described",
                    "merchant_contacted": u.merchant_contacted}
            yield from self._write("open_dispute", args, target)
            return

        yield from self._escalate("general", "ESC-5")

    def _write(self, tool: str, args: dict, target: dict | None):
        """Call a write tool, and do whatever the tool says is missing (ask, decline, escalate)."""
        for _attempt in range(3):
            res = yield ToolCall(tool, args)
            status, refs = res.get("status"), res.get("policy_refs", [])
            cite = " ".join(f"[{r}]" for r in refs)
            if status == "ok":
                if "refunded" in res:
                    yield Final("done", f"Done. I've refunded ${res['refunded']:.2f} to your account as a one-off goodwill "
                                f"gesture. {cite}")
                else:
                    lock = "I've locked your card to keep you safe, and a replacement is on its way. " if res.get("card_locked") else ""
                    yield Final("done", f"{lock}I've opened dispute {res['dispute_id']}. We'll acknowledge it within 1 business "
                                f"day, and most disputes are resolved within 30 days. {cite}")
                return
            if status == "escalation_required":
                yield from self._escalate(res["queue"], refs[0], res.get("message", ""))
                return
            if status == "conditions_not_met" and res.get("ask") == "confirm":
                fee = (target or {}).get("description", "fee").split(" - ")[0].lower()
                reply = yield Final("ask", f"I can refund the ${target['amount']:.2f} {fee} as a one-off goodwill gesture. "
                                    "Just so you know, we can only do this once in any 12-month period [FEE-2]. "
                                    "Would you like me to go ahead? [FEE-6]",
                                    {"type": "confirm", "action": tool, "txn_id": args["txn_id"]})
                if not is_affirmative((reply or {}).get("customer_reply", "")):
                    yield Final("inform", "No problem, I haven't made any changes to your account. [FEE-6]")
                    return
                continue  # retry: the tool re-checks the transcript itself
            if status == "conditions_not_met" and res.get("ask") == "merchant_contacted":
                merchant = (target or {}).get("merchant", "the merchant")
                reply = yield Final("ask", f"Have you already been in touch with {merchant} to try to sort this out? "
                                    "Merchants can often fix it directly. [DSP-3]", {"type": "merchant_contacted"})
                args = {**args, "merchant_contacted": parse_contacted((reply or {}).get("customer_reply", ""))}
                continue
            if status == "conditions_not_met":
                kind = "inform" if res.get("failed_gate") == "no_existing_dispute" else "decline"
                msg = res.get("message", "")
                if kind == "inform":
                    yield Final("inform", f"Good news: {msg}. {cite}")
                else:
                    verb = "refund that" if tool == "refund_fee" else "open a dispute"
                    yield Final("decline", f"I'm sorry, I can't {verb} here: {msg}. {cite}")
                return
            break
        yield from self._escalate("general", "ESC-5")
