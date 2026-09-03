"""Canonical transaction model.

Every source loader (internal_ledger, bank_settlement, gateway_report) maps its
raw rows into this one shape. Matching, exception classification, and the AI
reasoning layer all operate on CanonicalTransaction — none of them need to know
which source a record came from beyond the `source` field itself.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SourceName(str, Enum):
    INTERNAL_LEDGER = "internal_ledger"
    BANK_SETTLEMENT = "bank_settlement"
    GATEWAY_REPORT = "gateway_report"


class TrapCategory(str, Enum):
    """Ground-truth label set from BUILD_BRIEF Section 4b.

    Populated only by the dataset generator, for our own precision/recall
    evaluation. Never read by the matcher or the AI reasoning layer — doing so
    would make the evaluation meaningless.
    """

    CLEAN_EXACT_MATCH = "clean_exact_match"
    FEE_DEDUCTION = "fee_deduction"
    SPLIT_TRANSACTION = "split_transaction"
    NETTED_SETTLEMENT = "netted_settlement"
    DATE_TIMEZONE_OFFSET = "date_timezone_offset"
    REFERENCE_ID_TYPO = "reference_id_typo"
    CURRENCY_ROUNDING = "currency_rounding"
    PENDING_ON_HOLD = "pending_on_hold"
    GENUINELY_UNMATCHED = "genuinely_unmatched"
    DUPLICATE_RECORD = "duplicate_record"


class CanonicalTransaction(BaseModel):
    """One transaction record, normalized to a common shape regardless of source.

    Join-key design (drives the matching tiers built in later milestones):
      - internal_ledger <-> gateway_report: joins on `order_id` (both know it).
      - gateway_report <-> bank_settlement: joins on `reference_id`
        (gateway's settlement_utr vs. bank's own reference/UTR field) — this is
        the field the reference-ID-typo trap corrupts.
      - internal_ledger <-> bank_settlement: no direct join key exists in
        reality (the bank doesn't know order_id); gateway_report is the hub
        that bridges them. A direct amount+date fallback tier handles the case
        where a gateway record is itself missing.
    """

    model_config = ConfigDict(frozen=True)

    source: SourceName
    source_record_id: str

    order_id: Optional[str] = None
    reference_id: Optional[str] = None

    amount: Decimal
    net_amount: Optional[Decimal] = None
    fee: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    currency: str = "INR"

    created_at: datetime
    settled_at: Optional[datetime] = None
    settled: Optional[bool] = None
    on_hold: Optional[bool] = None

    settlement_id: Optional[str] = None

    raw: dict[str, Any] = Field(default_factory=dict)
    ground_truth_trap_category: Optional[TrapCategory] = None

    @property
    def settlement_amount(self) -> Decimal:
        """The amount that should actually hit the bank: net if known, else gross.

        Comparing this (not raw `amount`) against bank_settlement's amount is
        what keeps the fee-deduction trap from being misread as a mismatch.
        """
        return self.net_amount if self.net_amount is not None else self.amount
