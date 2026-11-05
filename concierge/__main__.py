"""CLI.

    python -m concierge --customer C001 "I was charged an overseas fee, can you refund it?"
    python -m concierge --interactive --customer C001 "..."     # you play the customer
    python -m concierge --unguarded ...                          # baseline: no gates or guardrails
"""

from __future__ import annotations

import argparse
import json

from .agent import handle_ticket
from .models import get_model
from .simulator import SimulatedCustomer


def print_trace(out) -> None:
    for e in out.audit:
        if "say" in e:
            who = "AGENT   " if e["say"] == "agent" else "CUSTOMER"
            print(f"  {who}: {e['text']}")
        elif "workflow" in e:
            for g in e["trace"]:
                mark = "pass" if g["passed"] else "FAIL"
                print(f"      gate {g['gate']:<38} {g['policy_ref']:<6} {g['kind']:<10} {mark}")
        else:
            res = e["result"]
            extra = f" {res.get('policy_refs')}" if res.get("policy_refs") else ""
            extra += f" ask={res['ask']}" if res.get("ask") else ""
            print(f"    tool {e['tool']}({json.dumps(e['args'])[:80]}) -> {res.get('status')}{extra}")
    for ev in out.guardrail_events:
        print(f"    guardrail: {ev}")
    print()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="concierge", description="Run one conversation through the Wattle Money concierge (mock).")
    p.add_argument("message", help="the customer's first message")
    p.add_argument("--customer", default="C001", help="authenticated customer id (C001-C010)")
    p.add_argument("--model", default="scripted", help="'scripted' (offline, default) or 'claude[:model-id]'")
    p.add_argument("--unguarded", action="store_true", help="baseline: switch off workflow gates and guardrails")
    p.add_argument("--interactive", action="store_true", help="answer the agent's questions yourself")
    p.add_argument("--says-no", action="store_true", help="simulated customer declines confirmations / hasn't contacted merchant")
    p.add_argument("--trace", action="store_true", help="print the conversation, tool calls and workflow gates")
    a = p.parse_args(argv)

    if a.interactive:
        def customer(ask):
            print(f"\nAGENT asks ({(ask or {}).get('type')}). Your reply (blank = go quiet): ", end="")
            return input().strip() or None
    else:
        facts = {"confirms": not a.says_no, "contacted_merchant": not a.says_no}
        customer = SimulatedCustomer("cli", facts)

    out = handle_ticket(a.message, a.customer, model=get_model(a.model), guarded=not a.unguarded, customer=customer)
    if a.trace or a.interactive:
        print_trace(out)
    print(f"ACTION : {out.action}" + (f" ({out.txn_id})" if out.txn_id else "") + (f" -> {out.queue}" if out.queue else ""))
    print(f"CITES  : {', '.join(out.cited) or '-'}")
    print(f"REPLY  : {out.reply}")


if __name__ == "__main__":
    main()
