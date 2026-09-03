"""Shared prompt-formatting helpers for the AI Reasoning Layer."""

from __future__ import annotations

from reconciliation.models import CanonicalTransaction


def describe_side(records: tuple[CanonicalTransaction, ...]) -> str:
    if not records:
        return "  (none)"
    lines = []
    for t in records:
        lines.append(
            f"  - id={t.source_record_id} source={t.source.value} currency={t.currency} amount={t.amount} "
            f"fee={t.fee} tax={t.tax} net_amount={t.net_amount} "
            f"order_id={t.order_id} reference_id={t.reference_id} "
            f"created_at={t.created_at.isoformat()} settled_at={t.settled_at.isoformat() if t.settled_at else None} "
            f"settled={t.settled} on_hold={t.on_hold}"
        )
    return "\n".join(lines)
