"""AI Reasoning Layer, item 1: resolve a below-threshold or ambiguous match
candidate that the deterministic tiers couldn't confidently decide on.

Only ever called for NEAR_MISS/AMBIGUOUS groups — the ~80% the deterministic
tiers already resolved never reach here (BUILD_BRIEF Section 4's stated
AI-vs-deterministic boundary).
"""

from __future__ import annotations

from reconciliation.ai.client import StructuredReasoningClient
from reconciliation.ai.prompt_helpers import describe_side
from reconciliation.ai.schemas import AmbiguousMatchDecision
from reconciliation.matching.types import GroupMatchResult

SYSTEM_PROMPT = (
    "You are a financial reconciliation analyst reviewing a payment-matching "
    "candidate that a deterministic matcher could not confidently resolve on "
    "its own (either a below-threshold fuzzy match, or multiple candidates "
    "sharing a join key that didn't resolve to a clean group). Decide whether "
    "the two sides genuinely represent the same underlying transaction. Your "
    "reasoning becomes the human-readable explanation a finance team reads in "
    "an exception report, so be specific about what you observed — not a "
    "generic statement. suspected_trap_category should name the most likely "
    "real-world cause (e.g. reference_id_typo, duplicate_record, "
    "split_transaction, netted_settlement, genuinely_unmatched, other). "
    "Use the exact `currency` field shown in the record data (e.g. write "
    "'INR 500.00', not '$500.00' or any other symbol not present in the data)."
)


def build_prompt(result: GroupMatchResult) -> str:
    rule_desc = "\n".join(
        f"- {rc.name}: {'PASSED' if rc.passed else 'FAILED'} ({rc.detail})" for rc in result.rule_checks
    ) or "- none evaluated"
    return (
        f"Deterministic matcher status: {result.status.value}, tier: {result.tier.value}, "
        f"key_similarity: {result.key_similarity}\n\n"
        f"Left side ({len(result.left)} record(s)):\n{describe_side(result.left)}\n\n"
        f"Right side ({len(result.right)} record(s)):\n{describe_side(result.right)}\n\n"
        f"Rule checks:\n{rule_desc}\n\n"
        f"Matcher notes: {'; '.join(result.notes) or 'none'}\n\n"
        "Decide: do these represent the same transaction? Return your decision, "
        "a confidence score (0-1), your reasoning, and the suspected trap category."
    )


def resolve_ambiguous_match(result: GroupMatchResult, client: StructuredReasoningClient) -> AmbiguousMatchDecision:
    return client.call_structured(
        system=SYSTEM_PROMPT,
        user=build_prompt(result),
        output_format=AmbiguousMatchDecision,
    )
