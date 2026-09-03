"""BUILD_BRIEF Section 4's "testing approach for the AI layer": evaluate the
ambiguous-match resolver against the dataset generator's hidden ground truth,
and measure decision consistency across repeated calls — reported honestly,
not hidden, per Section 4's explicit instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev
from typing import Optional

from reconciliation.ai.schemas import AmbiguousMatchDecision
from reconciliation.models import TrapCategory

# Trap categories where the deterministic-tier-missed candidate IS the same
# transaction (the AI layer should say match=True) vs. genuinely isn't
# (match=False). Categories not listed here have no fixed expectation — e.g.
# a true genuinely_unmatched record should never reach the ambiguous-match
# resolver at all (no candidate exists to be ambiguous about), so it's
# excluded from this accuracy measure rather than forced into either bucket.
EXPECTED_MATCH_TRUE = frozenset({
    TrapCategory.REFERENCE_ID_TYPO,
    TrapCategory.DATE_TIMEZONE_OFFSET,
    TrapCategory.CURRENCY_ROUNDING,
    TrapCategory.FEE_DEDUCTION,
    TrapCategory.SPLIT_TRANSACTION,
    TrapCategory.NETTED_SETTLEMENT,
})
EXPECTED_MATCH_FALSE = frozenset({
    TrapCategory.DUPLICATE_RECORD,
})


def expected_match_for(trap_category: Optional[TrapCategory]) -> Optional[bool]:
    if trap_category in EXPECTED_MATCH_TRUE:
        return True
    if trap_category in EXPECTED_MATCH_FALSE:
        return False
    return None


@dataclass(frozen=True)
class EvaluationRow:
    ground_truth: Optional[TrapCategory]
    predicted_match: bool

    @property
    def expected_match(self) -> Optional[bool]:
        return expected_match_for(self.ground_truth)

    @property
    def correct(self) -> Optional[bool]:
        expected = self.expected_match
        return None if expected is None else self.predicted_match == expected


def evaluate(rows: list[EvaluationRow]) -> dict:
    judged = [r for r in rows if r.correct is not None]
    skipped = len(rows) - len(judged)
    if not judged:
        return {"n_judged": 0, "n_skipped": skipped, "accuracy": None}
    correct = sum(1 for r in judged if r.correct)
    return {"n_judged": len(judged), "n_skipped": skipped, "accuracy": correct / len(judged)}


def consistency(decisions: list[AmbiguousMatchDecision]) -> dict:
    """Run the same ambiguous pair through the LLM multiple times and measure
    how much the decision varies — reported as part of "measured accuracy,"
    not hidden (BUILD_BRIEF Section 4)."""
    if not decisions:
        return {"n": 0, "agreement_rate": None, "confidence_mean": None, "confidence_stdev": None}
    matches = [d.match for d in decisions]
    confidences = [d.confidence for d in decisions]
    majority = matches.count(True) if matches.count(True) >= matches.count(False) else matches.count(False)
    return {
        "n": len(decisions),
        "agreement_rate": majority / len(matches),
        "confidence_mean": sum(confidences) / len(confidences),
        "confidence_stdev": pstdev(confidences) if len(confidences) > 1 else 0.0,
    }
