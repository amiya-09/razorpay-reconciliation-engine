from datetime import datetime
from decimal import Decimal

from reconciliation.ai.pipeline import AIAugmentedResult
from reconciliation.ai.schemas import AmbiguousMatchDecision
from reconciliation.audit.build import build_audit_log
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName


def txn(source_record_id):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT, source_record_id=source_record_id,
        amount=Decimal("100.00"), created_at=datetime(2026, 1, 1),
    )


def test_build_audit_log_records_match_and_ai_decision_for_each_group():
    result = GroupMatchResult(left=(txn("l1"),), right=(txn("r1"),), status=MatchStatus.NEAR_MISS, tier=MatchTier.FUZZY, confidence=0.5)
    decision = AmbiguousMatchDecision(match=True, confidence=0.9, reasoning="x", suspected_trap_category="reference_id_typo")
    augmented = AIAugmentedResult(match_result=result, ambiguous_decision=decision)

    log = build_audit_log([augmented], [])
    event_types = [e.event_type for e in log.entries]
    assert event_types == ["match_result", "ai_ambiguous_decision"]


def test_build_audit_log_preserves_join_order_across_both_joins():
    lg = AIAugmentedResult(match_result=GroupMatchResult(left=(txn("l1"),), right=(), status=MatchStatus.MATCHED, tier=MatchTier.EXACT, confidence=1.0))
    gb = AIAugmentedResult(match_result=GroupMatchResult(left=(txn("g1"),), right=(), status=MatchStatus.MATCHED, tier=MatchTier.EXACT, confidence=1.0))
    log = build_audit_log([lg], [gb])
    joins = [e.payload["join"] for e in log.entries]
    assert joins == ["ledger<->gateway", "gateway<->bank"]
