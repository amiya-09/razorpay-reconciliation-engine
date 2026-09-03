"""Repeat the same ambiguous candidate through the live LLM N times and
measure decision variance, per BUILD_BRIEF Section 4's explicit consistency-
check requirement. Picks the first real AMBIGUOUS ledger<->gateway group from
the generated dataset (a true duplicate-record case per ground truth).
"""

from __future__ import annotations

from reconciliation.ai.ambiguous_match import resolve_ambiguous_match
from reconciliation.ai.client import GroqReasoningClient
from reconciliation.ai.evaluation import consistency
from reconciliation.dataset.generator import generate_dataset
from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.matching.engine import run
from reconciliation.matching.types import MatchStatus
from reconciliation.models import TrapCategory

N_REPEATS = 5

ledger_rows, gateway_rows, bank_rows = generate_dataset()
ledger = [internal_ledger._to_canonical(r) for r in ledger_rows]
gateway = [gateway_report._to_canonical(r) for r in gateway_rows]
bank = [bank_settlement._to_canonical(r) for r in bank_rows]

match_result = run(ledger, gateway, bank)
ambiguous_groups = [g for g in match_result.ledger_vs_gateway if g.status is MatchStatus.AMBIGUOUS]
target = ambiguous_groups[0]

ground_truth = {t.ground_truth_trap_category for t in target.left + target.right}
print(f"Target group: left={target.left_ids} right={target.right_ids} ground_truth={ground_truth}")
print(f"Correct answer per ground truth: match=False (this IS {TrapCategory.DUPLICATE_RECORD.value})\n")

client = GroqReasoningClient()
decisions = []
for i in range(N_REPEATS):
    decision = resolve_ambiguous_match(target, client)
    decisions.append(decision)
    print(f"  run {i+1}: match={decision.match} confidence={decision.confidence} category={decision.suspected_trap_category!r}")

stats = consistency(decisions)
print("\nConsistency stats:", stats)

correct = sum(1 for d in decisions if d.match is False)
print(f"\nAgreement with ground truth: {correct}/{N_REPEATS} runs correctly said match=False")
