import pytest
from pydantic import ValidationError

from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation, QAAnswer


def test_ambiguous_match_decision_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        AmbiguousMatchDecision(match=True, confidence=1.5, reasoning="x", suspected_trap_category="other")


def test_ambiguous_match_decision_valid():
    d = AmbiguousMatchDecision(match=True, confidence=0.9, reasoning="x", suspected_trap_category="reference_id_typo")
    assert d.match is True


def test_exception_explanation_requires_all_fields():
    with pytest.raises(ValidationError):
        ExceptionExplanation(category="x")


def test_qa_answer_accepts_empty_citation_list():
    # cited_record_ids has no default (Groq's strict JSON-schema mode requires
    # every property in `required` — see docs/decision_log.md D20) — an empty
    # list must still be constructible as an explicit "no records cited" answer.
    answer = QAAnswer(answer="you were charged a 2% fee", cited_record_ids=[], confidence=0.95)
    assert answer.cited_record_ids == []


def test_qa_answer_requires_cited_record_ids_field():
    with pytest.raises(ValidationError):
        QAAnswer(answer="x", confidence=0.9)
