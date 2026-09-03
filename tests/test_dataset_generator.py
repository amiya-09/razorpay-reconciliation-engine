import json
from decimal import Decimal

from reconciliation.dataset.generator import DEFAULT_SEED, describe_mix, generate_dataset, write_dataset
from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.models import TrapCategory


def test_generation_is_deterministic_given_same_seed():
    a = generate_dataset(seed=DEFAULT_SEED)
    b = generate_dataset(seed=DEFAULT_SEED)
    assert a == b


def test_different_seed_produces_different_dataset():
    a = generate_dataset(seed=DEFAULT_SEED)
    b = generate_dataset(seed=DEFAULT_SEED + 1)
    assert a != b


def test_total_record_count_comfortably_clears_50_and_targets_150():
    ledger, gateway, bank = generate_dataset()
    total = len(ledger) + len(gateway) + len(bank)
    assert total >= 50
    assert 120 <= total <= 200


def test_every_row_has_a_valid_ground_truth_trap_category():
    ledger, gateway, bank = generate_dataset()
    valid = {c.value for c in TrapCategory}
    for row in ledger + gateway + bank:
        assert row["trap_category"] in valid


def test_generated_files_load_cleanly_through_canonical_loaders(tmp_path):
    ledger, gateway, bank = generate_dataset()
    (tmp_path / "internal_ledger.json").write_text(_to_json(ledger))
    (tmp_path / "gateway_report.json").write_text(_to_json(gateway))
    (tmp_path / "bank_settlement.json").write_text(_to_json(bank))

    ledger_txns = internal_ledger.load(tmp_path / "internal_ledger.json")
    gateway_txns = gateway_report.load(tmp_path / "gateway_report.json")
    bank_txns = bank_settlement.load(tmp_path / "bank_settlement.json")

    assert len(ledger_txns) == len(ledger)
    assert len(gateway_txns) == len(gateway)
    assert len(bank_txns) == len(bank)


def test_fee_deduction_arithmetic_invariant_holds_for_every_gateway_record():
    _, gateway, _ = generate_dataset()
    for row in gateway:
        amount, fee, tax, credit = (Decimal(row[k]) for k in ("amount", "fee", "tax", "credit"))
        assert amount - fee - tax == credit


def test_reference_typo_scenario_actually_corrupts_the_bank_reference():
    ledger, gateway, bank = generate_dataset()
    gateway_by_order = {row["order_id"]: row for row in gateway}
    typo_bank_rows = [row for row in bank if row["trap_category"] == TrapCategory.REFERENCE_ID_TYPO.value]
    assert typo_bank_rows
    for bank_row in typo_bank_rows:
        # find the matching internal ledger row's order_id via the shared trap-category batch is not
        # directly joinable by bank_ref (that's the point) — instead confirm no gateway row shares
        # this bank_ref verbatim, proving the corruption actually broke exact equality.
        assert not any(g["settlement_utr"] == bank_row["bank_ref"] for g in gateway)


def test_rounding_scenario_produces_a_small_but_nonzero_delta():
    _, gateway, bank = generate_dataset()
    gateway_by_utr = {row["settlement_utr"]: row for row in gateway}
    rounding_bank_rows = [row for row in bank if row["trap_category"] == TrapCategory.CURRENCY_ROUNDING.value]
    assert rounding_bank_rows
    for bank_row in rounding_bank_rows:
        gw = gateway_by_utr[bank_row["bank_ref"]]
        delta = abs(Decimal(bank_row["amount"]) - Decimal(gw["credit"]))
        assert Decimal("0") < delta <= Decimal("0.05")


def test_duplicate_scenario_creates_two_ledger_rows_for_one_order():
    ledger, _, _ = generate_dataset()
    duplicate_rows = [row for row in ledger if row["trap_category"] == TrapCategory.DUPLICATE_RECORD.value]
    order_ids = [row["order_id"] for row in duplicate_rows]
    assert len(order_ids) == len(set(order_ids)) * 2  # exactly 2 ledger rows per duplicated order_id


def test_netted_settlement_bank_amount_equals_sum_of_gateway_credits():
    ledger, gateway, bank = generate_dataset()
    netted_bank_rows = [row for row in bank if row["trap_category"] == TrapCategory.NETTED_SETTLEMENT.value]
    assert netted_bank_rows
    for bank_row in netted_bank_rows:
        matching_gateway = [g for g in gateway if g["settlement_utr"] == bank_row["bank_ref"]]
        assert len(matching_gateway) == 3
        assert sum(Decimal(g["credit"]) for g in matching_gateway) == Decimal(bank_row["amount"])


def test_split_transaction_gateway_credits_sum_close_to_ledger_amount():
    ledger, gateway, _ = generate_dataset()
    split_ledger_rows = [row for row in ledger if row["trap_category"] == TrapCategory.SPLIT_TRANSACTION.value]
    assert split_ledger_rows
    for ledger_row in split_ledger_rows:
        parts = [g for g in gateway if g["order_id"] == ledger_row["order_id"]]
        assert len(parts) == 2
        assert sum(Decimal(p["amount"]) for p in parts) == Decimal(ledger_row["amount"])


def test_pending_scenario_has_no_bank_settlement_row():
    ledger, gateway, bank = generate_dataset()
    pending_gateway = [row for row in gateway if row["trap_category"] == TrapCategory.PENDING_ON_HOLD.value]
    assert pending_gateway
    pending_utrs = {row["settlement_utr"] for row in pending_gateway}
    assert not any(row["bank_ref"] in pending_utrs for row in bank)


def test_genuinely_unmatched_rows_have_no_counterpart_in_other_sources():
    ledger, gateway, bank = generate_dataset()
    unmatched_ledger = [row for row in ledger if row["trap_category"] == TrapCategory.GENUINELY_UNMATCHED.value]
    unmatched_bank = [row for row in bank if row["trap_category"] == TrapCategory.GENUINELY_UNMATCHED.value]
    assert unmatched_ledger and unmatched_bank

    gateway_order_ids = {row["order_id"] for row in gateway}
    for row in unmatched_ledger:
        assert row["order_id"] not in gateway_order_ids

    gateway_utrs = {row["settlement_utr"] for row in gateway}
    for row in unmatched_bank:
        assert row["bank_ref"] not in gateway_utrs


def _to_json(rows: list[dict]) -> str:
    return json.dumps(rows)


def test_describe_mix_reports_total_and_per_category_breakdown():
    ledger, gateway, bank = generate_dataset()
    text = describe_mix(ledger, gateway, bank)
    assert "Total records:" in text
    assert "clean_exact_match" in text


def test_write_dataset_creates_the_three_expected_files(tmp_path):
    write_dataset(tmp_path, seed=DEFAULT_SEED)
    for name in ("internal_ledger.json", "bank_settlement.json", "gateway_report.json"):
        assert (tmp_path / name).exists()
    ledger, gateway, bank = generate_dataset(seed=DEFAULT_SEED)
    assert json.loads((tmp_path / "internal_ledger.json").read_text()) == ledger
