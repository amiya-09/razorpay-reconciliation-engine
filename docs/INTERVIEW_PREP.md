# Interview / Panel Prep

Every answer here should be explainable on a whiteboard without looking
anything up (BUILD_BRIEF's own bar). Each points to the exact code/decision
for follow-up depth. Organized by theme, roughly in the order a panel would
probe.

---

## 1. "Why isn't everything AI?" (the question BUILD_BRIEF names explicitly)

**Short answer:** deterministic logic is faster, free, zero-variance, and
fully auditable — use it wherever a string/arithmetic comparison already
gives a confident answer. Reserve the LLM for genuine judgment calls: a
below-threshold fuzzy match, multiple candidates that don't resolve to a
clean group, or a record that needs a *specific* explanation rather than a
status code.

**Why this is the *stronger* answer, not a cost-cutting shortcut:** this
project measured its own AI layer's variance live — the same ambiguous
duplicate-record candidate was judged `match=True` in one run and (correctly)
`match=False` in the next (`docs/decision_log.md` D20). Routing everything
through the LLM means paying that variance cost on the ~90% of records where
a plain comparison already gives a deterministic answer. Confining the
LLM to genuine judgment calls isn't just cheaper — it's more defensible,
because it's the *only* place variance can occur at all.

**Measured, not asserted:** AI layer invoked on 11 of 109 groups (10.1%) —
`test_clean_matched_groups_never_invoke_the_ai_layer` enforces this at the
code level (a clean match is structurally incapable of reaching the LLM).

**Code:** `reconciliation/matching/engine.py` (deterministic tiers) +
`reconciliation/ai/pipeline.py::augment_with_ai` (the routing boundary).

---

## 2. Why pydantic for the canonical model, not stdlib dataclasses?

Loaders read raw CSV/JSON where every value arrives untyped (strings from
CSV, loosely-typed from JSON). pydantic does coercion (`"1000.00"` →
`Decimal`, ISO string → `datetime`) and validation exactly at the ingestion
boundary — the one place it should happen, so nothing downstream needs to
defensively re-check types.

**But then why are the *matching engine's* result types plain
`@dataclass`, not pydantic (D8)?** Because they're computed internally from
already-validated data, never parsed from external input. The distinction
in the codebase is deliberate and consistent: pydantic means "just crossed a
trust boundary," dataclass means "we built this ourselves." Same logic
applied a third time for the AI layer's `schemas.py` — an LLM response is
exactly as much untrusted external input as a CSV row, so it gets pydantic
too (`docs/decision_log.md` D1, D8).

---

## 3. Why `Decimal`, not `float`, for money?

The fee-deduction trap's whole point is `credit == amount - fee - tax` must
NOT be misread as an amount mismatch. `Decimal` matches the input's actual
2dp precision with no float binary-rounding artifacts, so that comparison
can be an *exact* equality check, not a fuzzy tolerance band papering over
float error. `docs/decision_log.md` D2.

---

## 4. Why is the fuzzy-matching tier disabled on `order_id` but enabled on `reference_id`?

**Reproducible on demand:**
```python
from rapidfuzz import fuzz
fuzz.ratio("order_00019", "order_00023") / 100   # 0.818 — completely unrelated transactions
fuzz.ratio("order_00019", "order_00119") / 100   # 0.909 — above a naive accept threshold
```
Structured, zero-padded system identifiers are almost entirely shared
characters by *format*, so string similarity on them is dominated by
formatting, not the identifying digits. Applying fuzzy matching there risks
silently merging two unrelated transactions — worse than surfacing them as
unmatched. `reference_id`/UTR is different: typos and truncation there are
the *designed* failure mode this project's dataset deliberately injects, so
fuzzy matching is the correct tool exactly there. `docs/decision_log.md` D9.

---

## 5. How do you tell "ambiguous," "near-miss," "genuinely unmatched," and "duplicate" apart?

These are four distinct, honestly-named failure modes — never conflated
(a lesson carried forward explicitly from prior reconciliation-engine
experience, BUILD_BRIEF Section 2):

| Status | Meaning | Example in this dataset |
|---|---|---|
| `MATCHED` (fuzzy tier) | Below-exact but above-threshold key similarity, rules pass | typo'd bank reference |
| `NEAR_MISS` | A candidate exists but doesn't clear the accept threshold | escalated to the AI layer, never silently dropped |
| `AMBIGUOUS` | Multiple candidates share a key and don't resolve to a clean group | 2 ledger rows (duplicate) vs. 1 gateway record |
| `UNMATCHED` | No viable candidate at all | genuinely orphaned record, or a `pending`/`on_hold` payout with no bank leg yet |

The duplicate-record case is a *specific instance* of `AMBIGUOUS`: the
matcher's `_resolve_lopsided_group` checks whether the "many" side's amounts
sum to the "one" side (a real split) — if the sum fails **and** the "many"
side's amounts are near-identical to each other, it's flagged as a likely
duplicate rather than a generic ambiguous group. `reconciliation/matching/key_match.py`.

---

## 6. Why does split/netted resolution use plain summation, not subset-sum?

Within a key-grouped match (all `1:N`/`N:1` shapes here), every candidate is
*already known* to belong to the group — they share the join key. There's
no subset to search for; summing the whole group and comparing to the other
side is complete and correct. Generic subset-sum is only needed when
candidates must be *discovered* from a larger, unrelated pool with no shared
key — that's a harder, different problem (from the Zetheta project this
built on), deliberately not present in this dataset, so not built here.
`docs/decision_log.md` D11.

---

## 7. Walk me through the fee-deduction trap end to end.

`gateway_report` always deducts `fee` and `tax` before crediting
(`credit = amount - fee - tax`) — real Razorpay settlement behavior, not an
error. The canonical model exposes `settlement_amount` (net if known, else
gross) so the matcher always compares net-to-net for the settled-amount
check and gross-to-gross for the pre-fee check, without any per-source
special-casing in the matcher itself (D4). The dataset's `fee_deduction`
scenario deliberately uses a *larger* fee (7% vs. the standard 2%) so the
gap is big enough that a naive amount-tolerance check would wrongly flag it
— only the explicit arithmetic check resolves it correctly
(`reconciliation/matching/rules.py::check_fee_arithmetic`).

---

## 8. Why did the AI layer get built on structured output (`response_schema`/JSON-schema mode), not free text + regex parsing?

BUILD_BRIEF Section 4 requires every LLM call to return schema-conformant
JSON, validated in code, with a malformed response treated as a hard
failure. Structured-output modes do exactly that at the SDK level —
`response.parsed` (Gemini/Anthropic) or manual `json.loads` +
`model_validate` (Groq) either returns a validated object or fails loudly,
with no regex-scraping of free text needed. `docs/decision_log.md` D12.

---

## 9. Your AI backend changed twice mid-project (Anthropic → Gemini → Groq). What did that actually cost you?

One file (`reconciliation/ai/client.py`) plus one live-only test, both
times. Every caller (`ambiguous_match.py`, `exception_explain.py`,
`qa_agent.py`, `pipeline.py`) depends on `StructuredReasoningClient`, a
`Protocol` — structural typing, not inheritance — so nothing about the
provider leaks into the logic that decides *which* groups need AI review or
*how* a rejected decision triggers a follow-up explanation. All 108
fake-client tests passed through both swaps with zero modification. This
wasn't planned as a resilience demo; it happened because the actual account
credentials changed mid-project — but it's live-fire proof of the design
choice, not just a claim about it. `docs/decision_log.md` D13, D18, D20.

---

## 10. What did live validation actually catch that your test suite couldn't?

Four real bugs, none reachable by a fake-client test by construction:

1. A deprecated default model name (`gemini-2.5-flash` → `gemini-3.6-flash`)
   — Gemini only, found from the live API's own error message.
2. A token-budget cutoff — Gemini's invisible "thinking" tokens silently
   consumed the entire output budget on a longer prompt — Gemini only.
3. A JSON-schema strictness requirement (`additionalProperties: false`) —
   Groq only, broke the very first live Groq call.
4. Fabricated currency symbols in generated prose (₹/€/$ on a uniformly-INR
   dataset) — found reading Groq's live output; root cause (a missing
   `currency` field in the shared prompt formatter) is provider-agnostic.

**The general principle, not just the bug list:** fake-client tests verify
*wiring* (which groups escalate, how a rejection triggers a follow-up call,
that every group is accounted for exactly once) — they cannot and were never
meant to catch real model judgment variance, wire-format constraints, or
prompt-completeness gaps that only show up in actual generated text. That's
why the live smoke test and a live full-pipeline run exist as a deliberate,
separate verification step, not a formality. Full per-bug, per-provider
attribution: `docs/decision_log.md` D19/D20's table.

---

## 11. How do you know your dataset isn't too easy?

BUILD_BRIEF's own warning: "95%+ exact match on synthetic data means it's
too clean." This dataset lands at 46% clean exact-match, with all 10 trap
categories from Section 4b represented in meaningful numbers (3.7%–9.2%
each) — printed by `describe_mix()` every time the generator runs, not
asserted once and forgotten. Two real bugs were caught *by testing the
generator against the matcher*, not just the generator's own unit tests: a
UTR-truncation collision (D10) and a split-transaction false-duplicate flag
(pre-D-numbering, see the reference-typo/duplicate sections of the log) —
concrete evidence the adversarial design actually stresses the matcher.

---

## 12. What would you build next with more time? (the stretch goals)

In BUILD_BRIEF's own priority order: (a) cross-exception pattern detection —
have the LLM look across the full exception list for systemic patterns
(e.g. "12 exceptions share one `settlement_id`") rather than judging each in
isolation; (b) generic subset-sum search for split/netted candidates that
*don't* share a join key — the harder version of the problem this project's
dataset doesn't currently exercise (see §6 above). Neither was started, to
keep the delivered milestones (M1–M6) fully tested and live-validated rather
than partially building a seventh.

---

## 13. What's explicitly out of scope, and why?

MT940/CAMT.053/ISO20022 parsing, SFTP ingestion, auth/JWT, Redis, rate-
limiting infra, Docker, cryptographic hash-chaining, a web results viewer.
None of these are measured by Track 04's bar (match rate + honest
exceptions); every hour went into matching/exception quality and dataset
adversarial-ness instead — the explicit trade this track is scored on.
`BUILD_BRIEF.md` Section 3, `docs/decision_log.md` D17.
