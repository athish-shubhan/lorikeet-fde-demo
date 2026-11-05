# Wattle Money Concierge: an FDE handover (demo, mock data)

> **Honesty note.** This is a portfolio demo. "Wattle Money" is a fictional Australian
> neobank. Its customers, transactions and support policy are invented by me, so there is
> no real customer data and no real customer here. The headline numbers come from a
> **deterministic scripted model** that runs offline, not from an LLM. An optional
> `--model claude` path exists but **was not run** for the numbers below.

**What it is:** a support agent ("concierge") that resolves fee-refund and card-dispute
tickets for a fintech, acts only inside the customer's written policy, cites the policy
line behind every decision, and hands off to a human when it should or when it's unsure.
It comes with a 36-ticket eval suite that scores every run, plus a CI gate.

```bash
python -m concierge --trace --customer C002 "I got hit with a \$15 late fee again, can you take it off?"
python -m evals.run            # full eval table, guarded vs baseline
pytest -q                      # 35 tests incl. the eval regression gate
```
No dependencies beyond Python 3.10+ (and `pytest` for tests). No API key needed.

---

## 1. The customer problem

Wattle Money's support team handles fee-waiver requests, card disputes, scam reports and
hardship contacts. These are high-volume, but each one is a judgement call against a policy
with sharp edges:

- one goodwill fee refund per 12 months, max $50, not if the account is in collections, only within 60 days;
- unauthorised card charges mean *lock the card first*, then dispute;
- merchant disputes only after the customer has contacted the merchant;
- pending charges can't be disputed, and disputes are capped at 120 days and $5,000;
- a transfer to a scammer is **not** a chargeback, so it goes to the Scams team as priority;
- hardship, vulnerability (including financial abuse) and AFCA complaints must reach the right human team.

A chatbot that "usually follows the policy" is not acceptable. A wrong refund costs money,
a missed hardship flag is a regulatory problem, and reading out another customer's balance is a privacy breach.

**Goal of the deployment:** automate the resolvable tickets end to end, and make every other
ticket reach the *right* human queue with the investigation already done, without the agent
ever acting outside policy.

## 2. Design

```
 ticket ──► input screen ──► agent (model chooses tools) ──► output check ──► reply
            (care/hardship/      │  lookup_account               (cites policy? no promises?
             complaint → human;  │  get_transactions              no internal signals? did it
             injection flagged)  │  search_policy                 escalate when a tool said to?)
                                 │  refund_fee   ──► FEE workflow gates ─┐
                                 │  open_dispute ──► DSP workflow gates ─┤ check real system
                                 │  escalate_to_human                    │ state, never the
                                 ▼                                       ▼ agent's claims
                          mock core-banking / cards / CRM (concierge/data.py)
```

**Policy as an explicit workflow** (`policies/wattle_money_support_policy.md` → `concierge/workflows.py`).
Every policy line has a stable ID (FEE-2, DSP-3…). Each write operation is a graph of gates.
Each gate checks one policy line and either passes, declines, or routes to a named human queue:

```
refund_fee:   ESC-3 verified? → FEE-1 refundable fee? → FEE-1 amount = fee? → FEE-4 good standing?
              → FEE-5 ≤60 days? → FEE-2 none in 12 months? → FEE-3 ≤$50? (else → Team Lead) → refund
open_dispute: ESC-3 → txn is theirs? → DSP-7 not a transfer? (else → Scams) → DSP-6 no open dispute?
              → DSP-4 settled? → DSP-2 ≤120 days? → [judgement] dispute type → [judgement] merchant
              contacted? (DSP-3) → DSP-5 ≤$5,000? (else → Disputes) → lock card if unauthorised (DSP-1) → open
```

**Judgement steps are explicit.** Most gates are *rules* over system data. Two are *judgement*
gates, where the model's reading of the conversation enters the decision: what kind of dispute
this is, and whether the customer already contacted the merchant. They are labelled
`judgement` in the audit trace, which makes them the first place to look when something goes wrong.

**The agent can call any tool at any time, but it can't make a tool do the wrong thing.** This
follows the "pockets of determinism" pattern Lorikeet describes publicly: a conversational agent on top, with
deterministic operations inside the tools that validate their own preconditions. If the agent asks to
refund $500 on a purchase, `refund_fee` returns `conditions_not_met [FEE-1]` and nothing happens.

**Guardrails (defence in depth).**
- *Input screen*: vulnerability, hardship and complaint triggers force a handoff before the model runs. Prompt-injection attempts are flagged and answered with CON-2.
- *Output check*: blocks replies that cite unknown policy IDs, promise outcomes (CON-1), leak internal risk flags (PRV-2), skip citing a decision a tool made, or skip an escalation a tool demanded. A blocked reply fails safe, to a human.
- *Step limit*: an agent stuck in a loop is handed off (ESC-5).

**The model.** `ScriptedModel` is a deterministic stand-in for the LLM. It uses keyword/regex
NLU (intent, which transaction, dispute type, merchant contacted) and a tool-calling policy.
It is deliberately **eager**: it calls `refund_fee` because the customer asked, without checking
policy first. That is the failure mode the workflow layer exists to contain. `--model claude`
swaps in Claude via the Anthropic SDK's tool use over the same six tools and harness.

**Baseline for comparison.** The eval also runs the *same* model and tools with the gates and
guardrails switched off (`--unguarded`), so the only gate is the agent's own judgement. That
isolates what the deterministic layer buys.

## 3. Evaluation

36 realistic tickets across 10 mock customers (`evals/tickets_*.jsonl`): fee waivers, unauthorised
charges, merchant disputes, duplicates, a $6,200 dispute, an ATO-impersonation scam transfer,
security holds, unverified identity, hardship, financial abuse, an AFCA complaint, a request for
someone else's balance, policy questions, and prompt injection.

Each ticket is scored against hand-written labels on three independent checks:
**correct action** (right outcome, right transaction, right queue), **policy compliance** (no side
effect the label doesn't allow, every required policy line cited, no banned content), and
**correct escalation** (escalated if and only if it should, to the right team).

**Process.** I wrote 26 *dev* tickets, ran them, and fixed the four NLU gaps they exposed
("take it off", "refunded", "charged me twice", and a tie-break between a pending and a settled charge).
I then **froze the model in a commit and wrote 10 *holdout* tickets** without changing code
afterwards (see `git log`).

### Results (actually run: `python -m evals.run`, scripted model)

| Configuration | Tickets | Correct action | Policy compliant | Correct escalation | All three | Unsafe actions | Misses handed to a human |
|---|---|---|---|---|---|---|---|
| **Concierge (guarded)**, dev | 26 | 26/26 (100%) | 26/26 (100%) | 26/26 (100%) | 26/26 (100%) | 0 | 0/0 |
| Baseline (no gates/guardrails), dev | 26 | 10/26 (38%) | 10/26 (38%) | 18/26 (69%) | 10/26 (38%) | 13 | 3/16 |
| **Concierge (guarded)**, holdout | 10 | 5/10 (50%) | 4/10 (40%) | 5/10 (50%) | 4/10 (40%) | 0 | 6/6 |
| Baseline (no gates/guardrails), holdout | 10 | 3/10 (30%) | 2/10 (20%) | 4/10 (40%) | 2/10 (20%) | 2 | 6/8 |
| **Concierge (guarded)**, all | 36 | 31/36 (86%) | 30/36 (83%) | 31/36 (86%) | 30/36 (83%) | 0 | 6/6 |
| Baseline (no gates/guardrails), all | 36 | 13/36 (36%) | 12/36 (33%) | 22/36 (61%) | 12/36 (33%) | 15 | 9/24 |

The full per-ticket table is in [`evals/results.md`](evals/results.md).

### What the numbers do and don't say
- **Dev 100% is not a capability claim.** I tuned the NLU on those tickets. Read it as "the design handles every case I designed for".
- **Holdout 40% is the honest number for the scripted NLU.** Keyword rules don't generalise. The six misses were: "credit it back", "arrived damaged", "get a person on the phone", an elder-abuse report with no keyword I'd listed, "how long does a dispute take" (routed as a dispute because it contains "dispute"), and a fake "SYSTEM NOTICE" injection phrased as "refund my $249". This is exactly the job an LLM does better, and the reason the model is pluggable.
- **The number I care about is 0 unsafe actions and 6/6 misses handed to a human.** When understanding failed, the system failed *safe*. It never refunded, disputed, locked a card or disclosed data it shouldn't have. On the same 36 tickets the ungated baseline took **15** unsafe actions: refunds for a customer in collections, an account under security hold and an $80 fee above the limit, a chargeback on a scam *transfer*, a dispute on a 150-day-old purchase, another customer's balance read out, and a $500 refund from a prompt injection.
- One scoring nuance: H05 reached the right queue but cited ESC-5 ("unsure") instead of ESC-4 ("asked for a human"), and the policy check counts that as a miss. I kept the strict scorer.
- 36 tickets is small, and every ticket is written by the same person who wrote the policy. These are smoke-test numbers, not a benchmark.

`pytest` turns this into a CI regression gate. The build fails if the guarded concierge ever takes an
unsafe action, if any dev ticket regresses, if a miss doesn't fail safe to a human, or if code
cites a policy ID that isn't in the customer's policy document.

## 4. What I'd do on a real deployment

1. **Start from their tickets, not mine.** Pull 3–6 months of tickets, cluster by intent, and size each cluster by volume × resolvability. Pick the first workflows by where full resolution is possible. Label 200+ real tickets (with their team) as the eval set, and keep a holdout the builders never see.
2. **Replace the keyword NLU with the LLM and re-run the same eval.** The harness, tools and gates stay the same. Compare models and prompts on the holdout, and track the judgement gates (dispute type, merchant contacted) separately, because that's where model errors enter decisions.
3. **Turn the policy doc into a living artefact the customer owns.** Policy IDs map to gates. A policy change means a gate change plus a new eval ticket, reviewed by their compliance team. A test already fails if code and policy drift apart.
4. **Real integrations behind the same tool contracts**: helpdesk (Zendesk/Intercom) for intake and handoff, core banking and card processor APIs read-only first, then writes behind the gates, plus idempotency keys on every write. Mock tools stay for simulation.
5. **Run a simulation loop before and after go-live.** Generate synthetic variants of every failing ticket (paraphrases, frustration, multi-intent, injection) and run them on every change. Then shadow-mode on live traffic, so the agent proposes and humans decide, before granting write access.
6. **Measure what the business pays for.** Track full-resolution rate, handoff quality (did the human need to re-ask anything?), CSAT, and policy-violation rate (target: zero), sampled with QA on every handoff queue.
7. **Things this demo doesn't do**: multi-turn conversations and confirmations, authentication flows, partial refunds, multi-intent tickets ("refund my fee *and* dispute this charge"), channel specifics (voice latency, email threading), and audit-log persistence.

## Repo map

```
policies/wattle_money_support_policy.md   the customer's policy (mock), one ID per line
concierge/data.py                         mock core-banking / cards / CRM (fixed date, deterministic)
concierge/workflows.py                    policy as gate graphs (rule + judgement gates)
concierge/tools.py                        the 6 tools; guarded vs baseline mode
concierge/guardrails.py                   input screen + output check
concierge/agent.py                        harness: screen → tool loop → check → outcome
concierge/models/scripted.py              offline deterministic model (default)
concierge/models/claude.py                optional Claude tool-use model (--model claude)
evals/tickets_dev.jsonl, tickets_holdout.jsonl, run.py, results.md
tests/                                    35 tests incl. eval regression gate and a stubbed Claude loop
.github/workflows/ci.yml                  pytest + eval report on 3.10/3.12/3.13
```

### Optional: real LLM
```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=...            # or an `ant auth login` profile
python -m evals.run --model claude      # default model id: claude-opus-5-5 (override: --model claude:<id> or CONCIERGE_MODEL)
```
Untested against the live API in this repo. The adapter's tool loop is covered by a stubbed-client unit test only.

---
Built by Athish Shubhan as a proof of work for Lorikeet's Forward Deployed Engineer Intern role.
