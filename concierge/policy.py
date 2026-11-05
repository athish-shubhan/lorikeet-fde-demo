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


SYNONYMS = {"abroad": "overseas", "travelling": "overseas", "traveling": "overseas", "international": "overseas",
            "foreign": "overseas", "charge": "fee", "charges": "fee", "cost": "fee", "costs": "fee", "fees": "fee",
            "late": "late", "long": "timeline", "take": "timeline", "resolve": "resolved", "disputes": "dispute"}
_GENERIC = {"card", "account", "customer", "the", "and", "for", "you", "your", "our"}


def _terms(text: str) -> set[str]:
    return {SYNONYMS.get(w, w) for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in _GENERIC}


def search(query: str, k: int = 3, prefix: str = "") -> list[dict]:
    """Keyword search over policy lines (the 'fully agentic' knowledge lookup), with light synonyms.
    `prefix` restricts to a section (e.g. "INF" for customer-facing information)."""
    words = _terms(query)
    scored = []
    for pid, text in policy_lines().items():
        if prefix and not pid.startswith(prefix):
            continue
        overlap = len(words & (_terms(text) | ({"timeline"} if "timeline" in text.lower() else set())))
        if overlap:
            scored.append((overlap, pid, text))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [{"policy_ref": pid, "text": text} for _, pid, text in scored[:k]]


def citations_in(text: str) -> list[str]:
    return sorted(set(CITATION.findall(text)))
