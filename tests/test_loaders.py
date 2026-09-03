import json
from decimal import Decimal

from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.models import SourceName


def test_internal_ledger_csv(tmp_path):
    path = tmp_path / "internal_ledger.csv"
    path.write_text(
        "ledger_id,order_id,amount,currency,created_at,status,trap_category\n"
        "led_1,order_1,1000.00,INR,2026-01-01T10:00:00,captured,clean_exact_match\n"
    )
    [txn] = internal_ledger.load(path)
    assert txn.source is SourceName.INTERNAL_LEDGER
    assert txn.source_record_id == "led_1"
    assert txn.order_id == "order_1"
    assert txn.amount == Decimal("1000.00")
    assert txn.ground_truth_trap_category == "clean_exact_match"
    assert txn.raw["status"] == "captured"


def test_internal_ledger_blank_order_id_becomes_none(tmp_path):
    path = tmp_path / "internal_ledger.csv"
    path.write_text(
        "ledger_id,order_id,amount,currency,created_at,status,trap_category\n"
        "led_2,,500.00,INR,2026-01-02T10:00:00,captured,\n"
    )
    [txn] = internal_ledger.load(path)
    assert txn.order_id is None
    assert txn.ground_truth_trap_category is None


def test_bank_settlement_csv_marks_settled_true(tmp_path):
    path = tmp_path / "bank_settlement.csv"
    path.write_text(
        "bank_ref,amount,value_date,narration,account_id,trap_category\n"
        "UTR123,976.00,2026-01-02T00:00:00,PAYMENT FROM RAZORPAY,acc_1,\n"
    )
    [txn] = bank_settlement.load(path)
    assert txn.source is SourceName.BANK_SETTLEMENT
    assert txn.reference_id == "UTR123"
    assert txn.amount == Decimal("976.00")
    assert txn.net_amount == Decimal("976.00")
    assert txn.settled is True


def test_gateway_report_json_maps_fee_and_credit(tmp_path):
    path = tmp_path / "gateway_report.json"
    rows = [
        {
            "entity_id": "ent_1",
            "order_id": "order_1",
            "type": "payment",
            "amount": "1000.00",
            "fee": "20.00",
            "tax": "4.00",
            "credit": "976.00",
            "settled": True,
            "on_hold": False,
            "created_at": "2026-01-01T10:00:00",
            "settled_at": "2026-01-02T00:00:00",
            "settlement_id": "setl_1",
            "settlement_utr": "UTR123",
            "trap_category": "fee_deduction",
        }
    ]
    path.write_text(json.dumps(rows))
    [txn] = gateway_report.load(path)
    assert txn.source is SourceName.GATEWAY_REPORT
    assert txn.order_id == "order_1"
    assert txn.reference_id == "UTR123"
    assert txn.amount == Decimal("1000.00")
    assert txn.fee == Decimal("20.00")
    assert txn.tax == Decimal("4.00")
    assert txn.net_amount == Decimal("976.00")
    assert txn.settled is True
    assert txn.on_hold is False
    assert txn.settlement_id == "setl_1"
    # arithmetic invariant this record is meant to exercise later (fee-deduction trap)
    assert txn.amount - txn.fee - txn.tax == txn.net_amount


def test_gateway_report_csv_parses_string_booleans(tmp_path):
    path = tmp_path / "gateway_report.csv"
    path.write_text(
        "entity_id,order_id,type,amount,fee,tax,credit,settled,on_hold,"
        "created_at,settled_at,settlement_id,settlement_utr,trap_category\n"
        "ent_2,order_2,payment,500.00,0,0,500.00,false,true,"
        "2026-01-03T10:00:00,,setl_2,UTR456,pending_on_hold\n"
    )
    [txn] = gateway_report.load(path)
    assert txn.settled is False
    assert txn.on_hold is True
    assert txn.settled_at is None
