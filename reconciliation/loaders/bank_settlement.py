"""Loader for `bank_settlement` — what the bank statement actually shows credited.

Raw schema (CSV or JSON rows):
    bank_ref, amount, value_date, narration, account_id, trap_category

The bank has no concept of `order_id` — it only knows its own reference/UTR
(`bank_ref`, mapped to `reference_id`) and a free-text `narration`. That's why
gateway_report is the join hub between internal_ledger and bank_settlement
(see CanonicalTransaction docstring). Presence in a bank statement implies the
money has settled, so `settled=True` is fixed here rather than read from data.
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
        source=SourceName.BANK_SETTLEMENT,
        source_record_id=row["bank_ref"],
        reference_id=row["bank_ref"],
        amount=row["amount"],
        net_amount=row["amount"],
        currency=blank_to_none(row.get("currency")) or "INR",
        created_at=row["value_date"],
        settled_at=row["value_date"],
        settled=True,
        raw=dict(row),
        ground_truth_trap_category=blank_to_none(row.get("trap_category")),
    )
