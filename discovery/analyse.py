"""Week-one discovery on a ticket export: shadow-replay the concierge, size the opportunity.

    python -m discovery.generate     # synthetic export (skip if you have a real one)
    python -m discovery.analyse      # writes discovery/report.md and discovery/report.json

For every historical ticket, the concierge runs in *shadow mode* (fresh mock state,
simulated customer, no effect on anything) and its outcome is compared with what
the human agent recorded. That answers four questions:

  1. What share of volume could the concierge resolve end to end today, and is it right?
  2. Where can't it yet, and what is blocking each gap (missing API, out of policy scope)?
  3. Where did humans deviate from written policy? (Flagged for the customer's QA lead.
     In this synthetic export we know which human resolutions were seeded as wrong, so we
     can measure the replay's recall. The flagging logic never reads that column.)
  4. Which policy questions came up that the customer needs to decide?
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from concierge.agent import handle_ticket
from concierge.simulator import SimulatedCustomer

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "data" / "historical_tickets.csv"

BLOCKERS = {
    "card_delivery": ("Card delivery status", "read-only card-processor shipment-tracking API, plus a reissue policy line"),
    "app_access": ("App / login access", "identity re-verification flow and a session-reset API; high risk, so keep human until auth is designed"),
    "profile_update": ("Address / profile changes", "profile-update API behind step-up verification (OTP), plus a policy line on proof of address"),
    "credit_limit": ("Credit limit requests", "credit decisioning is a regulated lending decision, so route to humans, don't automate"),
    "account_closure": ("Account closure", "closure workflow: balance payout, direct-debit check, retention offer rules"),
}


def _facts(s: str) -> dict:
    out = {}
    for kv in filter(None, s.split("|")):
        k, v = kv.split("=")
        out[k] = v == "True"
    return out


def _norm(res: str) -> str:
    """'decline:FEE-2' -> 'decline'; 'escalate:hardship' stays; 'human:x' -> 'human'."""
    head, _, tail = res.partition(":")
    return "decline" if head == "decline" else ("human" if head == "human" else res)


def concierge_resolution(out) -> str:
    return f"escalate:{out.queue}" if out.action == "escalate" else out.action


def replay(rows: list[dict]) -> list[dict]:
    results = []
    for r in rows:
        out = handle_ticket(r["message"], r["customer_id"], customer=SimulatedCustomer(r["ticket_id"], _facts(r["facts"])))
        res = concierge_resolution(out)
        results.append({**r, "concierge": res, "concierge_cites": out.cited, "concierge_turns": out.agent_turns,
                        "unsafe_vs_policy": res in ("refund_fee", "open_dispute") and _norm(r["policy_correct_resolution"]) != res})
    return results


def analyse(results: list[dict]) -> dict:
    n = len(results)
    auto = [r for r in results if not r["concierge"].startswith("escalate")]
    auto_correct = [r for r in auto if _norm(r["concierge"]) == _norm(r["policy_correct_resolution"])]
    routed = [r for r in results if r["concierge"].startswith("escalate")]
    routed_right = [r for r in routed if r["concierge"] == r["policy_correct_resolution"]
                    or (r["policy_correct_resolution"].startswith("human:") and r["concierge"] == "escalate:general")]

    # Human deviations: the concierge (citing policy) and the human disagree on an in-scope ticket.
    flags = [r for r in results if not r["human_resolution"].startswith("human:")
             and _norm(r["concierge"]) != _norm(r["human_resolution"]) and r["concierge_cites"]]
    seeded = [r for r in results if r["seeded_human_error"] == "1"]
    caught = [r for r in flags if r["seeded_human_error"] == "1"]

    gaps = defaultdict(lambda: {"tickets": 0, "minutes": 0})
    for r in results:
        if r["human_resolution"].startswith("human:"):
            g = gaps[r["human_resolution"].split(":", 1)[1]]
            g["tickets"] += 1
            g["minutes"] += int(r["handle_minutes"])

    by_type = defaultdict(lambda: {"n": 0, "auto": 0, "correct": 0, "minutes": 0})
    for r in results:
        t = by_type[r["policy_correct_resolution"].split(":")[0] if not r["policy_correct_resolution"].startswith("escalate")
                    else r["policy_correct_resolution"]]
        t["n"] += 1
        t["minutes"] += int(r["handle_minutes"])
        if not r["concierge"].startswith("escalate"):
            t["auto"] += 1
            t["correct"] += _norm(r["concierge"]) == _norm(r["policy_correct_resolution"])

    days = 90
    return {
        "n": n, "auto": len(auto), "auto_correct": len(auto_correct),
        "auto_minutes": sum(int(r["handle_minutes"]) for r in auto_correct),
        "routed": len(routed), "routed_right": len(routed_right),
        "unsafe": sum(r["unsafe_vs_policy"] for r in results),
        "flags": len(flags), "seeded": len(seeded), "caught": len(caught),
        "false_flags": len(flags) - len(caught),
        "flag_examples": [{k: r[k] for k in ("ticket_id", "message", "human_resolution", "concierge", "concierge_cites", "seeded_human_error")}
                          for r in flags[:12]],
        "deviation_kinds": Counter(f"human {r['human_resolution']} vs policy {r['concierge']}" for r in caught).most_common(),
        "gaps": dict(sorted(gaps.items(), key=lambda kv: -kv[1]["tickets"])),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1]["n"])), "days": days,
        "wrong_auto": [{k: r[k] for k in ("ticket_id", "message", "policy_correct_resolution", "concierge")}
                       for r in auto if r not in auto_correct],
    }


def _p(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def render(s: dict) -> str:
    month = 30 / s["days"]
    L = [
        "# Discovery report: Wattle Money support queue (90-day export, SYNTHETIC)",
        "",
        "> The export is generated by `discovery/generate.py` from templates about the mock customers. It shows the",
        "> *method* I'd run in week one on a real export. The numbers describe this synthetic queue, not a real business.",
        "",
        "## Headline",
        "",
        f"- **{_p(s['auto_correct'], s['n'])} of tickets** could be resolved end to end by the concierge today, with the outcome matching "
        f"written policy. That's about **{s['auto_minutes'] * month / 60:.0f} agent-hours a month** at recorded handle times.",
        f"- {_p(s['routed'], s['n'])} go to a human. {_p(s['routed_right'], s['routed'])} of those reach the right queue with a summary "
        "(out-of-scope requests go to the general queue).",
        f"- **{s['unsafe']} unsafe action{'s' if s['unsafe'] != 1 else ''}** in replay (a refund or dispute the ticket's correct outcome didn't call for). "
        f"{len(s['wrong_auto'])} auto-resolution{'s' if len(s['wrong_auto']) != 1 else ''} didn't match the policy-correct outcome"
        + (" (listed below)." if s["wrong_auto"] else "."),
        f"- **{s['flags']} human resolutions disagree with written policy** and are flagged for QA review. In this synthetic export "
        f"{s['seeded']} human errors were seeded: the replay caught **{_p(s['caught'], s['seeded'])}**, with {s['false_flags']} false flags.",
        "",
        "## Where AI can resolve today, by policy-correct outcome",
        "",
        "| Correct outcome | Tickets | Concierge resolved | Of those, correct | Handle time (min, total) |",
        "|---|---|---|---|---|",
    ]
    for k, v in s["by_type"].items():
        L.append(f"| {k} | {v['n']} | {v['auto']} | {v['correct']} | {v['minutes']} |")
    L += ["", "## Where it can't yet, and what's blocking it", "",
          "| Request type | Tickets (90d) | Agent-hours / month | Blocker → what we'd need |", "|---|---|---|---|"]
    for k, v in s["gaps"].items():
        name, blocker = BLOCKERS.get(k, (k, "-"))
        L.append(f"| {name} | {v['tickets']} | {v['minutes'] * month / 60:.1f} | {blocker} |")
    L += ["", "## Human deviations from written policy (for the customer's QA lead)", "",
          "These are tickets where the human agent's recorded resolution conflicts with the policy line the concierge cited. "
          "They're not accusations. Some will be justified exceptions, and that's exactly the conversation to have before go-live.", "",
          "| Pattern | Count |", "|---|---|"]
    for k, c in s["deviation_kinds"]:
        L.append(f"| {k} | {c} |")
    L += ["", "| Ticket | Message | Human did | Policy says (concierge) | Cited |", "|---|---|---|---|---|"]
    for e in s["flag_examples"][:8]:
        L.append(f"| {e['ticket_id']} | {e['message'][:70]} | {e['human_resolution']} | {e['concierge']} | {', '.join(e['concierge_cites'])} |")
    if s["wrong_auto"]:
        L += ["", "## Concierge auto-resolutions that didn't match policy (need fixing before go-live)", "",
              "| Ticket | Message | Policy-correct | Concierge |", "|---|---|---|---|"]
        for e in s["wrong_auto"][:10]:
            L.append(f"| {e['ticket_id']} | {e['message'][:70]} | {e['policy_correct_resolution']} | {e['concierge']} |")
    L += ["", "## Policy questions for Wattle's CX lead (found while building)", "",
          "1. **Pending duplicates (DSP-4).** We tell the customer to wait. Should the concierge instead watch the pending charge and open the case automatically if it settles?",
          "2. **Business dishonour fees (FEE-3 vs INF-1).** Business dishonour fees can be $80, so *every* business refund request escalates to a Team Lead. Intended, or should the limit differ for business accounts?",
          "3. **Merchant contact is self-attested (DSP-3).** Do you want evidence (an email or screenshot) above some amount?",
          "4. **Unauthorised charge, card in wallet (DSP-1).** We lock the card immediately. Should we confirm first when the customer says the card is with them? Locking is safe but disruptive.",
          "5. **Security holds (ESC-3 + PRV-2).** When a held customer asks \"why is my card blocked?\", what *can* we say? Today we only say we're passing them to Verification.",
          "6. **FEE-2 window.** If a customer is offered a goodwill refund and says no, does that use up their one refund for the 12 months? (Built as: no.)",
          "7. **Scam transfers (DSP-7).** Is there a recall window where the Scams team needs a callback SLA in minutes, not hours?",
          "8. **Hardship + fee request in one message (ESC-1).** Built as: hardship wins and no fee decision is made. Confirm.",
          "", "## Suggested rollout", "",
          "1. **Weeks 1–2:** the in-scope workflows above in shadow mode on live traffic. The concierge drafts, humans send. Review every disagreement daily.",
          "2. **Weeks 3–4:** auto-send for `inform` and `decline` outcomes and for routing. Refunds and disputes stay human-approved.",
          "3. **Week 5+:** turn on refunds (≤$50) and unauthorised-charge disputes once the shadow disagreement rate is under the agreed threshold. Keep the CI eval gate on every change.",
          "4. **In parallel:** card-delivery status (the largest gap, read-only API) as the next workflow."]
    return "\n".join(L) + "\n"


def main() -> None:
    rows = list(csv.DictReader(EXPORT.open(encoding="utf-8")))
    results = replay(rows)
    s = analyse(results)
    (HERE / "report.md").write_text(render(s), encoding="utf-8")
    (HERE / "report.json").write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    print(render(s))


if __name__ == "__main__":
    main()
