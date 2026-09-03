"""One-off script: run the full reconciliation pipeline against the real
generated dataset using the live Groq API (no fake client). Prints the
full report plus a compact JSON summary for programmatic inspection.
"""

from __future__ import annotations

import json

from reconciliation.ai.client import GroqReasoningClient
from reconciliation.ai.pipeline import augment_with_ai
from reconciliation.audit.report import build_report
from reconciliation.dataset.generator import generate_dataset
from reconciliation.loaders import bank_settlement, gateway_report, internal_ledger
from reconciliation.matching.engine import run

ledger_rows, gateway_rows, bank_rows = generate_dataset()
ledger = [internal_ledger._to_canonical(r) for r in ledger_rows]
gateway = [gateway_report._to_canonical(r) for r in gateway_rows]
bank = [bank_settlement._to_canonical(r) for r in bank_rows]

match_result = run(ledger, gateway, bank)
client = GroqReasoningClient()

lg_augmented = augment_with_ai(match_result.ledger_vs_gateway, client)
gb_augmented = augment_with_ai(match_result.gateway_vs_bank, client)

report = build_report(
    match_result.ledger_vs_gateway, match_result.gateway_vs_bank,
    lg_augmented, gb_augmented, client=client,
)

print(report.render_text())

summary = {
    "ledger_gateway": {
        "total": report.ledger_gateway.total,
        "match_rate": report.ledger_gateway.match_rate,
        "match_rate_including_ai": report.ledger_gateway.match_rate_including_ai,
        "ai_resolved": report.ledger_gateway.ai_resolved,
    },
    "gateway_bank": {
        "total": report.gateway_bank.total,
        "match_rate": report.gateway_bank.match_rate,
        "match_rate_including_ai": report.gateway_bank.match_rate_including_ai,
        "ai_resolved": report.gateway_bank.ai_resolved,
    },
    "ai_usage": report.ai_usage,
    "exception_count": len(report.exceptions),
}
print("\n=== JSON SUMMARY ===")
print(json.dumps(summary, indent=2))
