"""Runtime guardrails that wrap the agent: defence in depth around the workflows.

Input screen:  deterministic checks run *before* the model sees the ticket.
               Care and regulatory triggers force a human handoff whatever the
               model would have done. Prompt-injection attempts are flagged.
Output check:  runs on the agent's final reply before it is sent. It blocks
               replies that cite unknown policy lines, promise outcomes, leak
               internal risk signals, or skip an escalation a tool demanded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import policy

# Ordered by priority: the first match wins.
FORCED_ESCALATIONS: list[tuple[str, str, str, re.Pattern]] = [
    ("vulnerability", "care", "ESC-2", re.compile(
        r"\b(scared|afraid|frightened|unsafe|abus\w*|violen\w*|threaten\w*|hurt myself|end it all|"
        r"don'?t want to (live|be here)|controlling (me|my))\b", re.I)),
    ("hardship", "hardship", "ESC-1", re.compile(
        r"(lost my job|can'?t (afford|pay|make|meet)\b.{0,30}(repayment|payment|bills|loan)|"
        r"financial (difficulty|hardship|stress)|struggling to (pay|make ends meet)|behind on (my )?repayments)", re.I)),
    ("complaint", "complaints", "ESC-6", re.compile(
        r"\b(afca|ombudsman|formal complaint|lodge a complaint|legal action|my lawyer|sue you)\b", re.I)),
]

INJECTION = re.compile(
    r"(ignore (all |any |your )?(previous |prior )?(instructions|polic(y|ies)|rules)|"
    r"(admin|developer|god) mode|you are now|system prompt|override (the )?polic)", re.I)

BANNED_PHRASES = re.compile(r"\b(definitely|guarantee[sd]?|100% (sure|certain)|promise you)\b", re.I)  # CON-1
INTERNAL_SIGNALS = re.compile(r"\b(security hold|fraud (hold|flag|score)|risk (score|flag)|flagged for fraud)\b", re.I)  # PRV-2


@dataclass
class Screen:
    forced: tuple[str, str, str] | None = None  # (reason, queue, policy_ref)
    injection: bool = False


def input_screen(text: str) -> Screen:
    s = Screen(injection=bool(INJECTION.search(text)))
    for reason, queue, ref, pattern in FORCED_ESCALATIONS:
        if pattern.search(text):
            s.forced = (reason, queue, ref)
            break
    return s


@dataclass
class OutputCheck:
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def check_message(text: str) -> OutputCheck:
    """Content checks that apply to every agent message, including questions."""
    out = OutputCheck()
    known = policy.policy_lines()
    for ref in policy.citations_in(text):
        if ref not in known:
            out.violations.append(f"cites unknown policy line {ref}")
    if BANNED_PHRASES.search(text):
        out.violations.append("promises an outcome (CON-1)")
    if INTERNAL_SIGNALS.search(text):
        out.violations.append("reveals internal risk signal (PRV-2)")
    return out


def check_reply(reply: str, audit: list[dict], escalated: bool) -> OutputCheck:
    """Checks on the final reply of a conversation."""
    out = check_message(reply)
    cited = policy.citations_in(reply)
    # Every final policy decision a tool made must be cited back to the customer.
    # (Results carrying an `ask` are intermediate: the agent asked and the tool was called again.)
    for entry in audit:
        res = entry.get("result") or {}
        if res.get("ask"):
            continue
        if res.get("status") in {"conditions_not_met", "escalation_required", "forbidden"}:
            for ref in res.get("policy_refs", []):
                if ref not in cited:
                    out.violations.append(f"decision on {ref} not cited in reply")
            if res.get("status") == "escalation_required" and not escalated:
                out.violations.append(f"tool required escalation to {res.get('queue')} but agent did not escalate")
    if not cited:
        out.violations.append("reply cites no policy line")
    return out
