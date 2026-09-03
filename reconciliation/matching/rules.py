"""Deterministic rule checks — arithmetic and date-window validation.

These are pure functions with no matching/join logic in them: given values
already extracted from one or two records, decide pass/fail and explain why.
`key_match.py` calls these while resolving a candidate group; they're kept
separate so each is unit-testable without constructing a full match.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from reconciliation.matching.types import RuleCheck

DEFAULT_AMOUNT_TOLERANCE = Decimal("0.05")  # paise-level rounding drift, not a real mismatch
DEFAULT_MAX_DATE_LAG_DAYS = 5  # generous settlement-lag window; real T+1/T+2 plus slack


def check_amount(label: str, left: Decimal, right: Decimal, tolerance: Decimal = DEFAULT_AMOUNT_TOLERANCE) -> RuleCheck:
    delta = abs(left - right)
    passed = delta <= tolerance
    return RuleCheck(
        name=label,
        passed=passed,
        detail=f"left={left} right={right} delta={delta} tolerance={tolerance}",
    )


def check_date_lag(
    label: str,
    earlier: datetime,
    later: Optional[datetime],
    max_lag_days: int = DEFAULT_MAX_DATE_LAG_DAYS,
) -> RuleCheck:
    if later is None:
        return RuleCheck(name=label, passed=True, detail="counterpart date unknown — skipped, not penalized")
    lag_days = (later - earlier).total_seconds() / 86400
    passed = 0 <= lag_days <= max_lag_days
    return RuleCheck(
        name=label,
        passed=passed,
        detail=f"lag={lag_days:.2f} days, allowed=[0, {max_lag_days}]",
    )


def check_fee_arithmetic(amount: Decimal, fee: Optional[Decimal], tax: Optional[Decimal], credit: Optional[Decimal]) -> RuleCheck:
    """Single-record check: credit == amount - fee - tax. Not a join — this
    validates one gateway_report record's own internal consistency, and is
    what proves the fee-deduction trap is arithmetic, not an error."""
    if fee is None or tax is None or credit is None:
        return RuleCheck(name="fee_arithmetic", passed=True, detail="fee/tax/credit not all present — skipped")
    expected = amount - fee - tax
    passed = expected == credit
    return RuleCheck(
        name="fee_arithmetic",
        passed=passed,
        detail=f"amount-fee-tax={expected} credit={credit}",
    )
