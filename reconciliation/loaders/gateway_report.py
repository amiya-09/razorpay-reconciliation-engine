"""Loader for `gateway_report` — Razorpay-style settlement recon report.

Raw schema (CSV or JSON rows), per BUILD_BRIEF Section 3:
    entity_id, order_id, type, amount, fee, tax, credit, settled, on_hold,
    created_at, settled_at, settlement_id, settlement_utr, trap_category

`credit` (net settled amount) maps to `net_amount`; `settlement_utr` maps to
`reference_id` since that's the field joined against bank_settlement.
`settled`/`on_hold` arrive as strings from CSV or native bools from JSON —
normalized by parse_optional_bool either way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from reconciliation.loaders.base import blank_to_none, parse_optional_bool, read_rows
from reconciliation.models import CanonicalTransaction, SourceName


def load(path: Union[str, Path]) -> list[CanonicalTransaction]:
    return [_to_canonical(row) for row in read_rows(path)]


def _to_canonical(row: dict) -> CanonicalTransaction:
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT,
        source_record_id=row["entity_id"],
        order_id=blank_to_none(row.get("order_id")),
        reference_id=blank_to_none(row.get("settlement_utr")),
        amount=row["amount"],
        net_amount=blank_to_none(row.get("credit")),
        fee=blank_to_none(row.get("fee")),
        tax=blank_to_none(row.get("tax")),
        currency=blank_to_none(row.get("currency")) or "INR",
        created_at=row["created_at"],
        settled_at=blank_to_none(row.get("settled_at")),
        settled=parse_optional_bool(row.get("settled")),
        on_hold=parse_optional_bool(row.get("on_hold")),
        settlement_id=blank_to_none(row.get("settlement_id")),
        raw=dict(row),
        ground_truth_trap_category=blank_to_none(row.get("trap_category")),
    )
