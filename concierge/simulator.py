"""A simulated customer for multi-turn evals (the "Simulations" idea, offline).

Each eval ticket carries the customer's private facts (do they want the refund?
have they contacted the merchant? what would they say if asked to clarify?).
When the agent asks a question, the simulator answers from those facts in
natural, varied language. Phrasing is picked deterministically from the ticket
id, so runs are reproducible.
"""

from __future__ import annotations

import zlib

YES = ["Yes please, go ahead.", "yep that's fine", "Sure, do it.", "Yes, that's great, thanks.", "ok please do"]
NO = ["Actually no, leave it for now.", "No thanks, I'll keep my one refund for later.", "Hmm, don't worry about it.",
      "nah that's ok"]
CONTACTED_YES = ["Yes, I've already contacted them and got nowhere.", "yeah I emailed them, no reply",
                 "Yes, I called them twice."]
CONTACTED_NO = ["No, I haven't contacted them yet.", "not yet, no", "No, I didn't think to."]
UNSURE = ["I'm not sure how else to explain it, can someone just look into it?", "idk, it's just wrong"]


def _pick(options: list[str], key: str) -> str:
    return options[zlib.crc32(key.encode()) % len(options)]


class SimulatedCustomer:
    def __init__(self, ticket_id: str, facts: dict | None = None, max_replies: int = 3):
        self.id, self.facts, self.left = ticket_id, facts or {}, max_replies

    def reply(self, ask: dict | None) -> str | None:
        """Answer the agent's question, or None if the customer has gone quiet."""
        if self.left <= 0:
            return None
        self.left -= 1
        kind = (ask or {}).get("type")
        if kind == "confirm":
            return _pick(YES if self.facts.get("confirms", True) else NO, self.id)
        if kind == "merchant_contacted":
            return _pick(CONTACTED_YES if self.facts.get("contacted_merchant", False) else CONTACTED_NO, self.id)
        if kind == "which_txn":
            return self.facts.get("txn_hint") or _pick(UNSURE, self.id)
        if kind == "clarify":
            return self.facts.get("clarification") or _pick(UNSURE, self.id)
        return _pick(UNSURE, self.id)
