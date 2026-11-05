"""The concierge harness: input screen -> agent/tool loop -> output check."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import guardrails, policy
from .data import MockBank
from .models import Final, ToolCall, get_model
from .models.scripted import escalation_reply
from .tools import Toolbox

MAX_STEPS = 10


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


def _escalate(tools: Toolbox, queue: str, ref: str, summary: str, events: list[str], why: str = "") -> str:
    tools.call("escalate_to_human", {"queue": queue, "summary": summary, "policy_ref": ref,
                                     "priority": queue in ("care", "scams")})
    events.append(why or f"escalated to {queue} ({ref})")
    return escalation_reply(queue, ref)


def handle_ticket(text: str, customer_id: str, model=None, guarded: bool = True,
                  bank: MockBank | None = None) -> Outcome:
    model = model or get_model("scripted")
    bank = bank or MockBank.fresh()
    tools = Toolbox(bank, customer_id, guarded=guarded)
    events: list[str] = []
    reply, kind = "", "inform"

    # 1. Input screen (guarded only): care/regulatory triggers never reach the model.
    screen = guardrails.input_screen(text) if guarded else guardrails.Screen()
    if screen.injection:
        events.append("input screen: possible prompt injection flagged (CON-2)")
    if screen.forced:
        reason, queue, ref = screen.forced
        reply = _escalate(tools, queue, ref, text, events, f"input screen: {reason} -> forced escalation to {queue} ({ref})")
        kind = "done"
    else:
        # 2. Agent loop.
        model.start(text, customer_id)
        last = None
        for _ in range(MAX_STEPS):
            step = model.next_step(last)
            if isinstance(step, Final):
                reply, kind = step.reply, step.kind
                break
            assert isinstance(step, ToolCall)
            last = tools.call(step.name, step.args)
        else:
            reply = _escalate(tools, "general", "ESC-5", text, events, "agent hit step limit -> escalated (ESC-5)")
            kind = "done"

    escalated = any(e.get("tool") == "escalate_to_human" and (e.get("result") or {}).get("status") == "ok"
                    for e in tools.audit)

    # 3. Output check (guarded only): block unsafe replies and fail safe to a human.
    if guarded:
        if screen.injection and "[CON-2]" not in reply:
            reply += " I can only help within our published policies. [CON-2]"
        check = guardrails.check_reply(reply, tools.audit, escalated)
        if not check.ok:
            events.extend(f"output check: {v}" for v in check.violations)
            if not escalated:
                reply = _escalate(tools, "general", "ESC-5", text, events, "output check failed -> escalated (ESC-5)")
                escalated = True
            else:
                reply = escalation_reply(next(e["args"]["queue"] for e in tools.audit
                                              if e.get("tool") == "escalate_to_human"), "ESC-5")

    # 4. Classify the outcome from what actually happened, not from what the model said.
    action, txn_id, queue = kind if kind in ("decline", "inform") else "inform", None, None
    effects = tools.side_effects
    for fx in effects:
        if fx["type"] == "refund":
            action, txn_id = "refund_fee", fx["txn_id"]
        elif fx["type"] == "dispute":
            action, txn_id = "open_dispute", fx["txn_id"]
    if escalated:
        handoffs = [e for e in tools.audit if e.get("tool") == "escalate_to_human" and e["result"].get("status") == "ok"]
        action, queue = "escalate", handoffs[-1]["args"]["queue"]

    return Outcome(action=action, reply=reply, cited=policy.citations_in(reply), txn_id=txn_id, queue=queue,
                   side_effects=list(effects), audit=tools.audit, guardrail_events=events)
