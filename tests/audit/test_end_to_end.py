"""Full pipeline integration: dataset generator -> matching engine -> AI
layer (fake client) -> audit log + report, run against the real generated
dataset rather than hand-built fixtures.
"""

from reconciliation.ai.pipeline import augment_with_ai
from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.audit.build import build_audit_log
from reconciliation.audit.report import build_report
from reconciliation.dataset.generator import generate_dataset
from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.matching.engine import run
from reconciliation.matching.types import MatchStatus
from tests.ai.fake_client import FakeReasoningClient


def _canned_responder(system, user, output_format):
    if output_format is AmbiguousMatchDecision:
        return AmbiguousMatchDecision(match=True, confidence=0.8, reasoning="plausible typo/lag", suspected_trap_category="reference_id_typo")
    return ExceptionExplanation(category="genuinely_unmatched", explanation="no counterpart in the data", recommended_action="manual review", confidence=0.8)


def test_full_pipeline_produces_a_coherent_report_against_real_dataset():
    ledger_rows, gateway_rows, bank_rows = generate_dataset()
    ledger = [internal_ledger._to_canonical(r) for r in ledger_rows]
    gateway = [gateway_report._to_canonical(r) for r in gateway_rows]
    bank = [bank_settlement._to_canonical(r) for r in bank_rows]

    match_result = run(ledger, gateway, bank)
    client = FakeReasoningClient(responder=_canned_responder)
    lg_augmented = augment_with_ai(match_result.ledger_vs_gateway, client)
    gb_augmented = augment_with_ai(match_result.gateway_vs_bank, client)

    report = build_report(
        match_result.ledger_vs_gateway, match_result.gateway_vs_bank,
        lg_augmented, gb_augmented, client=client,
    )

    # Every group is accounted for exactly once: matched, ai-confirmed, or an exception.
    total_groups = len(match_result.ledger_vs_gateway) + len(match_result.gateway_vs_bank)
    clean_matched = sum(
        1 for a in lg_augmented + gb_augmented if a.match_result.status is MatchStatus.MATCHED
    )
    ai_confirmed = report.ledger_gateway.ai_resolved + report.gateway_bank.ai_resolved
    assert clean_matched + ai_confirmed + len(report.exceptions) == total_groups

    # No record vanishes: every canonical record appears in exactly one group per join.
    lg_record_count = sum(len(r.left) + len(r.right) for r in match_result.ledger_vs_gateway)
    assert lg_record_count == len(ledger) + len(gateway)

    # Since our fake client always confirms ambiguous/near-miss candidates as
    # matches, match_rate_including_ai must be strictly >= the deterministic-only rate.
    assert report.ledger_gateway.match_rate_including_ai >= report.ledger_gateway.match_rate
    assert report.gateway_bank.match_rate_including_ai >= report.gateway_bank.match_rate

    audit_log = build_audit_log(lg_augmented, gb_augmented)
    assert len(audit_log.entries) >= total_groups  # at least one match_result event per group

    text = report.render_text()
    assert "Reconciliation Report" in text
    assert str(len(report.exceptions)) in text
