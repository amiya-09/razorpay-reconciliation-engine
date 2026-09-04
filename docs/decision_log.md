# Decision Log

Format: Decision / Context / Alternatives / Reason / Trade-offs.

## Index

**Canonical model & data (M1-M2)**
- [D1](#d1--pydantic-for-the-canonical-transaction-model) — pydantic for the canonical transaction model
- [D2](#d2--decimal-for-all-money-fields-not-float) — `Decimal` for all money fields
- [D3](#d3--canonical-join-key-design-order_id-vs-reference_id-gateway_report-as-hub) — join-key topology (order_id / reference_id / hub)
- [D4](#d4--settlement_amount-property-not-a-raw-field-comparison-drives-amount-matching) — `settlement_amount` property
- [D5](#d5--python-313-venv-not-system-python-39) — Python 3.13 venv
- [D6](#d6--dataset-generator-built-around-scenarios-not-per-record-percentage-sampling) — scenario-based dataset generator
- [D7](#d7--reference-id-typo-corruption-must-avoid-no-op-transpositions) — reference-ID-typo corruption bug (no-op transpositions)

**Matching engine (M3)**
- [D8](#d8--matching-engine-result-types-are-plain-dataclasses-not-pydantic) — result types are dataclasses, not pydantic
- [D9](#d9--fuzzy-matching-tier-is-disabled-for-order_id-enabled-only-for-reference_id) — fuzzy tier disabled for `order_id`
- [D10](#d10--synthetic-utrs-must-be-high-entropy-not-small-sequential-counters) — high-entropy UTRs (2 collision bugs)
- [D11](#d11--splitnetted-groups-resolved-by-plain-summation-not-subset-sum) — split/netted via summation, not subset-sum

**AI Reasoning Layer & Q&A agent (M4-M5)**
- [D12](#d12--ai-reasoning-layer-uses-messagesparse--output_format-not-manual-tool-forcing) — structured output via `messages.parse()` *(superseded by D18/D20 — kept as history)*
- [D13](#d13--ai-layer-is-tested-via-a-fake-structuredreasoningclient-live-call-gated-separately) — fake-client testing strategy
- [D14](#d14--fuzzy-tiers-danger-on-structured-ids-d9-generalizes-to-the-ai-layers-boundary) — AI layer only touches non-deterministic groups
- [D15](#d15--settlement-qa-agent-deterministic-retrieval--one-grounded-llm-call-not-a-tool-use-loop) — Q&A agent: retrieval + one grounded call

**Audit & reporting (M6)**
- [D16](#d16--a-group-only-counts-as-an-exception-if-no-confirmed-match-exists--not-if-the-deterministic-tier-didnt-match-it) — exception definition (AI-confirmed matches excluded)
- [D17](#d17--skipped-a-web-results-viewer-cli-text-report--jsonl-audit-log-instead) — no web viewer

**Provider swaps & live validation**
- [D18](#d18--ai-reasoning-layer-backend-switched-from-anthropic-to-gemini-google-genai) — Anthropic → Gemini
- [D19](#d19--live-api-debugging-on-the-gemini-backend-2-bugs-both-gemini-only-both-fixed-before-the-provider-swap) — 2 Gemini-only bugs (deprecated model, thinking-budget cutoff)
- [D20](#d20--ai-reasoning-layer-backend-switched-again-gemini-to-groq--after-live-run-validation-surfaced-2-more-real-bugs) — Gemini → Groq; 2 Groq-only bugs + live-run results + per-provider bug attribution table
- [D21](#d21--call_structured-gets-one-repair-retry-on-a-schema-invalid-generation) — one repair retry on a schema-invalid generation (partial success ≠ total failure)

---

## D1 — pydantic for the canonical transaction model

**Context:** Loaders read raw CSV/JSON rows where every value arrives untyped
(strings from CSV, loosely-typed from JSON). The canonical model is the
ingestion boundary — the one place type coercion and validation should happen,
so nothing downstream (matcher, AI layer, reports) has to defensively re-check
types.

**Alternatives:** stdlib `dataclasses` (zero dependency, manual parsing);
`attrs` (similar tradeoffs to dataclasses, less common now that pydantic v2 is fast).

**Reason:** pydantic gives coercion (`"1000.00"` → `Decimal`, ISO string →
`datetime`) and validation errors for free, exactly at the boundary where the
brief says validation belongs. The dependency is small and directly earns its
place — not an unjustified addition.

**Trade-offs:** one more third-party dependency; pydantic v2's frozen-model
semantics are slightly different from dataclasses' `frozen=True` (raises a
`ValidationError` on mutation attempt rather than `FrozenInstanceError` — noted
in tests, not a real cost).

---

## D2 — `Decimal` for all money fields, not `float`

**Context:** The fee-deduction check (`credit == amount - fee - tax`) is an
exact arithmetic comparison. Section 3's whole point is that this must NOT be
misclassified as an amount mismatch.

**Alternatives:** `float` (simplest, what most tutorials use); integer paise
(avoids Decimal entirely, common in real payment systems).

**Reason:** `Decimal` matches the input data's actual precision (2dp INR
amounts) without float binary-rounding artifacts, and needs no paise-conversion
step in loaders/reports where human-readable amounts matter (audit trail,
Q&A agent).

**Trade-offs:** slightly slower than float or int arithmetic — irrelevant at
150-record scale. Rounding tolerance for the currency-rounding trap case still
needs to be handled explicitly in the matcher (not solved by Decimal alone).

---

## D3 — Canonical join-key design: order_id vs. reference_id, gateway_report as hub

**Context:** The three sources don't share one universal key. `internal_ledger`
knows `order_id`; the bank statement only knows its own reference/UTR; only
`gateway_report` (Razorpay's real settlement schema) knows both, plus
`settlement_id` for netted batches.

**Alternatives:** force a single `external_id` field and hope one join key
covers all pairs (would silently break for internal_ledger <-> bank_settlement,
which has no real-world direct key); do fuzzy matching on everything uniformly
regardless of source pair (wastes the exact-match tier where a real key exists).

**Reason:** Modeling `order_id` and `reference_id` as two distinct fields
mirrors the real join topology: internal_ledger<->gateway_report on `order_id`
(exact tier viable), gateway_report<->bank_settlement on `reference_id`
(exact tier, but this is exactly the field the reference-ID-typo trap
corrupts — fuzzy tier is meant to catch it). internal_ledger<->bank_settlement
has to go through gateway_report as the hub, or fall back to an amount+date
rule tier if a gateway record is itself missing/unmatched.

**Trade-offs:** if a `gateway_report` record is missing for some transaction,
internal_ledger<->bank_settlement reconciliation has no exact tier at all and
must rely entirely on the fuzzy/rule tiers — this is intentional (mirrors
reality) but must be covered by the matching engine, not treated as an edge
case to special-case away.

---

## D4 — `settlement_amount` property, not a raw field comparison, drives amount matching

**Context:** Comparing `internal_ledger.amount` directly against
`gateway_report.amount` is correct; comparing it against `bank_settlement.amount`
is not, because the bank amount is net-of-fee. Section 3 explicitly calls this
the "fee-deduction trap, not an error."

**Alternatives:** let the matching engine (Milestone 3) special-case this with
if/else per source pair.

**Reason:** Putting `settlement_amount` (net_amount if known, else gross) on
the canonical model means the matcher's amount-comparison logic can be
source-agnostic — it always compares `settlement_amount` to `settlement_amount`
for the final settled-amount check, and separately compares gross `amount` to
`amount` for the pre-fee check. Keeps the trap handling out of matcher control
flow.

**Trade-offs:** none significant; this is a thin derived property, not stored
data, so it can't drift from `net_amount`/`amount`.

---

## D5 — Python 3.13 venv, not system Python 3.9

**Context:** System `python3` on this machine resolves to 3.9.6.

**Alternatives:** write code compatible with 3.9 (no `X | None` union syntax,
no `dict[str, Any]` builtin generics without `from __future__ import annotations`).

**Reason:** Homebrew already had 3.13 installed; using it removes syntax
constraints for zero cost, and matches what a fresh contributor pulling the
repo in 2026 would reasonably have.

**Trade-offs:** `README.md` setup instructions assume `python3.13` is
available; not a concern for a single-developer buildathon submission with a
committed `requirements.txt`.

---

## D6 — Dataset generator built around "scenarios," not per-record percentage sampling

**Context:** Section 4b's trap-category table gives target percentages of
*records*. But several traps (split_transaction, netted_settlement,
duplicate_record) are inherently multi-record by definition — you can't have
a "split transaction" trap that's one record. Sampling trap category
independently per record would either be incoherent (a lone record can't
"be" a split) or require post-hoc grouping that fights the generator's own
output.

**Alternatives:** generate all records for one source first with random trap
labels, then reconcile IDs across sources after the fact (fragile, easy to
produce inconsistent join keys); hit the record percentages exactly by
solving for scenario counts algebraically per category.

**Reason:** A scenario (`scenario_clean`, `scenario_split`, `scenario_netted`,
...) is the natural unit — one business event, emitting an internally
consistent set of rows across whichever sources it touches. `SCENARIO_PLAN`
fixes scenario *counts* (not record percentages) chosen so the resulting
record-level mix stays close to Section 4b's spirit — comfortably-populated
minority categories, clean-match well under the "too clean" 95% warning
threshold — without pretending to hit each percentage exactly. `describe_mix()`
prints the actual resulting breakdown so this is a transparent, inspectable
choice, not a hidden approximation.

**Result (seed=42):** 163 total records (internal_ledger=57, gateway_report=55,
bank_settlement=51); clean_exact_match=46.0%, all 9 other categories present
at 3.7%–9.2% each. Full breakdown reproducible via
`python -m reconciliation.dataset.generator`.

**Trade-offs:** the record-level percentages don't line up 1:1 with Section
4b's table (e.g. split/netted scenarios read higher than their table target
because each scenario emits 5–7 records); this is called out explicitly
rather than gamed by shrinking those scenario counts to hit a percentage that
would then under-populate the actual trap variety being tested.

---

## D7 — Reference-ID-typo corruption must avoid no-op transpositions

**Context:** `corrupt_reference()` simulates a bank reference that's a
truncated or transposed version of the gateway's UTR. UTRs are zero-padded
(`UTR000000012`), so a naive "swap two adjacent characters" transposition
frequently swaps two identical digits — producing a "corrupted" reference
that's byte-identical to the original, silently defeating the trap. Caught by
`test_reference_typo_scenario_actually_corrupts_the_bank_reference` failing
on the first generator run.

**Fix:** only transpose a pair of *differing* adjacent characters; fall back
to truncation if none exist.

**Why this matters beyond the immediate bug:** it's a concrete instance of
BUILD_BRIEF's own warning (Section 2) — a "trap" that doesn't actually trap
anything is worse than no trap, because it creates false confidence that the
fuzzy-match tier is being exercised when it isn't. Caught by a test that
asserted on the *effect* of the corruption (no exact match exists), not just
that the corruption function ran.

---

## D8 — Matching-engine result types are plain dataclasses, not pydantic

**Context:** `GroupMatchResult`, `RuleCheck` etc. are internal computation
results produced entirely by our own code, never parsed from external
input.

**Reason:** D1 established pydantic's job as boundary validation. These types
never cross that boundary — they're built from already-validated
`CanonicalTransaction` objects. Using plain `@dataclass(frozen=True)` keeps
the distinction visible in the code itself: pydantic means "this shape was
just validated from raw input," dataclass means "this is a computed result."

**Trade-offs:** none — this is a consistency choice, not a capability trade-off.

---

## D9 — Fuzzy matching tier is disabled for `order_id`, enabled only for `reference_id`

**Context:** The matching engine's fuzzy tier exists to catch the
reference-ID-typo trap (bank UTR truncated/transposed vs. gateway's
settlement_utr). While building it, I checked whether applying the same
fuzzy tier to the `order_id` join (internal_ledger <-> gateway_report) was
safe, since both joins reuse the same generic `match_by_key()`.

It isn't. `rapidfuzz.fuzz.ratio("order_00019", "order_00023") == 0.82`, and
`ratio("order_00019", "order_00119") == 0.91` — both above `NEAR_MISS_FLOOR`
(0.55) and the second above `FUZZY_ACCEPT_THRESHOLD` (0.85), despite being
two completely unrelated transactions. Structured, zero-padded system
identifiers are almost entirely shared characters by format, so string
similarity on them is dominated by the format, not the identifying digits.

**Alternatives:** raise the fuzzy threshold specifically for order_id (fragile
— the "safe" threshold shifts with ID length/format and there's no principled
value to pick); require exact ID length match before fuzzy comparison
(doesn't prevent the order_00019/00119 case above, same length).

**Reason:** `order_id` is a strict system identifier both sources are
expected to agree on exactly — if it doesn't match exactly, that's a genuine
data problem (wrong join, missing record) or an ambiguous/split case handled
by group-shape resolution, never a "close enough, probably a typo" situation.
Fuzzy matching belongs only where typos are the *expected*, *designed-for*
failure mode: the bank's own free-text reference field. Implemented as an
`enable_fuzzy_tier` flag on `match_by_key()`, off for the ledger<->gateway
join, on for the gateway<->bank join. Proven by
`test_fuzzy_tier_disabled_leaves_structurally_similar_ids_unmatched`.

**Trade-offs:** a real order_id typo (not modeled in this dataset) would
surface as UNMATCHED rather than a near-miss — the right trade-off, since
silently fuzzy-correcting a business identifier is more dangerous than
surfacing it as an honest exception for review.

---

## D10 — Synthetic UTRs must be high-entropy, not small sequential counters

**Context:** Found via the matching engine's own integration tests, not by
inspection — two independent bugs surfaced when the reference-ID-typo
scenario ran against the real matcher instead of just the dataset generator's
own unit tests:

1. `corrupt_reference()` originally truncated the last 3 characters of a
   sequential, zero-padded UTR (`UTR000000041` -> `UTR000000044`, etc, only
   4 apart because several typo scenarios ran back-to-back in the scenario
   plan). Truncating 3 chars destroyed exactly the digits that varied between
   them, collapsing multiple distinct "corrupted" references into the
   identical string `"UTR000000"` — the matcher correctly reported this as
   AMBIGUOUS (3 bank candidates sharing one key), which is the right response
   to bad input, but the input itself was wrong.
2. After switching to 1-char truncation, then to digit-transposition, a new
   collision appeared: with only ~60 UTRs live in a tiny, low-integer ID
   space, a single adjacent-digit swap on `UTR000000042` frequently produced
   a string that coincidentally equalled a different, unrelated, already-used
   UTR (e.g. `UTR000000024`) — an exact collision with someone else's real
   reference, not a "close but not quite" typo.

**Fix:** `next_utr()` now generates a random 12-digit numeric string (matching
real bank UTR/RRN format) with a uniqueness check against all previously
issued UTRs, and `corrupt_reference()` uses transposition only (no
truncation) — a permutation of existing digits can't collapse two distinct
UTRs into the same string the way truncation can, and with 12 random digits
a coincidental collision with another live UTR is negligible.

**Why this matters beyond the immediate bug:** both bugs were invisible to
the dataset generator's own unit tests (which only checked "is the corrupted
value different from the original?") and only surfaced once the actual
matching engine ran against the full generated dataset and asserted on
*outcomes* (`status is MATCHED`) rather than just structure. This is the
concrete case for building integration tests against ground truth as part of
the same milestone as the engine itself, not deferred to a later "testing
pass" — per BUILD_BRIEF Section 0 Rule 8.

---

## D11 — Split/netted groups resolved by plain summation, not subset-sum

**Context:** Section 4b's split_transaction and netted_settlement traps both
produce a group of records sharing one join key (order_id for split,
reference_id/settlement_utr for netted) whose amounts should sum to the
counterpart's amount. BUILD_BRIEF Section 7(b) lists "split/netted
transaction detection via subset-sum DP" as a stretch goal, carried over from
the Zetheta project's harder problem: reconciling an amount against an
*unordered pool* of candidates where the actual matching subset isn't known
in advance.

**Reason our case doesn't need that:** in a key-grouped match (both `1:N` and
`N:1` shapes in `_resolve_lopsided_group`), every record in the "many" side
is *already known* to belong to the group — they share the join key. There is
no subset to search for; summing the whole group and comparing to the "one"
side's amount is the correct and complete check. Generic subset-sum is only
needed when candidates must be *discovered* from a larger, unrelated pool
with no shared key at all (the real stretch case, deferred to Milestone 7 if
time allows).

**Trade-offs:** if a split/netted group's join key were itself corrupted
(e.g. a netted settlement_utr with a typo on top of being netted), this
matcher would not currently recover it — fuzzy tier resolves single 1:1
typo'd keys but doesn't extend to fuzzy-then-group-sum in one pass. Not
observed in the generated dataset (no scenario combines both traps
simultaneously) and explicitly out of scope for this milestone.

---

## D12 — AI Reasoning Layer uses `messages.parse()` + `output_format`, not manual tool-forcing

> **Superseded by D18**: the AI layer's backend moved from Anthropic to
> Gemini after this was written. The reasoning below (structured output as a
> boundary-validated hard-failure contract, not a design detail) still
> applies — only the concrete API/SDK it's implemented against changed. Left
> intact as the historical record of the original decision.

**Context:** BUILD_BRIEF Section 4 requires every LLM call to return
schema-conformant JSON, validated in code, with a malformed response treated
as a hard failure. The older pattern for this is forcing a specific tool via
`tool_choice={"type": "tool", "name": ...}` and manually parsing
`tool_use.input`. Checked the current Anthropic Python SDK (installed:
`anthropic==1.3.0`, latest) via the `claude-api` skill rather than assume —
`client.messages.parse(..., output_format=SomePydanticModel)` exists and
returns `response.parsed_output` as an already-validated instance of that
model, or `None` on failure.

**Reason:** `messages.parse()` does exactly what BUILD_BRIEF Section 4 asks
for — schema-conformant structured output, validated automatically — with
less code than manually declaring a tool schema, forcing `tool_choice`, and
parsing `tool_use.input` by hand. `AnthropicReasoningClient.call_structured()`
wraps it and raises when `parsed_output is None`, turning "malformed
response" into a hard failure per Section 4's testing requirement rather than
a silent `None` propagating downstream.

**Model choice:** `claude-opus-5` — the current default for new integrations,
confirmed via the skill rather than guessed (model IDs specific to the
Messages API can differ from Claude Code session model aliases).

**Trade-offs:** ties this project to a specific SDK version's structured-output
feature (`messages.parse`) rather than the more universally-supported raw
tool-forcing pattern; acceptable since the SDK version is pinned in
`requirements.txt` and the buildathon deliverable runs against that pin, not
an arbitrary future SDK version.

---

## D13 — AI layer is tested via a fake `StructuredReasoningClient`, live call gated separately

**Context:** No `ANTHROPIC_API_KEY` (or `ant auth login` profile) is
available in this development environment, and the AI layer's logic
(prompt construction, which groups get escalated to the LLM, how a
rejected ambiguous-match decision flows into a follow-up exception
explanation) needs to be tested regardless of whether a live key exists —
both here and later in CI/grading environments that may not have one either.

**Reason:** `StructuredReasoningClient` is a `Protocol` (structural typing,
not inheritance) implemented by both `AnthropicReasoningClient` (real) and
`FakeReasoningClient` (test-only, in `tests/ai/fake_client.py`) with a
configurable `responder` callable. Every wiring test — "clean matches never
invoke the AI layer," "a rejected ambiguous decision triggers a follow-up
exception explanation," "usage summary counts invoked groups correctly" —
runs with zero network access and no API key. A single live smoke test
(`test_client_live.py`) exists to catch drift between this wrapper and the
actual API contract, gated by `pytest.mark.skipif` so its absence never fails
a run — it just doesn't verify that one integration point until a key is
present.

**Trade-offs:** the fake client can't catch a real API/SDK behavior change
(e.g. a future SDK version altering what `parsed_output` looks like on
failure) — that risk is accepted and mitigated only by the gated live test,
which requires a key to actually exercise. Documented here as an explicit
open issue rather than a hidden gap: **run `test_client_live.py` with a real
key before treating the AI layer as verified end-to-end**, not just
unit-tested.

---

## D14 — Fuzzy tier's danger on structured IDs (D9) generalizes to the AI layer's boundary

**Context:** While writing `pipeline.py`, worth stating explicitly why the
AI layer is only invoked on NEAR_MISS/AMBIGUOUS/UNMATCHED groups and never on
a MATCHED group to "double-check" it.

**Reason:** This is the direct continuation of D9's principle: don't apply a
probabilistic judgment mechanism (fuzzy string matching there, an LLM here)
where a deterministic check has already produced a confident, correct
answer. Routing every record through the LLM "for safety" would both cost
more (violates BUILD_BRIEF's ~80/20 boundary) and introduce non-determinism
into the ~80% of cases where none was needed — strictly worse on every axis
that matters for this project. `test_clean_matched_groups_never_invoke_the_ai_layer`
enforces this at the code level, not just as a design intention.

---

## D15 — Settlement Q&A agent: deterministic retrieval + one grounded LLM call, not a tool-use loop

**Context:** BUILD_BRIEF Section 4 item 3 calls the Q&A agent "the most
clearly agentic, judge-recognizable component" and gives the exact example
question: "why did I receive ₹9,800 instead of ₹10,000?" That phrasing
sounds like it wants an agent that searches the data itself (e.g. via a
tool-use loop where the LLM calls a `search_records` tool).

**Alternatives:** give the LLM a `search_records` tool and let it run its own
retrieval loop (genuinely more "agentic" in the tool-use sense); dump the
entire ~163-record dataset into every prompt and let the model find what's
relevant unaided (no retrieval code needed, but wastes tokens on every call
and risks the model conflating similar-looking records).

**Reason:** BUILD_BRIEF Section 3 explicitly scopes this project's AI usage
to "single-shot structured reasoning calls, not multi-step autonomous
workflows" — a stated constraint, not an oversight. At ~163 records, a
regex-based identifier/amount extraction (`extract_identifiers`,
`extract_amounts`) plus a lookup-and-expand step (`SettlementIndex`) is
exactly reproducible, needs no LLM call to do retrieval, and is trivially
unit-testable. The "agentic" character the brief is really asking for is
grounding a natural-language answer in real data rather than having the
model guess — which this delivers via retrieval-then-one-call, not via
letting the LLM drive its own search loop. Given the tiny dataset, a tool-use
retrieval loop would add latency and non-determinism to the *retrieval* step
for no accuracy benefit.

**A concrete gap this design caught:** the shared `describe_side()` prompt
formatter (also used by the ambiguous-match and exception-explanation
prompts) originally omitted `fee`/`tax` fields. Writing
`test_build_prompt_includes_record_details_when_found` — which checks that a
"why is this short" question's prompt actually contains the fee amount —
failed immediately, because the exact data needed to answer BUILD_BRIEF's own
example question wasn't in the prompt at all. Fixed by adding fee/tax/settled/
on_hold to `describe_side()`, which improves all three prompt consumers, not
just the Q&A agent.

**Trade-offs:** a question that doesn't mention any ID and whose amount isn't
close to any record's amount (outside the ₹50 tolerance) returns no grounding
data even if a human would recognize which transaction is meant from other
context (e.g. "my payment from yesterday") — the agent correctly says "I
don't have a record matching that" rather than guessing, which is the
intended behavior per BUILD_BRIEF's explainability principle, but it does
mean the agent's recall is bounded by what's mentioned explicitly in the
question.

---

## D16 — A group only counts as an "exception" if no confirmed match exists — not "if the deterministic tier didn't match it"

**Context:** Building the results report, the naive definition of "exception
list" is "everything the deterministic matcher didn't mark MATCHED." That's
wrong once the AI layer is in the picture: a NEAR_MISS or AMBIGUOUS group the
AI layer subsequently reviewed and confirmed as a genuine match (e.g. a
reference-ID-typo case the fuzzy tier scored just under threshold, which the
LLM then correctly identified as the same transaction) is not an unresolved
exception — it's resolved, just by a different tier of the pipeline.

**Reason:** `build_exceptions()` explicitly excludes any group where
`ambiguous_decision.match is True`, so the exception list reflects what's
*actually* still unresolved after the full pipeline (deterministic + AI) has
run, not just what the deterministic layer alone couldn't handle. This is
also why the report distinguishes `match_rate` (deterministic tiers only)
from `match_rate_including_ai` — both numbers are reported, so "how much did
the AI layer actually contribute" stays honestly visible rather than being
folded into one number that overstates either the deterministic engine's
power or the AI layer's necessity.

**Trade-offs:** none identified — this is a correctness fix over the naive
definition, not a trade-off between two valid options.

---

## D17 — Skipped a web results viewer; CLI text report + JSONL audit log instead

**Context:** BUILD_BRIEF Milestone 6 lists "optional lightweight results
viewer" and Section 3 states "no web framework required unless a lightweight
results viewer is wanted (optional)."

**Reason:** Per Rule 6 (no unjustified dependencies), a web framework
(Flask/FastAPI + templates or a JS frontend) would need its own justification
tied to Track 04's actual bar — measured match rate + an honest exception
list — neither of which needs a browser to demonstrate. `ReconciliationReport.render_text()`
and `AuditLog.to_jsonl()` together give a complete, inspectable account
(match rate, per-tier breakdown, full exception list with AI reasoning, cost/
latency) that's trivially embeddable in the architecture doc or shown
directly in the pitch video's terminal. Revisit only if the pitch video
specifically benefits from an interactive view — not before.

**Trade-offs:** no visual/interactive exploration of the exception list for
the judging panel; mitigated by the text report being copy-pasteable into
the architecture doc directly, and by `AuditLog.to_jsonl()` being trivially
loadable into a notebook or spreadsheet if deeper inspection is wanted later.

---

## D18 — AI Reasoning Layer backend switched from Anthropic to Gemini (`google-genai`)

**Context:** The AI layer (M4/M5) was originally built against the Anthropic
API (D12), following BUILD_BRIEF Section 3's stack assumption. The builder
corrected this mid-project: the actual account/credentials being used for
this deliverable are Gemini's, not Anthropic's.

**What changed:** `reconciliation/ai/client.py`'s `AnthropicReasoningClient`
was replaced with `GeminiReasoningClient`, calling
`client.models.generate_content(model=..., contents=user, config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json", response_schema=output_format, max_output_tokens=max_tokens))`
from the `google-genai` SDK, reading the parsed result from
`response.parsed` and usage from `response.usage_metadata.prompt_token_count`
/ `.candidates_token_count`. Default model: `gemini-2.5-flash` (later
corrected to `gemini-3.6-flash` — deprecated between writing this and the
first live call; see D19 bug 1). Credentials
resolve from `GEMINI_API_KEY` or `GOOGLE_API_KEY` (Client() with no
`api_key` argument picks either up automatically, matching the pattern
`AnthropicReasoningClient` used for `ANTHROPIC_API_KEY`).

**What did NOT change, by construction:** the `StructuredReasoningClient`
Protocol (`call_structured(system, user, output_format, max_tokens) -> T`,
plus `call_log: list[AICallLog]`) is unchanged — it was already
provider-agnostic by design (D13's whole point was decoupling
ambiguous_match.py/exception_explain.py/qa_agent.py/pipeline.py from any
specific SDK via structural typing). Every one of the 89 tests written
against `FakeReasoningClient` continued to pass with zero modification,
which is the concrete payoff of D13's fake-client testing strategy: the
provider swap was contained entirely to one file
(`reconciliation/ai/client.py`) plus the one live smoke test
(`test_client_live.py`, updated to check `GEMINI_API_KEY`/`GOOGLE_API_KEY`
instead of Anthropic's credential resolution order) and doc/README mentions.

**Verification approach:** rather than trust training-data recall of the
`google-genai` API shape (which the `claude-api` skill's own "API drift"
warnings make a habit worth generalizing beyond just the Anthropic SDK), the
exact usage was cross-checked via WebSearch/WebFetch against
googleapis/python-genai's own docs and GitHub issues, then confirmed against
the actually-installed SDK version in this project's venv
(`python3 -c "from google.genai import types; types.GenerateContentConfig.model_fields.keys()"`)
before writing any code. This caught one questionable WebFetch result
(`client.interactions.create(...)`, which isn't a real method on this SDK —
likely a fetch-summarization artifact) that a second, independently-sourced
check ruled out. Also confirmed: `response.parsed` returns `None` (not an
exception) on schema-validation failure — the same silent-failure shape
`AnthropicReasoningClient` guarded against, so the existing
"`None` is a hard failure, raise" logic in `call_structured()` needed no
change beyond the attribute name (`parsed_output` -> `parsed`).

**Trade-offs:** none identified specific to correctness — this is a
same-capability provider swap behind an already-abstracted interface, which
is precisely the scenario the Protocol-based design (D13) was meant to make
cheap.

---

## D19 — Live-API debugging on the Gemini backend: 2 bugs, both Gemini-only, both fixed before the provider swap

Both bugs below were hit and fixed while `GeminiReasoningClient` was still
the active backend (D18), before the swap to Groq (D20). Neither occurred
on, or was tested against, Groq — see the corrected per-provider attribution
table at the end of D20.

**Bug 1 — deprecated default model.** `DEFAULT_MODEL` was set to
`gemini-2.5-flash` in D18. The first live smoke test against it failed with
`404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available to
new users`, with the API's own error message naming the exact replacement.
Fixed by changing `DEFAULT_MODEL` to `gemini-3.6-flash`. (This bug was
previously omitted from the decision log entirely — it only appeared in
chat — corrected here on request.)

**Bug 2 — thinking tokens silently consuming the output budget.** With the
model fixed, `GeminiReasoningClient` passed every fake-client test but the
first real end-to-end run against a realistic-length prompt failed with
`response.parsed is None`, despite an isolated short-prompt test succeeding.

Root cause, found by direct API introspection (not guessed): Gemini's
"thinking" tokens are drawn from the *same* `max_output_tokens` budget as the
visible response. `response.usage_metadata.thoughts_token_count` showed
608–673 tokens of invisible thinking consumed per call; on a longer prompt
this left nothing in the 1024-token cap for the actual JSON, so
`response.parsed` came back `None` — indistinguishable from a genuine
schema-validation failure without inspecting `finish_reason` and
`usage_metadata` directly. Fixed by capping
`thinking_config=types.ThinkingConfig(thinking_budget=512)` and raising
`max_output_tokens` to 2048.

**Both bugs were fully resolved on Gemini** — the subsequent swap to Groq
(D20) was for an unrelated reason (a Gemini free-tier *daily* quota
exhaustion), not because these bugs were unfixable. The general lesson
carried forward regardless of provider: **a structured-output call returning
"no output" can mean several different things — malformed content, a
token-budget cutoff, a safety refusal — and a hard-failure handler should
distinguish them via `finish_reason`/equivalent metadata, not assume "None"
always means "the model got the shape wrong."** `GroqReasoningClient` sets
`reasoning_effort="low"` as a preemptive precaution carrying this lesson
forward — **not** because the same failure was observed on Groq. It wasn't:
Groq was never run at default reasoning effort, so there is no evidence
either way whether `openai/gpt-oss-20b` would exhibit an equivalent
budget-cutoff behavior. Correcting an overstated claim from an earlier draft
of this entry, which asserted Groq "doesn't have" this behavior as if
confirmed.

---

## D20 — AI Reasoning Layer backend switched again, Gemini to Groq — after live-run validation surfaced 2 more real bugs

**Context:** After D18's swap to Gemini, the user asked for a full live end-to-end run (real API, real dataset, no fake client) to get real match-rate/cost/latency numbers. That run surfaced a genuine operational blocker: Gemini's free-tier daily quota (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, 20 requests/day for `gemini-3.6-flash`) was exhausted partway through — partly consumed by the debugging calls needed to fix D19's two Gemini-only bugs. The user then asked to switch to a Groq API key already present (commented out) in `.env`.

**A naming trap worth recording:** the user initially said "Grok" (xAI's chatbot). The key in `.env` started with `gsk_`, which is Groq's (the inference-hosting company at api.groq.com) key prefix, not xAI's. Rather than guess which was meant, I surfaced the mismatch and asked — confirmed as Groq. Two real companies, near-identical names, different APIs, different key formats: worth being deliberately suspicious of "sounds right" name matches in this space rather than assuming.

**What changed:** `GeminiReasoningClient` → `GroqReasoningClient`, using
`client.chat.completions.create(model=..., messages=[{"role":"system",...},{"role":"user",...}], response_format={"type":"json_schema","json_schema":{"name":...,"strict":True,"schema":output_format.model_json_schema()}}, max_completion_tokens=..., reasoning_effort="low")`
from the `groq` SDK (OpenAI-compatible shape), parsing `response.choices[0].message.content` via `json.loads` + `output_format.model_validate(...)` (Groq has no built-in `.parsed` helper the way Anthropic/Gemini do), and reading usage from `response.usage.prompt_tokens`/`.completion_tokens`. Default model: `openai/gpt-oss-20b` (confirmed via Groq's docs to support `strict: true` JSON-schema mode). Credentials resolve from `GROQ_API_KEY` (verified directly from the installed SDK's `Groq.__init__` source, not docs alone).

**Two more real bugs the live run caught, neither reachable by the fake-client test suite:**

1. **Strict JSON-schema mode requires `additionalProperties: false` on every object.** The very first live call to `GroqReasoningClient` returned `400 Bad Request: 'additionalProperties:false' must be set on every object`. Pydantic's `model_json_schema()` doesn't set this by default. Fixed by adding `model_config = ConfigDict(extra="forbid")` to every schema in `reconciliation/ai/schemas.py` — this is what makes pydantic emit `additionalProperties: false`. As a consequence, `QAAnswer.cited_record_ids` could no longer have a default value either (strict mode requires every property in `required`), so it's now a required field — callers must pass `cited_record_ids=[]` explicitly for "no citations" rather than relying on a default. This is a schema-format constraint the `FakeReasoningClient` tests structurally cannot catch, because they call the Python constructor directly and never touch the JSON-schema wire format at all.

2. **Fabricated currency symbols in generated explanations.** Reviewing the live output's prose (not a failing test — a manual read of the actual generated text), every exception explanation used a *different, invented* currency symbol (₹, €, $) despite the entire dataset being uniformly INR. Traced to `describe_side()` (the shared prompt formatter in `prompt_helpers.py`) never including the `currency` field at all — the model had no grounding for which currency to use and guessed. Fixed by adding `currency={t.currency}` to the record dump, plus an explicit system-prompt instruction ("use the exact `currency` field ... never invent a symbol not in the data") across all three AI-layer system prompts as a second line of defense. Confirmed fixed on the next live run: all explanations consistently say "INR" afterward. This is the same *class* of gap as D15's missing fee/tax fields — a prompt-completeness bug that only surfaces by reading real model output, since no automated test asserts on which currency string appears in free-text prose.

**Live validation results (real dataset, real API, two full runs — see chat log for full output):**

| Metric | Run 1 (pre currency-fix) | Run 2 (post currency-fix) |
|---|---|---|
| ledger↔gateway match_rate | 90.9% (50/55) | 90.9% (50/55) |
| ledger↔gateway match_rate_including_ai | 92.7% (1 ai-resolved) | 90.9% (0 ai-resolved) |
| gateway↔bank match_rate / incl. AI | 88.9% / 88.9% (0 ai-resolved) | 88.9% / 88.9% (0 ai-resolved) |
| AI layer invoked | 11 of 109 groups (10.1%) | 11 of 109 groups (10.1%) |
| LLM calls / cost | 12 calls, 6569 in + 2949 out tokens | 13 calls, 7955 in + 3168 out tokens |
| Exceptions | 10 | 11 |

**The one substantive discrepancy between the two runs — and the reason it's not a bug:** the same ambiguous duplicate-record candidate (`led_00054`/`led_00055` vs `pay_00054`) was judged `match=True` in run 1 and `match=False` in run 2. Ground truth says this pair is a genuine `duplicate_record`, so run 2's answer is correct and run 1's was a real model error, not a code defect — this is exactly the kind of variance BUILD_BRIEF Section 4 requires measuring and reporting honestly rather than smoothing over. Ran `evaluation.consistency()` (built in M4, never previously exercised against a live model) against this exact candidate over 5 repeated live calls: `agreement_rate=1.0`, all 5 runs correctly said `match=False` (confidence range 0.60–0.95, mean 0.834, stdev 0.127) — so the run-1 anomaly reads as an outlier within normal sampling variance, not a systematic bias, though n=5 on one candidate is a small sample and this should be read as indicative, not a full accuracy certification.

**What the fake-client test suite could and could not tell us, made explicit:** the 108 fake-client tests correctly verify *wiring* — which groups get escalated to the AI layer, how a rejected decision triggers a follow-up explanation, that clean matches never invoke the LLM, that every group is accounted for exactly once. Every structural invariant those tests encode held in both live runs (`match_rate_including_ai >= match_rate`; `clean_matched + ai_resolved + exceptions == total_groups`). What they cannot and were never meant to catch: real model judgment variance, JSON-schema wire-format constraints, and prompt-completeness gaps that only show up in the actual generated prose. Both categories of finding are exactly why the live smoke test and this live full-pipeline run exist as a separate, deliberate verification step (D13) rather than a formality.

**Full bug attribution by provider (corrected after being asked to verify — an earlier draft of this section, and a chat summary, both blurred this):**

| # | Bug | Provider it occurred on | Where documented |
|---|---|---|---|
| 1 | Deprecated default model (`gemini-2.5-flash` → `gemini-3.6-flash`) | **Gemini only** | D19 bug 1 |
| 2 | Thinking tokens silently consuming the output budget | **Gemini only** | D19 bug 2 |
| 3 | Strict JSON-schema mode requires `additionalProperties: false` | **Groq only** | D20 bug 1, below |
| 4 | Fabricated currency symbols in generated prose | **Found on Groq's live run.** Root cause (missing `currency` field in the shared `describe_side()` formatter) is provider-agnostic — Gemini was never run far enough (full pipeline, real amount-bearing prose) to confirm or rule out the same gap there. | D20 bug 2, below |

No bug occurred on both providers; none of the four were ever observed on Gemini and Groq alike. Bugs 1–2 were fully fixed on Gemini before the provider swap — the swap itself was for an unrelated reason (Gemini's free-tier *daily* quota, not these bugs).

**Trade-offs:** `openai/gpt-oss-20b` is a smaller/faster model than what a paid tier of Gemini or a larger Groq model would offer; reasoning quality on the live run was good (specific, correctly-grounded explanations citing actual amounts/IDs/flags) but this is one project's worth of qualitative observation, not a benchmarked comparison — revisit the model choice if exception-explanation quality becomes a concern during the pitch/demo prep.

---

## D21 — `call_structured` gets one repair retry on a schema-invalid generation

**Context:** A live demo-recording session hit `groq.BadRequestError` mid-run.
Inspecting the failure: `match`, `confidence`, and `reasoning` were all
correctly filled by the model, but `suspected_trap_category` was missing as
its own field — the model had instead written the trap category as a phrase
inside the `reasoning` text. Groq's strict JSON-schema mode rejected the
whole generation as invalid rather than returning it with one field empty,
and the previous `call_structured` treated this exactly like every other
hard failure: raise immediately, no retry.

**The assumption this breaks:** every prior failure mode this client
handled (D12's "malformed response," D20's `additionalProperties` schema
bug) was modeled as "the model either returns valid structured output or
the call fails outright." This incident is neither — the model *partially*
succeeded (3 of 4 fields correct, and correct in substance) and still got
rejected wholesale by the API's strict-mode enforcement. "Strict" schema
mode constrains generation; it does not make a schema violation impossible.

**Alternatives:** treat this identically to every other hard failure (the
prior behavior) — simple, but throws away a call that was one field away
from valid, on a task cheap enough that a second attempt is clearly worth
it; retry indefinitely until valid — risks masking a persistently broken
prompt/schema mismatch behind silent retries, and burns cost/latency for no
benefit if the failure is structural rather than a one-off sampling miss.

**Fix:** `call_structured` now retries exactly once on `BadRequestError`
(the API-level rejection), empty content, or a JSON/pydantic validation
failure — three ways a generation can fail to become valid structured
output, all handled by the same one-retry budget. The retry appends an
explicit reminder to the prompt ("every field must be filled in as its own
top-level field... never embed one field's value as text inside another
field") rather than resending the identical prompt and hoping for different
sampling luck. If the retry also fails, the client raises with both errors
attached — still a hard failure per D12's principle, just no longer an
overreaction to a single-field, single-generation mistake.

**Testing:** `tests/ai/test_client.py` mocks the underlying `groq` SDK
client directly (`client._client.chat.completions.create`) with a
`BadRequestError`-then-valid-response sequence — no network, no API key —
confirming the retry fires, the reminder is appended to the second attempt's
prompt, only the successful attempt logs usage, and a persistent failure
(both attempts invalid) still raises. This is a different test strategy
from `FakeReasoningClient` (which tests *callers* of the client against the
`StructuredReasoningClient` Protocol) — this tests the client's own retry
logic, which sits below that Protocol boundary and can't be exercised
through it.

**Trade-offs:** one repair retry roughly doubles worst-case latency and
cost for the ~10% of records that reach the AI layer at all — negligible in
absolute terms at this dataset's scale, and strictly better than either
failing on a recoverable mistake or retrying indefinitely on an
unrecoverable one.
