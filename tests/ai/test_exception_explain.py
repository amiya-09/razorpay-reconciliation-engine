from datetime import datetime
from decimal import Decimal

from reconciliation.ai.exception_explain import build_prompt, explain_exception
from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName
from tests.ai.fake_client import FakeReasoningClient


def make_txn(source_record_id, amount="1000.00"):
    return CanonicalTransaction(
        source=SourceName.BANK_SETTLEMENT,
        source_record_id=source_record_id,
        amount=Decimal(amount),
        created_at=datetime(2026, 1, 1),
    )


def make_unmatched_result():
    return GroupMatchResult(
        left=(), right=(make_txn("b1"),), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE,
        confidence=0.0, notes=("no counterpart found for key 'UTR123'",),
    )


def test_build_prompt_includes_notes_and_records():
    prompt = build_prompt(make_unmatched_result())
    assert "b1" in prompt
    assert "no counterpart found" in prompt


def test_build_prompt_includes_prior_ai_decision_when_given():
    prior = AmbiguousMatchDecision(match=False, confidence=0.7, reasoning="dates too far apart", suspected_trap_category="other")
    prompt = build_prompt(make_unmatched_result(), prior_decision=prior)
    assert "dates too far apart" in prompt


def test_explain_exception_returns_validated_explanation():
    result = make_unmatched_result()
    explanation = ExceptionExplanation(
        category="genuinely_unmatched", explanation="no gateway or ledger record shares this reference",
        recommended_action="manual review", confidence=0.85,
    )
    client = FakeReasoningClient(responder=lambda system, user, fmt: explanation)
    returned = explain_exception(result, client)
    assert returned is explanation
    assert len(client.call_log) == 1
