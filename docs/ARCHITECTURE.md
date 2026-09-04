# Architecture: AI Finance Controller — Multi-Source Reconciliation Engine

Razorpay AI Buildathon 2026 — Track 04. This document explains what the
system does, why it's built the way it is, and what it measured — the panel
review's stated bar is "throughput plus measured accuracy plus an honest
exception list," so that's what this doc leads with.

## 1. System overview

```
                    Canonical Transaction Model
                    (reconciliation/models.py)
                               │
      ┌────────────────────────┼────────────────────────┐
      │                        │                        │
internal_ledger          bank_settlement          gateway_report
  (loader)                  (loader)                  (loader)
      │                        │                        │
      └────────────────────────┼────────────────────────┘
                               │
              Deterministic Matching Tiers (reconciliation/matching/)
        exact (order_id) ──┐   │   ┌── exact/fuzzy (reference_id/UTR)
                           │   │   │
                  ledger <-> gateway   gateway <-> bank
                  (fuzzy tier OFF —    (fuzzy tier ON —
                   strict IDs, D9)      typo-prone refs)
                           │   │   │
              near-miss / ambiguous / unmatched groups
                           │   │   │
                           ▼   ▼   ▼
              ┌──────────────────────────────────┐
              │   AI Reasoning Layer (Groq API)   │
              │   reconciliation/ai/               │
              │   - resolves ambiguous candidates  │
              │   - explains unresolved exceptions │
              │   - settlement Q&A agent            │
              └──────────────────────────────────┘
                           │
              Audit Trail (reconciliation/audit/log.py)
              append-only, no crypto (out of scope, see §6)
                           │
              Results Report (reconciliation/audit/report.py)
              match rate %, per-tier breakdown incl. AI-resolved,
              honest exception list with AI-generated reasoning,
              AI cost/latency/consistency logs
```

Two upstream pieces feed this pipeline: the **canonical transaction model**
(one pydantic schema all three sources normalize into) and the **dataset
generator** (`reconciliation/dataset/generator.py`), which produces the
synthetic 163-record dataset with 10 known trap categories and hidden
ground-truth labels used only for our own evaluation.

## 2. Data flow, end to end

1. **Ingest.** Each source's raw CSV/JSON rows pass through a loader
   (`reconciliation/loaders/`) into `CanonicalTransaction` — the one place
   type coercion and validation happen (D1).
2. **Match.** Two independent joins run: `internal_ledger <-> gateway_report`
   on `order_id`, and `gateway_report <-> bank_settlement` on `reference_id`.
   Each group of records sharing a key resolves through exact -> fuzzy ->
   rule tiers, producing a `GroupMatchResult` with a status (`matched`,
   `near_miss`, `ambiguous`, `unmatched`), a tier, a confidence score, and
   rule-check detail — never a bare boolean.
3. **Reason.** Any group that isn't a clean deterministic match is handed to
   the AI Reasoning Layer: `NEAR_MISS`/`AMBIGUOUS` groups get a match/no-match
   judgment with reasoning; groups that end up unresolved (including ones
   the AI itself rejected) get a specific, actionable exception explanation
   — never a generic "STATUS: UNMATCHED."
4. **Record.** Every match decision and every AI call is logged
   (`AuditLog`, append-only).
5. **Report.** `ReconciliationReport` aggregates both joins into match rates
   (with and without AI contribution, reported separately — D16), a
   per-tier breakdown, and the full exception list.

## 3. The AI-vs-deterministic boundary — why isn't everything AI?

This is the single most likely panel question (BUILD_BRIEF names it
explicitly), so the answer is structural, not just asserted:

**Stays deterministic — no LLM call, ever:**
- Exact ID equality (order_id, reference_id)
- Fuzzy string similarity (rapidfuzz), **but only where typos are the
  designed failure mode** — the `reference_id`/UTR join. It is explicitly
  disabled on the `order_id` join, because `rapidfuzz.fuzz.ratio("order_00019",
  "order_00023") == 0.82` despite these being two unrelated transactions —
  applying fuzzy matching to a strict system identifier risks silently
  merging unrelated records, which is worse than surfacing them as
  unmatched (D9).
- Amount-sum resolution for split/netted groups sharing a join key — no
  LLM needed to check that two numbers sum to a third.
- Arithmetic invariant checks (`amount - fee - tax == credit`, rounding
  tolerance).

**Routed to the LLM — only when deterministic logic has already tried and
came up short:**
1. A `NEAR_MISS` or `AMBIGUOUS` group — the deterministic tiers found a
   candidate but couldn't clear their own bar, or found multiple candidates
   that don't resolve to a clean group.
2. A group that ends up unresolved needs a *specific* explanation, not a
   status code.
3. The Settlement Q&A agent — a genuinely open-ended natural-language
   surface with no fixed deterministic shape to fall back to.

**Measured, not asserted:** on the real generated dataset, the AI layer was
invoked on **11 of 109 groups (10.1%)** — comfortably inside the ~20%
boundary BUILD_BRIEF's own framing argues for. `test_clean_matched_groups_never_invoke_the_ai_layer`
enforces this at the code level: a clean deterministic match is structurally
incapable of reaching the LLM, not just conventionally routed away from it.

**Why this split, stated plainly:** an LLM call is non-deterministic,
costs money and latency, and (per this project's own live-run findings,
§7) can be wrong on the exact cases that matter — a duplicate-record
candidate was misjudged in one live run and correctly judged in the next
(D20). Routing everything through the LLM would mean paying that variance
cost on the ~90% of records where a plain string/arithmetic comparison
already gives a deterministic, auditable, zero-variance answer. Reserving
the LLM for genuine judgment calls is not a cost-cutting shortcut — it's
the more defensible design, because it confines the one non-deterministic
component to exactly the cases that need judgment.

## 4. Component reference

| Module | Responsibility | Key decision(s) |
|---|---|---|
| `reconciliation/models.py` | Canonical transaction schema, source-agnostic | D1 (pydantic boundary validation), D2 (Decimal for money), D3 (join-key topology), D4 (`settlement_amount` property) |
| `reconciliation/loaders/` | Raw CSV/JSON -> `CanonicalTransaction` | D1 |
| `reconciliation/dataset/generator.py` | Synthetic dataset, 10 trap categories, hidden ground truth, seeded/reproducible | D6 (scenario-based, not per-record sampling), D10 (high-entropy UTRs) |
| `reconciliation/matching/rules.py` | Pure arithmetic/date-window checks | — |
| `reconciliation/matching/key_match.py` | Generic exact -> fuzzy -> rule-based tiered matcher | D8 (dataclasses, not pydantic — internal, not boundary), D9 (fuzzy tier danger on structured IDs), D11 (sum-resolution vs. subset-sum) |
| `reconciliation/matching/engine.py` | Wires `key_match.py` to the 3 real sources | D9 (which join gets fuzzy enabled) |
| `reconciliation/ai/client.py` | `StructuredReasoningClient` Protocol + `GroqReasoningClient` | D12 (structured-output contract), D13 (fake-client testability), D18/D20 (provider swaps) |
| `reconciliation/ai/{ambiguous_match,exception_explain}.py` | The two AI Reasoning Layer core calls | D14 (only non-deterministic groups reach here) |
| `reconciliation/ai/qa_agent.py` | Settlement Q&A — deterministic retrieval + one grounded LLM call | D15 (retrieval-then-call, not a tool-use loop) |
| `reconciliation/ai/evaluation.py` | Ground-truth precision/recall + consistency measurement | — |
| `reconciliation/audit/{log,report,build}.py` | Append-only audit trail + full results report | D16 (exception definition), D17 (no web viewer) |

## 5. Measured results (live, real API, real dataset — not a fake-client estimate)

From `scripts/run_full_pipeline.py` against `openai/gpt-oss-20b` via Groq
(full detail and a two-run comparison in `docs/decision_log.md` D20):

| | ledger &lt;-&gt; gateway | gateway &lt;-&gt; bank |
|---|---|---|
| Total groups | 55 | 54 |
| Deterministic match rate | 90.9% (50/55) | 88.9% (48/54) |
| Match rate incl. AI-resolved | 90.9% | 88.9% |
| Ambiguous / unmatched | 2 / 3 | 0 / 6 |
| Tier breakdown | exact=52, none=3 | exact=44, fuzzy=4, none=6 |

- **AI layer invoked on 11 of 109 groups (10.1%)** — 13 LLM calls, 7,955
  input + 3,168 output tokens.
- **Decision consistency, measured directly** (not assumed): the same
  ambiguous duplicate-record candidate run 5 times live agreed 5/5 on
  `match=False` (correct per ground truth), confidence range 0.60–0.95,
  mean 0.834, stdev 0.127 (`scripts/run_consistency_check.py`).
- **Exception list is specific, not generic.** Every one of the 11
  exceptions in the live run cited the actual record IDs, amounts, and flags
  involved (e.g. "gateway record pay_00052 shows on_hold=True and
  settled=False — payout pending, not missing" — correctly distinguishing
  the pending-settlement trap from a genuine mismatch, per BUILD_BRIEF's
  explicit requirement).
- **Dataset is adversarial, not decorative:** 46% clean exact-match (well
  under the "too clean" 95% flag BUILD_BRIEF warns against), all 10 trap
  categories from Section 4b represented in the generated data.

## 6. Explicitly out of scope, and why

Per BUILD_BRIEF Section 3 — restated here because "why didn't you build X"
is a fair panel question:

- **Banking-format parsers** (MT940/CAMT.053/ISO20022), **SFTP ingestion**,
  **auth/JWT**, **Redis caching**, **rate limiting infra**, **Docker**,
  **cryptographic hash-chaining** for the audit log. None of these are
  measured by Track 04's bar (match rate + honest exceptions); time went
  into matching/exception quality and dataset adversarial-ness instead.
- **A web results viewer** (D17) — the CLI text report + JSONL audit log
  fully satisfy the bar without an unjustified framework dependency.
- **Generic subset-sum search** for split/netted detection (D11) — this
  project's split/netted traps are always key-grouped (they share
  order_id/settlement_utr), so plain summation resolves them completely.
  Generic subset-sum search across an *unrelated* pool with no shared key
  remains a real, harder problem (the Zetheta project's original use case)
  but doesn't exist in this dataset, so it wasn't built.

## 7. A concrete demonstration of the architecture's resilience

The AI backend was swapped twice during development — Anthropic to Gemini
(D18), then Gemini to Groq (D20, forced by a Gemini free-tier daily quota
exhaustion). Both swaps were contained entirely to one file
(`reconciliation/ai/client.py`) plus one live-only test, because
`StructuredReasoningClient` is a `Protocol` (D13): every caller
(`ambiguous_match.py`, `exception_explain.py`, `qa_agent.py`, `pipeline.py`)
depends on the interface shape, never a concrete SDK. All 108 fake-client
tests passed through both swaps with zero modification. This wasn't
planned as a resilience demo — it happened because the actual account
credentials changed mid-project — but it's a real, live-fire validation of
the design decision, not just a claim about it.

Live validation of the AI layer also caught 4 real bugs that no
unit/integration test against a fake client could reach — a deprecated
default model name, a token-budget cutoff, a JSON-schema strictness
requirement, and fabricated currency symbols in generated prose (full
per-bug, per-provider attribution table in `docs/decision_log.md` D20).
This is the concrete argument for why the live smoke test (`test_client_live.py`,
gated behind `pytest.mark.skipif` so its absence never fails a run) exists
as a deliberate, separate verification step rather than a formality.

## 8. Testing strategy

- **124 tests, 98% line coverage** on `reconciliation/`. Every gap in the
  remaining 2% is accounted for: two branches in `ai/client.py`'s real
  network path (the rate-limit retry loop and the JSON/schema-validation-
  error branch inside D21's repair-retry logic) that only fire under live
  API conditions the credential-gated live test and D21's mocked-SDK tests
  don't each individually reach, and two explicitly-documented
  near-impossible defensive branches.
- **Fake-client testing for the AI layer** (`tests/ai/fake_client.py`): a
  `StructuredReasoningClient` implementation with a configurable responder,
  used to test every branch of the pipeline's decision logic (which groups
  escalate, how a rejected decision triggers a follow-up explanation) with
  zero network access and no API key required.
- **Integration tests run the real matching engine and real dataset
  generator together** (not just isolated unit fixtures), asserting against
  the dataset's hidden ground-truth trap labels — e.g.
  `test_reference_typo_is_resolved_by_fuzzy_tier_not_left_unmatched` proves
  the fuzzy tier actually resolves the specific trap it exists for, not just
  that it runs without crashing.
- **Two real bugs were caught by tests, not by inspection**, both in the
  dataset generator (a UTR-truncation collision, D10; a split-transaction
  false-duplicate flag, before D-numbering) — concrete evidence the test
  suite does real work rather than rubber-stamping already-correct code.
