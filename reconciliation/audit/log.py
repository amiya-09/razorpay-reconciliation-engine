"""Append-only audit log — the concept from the Zetheta project carried
forward, minus the crypto (BUILD_BRIEF Section 3 explicitly drops
hash-chaining as out of scope for this project's scale/timeline). Each event
is immutable once recorded; the log only ever grows.

Records references (record IDs), not full record dumps — the audit trail is
a trace of *what decisions were made*, not a second copy of the dataset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from reconciliation.ai.schemas import AmbiguousMatchDecision, ExceptionExplanation
from reconciliation.matching.types import GroupMatchResult


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: str
    event_type: str
    payload: dict[str, Any]


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def record(self, event_type: str, **payload: Any) -> AuditEntry:
        entry = AuditEntry(
            seq=len(self._entries),
            timestamp=datetime.now().isoformat(),
            event_type=event_type,
            payload=payload,
        )
        self._entries.append(entry)
        return entry

    def record_match(self, join_label: str, result: GroupMatchResult) -> AuditEntry:
        return self.record(
            "match_result",
            join=join_label,
            left_ids=list(result.left_ids),
            right_ids=list(result.right_ids),
            status=result.status.value,
            tier=result.tier.value,
            confidence=result.confidence,
            notes=list(result.notes),
        )

    def record_ai_decision(
        self, join_label: str, result: GroupMatchResult, decision: Optional[AmbiguousMatchDecision]
    ) -> Optional[AuditEntry]:
        if decision is None:
            return None
        return self.record(
            "ai_ambiguous_decision",
            join=join_label,
            left_ids=list(result.left_ids),
            right_ids=list(result.right_ids),
            match=decision.match,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            suspected_trap_category=decision.suspected_trap_category,
        )

    def record_ai_explanation(
        self, join_label: str, result: GroupMatchResult, explanation: Optional[ExceptionExplanation]
    ) -> Optional[AuditEntry]:
        if explanation is None:
            return None
        return self.record(
            "ai_exception_explanation",
            join=join_label,
            left_ids=list(result.left_ids),
            right_ids=list(result.right_ids),
            category=explanation.category,
            explanation=explanation.explanation,
            recommended_action=explanation.recommended_action,
            confidence=explanation.confidence,
        )

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps({"seq": e.seq, "timestamp": e.timestamp, "event_type": e.event_type, **e.payload})
            for e in self._entries
        )
