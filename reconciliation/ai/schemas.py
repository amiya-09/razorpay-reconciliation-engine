"""Structured-output contracts for the AI Reasoning Layer.

These validate LLM responses (D1's boundary-validation principle applies here
too — an LLM response is exactly as much "untrusted external input" as a CSV
row). A response that doesn't conform is a hard failure at the SDK level
(`messages.parse` raises), never a silently-accepted best-effort parse.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Groq's strict JSON-schema mode (response_format={"type": "json_schema", ...,
# "strict": True}) requires `additionalProperties: false` on every object in
# the schema — pydantic's model_json_schema() doesn't set that by default.
# extra="forbid" is what makes pydantic emit it. Found via a live 400 from the
# real API (D20) — every structured-output schema in this module needs it.
_STRICT_SCHEMA = ConfigDict(extra="forbid")


class AmbiguousMatchDecision(BaseModel):
    """Resolution for a single below-threshold or ambiguous match candidate
    (BUILD_BRIEF Section 4, item 1)."""

    model_config = _STRICT_SCHEMA

    match: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    suspected_trap_category: str


class ExceptionExplanation(BaseModel):
    """Human-readable categorization for a record that ends up unmatched
    (BUILD_BRIEF Section 4, item 2) — replaces a generic "STATUS: UNMATCHED"
    with a specific, actionable explanation."""

    model_config = _STRICT_SCHEMA

    category: str
    explanation: str
    recommended_action: str
    confidence: float = Field(ge=0.0, le=1.0)


class QAAnswer(BaseModel):
    """Answer for the settlement Q&A agent (BUILD_BRIEF Section 4, item 3)."""

    model_config = _STRICT_SCHEMA

    answer: str
    # No default: Groq's strict mode requires every property in `required`,
    # so the model must always emit this field explicitly (an empty list is
    # a valid, meaningful "no records cited" answer) rather than omit it.
    cited_record_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
