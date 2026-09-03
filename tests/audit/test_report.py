from datetime import datetime
from decimal import Decimal

from reconciliation.ai.client import AICallLog
from reconciliation.ai.pipeline import AIAugmentedResult
from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.audit.report import build_breakdown, build_exceptions, build_report
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName
from tests.ai.fake_client import FakeReasoningClient


def txn(source_record_id):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT, source_record_id=source_record_id,
        amount=Decimal("100.00"), created_at=datetime(2026, 1, 1),
    )


def matched():
    return GroupMatchResult(left=(txn("l1"),), right=(txn("r1"),), status=MatchStatus.MATCHED, tier=MatchTier.EXACT, confidence=1.0)


def near_miss_confirmed():
    result = GroupMatchResult(left=(txn("l2"),), right=(txn("r2"),), status=MatchStatus.NEAR_MISS, tier=MatchTier.FUZZY, confidence=0.5)
    decision = AmbiguousMatchDecision(match=True, confidence=0.85, reasoning="typo, same amount/date", suspected_trap_category="reference_id_typo")
    return AIAugmentedResult(match_result=result, ambiguous_decision=decision, exception_explanation=None)


def near_miss_rejected():
    result = GroupMatchResult(left=(txn("l3"),), right=(txn("r3"),), status=MatchStatus.NEAR_MISS, tier=MatchTier.FUZZY, confidence=0.4)
    decision = AmbiguousMatchDecision(match=False, confidence=0.7, reasoning="different transactions", suspected_trap_category="genuinely_unmatched")
    explanation = ExceptionExplanation(category="genuinely_unmatched", explanation="no real counterpart", recommended_action="manual review", confidence=0.7)
    return AIAugmentedResult(match_result=result, ambiguous_decision=decision, exception_explanation=explanation)


def unmatched_with_explanation():
    result = GroupMatchResult(left=(), right=(txn("r4"),), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE, confidence=0.0)
    explanation = ExceptionExplanation(category="genuinely_unmatched", explanation="bank credit with no gateway record", recommended_action="review", confidence=0.9)
    return AIAugmentedResult(match_result=result, ambiguous_decision=None, exception_explanation=explanation)


def unmatched_no_ai():
    result = GroupMatchResult(left=(), right=(txn("r5"),), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE, confidence=0.0, notes=("no counterpart found",))
    return AIAugmentedResult(match_result=result, ambiguous_decision=None, exception_explanation=None)


def test_build_breakdown_counts_statuses_and_ai_resolved():
    augmented = [
        AIAugmentedResult(match_result=matched()),
        near_miss_confirmed(),
        near_miss_rejected(),
    ]
    results = [a.match_result for a in augmented]
    breakdown = build_breakdown("test", results, augmented)
    assert breakdown.total == 3
    assert breakdown.matched == 1
    assert breakdown.near_miss == 2
    assert breakdown.ai_resolved == 1  # only near_miss_confirmed
    assert breakdown.match_rate == 1 / 3
    assert breakdown.match_rate_including_ai == 2 / 3


def test_clean_matched_group_is_never_an_exception():
    exceptions = build_exceptions("j", [AIAugmentedResult(match_result=matched())])
    assert exceptions == []


def test_ai_confirmed_near_miss_is_not_an_exception():
    exceptions = build_exceptions("j", [near_miss_confirmed()])
    assert exceptions == []


def test_ai_rejected_near_miss_is_an_exception_with_ai_category_and_explanation():
    [exc] = build_exceptions("j", [near_miss_rejected()])
    assert exc.category == "genuinely_unmatched"
    assert exc.explanation == "no real counterpart"
    assert exc.status == "near_miss"


def test_unmatched_with_ai_explanation_uses_ai_category():
    [exc] = build_exceptions("j", [unmatched_with_explanation()])
    assert exc.category == "genuinely_unmatched"
    assert "bank credit" in exc.explanation


def test_unmatched_without_ai_falls_back_to_matcher_notes():
    [exc] = build_exceptions("j", [unmatched_no_ai()])
    assert exc.category == "unmatched"
    assert exc.explanation == "no counterpart found"


def test_build_report_aggregates_both_joins_and_renders_text():
    lg_augmented = [AIAugmentedResult(match_result=matched()), near_miss_rejected()]
    gb_augmented = [unmatched_with_explanation()]
    client = FakeReasoningClient()
    client.call_log.append(AICallLog(model="fake", input_tokens=100, output_tokens=50, latency_ms=200.0))
    report = build_report(
        ledger_vs_gateway=[a.match_result for a in lg_augmented],
        gateway_vs_bank=[a.match_result for a in gb_augmented],
        lg_augmented=lg_augmented, gb_augmented=gb_augmented, client=client,
    )
    assert len(report.exceptions) == 2
    assert report.ai_usage["calls"] == 1
    assert report.ai_usage["input_tokens"] == 100
    text = report.render_text()
    assert "Reconciliation Report" in text
    assert "Exceptions (2)" in text
    assert "genuinely_unmatched" in text


def test_build_report_handles_no_client_gracefully():
    report = build_report(ledger_vs_gateway=[matched()], gateway_vs_bank=[], lg_augmented=[AIAugmentedResult(match_result=matched())], gb_augmented=[], client=None)
    assert report.ai_usage["calls"] == 0
    assert report.exceptions == []
