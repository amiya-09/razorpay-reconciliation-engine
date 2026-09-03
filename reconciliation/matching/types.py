"""Result types for the matching engine.

Plain dataclasses, not pydantic — these are internal computation results we
construct ourselves, not data crossing an ingestion boundary. Validation
belongs at the boundary (reconciliation.models.CanonicalTransaction); these
just need to hold values (see docs/decision_log.md D8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from reconciliation.models import CanonicalTransaction


class MatchTier(str, Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    RULE = "rule"  # matched by amount+date proximity alone — no reliable key existed on either side
    NONE = "none"


class MatchStatus(str, Enum):
    MATCHED = "matched"
    NEAR_MISS = "near_miss"  # candidate found, but below the accept threshold — never silently dropped
    AMBIGUOUS = "ambiguous"  # multiple candidates share a key and don't resolve to a clean group
    UNMATCHED = "unmatched"  # no viable candidate at all


@dataclass(frozen=True)
class RuleCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GroupMatchResult:
    left: tuple[CanonicalTransaction, ...]
    right: tuple[CanonicalTransaction, ...]
    status: MatchStatus
    tier: MatchTier
    confidence: float
    key_similarity: Optional[float] = None
    rule_checks: tuple[RuleCheck, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def left_ids(self) -> tuple[str, ...]:
        return tuple(t.source_record_id for t in self.left)

    @property
    def right_ids(self) -> tuple[str, ...]:
        return tuple(t.source_record_id for t in self.right)
