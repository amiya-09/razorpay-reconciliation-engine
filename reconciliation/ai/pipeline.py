"""Wires the AI Reasoning Layer to the matching engine's output.

Only NEAR_MISS/AMBIGUOUS/UNMATCHED groups reach the LLM — the deterministic
tiers' clean MATCHED groups never do. This is the concrete enforcement of
BUILD_BRIEF Section 4's stated boundary ("deterministic logic for the ~80% of
unambiguous cases, LLM for the ~20% requiring judgment"): `augment_with_ai`
counts exactly how many of the total groups it invoked the LLM on, so the
results report can state that number rather than assert it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from reconciliation.ai.ambiguous_match import resolve_ambiguous_match
from reconciliation.ai.client import StructuredReasoningClient
from reconciliation.ai.exception_explain import explain_exception
from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.matching.types import GroupMatchResult, MatchStatus


@dataclass(frozen=True)
class AIAugmentedResult:
    match_result: GroupMatchResult
    ambiguous_decision: Optional[AmbiguousMatchDecision] = None
    exception_explanation: Optional[ExceptionExplanation] = None

    @property
    def ai_invoked(self) -> bool:
        return self.ambiguous_decision is not None or self.exception_explanation is not None


def augment_with_ai(
    results: list[GroupMatchResult], client: StructuredReasoningClient
) -> list[AIAugmentedResult]:
    augmented = []
    for result in results:
        decision: Optional[AmbiguousMatchDecision] = None
        explanation: Optional[ExceptionExplanation] = None

        if result.status in (MatchStatus.NEAR_MISS, MatchStatus.AMBIGUOUS):
            decision = resolve_ambiguous_match(result, client)
            if not decision.match:
                # The LLM itself concluded this isn't a real match — it still
                # needs a categorized, actionable exception, not just a
                # negative verdict.
                explanation = explain_exception(result, client, prior_decision=decision)
        elif result.status is MatchStatus.UNMATCHED:
            explanation = explain_exception(result, client)

        augmented.append(AIAugmentedResult(
            match_result=result, ambiguous_decision=decision, exception_explanation=explanation,
        ))
    return augmented


def summarize_ai_usage(augmented: list[AIAugmentedResult], client: StructuredReasoningClient) -> str:
    invoked = sum(1 for a in augmented if a.ai_invoked)
    invoked_pct = invoked / len(augmented) if augmented else 0.0
    total_input = sum(c.input_tokens for c in client.call_log)
    total_output = sum(c.output_tokens for c in client.call_log)
    total_latency = sum(c.latency_ms for c in client.call_log)
    return (
        f"AI layer invoked on {invoked} of {len(augmented)} groups ({invoked_pct:.1%}). "
        f"{len(client.call_log)} LLM calls, "
        f"{total_input} input tokens, {total_output} output tokens, "
        f"{total_latency:.0f}ms total latency."
    )
