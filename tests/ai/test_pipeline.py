from datetime import datetime
from decimal import Decimal

from reconciliation.ai.pipeline import augment_with_ai, summarize_ai_usage
from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName
from tests.ai.fake_client import FakeReasoningClient


def make_txn(source_record_id, amount="1000.00"):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT, source_record_id=source_record_id,
        amount=Decimal(amount), created_at=datetime(2026, 1, 1),
    )


def matched_result():
    return GroupMatchResult(left=(make_txn("l1"),), right=(make_txn("r1"),), status=MatchStatus.MATCHED, tier=MatchTier.EXACT, confidence=1.0)


def near_miss_result():
    return GroupMatchResult(left=(make_txn("l2"),), right=(make_txn("r2"),), status=MatchStatus.NEAR_MISS, tier=MatchTier.FUZZY, confidence=0.5, key_similarity=0.6)


def ambiguous_result():
    return GroupMatchResult(left=(make_txn("l3"),), right=(make_txn("r3"),), status=MatchStatus.AMBIGUOUS, tier=MatchTier.EXACT, confidence=0.2)


def unmatched_result():
    return GroupMatchResult(left=(), right=(make_txn("r4"),), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE, confidence=0.0)


def test_clean_matched_groups_never_invoke_the_ai_layer():
    client = FakeReasoningClient(responder=lambda s, u, f: (_ for _ in ()).throw(AssertionError("should not be called")))
    augmented = augment_with_ai([matched_result()], client)
    assert len(client.call_log) == 0
    assert augmented[0].ai_invoked is False


def test_near_miss_where_ai_confirms_match_produces_decision_only():
    decision = AmbiguousMatchDecision(match=True, confidence=0.9, reasoning="close enough", suspected_trap_category="reference_id_typo")
    client = FakeReasoningClient(responder=lambda s, u, f: decision if f.__name__ == "AmbiguousMatchDecision" else None)
    [augmented] = augment_with_ai([near_miss_result()], client)
    assert augmented.ambiguous_decision is decision
    assert augmented.exception_explanation is None
    assert len(client.call_log) == 1  # only the ambiguous-match call, no follow-up explanation needed


def test_near_miss_where_ai_rejects_match_also_produces_an_explanation():
    decision = AmbiguousMatchDecision(match=False, confidence=0.8, reasoning="different transactions", suspected_trap_category="genuinely_unmatched")
    explanation = ExceptionExplanation(category="genuinely_unmatched", explanation="x", recommended_action="review", confidence=0.8)

    def responder(system, user, fmt):
        return decision if fmt is AmbiguousMatchDecision else explanation

    client = FakeReasoningClient(responder=responder)
    [augmented] = augment_with_ai([ambiguous_result()], client)
    assert augmented.ambiguous_decision is decision
    assert augmented.exception_explanation is explanation
    assert len(client.call_log) == 2  # decision + follow-up explanation


def test_plain_unmatched_group_goes_straight_to_explanation_no_decision_call():
    explanation = ExceptionExplanation(category="genuinely_unmatched", explanation="x", recommended_action="review", confidence=0.9)
    client = FakeReasoningClient(responder=lambda s, u, f: explanation)
    [augmented] = augment_with_ai([unmatched_result()], client)
    assert augmented.ambiguous_decision is None
    assert augmented.exception_explanation is explanation
    assert len(client.call_log) == 1


def test_summarize_ai_usage_counts_invoked_groups_and_tokens():
    decision = AmbiguousMatchDecision(match=True, confidence=0.9, reasoning="x", suspected_trap_category="other")
    client = FakeReasoningClient(responder=lambda s, u, f: decision)
    augmented = augment_with_ai([matched_result(), near_miss_result()], client)
    summary = summarize_ai_usage(augmented, client)
    assert "1 of 2 groups" in summary
    assert "50.0%" in summary


def test_summarize_ai_usage_handles_empty_input_without_dividing_by_zero():
    client = FakeReasoningClient()
    summary = summarize_ai_usage([], client)
    assert "0 of 0 groups" in summary
