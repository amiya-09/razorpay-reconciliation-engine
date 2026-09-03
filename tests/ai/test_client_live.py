"""One real call to the Groq API, to catch drift between our
GroqReasoningClient wrapper and the actual SDK/API contract that the
FakeReasoningClient-based tests can't catch. Skipped automatically when no
credentials are configured (no GROQ_API_KEY) — never fails CI/local runs
just because a key isn't present.
"""

import os

import pytest

from reconciliation.ai.client import GroqReasoningClient
from reconciliation.ai.schemas import ExceptionExplanation


def _has_groq_credentials() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


@pytest.mark.skipif(not _has_groq_credentials(), reason="no Groq API credentials configured")
def test_live_call_structured_returns_validated_object_and_logs_usage():
    client = GroqReasoningClient()
    result = client.call_structured(
        system="You are a financial reconciliation analyst.",
        user=(
            "A bank credit of ₹500.00 has no matching gateway settlement_utr "
            "or internal ledger order_id. Categorize this exception."
        ),
        output_format=ExceptionExplanation,
        max_tokens=512,
    )
    assert isinstance(result, ExceptionExplanation)
    assert 0.0 <= result.confidence <= 1.0
    assert len(client.call_log) == 1
    assert client.call_log[0].input_tokens > 0
