"""AI Reasoning Layer, item 2: turn an unresolved match group into a specific,
actionable exception explanation instead of a generic "STATUS: UNMATCHED".

Takes any GroupMatchResult that isn't a clean MATCHED outcome — this includes
groups the ambiguous_match resolver already looked at (AmbiguousMatchDecision
can be passed in as extra context) as well as plain UNMATCHED groups that
never needed a match/no-match judgment call, just an explanation of why.
"""

from __future__ import annotations

from typing import Optional

from reconciliation.ai.prompt_helpers import describe_side
from reconciliation.ai.client import StructuredReasoningClient
from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.matching.types import GroupMatchResult

SYSTEM_PROMPT = (
    "You are a financial reconciliation analyst writing the exception report "
    "a finance team reads to decide what needs manual follow-up. Given a "
    "record (or group of records) a deterministic matcher could not resolve, "
    "produce a specific category, a concrete explanation citing the actual "
    "amounts/dates/IDs involved, and a recommended next action. Never write a "
    "generic explanation like 'no match found' — say what you can tell from "
    "the data (e.g. 'this gateway record shows on_hold=true, so the payout is "
    "pending, not missing' or 'this bank credit has no matching gateway "
    "settlement_utr and its amount doesn't match any known ledger record — "
    "likely a misdirected or non-order-related deposit'). Use the exact "
    "`currency` field shown in the record data (e.g. write 'INR 500.00', "
    "not '$500.00' or any other symbol not present in the data)."
)


def build_prompt(result: GroupMatchResult, prior_decision: Optional[AmbiguousMatchDecision] = None) -> str:
    prior = (
        f"\nAn earlier AI review already judged this NOT a match "
        f"(confidence={prior_decision.confidence}): {prior_decision.reasoning}\n"
        if prior_decision is not None
        else ""
    )
    return (
        f"Matcher status: {result.status.value}, tier: {result.tier.value}\n\n"
        f"Left side ({len(result.left)} record(s)):\n{describe_side(result.left)}\n\n"
        f"Right side ({len(result.right)} record(s)):\n{describe_side(result.right)}\n\n"
        f"Matcher notes: {'; '.join(result.notes) or 'none'}\n"
        f"{prior}\n"
        "Produce the exception's category, a specific explanation, a "
        "recommended action, and your confidence in this categorization."
    )


def explain_exception(
    result: GroupMatchResult,
    client: StructuredReasoningClient,
    prior_decision: Optional[AmbiguousMatchDecision] = None,
) -> ExceptionExplanation:
    return client.call_structured(
        system=SYSTEM_PROMPT,
        user=build_prompt(result, prior_decision),
        output_format=ExceptionExplanation,
    )
