"""Loader for `internal_ledger` — the business's own record of each transaction.

Raw schema (CSV or JSON rows):
    ledger_id, order_id, amount, currency, created_at, status, trap_category

`trap_category` is the hidden ground-truth label injected by the dataset
generator (Milestone 2) — carried through to CanonicalTransaction.ground_truth_trap_category
for evaluation only, never consumed by the matcher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from reconciliation.loaders.base import blank_to_none, read_rows
from reconciliation.models import CanonicalTransaction, SourceName


def load(path: Union[str, Path]) -> list[CanonicalTransaction]:
    return [_to_canonical(row) for row in read_rows(path)]


def _to_canonical(row: dict) -> CanonicalTransaction:
    return CanonicalTransaction(
        source=SourceName.INTERNAL_LEDGER,
        source_record_id=row["ledger_id"],
        order_id=blank_to_none(row.get("order_id")),
        amount=row["amount"],
        currency=blank_to_none(row.get("currency")) or "INR",
        created_at=row["created_at"],
        raw=dict(row),
        ground_truth_trap_category=blank_to_none(row.get("trap_category")),
    )
