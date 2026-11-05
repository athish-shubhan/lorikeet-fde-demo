from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class Final:
    """The agent's message to the customer. kind="ask" keeps the conversation open:
    the harness sends `reply`, waits for the customer, and passes their answer back
    to next_step() as {"customer_reply": "..."}."""
    kind: str  # "done" (action taken / escalated) | "decline" | "inform" | "ask"
    reply: str
    ask: dict | None = None  # e.g. {"type": "confirm", "action": "refund_fee", "txn_id": "T1001"}


class Model(Protocol):
    name: str

    def start(self, ticket: str, customer_id: str) -> None: ...

    def next_step(self, last_result: dict | None) -> ToolCall | Final: ...
