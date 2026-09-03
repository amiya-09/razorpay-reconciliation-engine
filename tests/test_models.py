from decimal import Decimal

import pytest
from pydantic import ValidationError

from reconciliation.models import CanonicalTransaction, SourceName, TrapCategory


def make_txn(**overrides):
    defaults = dict(
        source=SourceName.GATEWAY_REPORT,
        source_record_id="ent_1",
        amount=Decimal("1000.00"),
        created_at="2026-01-01T10:00:00",
    )
    defaults.update(overrides)
    return CanonicalTransaction(**defaults)


def test_minimal_construction_uses_defaults():
    txn = make_txn()
    assert txn.currency == "INR"
    assert txn.net_amount is None
    assert txn.raw == {}
    assert txn.ground_truth_trap_category is None


def test_settlement_amount_falls_back_to_gross_amount_when_net_unknown():
    txn = make_txn(amount=Decimal("1000.00"))
    assert txn.settlement_amount == Decimal("1000.00")


def test_settlement_amount_prefers_net_amount_when_present():
    txn = make_txn(amount=Decimal("1000.00"), net_amount=Decimal("976.00"))
    assert txn.settlement_amount == Decimal("976.00")


def test_amount_coerces_string_to_decimal():
    txn = make_txn(amount="1234.56")
    assert txn.amount == Decimal("1234.56")
    assert isinstance(txn.amount, Decimal)


def test_record_is_immutable():
    txn = make_txn()
    with pytest.raises(ValidationError):
        txn.amount = Decimal("1.00")


def test_ground_truth_trap_category_round_trips():
    txn = make_txn(ground_truth_trap_category=TrapCategory.FEE_DEDUCTION)
    assert txn.ground_truth_trap_category is TrapCategory.FEE_DEDUCTION


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        CanonicalTransaction(source=SourceName.INTERNAL_LEDGER, source_record_id="x")
