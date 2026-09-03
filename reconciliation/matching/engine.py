"""Wires the generic tiered matcher to our 3 concrete sources.

Two joins, run independently (stitching them into a full ledger->gateway->bank
chain per transaction is a reporting concern — Milestone 6):

  ledger_vs_gateway: on `order_id`, comparing gross `amount` to gross `amount`.
    Fuzzy tier disabled — order_id is a strict system identifier both sources
    always agree on exactly; a non-exact order_id is a data problem, not a
    typo to guess through (see key_match.py enable_fuzzy_tier, D9).

  gateway_vs_bank: on `reference_id` (gateway's settlement_utr vs. bank's own
    reference), comparing `settlement_amount` (net) to `settlement_amount`.
    Fuzzy tier enabled — this is exactly the field the reference-ID-typo trap
    corrupts, and the field real bank exports actually truncate/garble.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from reconciliation.matching.key_match import match_by_key
from reconciliation.matching.types import GroupMatchResult, MatchStatus
from reconciliation.models import CanonicalTransaction


@dataclass(frozen=True)
class JoinSummary:
    label: str
    total_groups: int
    status_counts: dict[str, int]
    tier_counts: dict[str, int]

    @property
    def match_rate(self) -> float:
        matched = self.status_counts.get(MatchStatus.MATCHED.value, 0)
        return matched / self.total_groups if self.total_groups else 0.0

    def __str__(self) -> str:
        lines = [f"{self.label}: {self.total_groups} groups, match_rate={self.match_rate:.1%}"]
        lines.append(f"  status: {dict(self.status_counts)}")
        lines.append(f"  tier:   {dict(self.tier_counts)}")
        return "\n".join(lines)


def summarize(label: str, results: list[GroupMatchResult]) -> JoinSummary:
    return JoinSummary(
        label=label,
        total_groups=len(results),
        status_counts=dict(Counter(r.status.value for r in results)),
        tier_counts=dict(Counter(r.tier.value for r in results)),
    )


def match_ledger_to_gateway(
    ledger: list[CanonicalTransaction], gateway: list[CanonicalTransaction]
) -> list[GroupMatchResult]:
    return match_by_key(
        left=ledger,
        right=gateway,
        key_fn=lambda t: t.order_id,
        amount_fn_left=lambda t: t.amount,
        amount_fn_right=lambda t: t.amount,
        date_fn_left=lambda t: t.created_at,
        date_fn_right=lambda t: t.created_at,
        max_date_lag_days=1,
        enable_fuzzy_tier=False,
    )


def match_gateway_to_bank(
    gateway: list[CanonicalTransaction], bank: list[CanonicalTransaction]
) -> list[GroupMatchResult]:
    return match_by_key(
        left=gateway,
        right=bank,
        key_fn=lambda t: t.reference_id,
        amount_fn_left=lambda t: t.settlement_amount,
        amount_fn_right=lambda t: t.settlement_amount,
        date_fn_left=lambda t: t.created_at,
        date_fn_right=lambda t: t.settled_at or t.created_at,
        max_date_lag_days=5,
        enable_fuzzy_tier=True,
    )


@dataclass(frozen=True)
class ReconciliationResult:
    ledger_vs_gateway: list[GroupMatchResult]
    gateway_vs_bank: list[GroupMatchResult]

    def summary(self) -> str:
        return "\n\n".join([
            str(summarize("ledger <-> gateway (order_id)", self.ledger_vs_gateway)),
            str(summarize("gateway <-> bank (reference_id)", self.gateway_vs_bank)),
        ])


def run(
    ledger: list[CanonicalTransaction],
    gateway: list[CanonicalTransaction],
    bank: list[CanonicalTransaction],
) -> ReconciliationResult:
    return ReconciliationResult(
        ledger_vs_gateway=match_ledger_to_gateway(ledger, gateway),
        gateway_vs_bank=match_gateway_to_bank(gateway, bank),
    )
