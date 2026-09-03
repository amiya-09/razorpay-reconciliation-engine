import json
from datetime import datetime
from decimal import Decimal

from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.audit.log import AuditLog
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName


def make_txn(source_record_id):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT, source_record_id=source_record_id,
        amount=Decimal("100.00"), created_at=datetime(2026, 1, 1),
    )


def test_record_assigns_incrementing_sequence_numbers():
    log = AuditLog()
    log.record("event_a", x=1)
    log.record("event_b", y=2)
    assert [e.seq for e in log.entries] == [0, 1]


def test_entries_are_immutable_snapshot():
    log = AuditLog()
    log.record("event_a")
    entries = log.entries
    log.record("event_b")
    assert len(entries) == 1  # the earlier snapshot doesn't see later appends


def test_record_match_captures_ids_and_status():
    log = AuditLog()
    result = GroupMatchResult(
        left=(make_txn("l1"),), right=(make_txn("r1"),), status=MatchStatus.MATCHED,
        tier=MatchTier.EXACT, confidence=1.0,
    )
    entry = log.record_match("ledger<->gateway", result)
    assert entry.payload["left_ids"] == ["l1"]
    assert entry.payload["right_ids"] == ["r1"]
    assert entry.payload["status"] == "matched"


def test_record_ai_decision_returns_none_when_no_decision():
    log = AuditLog()
    result = GroupMatchResult(left=(), right=(), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE, confidence=0.0)
    assert log.record_ai_decision("j", result, None) is None
    assert len(log.entries) == 0


def test_record_ai_decision_captures_reasoning():
    log = AuditLog()
    result = GroupMatchResult(left=(), right=(), status=MatchStatus.NEAR_MISS, tier=MatchTier.FUZZY, confidence=0.5)
    decision = AmbiguousMatchDecision(match=True, confidence=0.8, reasoning="typo in reference", suspected_trap_category="reference_id_typo")
    entry = log.record_ai_decision("gateway<->bank", result, decision)
    assert entry.payload["reasoning"] == "typo in reference"
    assert entry.payload["match"] is True


def test_record_ai_explanation_captures_category_and_action():
    log = AuditLog()
    result = GroupMatchResult(left=(), right=(), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE, confidence=0.0)
    explanation = ExceptionExplanation(category="genuinely_unmatched", explanation="no counterpart", recommended_action="manual review", confidence=0.9)
    entry = log.record_ai_explanation("j", result, explanation)
    assert entry.payload["category"] == "genuinely_unmatched"
    assert entry.payload["recommended_action"] == "manual review"


def test_to_jsonl_produces_one_valid_json_object_per_line():
    log = AuditLog()
    log.record("event_a", x=1)
    log.record("event_b", y="two")
    lines = log.to_jsonl().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event_type"] == "event_a"
    assert parsed[1]["y"] == "two"
