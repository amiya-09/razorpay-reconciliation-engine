# AI Finance Controller — Multi-Source Reconciliation Engine

Razorpay AI Buildathon 2026 — Track 04.

Reconciles transactions across three synthetic sources (`internal_ledger`,
`bank_settlement`, `gateway_report`, modeled on Razorpay's real Settlement
Recon API shape) using deterministic matching tiers for the unambiguous
majority of records and an LLM reasoning layer for genuinely ambiguous
cases. Full architecture and reasoning: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Every non-trivial choice, with alternatives considered: [`docs/decision_log.md`](docs/decision_log.md).
Panel-question prep, including "why isn't everything AI?": [`docs/INTERVIEW_PREP.md`](docs/INTERVIEW_PREP.md).

## Results (live, real API, real dataset)

The bar this track is judged on is throughput + measured accuracy + an
honest exception list — not a cherry-picked demo. These numbers are from
`scripts/run_full_pipeline.py` run against the real Groq API
(`openai/gpt-oss-20b`), not a fake client:

| | ledger &lt;-&gt; gateway | gateway &lt;-&gt; bank |
|---|---|---|
| Total groups | 55 | 54 |
| Deterministic match rate | **90.9%** (50/55) | **88.9%** (48/54) |
| Match rate incl. AI-resolved | 90.9% | 88.9% |
| Ambiguous / unmatched | 2 / 3 | 0 / 6 |

- **AI layer invoked on 11 of 109 groups (10.1%)** — 13 LLM calls, 7,955
  input + 3,168 output tokens.
- **Decision consistency measured directly**, not assumed: the same
  ambiguous candidate run 5× live agreed 5/5 (confidence 0.60–0.95, mean
  0.834) — `scripts/run_consistency_check.py`.
- **Dataset is adversarial, not decorative**: 46% clean exact-match (well
  under the "too clean" 95% flag), all 10 trap categories from BUILD_BRIEF
  Section 4b represented.
- Full breakdown, the two-run comparison, and 4 real bugs live validation
  caught (with exact per-provider attribution) are in
  [`docs/decision_log.md`](docs/decision_log.md) D18–D20.

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The AI Reasoning Layer needs a Groq API key: set `GROQ_API_KEY` in the
environment, or create a `.env` in the project root (already git-ignored)
with `GROQ_API_KEY=...`. Everything else (matching engine, dataset
generator, tests) runs with no key at all.

## Quickstart

```bash
source .venv/bin/activate
python -m pytest                                      # 124 tests, 1 skipped without a key, 98% coverage
python -m reconciliation.dataset.generator             # regenerate data/raw/*.json (deterministic, seeded)
PYTHONPATH=. python scripts/run_full_pipeline.py       # full report against the live API (needs GROQ_API_KEY)
PYTHONPATH=. python scripts/run_consistency_check.py   # decision-variance check on a real ambiguous candidate
```

## Project layout

```
reconciliation/
  models.py            canonical transaction model (source-agnostic)
  loaders/              per-source raw -> canonical mapping
  dataset/
    generator.py         synthetic dataset generator (seeded, reproducible)
  matching/
    rules.py             deterministic arithmetic/date-window checks
    key_match.py          generic exact -> fuzzy -> rule-based tiered matcher
    engine.py             wires key_match.py to the 3 real sources + summary stats
  ai/
    schemas.py             pydantic contracts for LLM structured output
    client.py              Groq API wrapper (chat.completions + strict JSON schema)
    ambiguous_match.py      resolves NEAR_MISS/AMBIGUOUS groups via the LLM
    exception_explain.py    turns an unresolved group into a specific exception
    qa_agent.py             settlement Q&A: retrieval (regex/lookup) + one grounded LLM call
    pipeline.py             wires the AI layer to the matching engine's output
    evaluation.py           ground-truth precision/recall + consistency checks
  audit/
    log.py                  append-only audit log (no crypto — see BUILD_BRIEF.md Section 3)
    report.py               match rate %, per-tier breakdown, exception list, AI usage
    build.py                assembles the audit log from both joins' AI-augmented results
scripts/
  run_full_pipeline.py       live end-to-end run against the real dataset + real API
  run_consistency_check.py   repeats one ambiguous candidate N times, reports variance
tests/                    mirrors reconciliation/ 1:1, plus tests/ai/fake_client.py
docs/
  ARCHITECTURE.md         system design, AI-vs-deterministic boundary, measured results
  decision_log.md         Decision / Context / Alternatives / Reason / Trade-offs, D1-D20
data/
  raw/                    generated synthetic source files (not hand-edited)
```

## The AI Reasoning Layer, briefly

Three call sites, all behind one `StructuredReasoningClient` Protocol so the
provider is swappable without touching any caller (proven live — see
`docs/decision_log.md` D18/D20):

```python
from reconciliation.ai.client import GroqReasoningClient
from reconciliation.ai.pipeline import augment_with_ai, summarize_ai_usage
from reconciliation.ai.qa_agent import SettlementIndex, answer_question
from reconciliation.audit.build import build_audit_log
from reconciliation.audit.report import build_report
from reconciliation.matching.engine import run

match_result = run(ledger, gateway, bank)          # ledger/gateway/bank: lists of CanonicalTransaction
client = GroqReasoningClient()                      # picks up GROQ_API_KEY from env

lg_augmented = augment_with_ai(match_result.ledger_vs_gateway, client)
gb_augmented = augment_with_ai(match_result.gateway_vs_bank, client)
report = build_report(match_result.ledger_vs_gateway, match_result.gateway_vs_bank, lg_augmented, gb_augmented, client=client)
print(report.render_text())

audit_log = build_audit_log(lg_augmented, gb_augmented)   # audit_log.to_jsonl() for a full event trace

index = SettlementIndex(ledger, gateway, bank)
answer = answer_question("why did I receive ₹9,799.40 instead of ₹10,000?", index, client)
```

`tests/ai/` exercises all wiring/prompt-construction logic against a fake
client (`tests/ai/fake_client.py`, no network, no key) and gates the one
live smoke test (`test_client_live.py`) with `pytest.mark.skipif` so its
absence never fails a run — but it has been run, live, against the real
API, with results above.

## Testing

```bash
source .venv/bin/activate
python -m pytest -q                                              # 124 tests, 1 skipped without GROQ_API_KEY
pip install pytest-cov                                            # optional, not in requirements.txt (dev-only check)
python -m pytest --cov=reconciliation --cov-report=term-missing  # 98% line coverage
```

Every remaining coverage gap is accounted for: two branches in
`ai/client.py`'s real network path (the rate-limit retry loop, and the
JSON/schema-validation-error branch inside the D21 repair-retry logic) that
only fire under live API conditions the credential-gated live test and
D21's mocked-SDK tests don't each individually reach, plus two
explicitly-documented near-impossible defensive branches. Two real bugs were caught by the test
suite itself during development (a UTR-truncation collision, a
split-transaction false-duplicate flag) — see `docs/decision_log.md` D7,
D10.

## Status

Core reconciliation engine (Milestones 1–6) is complete, tested, and
validated live end-to-end. See `docs/ARCHITECTURE.md` for the full design
writeup and `docs/decision_log.md` for every decision's reasoning and
trade-offs.
