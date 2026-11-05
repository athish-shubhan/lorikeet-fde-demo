"""Load the customer's policy document and expose it by line ID."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "wattle_money_support_policy.md"
_LINE = re.compile(r"^- \*\*(?P<id>[A-Z]{3}-\d+)\*\*: (?P<text>.+)$")
CITATION = re.compile(r"\[([A-Z]{3}-\d+)\]")


@lru_cache(maxsize=1)
def policy_lines() -> dict[str, str]:
    lines: dict[str, str] = {}
    for raw in POLICY_PATH.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(raw.strip())
        if m:
            lines[m["id"]] = m["text"]
    return lines


def policy_text() -> str:
    return POLICY_PATH.read_text(encoding="utf-8")


def search(query: str, k: int = 3) -> list[dict]:
    """Naive keyword search over policy lines (the 'fully agentic' knowledge lookup)."""
    words = {w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2}
    scored = []
    for pid, text in policy_lines().items():
        overlap = len(words & set(re.findall(r"[a-z]+", text.lower())))
        if overlap:
            scored.append((overlap, pid, text))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [{"policy_ref": pid, "text": text} for _, pid, text in scored[:k]]


def citations_in(text: str) -> list[str]:
    return sorted(set(CITATION.findall(text)))
