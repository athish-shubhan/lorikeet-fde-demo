from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class Final:
    kind: str  # "done" (an action was taken) | "decline" | "inform"
    reply: str


class Model(Protocol):
    name: str

    def start(self, ticket: str, customer_id: str) -> None: ...

    def next_step(self, last_result: dict | None) -> ToolCall | Final: ...
