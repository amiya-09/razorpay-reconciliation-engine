"""Results report: match rate %, per-tier breakdown (incl. AI-resolved count),
full exception list with AI-generated reasoning, AI call cost/latency/
consistency logs. This is the direct deliverable for BUILD_BRIEF's "the bar"
— measured accuracy plus an honest exception list, not a cherry-picked demo.

A group only counts as an "exception" if it never resolved to a confirmed
match — including a NEAR_MISS/AMBIGUOUS group the AI layer subsequently
confirmed as a real match (see build_exceptions). Conversely, `match_rate`
(deterministic-only) and `match_rate_including_ai` are reported separately so
the report is honest about how much of the result rests on the LLM's
judgment vs. the deterministic tiers alone (BUILD_BRIEF Section 4's stated
requirement: state how much of the ~20% the AI layer actually touched).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

from reconciliation.ai.client import StructuredReasoningClient
from reconciliation.ai.pipeline import AIAugmentedResult
from reconciliation.matching.types import GroupMatchResult, MatchStatus


@dataclass(frozen=True)
class TierBreakdown:
    label: str
    total: int
    matched: int
    near_miss: int
    ambiguous: int
    unmatched: int
    ai_resolved: int  # of the non-deterministic-matched groups, how many the AI confirmed as real matches
    tier_counts: dict[str, int]

    @property
    def match_rate(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def match_rate_including_ai(self) -> float:
        return (self.matched + self.ai_resolved) / self.total if self.total else 0.0

    def render(self) -> str:
        return (
            f"{self.label}: {self.total} groups\n"
            f"  deterministic match rate: {self.match_rate:.1%} ({self.matched}/{self.total})\n"
            f"  match rate incl. AI-resolved: {self.match_rate_including_ai:.1%} "
            f"({self.matched}+{self.ai_resolved} ai-resolved)/{self.total}\n"
            f"  near_miss={self.near_miss} ambiguous={self.ambiguous} unmatched={self.unmatched}\n"
            f"  tier breakdown: {self.tier_counts}"
        )


@dataclass(frozen=True)
class ExceptionRecord:
    join: str
    left_ids: tuple[str, ...]
    right_ids: tuple[str, ...]
    status: str
    category: str
    explanation: str
    confidence: float


def build_breakdown(label: str, results: list[GroupMatchResult], augmented: list[AIAugmentedResult]) -> TierBreakdown:
    status_counts = Counter(r.status for r in results)
    ai_resolved = sum(1 for a in augmented if a.ambiguous_decision is not None and a.ambiguous_decision.match)
    return TierBreakdown(
        label=label,
        total=len(results),
        matched=status_counts.get(MatchStatus.MATCHED, 0),
        near_miss=status_counts.get(MatchStatus.NEAR_MISS, 0),
        ambiguous=status_counts.get(MatchStatus.AMBIGUOUS, 0),
        unmatched=status_counts.get(MatchStatus.UNMATCHED, 0),
        ai_resolved=ai_resolved,
        tier_counts=dict(Counter(r.tier.value for r in results)),
    )


def _is_ai_confirmed_match(augmented: AIAugmentedResult) -> bool:
    return augmented.ambiguous_decision is not None and augmented.ambiguous_decision.match


def build_exceptions(join_label: str, augmented: list[AIAugmentedResult]) -> list[ExceptionRecord]:
    exceptions = []
    for a in augmented:
        result = a.match_result
        if result.status is MatchStatus.MATCHED:
            continue  # clean deterministic match — never an exception
        if _is_ai_confirmed_match(a):
            continue  # AI reviewed a near-miss/ambiguous candidate and confirmed it's real

        if a.exception_explanation is not None:
            category = a.exception_explanation.category
            explanation = a.exception_explanation.explanation
            confidence = a.exception_explanation.confidence
        elif a.ambiguous_decision is not None:
            category = a.ambiguous_decision.suspected_trap_category
            explanation = a.ambiguous_decision.reasoning
            confidence = a.ambiguous_decision.confidence
        else:
            # AI layer wasn't invoked on this group at all (e.g. no client passed in)
            category = result.status.value
            explanation = "; ".join(result.notes) or "no explanation available — AI layer was not invoked"
            confidence = result.confidence

        exceptions.append(ExceptionRecord(
            join=join_label, left_ids=result.left_ids, right_ids=result.right_ids,
            status=result.status.value, category=category, explanation=explanation, confidence=confidence,
        ))
    return exceptions


@dataclass(frozen=True)
class ReconciliationReport:
    ledger_gateway: TierBreakdown
    gateway_bank: TierBreakdown
    exceptions: list[ExceptionRecord]
    ai_usage: dict
    consistency: Optional[dict] = None

    def render_text(self) -> str:
        lines = [
            "=== Reconciliation Report ===",
            "",
            self.ledger_gateway.render(),
            "",
            self.gateway_bank.render(),
            "",
            f"AI usage: {self.ai_usage}",
        ]
        if self.consistency is not None:
            lines.append(f"AI consistency check: {self.consistency}")
        lines.append("")
        lines.append(f"=== Exceptions ({len(self.exceptions)}) ===")
        for exc in self.exceptions:
            lines.append(
                f"[{exc.join}] status={exc.status} category={exc.category} confidence={exc.confidence:.2f} "
                f"left={list(exc.left_ids)} right={list(exc.right_ids)}\n    {exc.explanation}"
            )
        if not self.exceptions:
            lines.append("(none)")
        return "\n".join(lines)


def _ai_usage_summary(augmented: list[AIAugmentedResult], client: Optional[StructuredReasoningClient]) -> dict:
    invoked = sum(1 for a in augmented if a.ai_invoked)
    if client is None:
        return {"invoked": invoked, "total": len(augmented), "calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0.0}
    return {
        "invoked": invoked,
        "total": len(augmented),
        "calls": len(client.call_log),
        "input_tokens": sum(c.input_tokens for c in client.call_log),
        "output_tokens": sum(c.output_tokens for c in client.call_log),
        "latency_ms": round(sum(c.latency_ms for c in client.call_log), 1),
    }


def build_report(
    ledger_vs_gateway: list[GroupMatchResult],
    gateway_vs_bank: list[GroupMatchResult],
    lg_augmented: list[AIAugmentedResult],
    gb_augmented: list[AIAugmentedResult],
    client: Optional[StructuredReasoningClient] = None,
    consistency: Optional[dict] = None,
) -> ReconciliationReport:
    all_augmented = lg_augmented + gb_augmented
    return ReconciliationReport(
        ledger_gateway=build_breakdown("ledger<->gateway", ledger_vs_gateway, lg_augmented),
        gateway_bank=build_breakdown("gateway<->bank", gateway_vs_bank, gb_augmented),
        exceptions=build_exceptions("ledger<->gateway", lg_augmented) + build_exceptions("gateway<->bank", gb_augmented),
        ai_usage=_ai_usage_summary(all_augmented, client),
        consistency=consistency,
    )
