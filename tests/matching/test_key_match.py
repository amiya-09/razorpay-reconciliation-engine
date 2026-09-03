from datetime import datetime, timedelta
from decimal import Decimal

from reconciliation.matching.key_match import match_by_key
from reconciliation.matching.types import MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction, SourceName

T0 = datetime(2026, 1, 1, 10, 0, 0)


def txn(source_record_id, key=None, amount="1000.00", created_at=T0, **overrides):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT,
        source_record_id=source_record_id,
        order_id=key,
        reference_id=key,
        amount=Decimal(amount),
        created_at=created_at,
        **overrides,
    )


def match_on_order_id(left, right, **kwargs):
    return match_by_key(
        left, right,
        key_fn=lambda t: t.order_id,
        amount_fn_left=lambda t: t.amount,
        amount_fn_right=lambda t: t.amount,
        date_fn_left=lambda t: t.created_at,
        date_fn_right=lambda t: t.created_at,
        **kwargs,
    )


def test_exact_one_to_one_match():
    left = [txn("l1", key="order_1", amount="1000.00")]
    right = [txn("r1", key="order_1", amount="1000.00")]
    [result] = match_on_order_id(left, right)
    assert result.status is MatchStatus.MATCHED
    assert result.tier is MatchTier.EXACT
    assert result.confidence == 1.0


def test_exact_match_with_amount_rule_violation_is_flagged_not_dropped():
    left = [txn("l1", key="order_1", amount="1000.00")]
    right = [txn("r1", key="order_1", amount="1500.00")]
    [result] = match_on_order_id(left, right)
    assert result.status is MatchStatus.MATCHED  # key matched exactly
    assert result.tier is MatchTier.EXACT
    assert result.confidence < 1.0  # but rule violation drags confidence down
    assert not result.rule_checks[0].passed
    assert result.notes  # explains the disagreement rather than staying silent


def test_left_key_with_no_right_counterpart_is_unmatched():
    # enable_fuzzy_tier=False mirrors production usage for structured IDs like
    # order_id — "order_1" vs "order_2" would otherwise score high on pure
    # string similarity despite being unrelated (see the dedicated test below).
    left = [txn("l1", key="order_1")]
    right = [txn("r1", key="order_2")]
    results = match_on_order_id(left, right, enable_fuzzy_tier=False)
    statuses = {r.status for r in results}
    assert statuses == {MatchStatus.UNMATCHED}
    assert len(results) == 2  # both sides reported, neither silently dropped


def test_split_group_resolves_via_amount_sum_rule():
    left = [txn("l1", key="order_1", amount="1000.00")]
    right = [
        txn("r1", key="order_1", amount="600.00"),
        txn("r2", key="order_1", amount="400.00"),
    ]
    [result] = match_on_order_id(left, right)
    assert result.status is MatchStatus.MATCHED
    assert "split" in result.notes[0] or "netted" in result.notes[0]


def test_lopsided_group_with_bad_sum_is_ambiguous_not_matched():
    left = [txn("l1", key="order_1", amount="1000.00")]
    right = [
        txn("r1", key="order_1", amount="600.00"),
        txn("r2", key="order_1", amount="600.00"),  # sums to 1200, doesn't reconcile
    ]
    [result] = match_on_order_id(left, right)
    assert result.status is MatchStatus.AMBIGUOUS


def test_duplicate_candidates_are_flagged_as_such_within_ambiguous():
    left = [
        txn("l1", key="order_1", amount="1000.00"),
        txn("l2", key="order_1", amount="1000.00"),  # identical duplicate
    ]
    right = [txn("r1", key="order_1", amount="1000.00")]
    [result] = match_on_order_id(left, right)
    assert result.status is MatchStatus.AMBIGUOUS
    assert "duplicate" in result.notes[0]


def test_n_by_m_group_is_ambiguous():
    left = [txn("l1", key="order_1", amount="500.00"), txn("l2", key="order_1", amount="500.00")]
    right = [txn("r1", key="order_1", amount="500.00"), txn("r2", key="order_1", amount="500.00")]
    [result] = match_on_order_id(left, right)
    assert result.status is MatchStatus.AMBIGUOUS


def test_fuzzy_tier_disabled_leaves_structurally_similar_ids_unmatched():
    # order_00019 vs order_00023 score ~0.82 on pure string similarity despite
    # being unrelated transactions — fuzzy tier must stay off for strict IDs.
    left = [txn("l1", key="order_00019", amount="1000.00")]
    right = [txn("r1", key="order_00023", amount="1000.00", created_at=T0)]
    results = match_on_order_id(left, right, enable_fuzzy_tier=False)
    assert all(r.status is MatchStatus.UNMATCHED for r in results)


def test_fuzzy_tier_enabled_catches_a_typo_in_reference_id():
    left = [txn("l1", key="UTR000000123", amount="1000.00")]
    right = [txn("r1", key="UTR000000132", amount="1000.00")]  # transposed digits
    [result] = match_on_order_id(left, right, enable_fuzzy_tier=True, near_miss_floor=0.5, fuzzy_accept_threshold=0.85)
    assert result.tier is MatchTier.FUZZY
    assert result.status is MatchStatus.MATCHED


def test_fuzzy_tier_near_miss_is_recorded_not_dropped():
    left = [txn("l1", key="AAAAAAAAAA", amount="1000.00")]
    right = [txn("r1", key="AAAAZZZZZZ", amount="1000.00")]  # ~50% similar — below accept, above floor
    [result] = match_on_order_id(left, right, enable_fuzzy_tier=True, near_miss_floor=0.3, fuzzy_accept_threshold=0.9)
    assert result.status is MatchStatus.NEAR_MISS
    assert result.tier is MatchTier.FUZZY
    assert result.key_similarity is not None and result.key_similarity < 0.9


def test_records_with_no_key_at_all_match_via_rule_tier_on_amount_and_date():
    left = [txn("l1", key=None, amount="777.77", created_at=T0)]
    right = [txn("r1", key=None, amount="777.77", created_at=T0 + timedelta(hours=1))]
    [result] = match_on_order_id(left, right)
    assert result.tier is MatchTier.RULE
    assert result.status is MatchStatus.MATCHED


def test_unkeyed_record_with_no_plausible_candidate_is_unmatched():
    left = [txn("l1", key=None, amount="777.77", created_at=T0)]
    right = [txn("r1", key=None, amount="5.00", created_at=T0)]
    results = match_on_order_id(left, right)
    # neither side found a plausible candidate — both surface separately, not merged or dropped
    assert len(results) == 2
    assert all(r.status is MatchStatus.UNMATCHED for r in results)
