"""Offline understanding layer for the scripted model (v2).

v1 was hand-written regexes, and it generalised badly (4/10 on its holdout). v2 is a
small, deterministic nearest-neighbour classifier over a labelled utterance bank,
using TF-IDF on word unigrams, bigrams and character n-grams, with a calibrated
"I'm not sure" output. Regexes are kept only for slot extraction (amounts,
customer refs) where they are reliable.

In production this whole module is what the LLM replaces. It exists so the demo,
tests and CI run with no API key, and so there's a concrete baseline the LLM has to beat.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- bank
# Written for the dev set and general phrasing. Holdout tickets are never copied in here.
INTENT_EXAMPLES: dict[str, list[str]] = {
    "fee_refund": [
        "can you refund the overseas transaction fee", "please waive the late fee", "refund the fee you charged me",
        "can I get the international fee back", "take the late payment fee off my account", "reverse the dishonour fee",
        "remove the fee from my statement", "I was charged a fee, can it be refunded as a one off",
        "credit the fee back to my account", "I paid a day late, please waive the fee",
        "you charged me a fee for a bounced direct debit, refund it", "can the currency conversion fee be refunded",
        "I'd like that charge for paying late removed", "get rid of the late payment fee",
        "the foreign transaction fee on my trip, can you give it back", "refund $500 to my account",
    ],
    "dispute": [
        "there's a charge I never made", "I don't recognise this transaction", "someone used my card",
        "my card details were stolen and used", "the order never arrived", "I paid but never received the goods",
        "the item was faulty and the store won't refund", "the product arrived broken", "not as described",
        "I cancelled but they still charged me", "they keep billing me after I cancelled my subscription",
        "I was charged twice for the same thing", "duplicate charge on my card", "I want to dispute this charge",
        "can you do a chargeback", "this payment wasn't me", "the seller won't respond and I want my money back",
        "my parcel hasn't turned up and the shop is ignoring me", "the goods were damaged when they arrived",
        "the merchant refused to refund me so I want to dispute it", "the host won't give my money back after I cancelled",
    ],
    "scam": [
        "I transferred money to someone who said they were from the tax office", "I think I've been scammed",
        "someone pretending to be from the bank told me to move my money", "I sent money to a scammer",
        "a caller impersonated the police and I paid them", "I was tricked into transferring money",
        "I paid an invoice that turned out to be fake and want the transfer back",
    ],
    "dispute_status": [
        "any update on my dispute", "what's the status of my claim", "where is my dispute up to",
        "has my dispute been resolved yet", "what's happening with my chargeback", "checking on the progress of my case",
    ],
    "info": [
        "what is the overseas transaction fee", "how much is the late fee", "how long does a dispute take",
        "how long until a dispute is resolved", "what fees do you charge", "what's your fee for international purchases",
        "how much do you charge when a payment bounces", "when will I hear back about a dispute",
    ],
    "human": [
        "can I talk to a real person", "I want to speak to a human", "put me through to an agent",
        "get me a person on the phone", "your bot isn't helping, I need a human", "can someone call me",
        "let me speak to a staff member", "transfer me to a person please",
    ],
    "other_customer": [
        "what's my husband's account balance", "can you check my wife's transactions", "tell me my son's balance",
        "look up my partner's account", "can you check my dad's account for me", "my friend's customer number is",
        "check whether my mother's payment went through on her account",
    ],
    "hardship": [
        "I lost my job and can't make my repayments", "I can't afford this month's payment",
        "I'm in financial difficulty", "I'm struggling to pay my bills", "I'm behind on my repayments",
        "I don't have enough money to pay my card this month", "my hours were cut and I can't keep up with payments",
        "I'm out of work and worried about my loan",
    ],
    "vulnerability": [
        "my ex is taking money from my account and I'm scared", "I'm afraid of my partner", "someone is controlling my money",
        "my carer has been using my elderly mother's card", "my family is pressuring me to hand over my savings",
        "I feel unsafe at home", "he takes all my money and won't let me have any", "I don't want to be here anymore",
        "someone is abusing my dad financially", "my partner monitors everything I spend and takes my wages",
    ],
    "out_of_scope": [
        "I want to close my account", "please update my address", "can you increase my credit limit",
        "change my phone number", "I'd like to open a savings account", "how do I set up a direct debit",
        "can I get a new PIN", "move all my money to another bank",
    ],
    "complaint": [
        "I want to make a formal complaint", "I'm going to the ombudsman", "I'm lodging a complaint with AFCA",
        "I'll be taking legal action", "this is unacceptable, I'm escalating this to the ombudsman",
        "I want to complain about how I've been treated", "my lawyer will be in touch",
    ],
}

DISPUTE_TYPE_EXAMPLES: dict[str, list[str]] = {
    "unauthorised": ["I never made this", "someone used my card", "it wasn't me", "I don't recognise it", "my card was stolen",
                     "I've never shopped there", "fraudulent charge on my card", "not authorised by me", "never been a member there"],
    "not_received": ["it never arrived", "I never received it", "hasn't shown up", "the parcel never came", "still waiting for delivery",
                     "never got the order"],
    "not_as_described": ["it was faulty", "arrived broken", "it fell apart", "damaged", "not as described", "doesn't work",
                         "poor quality and broke"],
    "cancelled": ["I cancelled but was still charged", "billed after cancelling", "charged after I cancelled the subscription",
                  "cancelled my booking and they won't refund", "they keep charging me after I cancelled"],
    "duplicate": ["charged twice", "double charged", "duplicate charge", "billed two times for the same thing",
                  "took the money twice"],
    "scam": ["I was scammed", "they pretended to be from the bank", "a scammer told me to transfer", "moved money to a safe account"],
}

STOP = set("""a an the i me my mine we our you your it its is was were be been am are to of and or for on in at by with from
this that these those there here so just hi hey hello please thanks thank can could would will do did does have has had
get got any some about as if then than up out into also really bit tbh""".split())

AMOUNT = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)|\b(\d+\.\d{2})\b")
CUSTOMER_REF = re.compile(r"\bC\d{3}\b")


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _normalise(text: str) -> list[str]:
    t = text.lower().replace("’", "'")
    t = AMOUNT.sub(" amt ", t)
    t = re.sub(r"n't\b", " not", t)
    t = re.sub(r"[^a-z\s]", " ", t)
    return [_stem(w) for w in t.split() if w not in STOP]


def features(text: str) -> Counter:
    words = _normalise(text)
    f: Counter = Counter(words)
    f.update(f"{a}_{b}" for a, b in zip(words, words[1:]))
    for w in words:
        padded = f"<{w}>"
        f.update({f"#{padded[i:i + 4]}": 0.3 for i in range(max(1, len(padded) - 3))})
    return f


class KNN:
    """Cosine nearest-neighbour over TF-IDF vectors. Score per label = mean of its top-2 similarities."""

    def __init__(self, bank: dict[str, list[str]]):
        docs = [(label, features(t)) for label, ex in bank.items() for t in ex]
        df: Counter = Counter()
        for _, f in docs:
            df.update(f.keys())
        n = len(docs)
        self.idf = {k: math.log((1 + n) / (1 + v)) + 1 for k, v in df.items()}
        self.docs = [(label, self._vec(f)) for label, f in docs]

    def _vec(self, f: Counter) -> dict[str, float]:
        v = {k: c * self.idf.get(k, 0.0) for k, c in f.items() if k in self.idf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {k: x / norm for k, x in v.items()}

    def scores(self, text: str) -> list[tuple[str, float]]:
        q = self._vec(features(text))
        per: dict[str, list[float]] = {}
        for label, d in self.docs:
            per.setdefault(label, []).append(sum(q[k] * d[k] for k in q.keys() & d.keys()))
        ranked = [(label, sum(sorted(s, reverse=True)[:2]) / 2) for label, s in per.items()]
        return sorted(ranked, key=lambda x: -x[1])


INTENT_KNN = KNN(INTENT_EXAMPLES)
DISPUTE_KNN = KNN(DISPUTE_TYPE_EXAMPLES)
MIN_CONFIDENCE = 0.20
MIN_MARGIN = 0.015

FEE_HINTS = {
    "overseas_fee": re.compile(r"overseas|international|foreign|currency|abroad|\btrip\b", re.I),
    "late_fee": re.compile(r"\blate\b|paid (a day |one day )?after", re.I),
    "dishonour_fee": re.compile(r"dishono|bounc|failed (direct debit|payment)", re.I),
}
CONTACTED = re.compile(
    r"\b(contacted|emailed|e-mailed|called|rang|phoned|messaged|spoke to|spoken to|reached out|asked|told|complained to|chased)\b"
    r"|\b(they|the (seller|merchant|store|shop|host|retailer|company)|seller|merchant|shop|store)\b.{0,20}"
    r"\b(refused|refuse|won'?t|wouldn'?t|ignored|ignoring|ghosted|didn'?t reply|haven'?t replied|no reply|said no|don'?t do)\b", re.I)
NOT_CONTACTED = re.compile(r"\b(haven'?t|have not|didn'?t|did not|not yet)\b.{0,15}\b(contact|email|call|message|spoke|spoken|reach|ask|told)", re.I)


@dataclass
class Understanding:
    intent: str
    confidence: float
    ranked: list[tuple[str, float]] = field(default_factory=list)
    amounts: list[float] = field(default_factory=list)
    dispute_type: str | None = None
    merchant_contacted: bool | None = None  # None = unknown -> ask
    fee_code: str | None = None
    other_customer: str | None = None


def understand(text: str, session_customer: str) -> Understanding:
    ranked = INTENT_KNN.scores(text)
    (top, s1), (_, s2) = ranked[0], ranked[1]
    intent = top if (s1 >= MIN_CONFIDENCE and s1 - s2 >= MIN_MARGIN) else "unknown"
    u = Understanding(intent=intent, confidence=round(s1, 3), ranked=[(k, round(v, 3)) for k, v in ranked[:3]])
    u.amounts = [float((a or b).replace(",", "")) for a, b in AMOUNT.findall(text)]
    if intent in ("dispute", "scam"):
        u.dispute_type = "scam" if intent == "scam" else DISPUTE_KNN.scores(text)[0][0]
    if NOT_CONTACTED.search(text):
        u.merchant_contacted = False
    elif CONTACTED.search(text):
        u.merchant_contacted = True
    u.fee_code = next((c for c, pat in FEE_HINTS.items() if pat.search(text)), None)
    refs = [r for r in CUSTOMER_REF.findall(text) if r != session_customer]
    u.other_customer = refs[0] if refs else None
    return u
