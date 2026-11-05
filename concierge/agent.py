"""The concierge harness: input screen -> agent/tool loop (multi-turn) -> output check.

The harness owns the conversation transcript. Tools read it (e.g. "did the customer
actually say yes after the disclosure?"), so the agent can't claim a confirmation
that never happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import guardrails, policy
from .data import MockBank
from .models import Final, ToolCall, get_model
from .models.scripted import escalation_reply
from .simulator import SimulatedCustomer
from .tools import Toolbox

MAX_STEPS = 16


@dataclass
class Outcome:
    action: str  # refund_fee | open_dispute | escalate | decline | inform
    reply: str
    cited: list[str]
    txn_id: str | None = None
    queue: str | None = None
    side_effects: list[dict] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)
    guardrail_events: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)

    @property
    def agent_turns(self) -> int:
        return sum(t["role"] == "agent" for t in self.transcript)


def handle_ticket(text: str, customer_id: str, model=None, guarded: bool = True, bank: MockBank | None = None,
                  customer: SimulatedCustomer | Callable[[dict | None], str | None] | None = None) -> Outcome:
    model = model or get_model("scripted")
    bank = bank or MockBank.fresh()
    customer = customer or SimulatedCustomer("adhoc")
    answer = customer.reply if isinstance(customer, SimulatedCustomer) else customer
    transcript: list[dict] = [{"role": "customer", "text": text}]
    tools = Toolbox(bank, customer_id, guarded=guarded, transcript=transcript)
    events: list[str] = []

    def escalate(queue: str, ref: str, why: str) -> Final:
        tools.call("escalate_to_human", {"queue": queue, "summary": " / ".join(t["text"] for t in transcript if t["role"] == "customer"),
                                         "policy_ref": ref, "priority": queue in ("care", "scams")})
        events.append(why)
        return Final("done", escalation_reply(queue, ref))

    def screen(msg: str) -> Final | None:
        """Input screen on every customer message (guarded only)."""
        if not guarded:
            return None
        s = guardrails.input_screen(msg)
        if s.injection:
            events.append("input screen: possible prompt injection flagged (CON-2)")
        if s.forced:
            reason, queue, ref = s.forced
            return escalate(queue, ref, f"input screen: {reason} -> forced escalation to {queue} ({ref})")
        return None

    final = screen(text)
    if final is None:
        model.start(text, customer_id)
        last: dict | None = None
        for _ in range(MAX_STEPS):
            step = model.next_step(last)
            if isinstance(step, ToolCall):
                last = tools.call(step.name, step.args)
                continue
            assert isinstance(step, Final)
            if step.kind != "ask":
                final = step
                break
            # The agent asked the customer something: send it, wait for the reply.
            if guarded and not guardrails.check_message(step.reply).ok:
                final = escalate("general", "ESC-5", "output check failed on a question -> escalated (ESC-5)")
                break
            transcript.append({"role": "agent", "text": step.reply, "ask": step.ask})
            tools.audit.append({"say": "agent", "text": step.reply})
            reply = answer(step.ask)
            if reply is None:
                final = Final("inform", "I'll leave this here for now. Just message us whenever you're ready and we'll pick it up. [ESC-5]")
                events.append("customer went quiet")
                break
            transcript.append({"role": "customer", "text": reply})
            tools.audit.append({"say": "customer", "text": reply})
            final = screen(reply)
            if final is not None:
                break
            last = {"customer_reply": reply}
        else:
            final = escalate("general", "ESC-5", "agent hit step limit -> escalated (ESC-5)")

    def escalated() -> bool:
        return any(e.get("tool") == "escalate_to_human" and (e.get("result") or {}).get("status") == "ok" for e in tools.audit)

    reply, kind = final.reply, final.kind
    if guarded:
        if any("injection" in e for e in events) and "[CON-2]" not in reply:
            reply += " I can only help within our published policies. [CON-2]"
        check = guardrails.check_reply(reply, tools.audit, escalated())
        if not check.ok:
            events.extend(f"output check: {v}" for v in check.violations)
            if not escalated():
                reply = escalate("general", "ESC-5", "output check failed -> escalated (ESC-5)").reply
            else:
                q = [e["args"]["queue"] for e in tools.audit if e.get("tool") == "escalate_to_human"][-1]
                reply = escalation_reply(q, "ESC-5")
            kind = "done"
    transcript.append({"role": "agent", "text": reply})

    # Classify the outcome from what actually happened, not from what the model said.
    action, txn_id, queue = (kind if kind in ("decline", "inform") else "inform"), None, None
    tools.audit.append({"say": "agent", "text": reply})
    for fx in tools.side_effects:
        if fx["type"] == "refund":
            action, txn_id = "refund_fee", fx["txn_id"]
        elif fx["type"] == "dispute":
            action, txn_id = "open_dispute", fx["txn_id"]
    if escalated():
        handoffs = [e for e in tools.audit if e.get("tool") == "escalate_to_human" and e["result"].get("status") == "ok"]
        action, queue = "escalate", handoffs[-1]["args"]["queue"]

    return Outcome(action=action, reply=reply, cited=policy.citations_in(reply), txn_id=txn_id, queue=queue,
                   side_effects=list(tools.side_effects), audit=tools.audit, guardrail_events=events, transcript=transcript)
