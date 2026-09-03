from reconciliation.ai.evaluation import EvaluationRow, consistency, evaluate, expected_match_for
from reconciliation.ai.schemas import AmbiguousMatchDecision
from reconciliation.models import TrapCategory


def test_expected_match_true_categories():
    assert expected_match_for(TrapCategory.REFERENCE_ID_TYPO) is True
    assert expected_match_for(TrapCategory.NETTED_SETTLEMENT) is True


def test_expected_match_false_categories():
    assert expected_match_for(TrapCategory.DUPLICATE_RECORD) is False


def test_expected_match_none_for_unlabeled_categories():
    assert expected_match_for(TrapCategory.GENUINELY_UNMATCHED) is None
    assert expected_match_for(None) is None


def test_evaluate_computes_accuracy_over_judged_rows_only():
    rows = [
        EvaluationRow(ground_truth=TrapCategory.REFERENCE_ID_TYPO, predicted_match=True),   # correct
        EvaluationRow(ground_truth=TrapCategory.DUPLICATE_RECORD, predicted_match=True),    # wrong (expected False)
        EvaluationRow(ground_truth=TrapCategory.GENUINELY_UNMATCHED, predicted_match=True), # unjudged, skipped
    ]
    result = evaluate(rows)
    assert result["n_judged"] == 2
    assert result["n_skipped"] == 1
    assert result["accuracy"] == 0.5


def test_evaluate_handles_no_judged_rows():
    rows = [EvaluationRow(ground_truth=TrapCategory.GENUINELY_UNMATCHED, predicted_match=True)]
    result = evaluate(rows)
    assert result["n_judged"] == 0
    assert result["accuracy"] is None


def test_consistency_measures_agreement_and_confidence_spread():
    decisions = [
        AmbiguousMatchDecision(match=True, confidence=0.8, reasoning="x", suspected_trap_category="other"),
        AmbiguousMatchDecision(match=True, confidence=0.9, reasoning="x", suspected_trap_category="other"),
        AmbiguousMatchDecision(match=False, confidence=0.4, reasoning="x", suspected_trap_category="other"),
    ]
    result = consistency(decisions)
    assert result["n"] == 3
    assert result["agreement_rate"] == 2 / 3
    assert 0.6 < result["confidence_mean"] < 0.75
    assert result["confidence_stdev"] > 0


def test_consistency_handles_empty_input():
    result = consistency([])
    assert result["n"] == 0
    assert result["agreement_rate"] is None
