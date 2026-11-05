"""Mock core-banking systems for the fictional neobank "Wattle Money".

Everything here is invented. Dates are relative to a fixed TODAY so that every
run, test and eval is deterministic. `MockBank.fresh()` returns a brand-new copy,
so each ticket starts from the same state.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta

TODAY = date(2026, 11, 5)


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


@dataclass
class Account:
    customer_id: str
    name: str
    identity_verified: bool = True
    security_hold: bool = False  # internal risk signal: never shown to customer (PRV-2)
    in_collections: bool = False
    account_type: str = "personal"
    balance: float = 0.0
    card_id: str = ""
    card_locked: bool = False
    last_goodwill_refund: str | None = None  # ISO date


@dataclass
class Txn:
    txn_id: str
    customer_id: str
    date: str
    kind: str  # "card" | "fee" | "transfer"
    description: str
    amount: float
    merchant: str = ""
    fee_code: str = ""  # overseas_fee | late_fee | dishonour_fee
    status: str = "settled"  # settled | pending
    dispute: dict | None = None


def _seed() -> tuple[dict[str, Account], dict[str, Txn]]:
    accounts = [
        Account("C001", "Priya Raman", balance=2_431.18, card_id="CARD-001"),
        Account("C002", "Tom Nguyen", balance=812.40, card_id="CARD-002", last_goodwill_refund=ago(120)),
        Account("C003", "Mei Chen", balance=-212.00, card_id="CARD-003", in_collections=True),
        Account("C004", "Liam O'Brien", balance=95.00, card_id="CARD-004", security_hold=True),
        Account("C005", "Aisha Khan", balance=1_300.00, card_id="CARD-005", identity_verified=False),
        Account("C006", "Jack Wilson", balance=9_870.55, card_id="CARD-006"),
        Account("C007", "Sofia Rossi", balance=410.00, card_id="CARD-007"),
        Account("C008", "Noah Patel", balance=3_002.10, card_id="CARD-008"),
        Account("C009", "Grace Lee", balance=15_220.00, card_id="CARD-009", account_type="business"),
        Account("C010", "Ethan Brown", balance=640.75, card_id="CARD-010"),
    ]
    txns = [
        # C001 Priya: clean account, no prior goodwill refund
        Txn("T1001", "C001", ago(10), "fee", "Overseas transaction fee - Bali Surf Co", 3.42, fee_code="overseas_fee"),
        Txn("T1002", "C001", ago(20), "fee", "Late payment fee", 15.00, fee_code="late_fee"),
        Txn("T1003", "C001", ago(3), "card", "QuickBite Delivery", 42.10, merchant="QuickBite Delivery"),
        Txn("T1004", "C001", ago(21), "card", "SoundWave Online", 249.00, merchant="SoundWave Online"),
        Txn("T1005", "C001", ago(10), "card", "Bali Surf Co", 114.00, merchant="Bali Surf Co"),
        Txn("T1006", "C001", ago(2), "card", "Greengrocer Lane", 63.20, merchant="Greengrocer Lane"),
        # C002 Tom: had a goodwill refund 4 months ago
        Txn("T2001", "C002", ago(5), "fee", "Late payment fee", 15.00, fee_code="late_fee"),
        Txn("T2002", "C002", ago(12), "card", "FitLife Gym", 59.00, merchant="FitLife Gym"),
        # C003 Mei: in collections
        Txn("T3001", "C003", ago(8), "fee", "Late payment fee", 15.00, fee_code="late_fee"),
        Txn("T3002", "C003", ago(8), "fee", "Dishonour fee", 15.00, fee_code="dishonour_fee"),
        # C004 Liam: security hold
        Txn("T4001", "C004", ago(4), "fee", "Overseas transaction fee - Kyoto Rail", 2.10, fee_code="overseas_fee"),
        Txn("T4002", "C004", ago(2), "card", "Kyoto Rail", 70.00, merchant="Kyoto Rail"),
        # C005 Aisha: identity not verified
        Txn("T5001", "C005", ago(6), "card", "TechMart", 89.00, merchant="TechMart"),
        # C006 Jack: large purchase, duplicate booking (one pending)
        Txn("T6001", "C006", ago(15), "card", "Northside Electronics", 6_200.00, merchant="Northside Electronics"),
        Txn("T6002", "C006", ago(1), "card", "Staywell Stays", 890.00, merchant="Staywell Stays", status="pending"),
        Txn("T6003", "C006", ago(2), "card", "Staywell Stays", 890.00, merchant="Staywell Stays"),
        # C007 Sofia: old purchase, outgoing transfer to a scammer
        Txn("T7001", "C007", ago(150), "card", "Vintage Threads", 180.00, merchant="Vintage Threads"),
        Txn("T7002", "C007", ago(1), "transfer", "Transfer to R. Harlow", 2_000.00, merchant="R. Harlow"),
        Txn("T7003", "C007", ago(30), "card", "PetPal Supplies", 75.00, merchant="PetPal Supplies"),
        # C008 Noah: existing open dispute
        Txn("T8001", "C008", ago(25), "card", "GymCo", 120.00, merchant="GymCo",
            dispute={"id": "D-8801", "status": "under review - the merchant has until 20 Nov 2026 to respond"}),
        Txn("T8002", "C008", ago(70), "fee", "Overseas transaction fee - Lisbon Eats", 1.80, fee_code="overseas_fee"),
        # C009 Grace: business account, $80 dishonour fee
        Txn("T9001", "C009", ago(6), "fee", "Dishonour fee (business)", 80.00, fee_code="dishonour_fee"),
        Txn("T9002", "C009", ago(9), "card", "OfficeHub", 340.00, merchant="OfficeHub"),
        # C010 Ethan: old late fee, cancelled subscription still charged
        Txn("T10001", "C010", ago(75), "fee", "Late payment fee", 15.00, fee_code="late_fee"),
        Txn("T10002", "C010", ago(12), "card", "StreamFlix", 16.99, merchant="StreamFlix"),
    ]
    return {a.customer_id: a for a in accounts}, {t.txn_id: t for t in txns}


_ACCOUNTS, _TXNS = _seed()


@dataclass
class MockBank:
    """In-memory stand-in for the customer's core banking + card + CRM APIs."""

    accounts: dict[str, Account]
    txns: dict[str, Txn]
    refunds: list[dict] = field(default_factory=list)
    disputes: list[dict] = field(default_factory=list)
    handoffs: list[dict] = field(default_factory=list)

    @classmethod
    def fresh(cls) -> "MockBank":
        return cls(copy.deepcopy(_ACCOUNTS), copy.deepcopy(_TXNS))

    def txns_for(self, customer_id: str) -> list[Txn]:
        return sorted((t for t in self.txns.values() if t.customer_id == customer_id),
                      key=lambda t: t.date, reverse=True)
