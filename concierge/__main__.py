"""CLI: python -m concierge --customer C001 "I was charged an overseas fee, can you refund it?" """

from __future__ import annotations

import argparse
import json

from .agent import handle_ticket
from .models import get_model


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="concierge", description="Run one ticket through the Wattle Money concierge (mock).")
    p.add_argument("message", help="the customer's message")
    p.add_argument("--customer", default="C001", help="authenticated customer id (C001-C010)")
    p.add_argument("--model", default="scripted", help="'scripted' (offline, default) or 'claude[:model-id]'")
    p.add_argument("--unguarded", action="store_true", help="baseline: switch off workflow gates and guardrails")
    p.add_argument("--trace", action="store_true", help="print the tool calls and workflow gate trace")
    a = p.parse_args(argv)

    out = handle_ticket(a.message, a.customer, model=get_model(a.model), guarded=not a.unguarded)
    if a.trace:
        for e in out.audit:
            if "workflow" in e:
                for g in e["trace"]:
                    mark = "pass" if g["passed"] else "FAIL"
                    print(f"    gate {g['gate']:<32} {g['policy_ref']:<6} {g['kind']:<9} {mark}")
            else:
                res = e["result"]
                print(f"  tool {e['tool']}({json.dumps(e['args'])[:90]}) -> {res.get('status')}"
                      + (f" {res.get('policy_refs')}" if res.get("policy_refs") else ""))
        for ev in out.guardrail_events:
            print(f"  guardrail: {ev}")
        print()
    print(f"ACTION : {out.action}" + (f" ({out.txn_id})" if out.txn_id else "") + (f" -> {out.queue}" if out.queue else ""))
    print(f"CITES  : {', '.join(out.cited) or '-'}")
    print(f"REPLY  : {out.reply}")


if __name__ == "__main__":
    main()
