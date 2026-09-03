from datetime import datetime, timedelta
from decimal import Decimal

from reconciliation.matching.rules import check_amount, check_date_lag, check_fee_arithmetic


def test_check_amount_passes_within_tolerance():
    result = check_amount("x", Decimal("100.00"), Decimal("100.03"), Decimal("0.05"))
    assert result.passed


def test_check_amount_fails_beyond_tolerance():
    result = check_amount("x", Decimal("100.00"), Decimal("101.00"), Decimal("0.05"))
    assert not result.passed


def test_check_date_lag_passes_within_window():
    earlier = datetime(2026, 1, 1)
    later = earlier + timedelta(days=1)
    assert check_date_lag("x", earlier, later, max_lag_days=5).passed


def test_check_date_lag_fails_outside_window():
    earlier = datetime(2026, 1, 1)
    later = earlier + timedelta(days=10)
    assert not check_date_lag("x", earlier, later, max_lag_days=5).passed


def test_check_date_lag_negative_lag_fails():
    earlier = datetime(2026, 1, 5)
    later = datetime(2026, 1, 1)  # settled before created — invalid
    assert not check_date_lag("x", earlier, later, max_lag_days=5).passed


def test_check_date_lag_skips_when_counterpart_date_missing():
    result = check_date_lag("x", datetime(2026, 1, 1), None, max_lag_days=5)
    assert result.passed


def test_check_fee_arithmetic_passes_when_consistent():
    result = check_fee_arithmetic(Decimal("1000.00"), Decimal("20.00"), Decimal("3.60"), Decimal("976.40"))
    assert result.passed


def test_check_fee_arithmetic_fails_when_inconsistent():
    result = check_fee_arithmetic(Decimal("1000.00"), Decimal("20.00"), Decimal("3.60"), Decimal("950.00"))
    assert not result.passed


def test_check_fee_arithmetic_skips_when_fields_missing():
    result = check_fee_arithmetic(Decimal("1000.00"), None, None, None)
    assert result.passed
