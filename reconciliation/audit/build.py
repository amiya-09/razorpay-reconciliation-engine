"""Assembles the AuditLog from both joins' AI-augmented results, in order."""

from __future__ import annotations

from reconciliation.ai.pipeline import AIAugmentedResult
from reconciliation.audit.log import AuditLog


def build_audit_log(lg_augmented: list[AIAugmentedResult], gb_augmented: list[AIAugmentedResult]) -> AuditLog:
    log = AuditLog()
    for join_label, augmented_list in (("ledger<->gateway", lg_augmented), ("gateway<->bank", gb_augmented)):
        for augmented in augmented_list:
            log.record_match(join_label, augmented.match_result)
            log.record_ai_decision(join_label, augmented.match_result, augmented.ambiguous_decision)
            log.record_ai_explanation(join_label, augmented.match_result, augmented.exception_explanation)
    return log
