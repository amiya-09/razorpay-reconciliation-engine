"""Integration tests: run the real matching engine against the generated
dataset and check outcomes against the hidden ground-truth trap category.
"""

from reconciliation.dataset.generator import generate_dataset
from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.matching.engine import run
from reconciliation.matching.types import MatchStatus
from reconciliation.models import TrapCategory


def _load_canonical_dataset():
    ledger_rows, gateway_rows, bank_rows = generate_dataset()
    ledger = [internal_ledger._to_canonical(r) for r in ledger_rows]
    gateway = [gateway_report._to_canonical(r) for r in gateway_rows]
    bank = [bank_settlement._to_canonical(r) for r in bank_rows]
    return ledger, gateway, bank


def test_clean_exact_match_transactions_resolve_as_matched_on_both_joins():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)

    def ground_truth_of(group):
        cats = {t.ground_truth_trap_category for t in group.left + group.right}
        return cats

    clean = {TrapCategory.CLEAN_EXACT_MATCH}
    clean_lg = [g for g in result.ledger_vs_gateway if ground_truth_of(g) == clean]
    clean_gb = [g for g in result.gateway_vs_bank if ground_truth_of(g) == clean]
    assert clean_lg and all(g.status is MatchStatus.MATCHED for g in clean_lg)
    assert clean_gb and all(g.status is MatchStatus.MATCHED for g in clean_gb)


def test_fee_deduction_does_not_get_flagged_as_amount_mismatch():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    fee_groups = [
        g for g in result.gateway_vs_bank
        if any(t.ground_truth_trap_category == TrapCategory.FEE_DEDUCTION for t in g.left + g.right)
    ]
    assert fee_groups
    for g in fee_groups:
        assert g.status is MatchStatus.MATCHED
        assert all(rc.passed for rc in g.rule_checks)


def test_reference_typo_is_resolved_by_fuzzy_tier_not_left_unmatched():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    typo_groups = [
        g for g in result.gateway_vs_bank
        if any(t.ground_truth_trap_category == TrapCategory.REFERENCE_ID_TYPO for t in g.left + g.right)
    ]
    assert typo_groups
    for g in typo_groups:
        assert g.status is MatchStatus.MATCHED
        assert g.tier.value == "fuzzy"


def test_netted_settlement_resolves_as_matched_group_on_gateway_bank_join():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    netted_groups = [
        g for g in result.gateway_vs_bank
        if any(t.ground_truth_trap_category == TrapCategory.NETTED_SETTLEMENT for t in g.left + g.right)
    ]
    assert netted_groups
    for g in netted_groups:
        assert g.status is MatchStatus.MATCHED
        assert len(g.left) == 3  # 3 gateway records netted into 1 bank credit
        assert len(g.right) == 1


def test_split_transaction_resolves_as_matched_group_on_ledger_gateway_join():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    split_groups = [
        g for g in result.ledger_vs_gateway
        if any(t.ground_truth_trap_category == TrapCategory.SPLIT_TRANSACTION for t in g.left + g.right)
    ]
    assert split_groups
    for g in split_groups:
        assert g.status is MatchStatus.MATCHED
        assert len(g.left) == 1
        assert len(g.right) == 2


def test_duplicate_records_surface_as_ambiguous_not_generic_unmatched():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    dup_groups = [
        g for g in result.ledger_vs_gateway
        if any(t.ground_truth_trap_category == TrapCategory.DUPLICATE_RECORD for t in g.left + g.right)
    ]
    assert dup_groups
    for g in dup_groups:
        assert g.status is MatchStatus.AMBIGUOUS


def test_genuinely_unmatched_records_surface_honestly_never_silently_dropped():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)

    total_input_records = len(ledger) + len(gateway)
    total_output_records = sum(len(g.left) + len(g.right) for g in result.ledger_vs_gateway)
    assert total_output_records == total_input_records  # no record vanishes

    unmatched_orphans = [g for g in result.ledger_vs_gateway if g.status is MatchStatus.UNMATCHED]
    orphan_categories = {
        t.ground_truth_trap_category for g in unmatched_orphans for t in g.left + g.right
    }
    assert TrapCategory.GENUINELY_UNMATCHED in orphan_categories


def test_pending_on_hold_gateway_records_are_unmatched_on_bank_join_not_erroneously_forced():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    pending_groups = [
        g for g in result.gateway_vs_bank
        if any(t.ground_truth_trap_category == TrapCategory.PENDING_ON_HOLD for t in g.left + g.right)
    ]
    assert pending_groups
    for g in pending_groups:
        assert g.status is MatchStatus.UNMATCHED  # correctly has no bank counterpart yet
        assert len(g.right) == 0


def test_overall_match_rate_reflects_a_realistic_not_perfect_dataset():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    lg_summary_matched = sum(1 for g in result.ledger_vs_gateway if g.status is MatchStatus.MATCHED)
    gb_summary_matched = sum(1 for g in result.gateway_vs_bank if g.status is MatchStatus.MATCHED)
    lg_rate = lg_summary_matched / len(result.ledger_vs_gateway)
    gb_rate = gb_summary_matched / len(result.gateway_vs_bank)
    # Sanity bounds: high (most records are clean-ish) but not suspiciously perfect.
    assert 0.5 <= lg_rate < 1.0
    assert 0.5 <= gb_rate < 1.0


def test_reconciliation_result_summary_renders_both_joins_with_match_rates():
    ledger, gateway, bank = _load_canonical_dataset()
    result = run(ledger, gateway, bank)
    text = result.summary()
    assert "ledger <-> gateway (order_id)" in text
    assert "gateway <-> bank (reference_id)" in text
    assert "match_rate=" in text
    assert "status:" in text
    assert "tier:" in text
