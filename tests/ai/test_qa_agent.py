from datetime import datetime
from decimal import Decimal

from reconciliation.ai.qa_agent import (
    SettlementIndex,
    answer_question,
    build_prompt,
    extract_amounts,
    extract_identifiers,
    find_relevant_records,
)
from reconciliation.ai.schemas import QAAnswer
from reconciliation.models import CanonicalTransaction, SourceName
from tests.ai.fake_client import FakeReasoningClient


def ledger_txn(order_id, amount):
    return CanonicalTransaction(
        source=SourceName.INTERNAL_LEDGER, source_record_id=f"led_{order_id}",
        order_id=order_id, amount=Decimal(amount), created_at=datetime(2026, 1, 1),
    )


def gateway_txn(order_id, reference_id, amount, fee, tax, credit):
    return CanonicalTransaction(
        source=SourceName.GATEWAY_REPORT, source_record_id=f"pay_{order_id}",
        order_id=order_id, reference_id=reference_id,
        amount=Decimal(amount), fee=Decimal(fee), tax=Decimal(tax), net_amount=Decimal(credit),
        created_at=datetime(2026, 1, 1),
    )


def bank_txn(reference_id, amount):
    return CanonicalTransaction(
        source=SourceName.BANK_SETTLEMENT, source_record_id=reference_id,
        reference_id=reference_id, amount=Decimal(amount), net_amount=Decimal(amount),
        created_at=datetime(2026, 1, 2),
    )


def build_index():
    ledger = [ledger_txn("order_00041", "10000.00")]
    gateway = [gateway_txn("order_00041", "123456789012", "10000.00", "170.00", "30.60", "9799.40")]
    bank = [bank_txn("123456789012", "9799.40")]
    return SettlementIndex(ledger, gateway, bank)


def test_extract_identifiers_finds_order_id():
    assert extract_identifiers("what happened to order_00041?") == ["order_00041"]


def test_extract_identifiers_finds_utr():
    assert extract_identifiers("UTR 123456789012 never arrived") == ["123456789012"]


def test_extract_amounts_handles_rupee_symbol_and_comma_grouping():
    amounts = extract_amounts("why did I receive ₹9,800 instead of ₹10,000?")
    assert Decimal("9800") in amounts
    assert Decimal("10000") in amounts


def test_extract_amounts_handles_explicit_decimal():
    assert extract_amounts("the credit was 9799.40 not the full amount") == [Decimal("9799.40")]


def test_find_relevant_records_by_order_id_expands_across_all_three_sources():
    index = build_index()
    records = find_relevant_records("why is order_00041 short?", index)
    sources = {t.source for t in records}
    assert sources == {SourceName.INTERNAL_LEDGER, SourceName.GATEWAY_REPORT, SourceName.BANK_SETTLEMENT}


def test_find_relevant_records_by_amount_when_no_identifier_present():
    index = build_index()
    records = find_relevant_records("why did I receive 9799.40 instead of 10000.00?", index)
    assert len(records) >= 2  # at minimum the ledger (10000) and bank (9799.40) records


def test_find_relevant_records_returns_empty_when_nothing_matches():
    index = build_index()
    records = find_relevant_records("what is your refund policy?", index)
    assert records == []


def test_build_prompt_tells_llm_when_no_grounding_data_exists():
    prompt = build_prompt("some unrelated question", [])
    assert "No matching records were found" in prompt


def test_build_prompt_includes_record_details_when_found():
    index = build_index()
    records = find_relevant_records("order_00041", index)
    prompt = build_prompt("why is order_00041 short?", records)
    assert "170.00" in prompt  # the fee
    assert "9799.40" in prompt  # the credit


def test_answer_question_returns_validated_answer_and_grounds_on_found_records():
    index = build_index()
    answer = QAAnswer(
        answer="You were charged a ₹170.00 fee plus ₹30.60 GST, so you received ₹9,799.40 net.",
        cited_record_ids=["pay_order_00041"], confidence=0.95,
    )
    client = FakeReasoningClient(responder=lambda s, u, f: answer)
    result = answer_question("why did I only get ₹9,799.40 for order_00041?", index, client)
    assert result is answer
    assert len(client.call_log) == 1


def test_answer_question_with_no_grounding_still_returns_a_validated_answer():
    index = build_index()
    answer = QAAnswer(answer="I don't have a record matching that.", cited_record_ids=[], confidence=0.9)
    client = FakeReasoningClient(responder=lambda s, u, f: answer)
    result = answer_question("what's the weather like?", index, client)
    assert result.cited_record_ids == []
