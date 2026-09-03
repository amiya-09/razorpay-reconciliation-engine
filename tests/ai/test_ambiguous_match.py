from datetime import datetime
from decimal import Decimal

from reconciliation.ai.ambiguous_match import build_prompt, resolve_ambiguous_match
from reconciliation.ai.schemas import AmbiguousMatchDecision
from reconciliation.matching.rules import check_amount
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName
from tests.ai.fake_client import FakeReasoningClient


def make_txn(source_record_id, amount="1000.00"):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT,
        source_record_id=source_record_id,
        amount=Decimal(amount),
        created_at=datetime(2026, 1, 1),
    )


def make_near_miss_result():
    left = (make_txn("l1"),)
    right = (make_txn("r1"),)
    return GroupMatchResult(
        left=left, right=right, status=MatchStatus.NEAR_MISS, tier=MatchTier.FUZZY,
        confidence=0.6, key_similarity=0.7,
        rule_checks=(check_amount("amount_match", Decimal("1000.00"), Decimal("1000.00")),),
    )


def test_build_prompt_includes_record_ids_and_rule_checks():
    prompt = build_prompt(make_near_miss_result())
    assert "l1" in prompt
    assert "r1" in prompt
    assert "amount_match" in prompt


def test_resolve_ambiguous_match_returns_validated_decision():
    result = make_near_miss_result()
    decision = AmbiguousMatchDecision(
        match=True, confidence=0.8, reasoning="amounts and dates align closely",
        suspected_trap_category="reference_id_typo",
    )
    client = FakeReasoningClient(responder=lambda system, user, fmt: decision)
    returned = resolve_ambiguous_match(result, client)
    assert returned is decision
    assert len(client.call_log) == 1


def test_resolve_ambiguous_match_passes_correct_output_format():
    result = make_near_miss_result()
    captured = {}

    def responder(system, user, fmt):
        captured["fmt"] = fmt
        return AmbiguousMatchDecision(match=False, confidence=0.9, reasoning="x", suspected_trap_category="other")

    client = FakeReasoningClient(responder=responder)
    resolve_ambiguous_match(result, client)
    assert captured["fmt"] is AmbiguousMatchDecision
