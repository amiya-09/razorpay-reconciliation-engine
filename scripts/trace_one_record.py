"""One-off trace: follow a chosen record set through the full pipeline,
printing the actual object at each stage. Written to answer guided-tour
requests with real, freshly-run output rather than recalled numbers.

Usage:
    python scripts/trace_one_record.py [reference_id_typo|duplicate_record] [--concise]

Default (no --concise) prints the full verbose object dump at every stage —
unchanged from before this flag existed. --concise trims Stage 1 to only the
fields relevant to the trap category being traced, and expands Stage 2's
rule-check tuple into one short line per check so the number that actually
matters isn't buried in a long repr. Stages 3 and 4 are identical in both
modes — they were already short.
"""

from __future__ import annotations

import argparse

from rapidfuzz import fuzz

from reconciliation.ai.ambiguous_match import resolve_ambiguous_match
from reconciliation.ai.client import GroqReasoningClient
from reconciliation.ai.exception_explain import explain_exception
from reconciliation.ai.pipeline import AIAugmentedResult, augment_with_ai
from reconciliation.audit.report import build_exceptions
from reconciliation.dataset.generator import generate_dataset
from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.matching.engine import run
from reconciliation.matching.types import MatchStatus

parser = argparse.ArgumentParser()
parser.add_argument("target", nargs="?", default="reference_id_typo",
                     choices=["reference_id_typo", "duplicate_record"])
parser.add_argument("--concise", action="store_true",
                     help="presentation-friendly output for screen recording")
args = parser.parse_args()
TARGET = args.target
CONCISE = args.concise


def print_concise_row(t, label: str, value) -> None:
    print(f"  {t.source.value:<16} {t.source_record_id:<14} {label}={value!s:<14} amount={t.amount}")


ledger_rows, gateway_rows, bank_rows = generate_dataset()
ledger = [internal_ledger._to_canonical(r) for r in ledger_rows]
gateway = [gateway_report._to_canonical(r) for r in gateway_rows]
bank = [bank_settlement._to_canonical(r) for r in bank_rows]

if TARGET == "duplicate_record":
    print("=== STAGE 1: canonical records (post-loader) ===")
    ledger_targets = [t for t in ledger if t.source_record_id in ("led_00054", "led_00055")]
    gateway_targets = [t for t in gateway if t.source_record_id == "pay_00054"]
    if CONCISE:
        for t in ledger_targets + gateway_targets:
            print_concise_row(t, "order_id", t.order_id)
    else:
        for t in ledger_targets + gateway_targets:
            print(t)

    match_result = run(ledger, gateway, bank)
    target = next(
        g for g in match_result.ledger_vs_gateway
        if set(g.left_ids) == {"led_00054", "led_00055"}
    )
    join_label = "ledger<->gateway"

elif TARGET == "reference_id_typo":
    gw_entity_id = "pay_00045"
    bank_ref = "003302932719"

    print("=== STAGE 1: canonical records (post-loader) ===")
    gw_txn = next(t for t in gateway if t.source_record_id == gw_entity_id)
    bank_txn = next(t for t in bank if t.source_record_id == bank_ref)
    if CONCISE:
        print_concise_row(gw_txn, "reference_id", gw_txn.reference_id)
        print_concise_row(bank_txn, "reference_id", bank_txn.reference_id)
    else:
        print(gw_txn)
        print(bank_txn)

    print(f"\nrapidfuzz.fuzz.ratio({gw_txn.reference_id!r}, {bank_txn.reference_id!r}) / 100 =",
          fuzz.ratio(gw_txn.reference_id, bank_txn.reference_id) / 100)

    match_result = run(ledger, gateway, bank)
    target = next(
        g for g in match_result.gateway_vs_bank
        if gw_entity_id in g.left_ids and bank_ref in g.right_ids
    )
    join_label = "gateway<->bank"

else:
    raise ValueError(TARGET)

print("\n=== STAGE 2: GroupMatchResult (post-matching-engine) ===")
print("status:", target.status)
print("tier:", target.tier)
print("confidence:", target.confidence)
print("key_similarity:", target.key_similarity)
if CONCISE:
    print("rule checks:")
    for rc in target.rule_checks:
        print(f"  {rc.name}: {'PASSED' if rc.passed else 'FAILED'} ({rc.detail})")
    if not target.rule_checks:
        print("  (none evaluated)")
else:
    print("rule_checks:", target.rule_checks)
print("notes:", target.notes)

client = GroqReasoningClient()

if target.status in (MatchStatus.NEAR_MISS, MatchStatus.AMBIGUOUS, MatchStatus.UNMATCHED):
    [augmented] = augment_with_ai([target], client)
    if augmented.ambiguous_decision is not None:
        print("\n=== STAGE 3a: AmbiguousMatchDecision (post-AI, ambiguous_match.py) ===")
        print(augmented.ambiguous_decision)
    if augmented.exception_explanation is not None:
        print("\n=== STAGE 3b: ExceptionExplanation (post-AI, exception_explain.py) ===")
        print(augmented.exception_explanation)
else:
    print(f"\n=== STAGE 3: status is {target.status.value} — AI layer NOT invoked ===")
    print("(only NEAR_MISS/AMBIGUOUS/UNMATCHED groups ever reach the AI layer — see pipeline.py)")
    augmented = AIAugmentedResult(match_result=target)

[exc] = build_exceptions(join_label, [augmented]) if build_exceptions(join_label, [augmented]) else [None]
print("\n=== STAGE 4: report outcome (audit/report.py) ===")
if exc is None:
    print("Not an exception — this group resolved as a clean MATCHED result and contributes")
    print("directly to the deterministic match rate. No ExceptionRecord is produced.")
else:
    print(exc)
