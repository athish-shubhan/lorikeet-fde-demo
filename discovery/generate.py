"""Generate a synthetic 90-day support-ticket export for Wattle Money (deterministic, seed 7).

This stands in for the CSV a customer hands an FDE in week one. It is SYNTHETIC:
templated messages about the mock customers in concierge/data.py, plus the resolution
a human agent recorded. To test whether a shadow replay can find human policy
deviations, a known fraction of human resolutions is deliberately *wrong*
(`seeded_human_error=1`). The analysis never reads that column, it's only used to
measure the replay's recall.

    python -m discovery.generate      # writes discovery/data/historical_tickets.csv
"""

from __future__ import annotations

import csv
import random
from datetime import timedelta
from pathlib import Path

from concierge.data import TODAY

OUT = Path(__file__).resolve().parent / "data" / "historical_tickets.csv"
N_TICKETS = 400

# type: (weight, customer, txn or None, correct resolution, handle-time minutes, (human error rate, wrong resolution), facts)
TYPES = {
    "fee_refund_ok":          (14, ["C001"], ["T1001", "T1002"], "refund_fee", 6, (0.0, None), {"confirms": True}),
    "fee_refund_repeat":      (6, ["C002"], ["T2001"], "decline:FEE-2", 7, (0.30, "refund_fee"), {}),
    "fee_refund_collections": (3, ["C003"], ["T3001", "T3002"], "decline:FEE-4", 7, (0.25, "refund_fee"), {}),
    "fee_refund_old":         (4, ["C010", "C008"], ["T10001", "T8002"], "decline:FEE-5", 6, (0.20, "refund_fee"), {}),
    "fee_refund_over_limit":  (2, ["C009"], ["T9001"], "escalate:team_lead", 9, (0.30, "refund_fee"), {}),
    "dispute_unauthorised":   (10, ["C001", "C001", "C002"], ["T1003", "T1006", "T2002"], "open_dispute", 14, (0.0, None), {}),
    "dispute_merchant_ok":    (8, ["C001", "C001", "C010", "C007"], ["T1004", "T1005", "T10002", "T7003"], "open_dispute", 15,
                               (0.0, None), {"contacted_merchant": True}),
    "dispute_merchant_not_contacted": (5, ["C001", "C010"], ["T1004", "T10002"], "decline:DSP-3", 8, (0.25, "open_dispute"),
                                       {"contacted_merchant": False}),
    "dispute_pending":        (3, ["C006"], ["T6002"], "decline:DSP-4", 6, (0.30, "open_dispute"), {}),
    "dispute_old":            (2, ["C007"], ["T7001"], "decline:DSP-2", 8, (0.25, "open_dispute"), {"contacted_merchant": True}),
    "dispute_large":          (1, ["C006"], ["T6001"], "escalate:disputes", 20, (0.0, None), {"contacted_merchant": True}),
    "dispute_status":         (6, ["C008"], ["T8001"], "inform", 4, (0.0, None), {}),
    "scam_transfer":          (2, ["C007"], ["T7002"], "escalate:scams", 25, (0.35, "open_dispute"), {}),
    "hardship":               (4, ["C001", "C003", "C010"], [None], "escalate:hardship", 20, (0.25, "decline"), {}),
    "vulnerability":          (2, ["C002", "C006"], [None], "escalate:care", 30, (0.0, None), {}),
    "complaint":              (3, ["C003", "C009"], [None], "escalate:complaints", 18, (0.0, None), {}),
    "human_request":          (4, ["C010", "C001"], [None], "escalate:general", 5, (0.0, None), {}),
    "info_fees":              (6, ["C001", "C006"], [None], "inform", 3, (0.0, None), {}),
    "verification_blocked":   (4, ["C004", "C005"], [None], "escalate:verification", 10, (0.0, None), {}),
    # Out of scope for this concierge today: the "where it can't yet" part of discovery.
    "oos_card_not_arrived":   (8, ["C001", "C006", "C010"], [None], "human:card_delivery", 9, (0.0, None), {}),
    "oos_app_login":          (7, ["C002", "C009"], [None], "human:app_access", 11, (0.0, None), {}),
    "oos_address_change":     (5, ["C001", "C007"], [None], "human:profile_update", 7, (0.0, None), {}),
    "oos_credit_limit":       (4, ["C006", "C009"], [None], "human:credit_limit", 12, (0.0, None), {}),
    "oos_close_account":      (3, ["C004", "C010"], [None], "human:account_closure", 10, (0.0, None), {}),
}

TEMPLATES = {
    "fee_refund_ok": ["Could you refund the {fee} please? It was a one-off.", "hi, any chance of getting the {fee} (${amt}) waived?",
                      "I got charged a {fee}, can it be reversed this once?", "Please remove the {fee} from my account, thanks"],
    "fee_refund_repeat": ["Can you waive the {fee} again please?", "I'd like the ${amt} {fee} refunded", "please reverse my {fee}"],
    "fee_refund_collections": ["Take the {fee} off please.", "Can I get the {fee} refunded?"],
    "fee_refund_old": ["There's an old {fee} on my account from a while back, can I have it refunded?", "refund my {fee} from {month} please"],
    "fee_refund_over_limit": ["Please refund the ${amt} {fee} on our business account.", "Can the {fee} of ${amt} be reversed?"],
    "dispute_unauthorised": ["I don't recognise a {merchant} charge of ${amt}.", "{merchant} took ${amt} from my card and it wasn't me",
                             "Someone has used my card at {merchant}!", "fraud on my card - {merchant} ${amt}, never been there"],
    "dispute_merchant_ok": ["My {merchant} purchase (${amt}) has a problem and the merchant won't sort it out, I've emailed them.",
                            "I've contacted {merchant} twice about my ${amt} order and they've ignored me. I want to dispute it.",
                            "{merchant} refused to refund me for an order that never came. I'd like a chargeback."],
    "dispute_merchant_not_contacted": ["I want to dispute the {merchant} charge, the order never came.",
                                       "Can you get my ${amt} back from {merchant}? It never arrived."],
    "dispute_pending": ["{merchant} charged me twice, one is still pending, can you remove the duplicate?",
                        "Double charge from {merchant} for ${amt}, one hasn't cleared yet"],
    "dispute_old": ["I want to dispute a {merchant} order from months ago that never arrived. The seller won't reply."],
    "dispute_large": ["I need to dispute the ${amt} {merchant} purchase, it's faulty and they refused to help."],
    "dispute_status": ["What's the status of my {merchant} dispute?", "any update on my dispute please", "has my {merchant} claim been sorted yet"],
    "scam_transfer": ["I sent ${amt} to someone pretending to be from my bank. I think it was a scam.",
                      "I was tricked into transferring ${amt} by a caller claiming to be the police."],
    "hardship": ["I've lost my job and I can't make my repayments.", "I'm struggling to pay my card this month, what can I do?",
                 "Money's really tight, I don't think I can afford my repayment."],
    "vulnerability": ["My partner takes my money and I'm scared to say anything.", "Someone is controlling my finances and I feel unsafe."],
    "complaint": ["I want to make a formal complaint.", "This is ridiculous, I'm going to the ombudsman."],
    "human_request": ["Can I talk to a real person?", "put me through to a human please"],
    "info_fees": ["What's the late payment fee?", "How much is the overseas transaction fee?", "what fees do you charge for using my card abroad"],
    "verification_blocked": ["Can you refund the fee on my account?", "I want to dispute a charge on my card."],
    "oos_card_not_arrived": ["My new card still hasn't arrived, it's been two weeks.", "where is my replacement card?",
                             "I ordered a card 10 days ago and nothing yet"],
    "oos_app_login": ["The app keeps logging me out.", "I can't log in to the app, it says my password is wrong",
                      "locked out of online banking"],
    "oos_address_change": ["I've moved house, how do I update my address?", "please change my address"],
    "oos_credit_limit": ["Can I get a credit limit increase?", "I'd like to raise my card limit"],
    "oos_close_account": ["Please close my account.", "I want to close my account and transfer my balance out"],
}
FEE_NAMES = {"overseas_fee": "overseas transaction fee", "late_fee": "late payment fee", "dishonour_fee": "dishonour fee"}


def generate(n: int = N_TICKETS, seed: int = 7) -> list[dict]:
    from concierge.data import MockBank

    bank = MockBank.fresh()
    rng = random.Random(seed)
    names = list(TYPES)
    weights = [TYPES[k][0] for k in names]
    rows = []
    for i in range(n):
        typ = rng.choices(names, weights)[0]
        _, custs, txns, correct, minutes, (err_rate, wrong), facts = TYPES[typ]
        j = rng.randrange(len(custs)) if txns[0] is not None else None
        cust = custs[j] if j is not None else rng.choice(custs)
        txn = bank.txns.get(txns[j % len(txns)]) if j is not None else None
        text = rng.choice(TEMPLATES[typ]).format(
            fee=FEE_NAMES.get(getattr(txn, "fee_code", ""), "fee"), amt=f"{txn.amount:,.2f}" if txn else "",
            merchant=getattr(txn, "merchant", ""), month="August")
        seeded = rng.random() < err_rate
        rows.append({
            "ticket_id": f"WM-{10000 + i}",
            "created_at": (TODAY - timedelta(days=rng.randrange(1, 91))).isoformat(),
            "channel": rng.choice(["chat", "chat", "email", "email", "app"]),
            "customer_id": cust, "message": text, "true_type": typ, "txn_id": txn.txn_id if txn else "",
            "human_resolution": wrong if seeded else correct, "policy_correct_resolution": correct,
            "seeded_human_error": int(seeded), "handle_minutes": max(2, round(rng.gauss(minutes, minutes * 0.25))),
            "facts": "|".join(f"{k}={v}" for k, v in facts.items()),
        })
    return rows


def main() -> None:
    rows = generate()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} synthetic tickets -> {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")


if __name__ == "__main__":
    main()
