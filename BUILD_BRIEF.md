# Build Brief: AI Finance Controller — Multi-Source Reconciliation Engine
### Razorpay AI Buildathon 2026 — Track 04

This document is the full context handoff for building this project in Claude Code.
Read this fully before writing any code. Follow the workflow rules in Section 0 for
the entire project — they govern how we work together, not just how the code is written.

---

## 0. Working Protocol (apply for the whole project)

You are acting as a Senior Staff Engineer + Mentor, not an autocomplete. Rules:

1. **Continuous workflow.** Move through: requirements → architecture → data model →
   core logic → implementation → test → review → next milestone — automatically.
   Don't ask "what should we do next" when the next step is obvious from the milestone plan below.
2. **Don't offer A/B/C choices for routine decisions.** Make the reasonable engineering
   call, state it as an `Assumption:` or `Decision:`, briefly justify it, and continue.
   Only stop and ask when: a requirement is genuinely ambiguous, two approaches have
   materially different consequences, it's a personal preference, or it would
   significantly change scope.
3. **Maintain living project state** at each milestone boundary: Completed / Current /
   Next / Decisions / Assumptions / Open Issues. Don't make me re-explain prior decisions.
4. **Incremental build, not a code dump.** For each component: Explain what/why →
   Design + trade-offs → give structure/pseudocode/interfaces → let me implement where
   practical → review my code like a PR (correctness, bugs, edge cases, performance,
   design, maintainability) → help write tests → move on.
5. **Teach the reasoning**, not just output code. I should be able to explain every
   major decision on a whiteboard afterward without you.
6. **Don't over-engineer.** Every dependency/technology needs a stated reason tied to
   this project's actual requirements. Prefer the simpler option when it's sufficient.
7. **Dataset must be adversarial, not decorative** — it should test the system, not
   flatter it. See Section 4 for required trap cases.
8. **Testing is continuous**, not a final step. Each meaningful component gets tests
   as it's built.
9. **Keep a decision log** (Decision / Context / Alternatives / Reason / Trade-offs)
   for every significant architectural choice — this becomes part of final documentation.
10. **Interview prep is part of the deliverable**, not an afterthought — flag concepts
    I should be able to explain as we go, not just at the end.

Default behavior loop: **Think → Decide → Explain → Build → Test → Review → Continue.**
Not: Think → Ask → Wait → Ask again.

---

## 1. Context: What This Is For

Razorpay AI Buildathon 2026 — a build-first hiring pipeline for AI Builder Interns.
No resume screening; selection is based on a working project, a public repo, a 5-minute
pitch video, and architecture documentation, followed by a panel review.

**Track 04 — AI Finance Controller**
> Build an agent that closes one finance-ops loop across a 50+ record batch of synthetic
> data, reporting its match rate and the exceptions it could not resolve.

**The Bar (what judges explicitly said they're checking for):**
> Throughput plus measured accuracy plus an honest exception list. One cherry-picked
> match proves nothing.

This means: don't optimize for a demo that looks good once. Optimize for defensible,
measured numbers (match rate %, per-tier breakdown, confidence scores) and an honest,
categorized account of everything that *couldn't* be resolved automatically.

**Deadline: September 5, 2026.**

---

## 2. Background: Prior Experience to Draw On

The builder previously built a **production-grade multi-bank reconciliation engine**
during a Zetheta internship — ingest (CSV/MT940/CAMT.053) → canonical normalization →
3-tier matching (exact → fuzzy → rule-based, confidence-scored) → split/netted
transaction detection via subset-sum DP → 18-category exception classifier with 4-tier
escalation → hash-chained audit log → FastAPI + auth + rate limiting → dashboard →
Docker deploy. That was a 7-day, 100K–500K record scale build.

**This project is NOT that project.** It is a fresh, original build for this buildathon,
*informed by* lessons learned there, at a scope appropriate to 50–150 synthetic records
instead of 500K, and without banking-standard format parsers (MT940/CAMT.053) since
Track 04 doesn't require them and they add zero scoring value here.

**Specific lessons to carry forward (apply, don't re-explain to yourself — just apply):**
- Source record counts are *not* expected to be 1:1 across sources — splits, netted
  settlements, and genuinely unmatched records all cause count mismatches on purpose.
  Never measure "equal record counts." Always measure match % and correctly-identified
  exceptions.
- "95%+ exact match" is normal on clean synthetic data; production-realistic data should
  land exact-match around 70–85%, with fuzzy/rule tiers picking up the rest. If our
  synthetic dataset yields 95%+ *exact* matches, it's too clean — go back and inject
  more of the trap cases in Section 4.
- When something *should* be auto-resolved by the matching engine (e.g., a partial
  match or netted settlement), it reaching the exception classifier as an unresolved
  case is a debugging signal that the matcher failed — not a normal outcome.
- "Ambiguous match" (two candidates share the same composite key) is a distinct,
  correctly-named failure mode — don't conflate it with a generic "no match" exception.
- Record *near-miss information* during matching (e.g., "closest candidate found,
  confidence 0.55, just under threshold") rather than discarding that signal the moment
  a candidate fails to clear the bar. This is what makes the exception list *explainable*
  instead of a black-box dump — directly serves "the bar."

---

## 3. Scope Decisions (already made — do not re-litigate unless truly necessary)

**Decision:** Build a lean, single-run reconciliation engine across 3 synthetic sources,
with full matching + exception + near-miss + audit-trail logic. No banking-format
parsers, no production API hardening (auth/rate-limiting/Redis), no multi-tenant
concerns, no deployment infra beyond "runs locally / simple to demo."

**Reason:** Track 04's bar is measured accuracy + honest exceptions on 50+ records —
not format breadth or production infra. Time and effort should go into matching/exception
logic quality and dataset adversarial-ness, since that's what's actually being judged.

**Sources (3):**
1. `internal_ledger` — the business's own transaction records (source of truth intent)
2. `bank_settlement` — what the bank actually shows as settled
3. `gateway_report` — Razorpay-style gateway settlement report (fees/commission deducted
   before payout)

**`gateway_report` field schema — modeled on Razorpay's real Settlement Recon API**
(not invented placeholder fields — this is deliberately grounded in Razorpay's actual
settlement report shape, which strengthens the architecture doc's credibility with the
panel):

| Field | Meaning | Reconciliation role |
|---|---|---|
| `entity_id` / `order_id` | The underlying payment/order reference | Primary key candidate for exact-match tier |
| `type` | e.g. "payment" | Context field |
| `amount` | Gross transaction amount | Compare against `internal_ledger` amount |
| `fee` | Razorpay's commission | `credit = amount - fee - tax` — this is the fee-deduction trap, not an "error" |
| `tax` | GST on the fee | See above |
| `credit` | Net amount actually settled | What should match `bank_settlement` inflow |
| `settled` | boolean | Distinguishes "not yet settled" from "mismatch" |
| `on_hold` | boolean | Extra realistic field — becomes its own exception category: `on_hold=true` is "pending," not an error, and must be classified differently from a genuine mismatch |
| `created_at` / `settled_at` | timestamps | Source of the date/timezone-offset trap |
| `settlement_id` | Groups multiple payment line items under one settlement | This is what makes the netted/batched-settlement trap realistic — real Razorpay settlements genuinely roll up multiple payments under one `settlement_id` |
| `settlement_utr` | Bank-side settlement reference | The field your matcher should try to join against `bank_settlement`'s UTR/reference field — realistic reference-ID typo/truncation trap surface |

Explicitly excluded from scope (real Razorpay features, not worth the complexity here):
instant/on-demand settlements (`settlement.ondemand`), multi-currency/USD settlement
conversion. Both are real but add complexity with no scoring benefit for Track 04.

**Stack:** Python (matches builder's existing proficiency from the Zetheta project).
Assumption: pandas for data handling, a fuzzy-matching library (e.g. `rapidfuzz`) for
the deterministic fuzzy tier, pytest for testing, Anthropic API (Claude) for the AI
Reasoning Layer (Section 4a) via direct API calls — no agent framework needed, since
the tasks are single-shot structured reasoning calls, not multi-step autonomous
workflows. No web framework required unless a lightweight results viewer is wanted
(optional — see Milestone 6).

**Why this matters for eligibility, not just architecture:** this is the "AI
Buildathon," hiring "AI Builder Interns" — every track's language ("agent," "AI
Reasoning") signals AI use is a requirement, not a nice-to-have. A purely deterministic
matching pipeline would technically satisfy Track 04's literal wording (match rate +
exceptions) but would not demonstrate what the program is actually screening for. See
Section 4a for exactly where AI is and isn't used, and why.

**Explicitly out of scope:** MT940/CAMT.053/ISO20022 parsing, SFTP ingestion, auth/JWT,
Redis caching, rate limiting, Docker, cryptographic hash-chaining (keep the *concept* of
an append-only audit log; skip the crypto). If any of these seem tempting to add, they
need a stated reason tied to Track 04's actual requirements first (Rule 6).

---

## 4. AI Reasoning Layer (first-class component, not a stretch add-on)

**Principle:** use deterministic logic where it's sufficient; use the LLM only where
genuine judgment is required. State this boundary explicitly in the architecture doc —
"why isn't everything AI?" is a likely panel question, and "we used deterministic logic
for the ~80% of unambiguous cases and reserved the LLM for the ~20% requiring judgment"
is a stronger answer than routing everything through a model.

**Stays deterministic (no LLM):**
- Exact-match tier (string/ID equality)
- Fuzzy-match tier (rapidfuzz similarity scoring)
- Arithmetic checks (`credit = amount - fee - tax`, rounding tolerance)

**Routed to the AI Reasoning Layer:**
1. **Ambiguous/near-miss match resolution.** When the deterministic tiers produce a
   below-threshold candidate (e.g., amount matches, date off by N days, reference ID
   62% similar), pass the specific record pair + surrounding context to the LLM and
   have it return a structured decision: `{match: bool, confidence: float, reasoning:
   string, suspected_trap_category: string}`. This reasoning string becomes the
   human-readable exception explanation — directly serves "the bar" (explainability).
2. **Exception categorization & explanation.** For records that end up unmatched, the
   LLM generates a specific, actionable explanation (not a generic "STATUS: UNMATCHED")
   — e.g., identifying a likely netted-settlement pattern and recommending manual review.
3. **Settlement Q&A agent** (from Track 04's own example directions). Natural-language
   question in → answer grounded in the actual settlement data out (e.g., "why did I
   receive ₹9,800 instead of ₹10,000?" → explanation citing the specific `fee`/`tax`
   fields). This is the most clearly agentic, judge-recognizable component — build it
   as a real milestone, not an afterthought.
4. **Cross-exception pattern detection** (stretch). Once the exception list exists,
   have the LLM look across all of them for systemic patterns (e.g., "12 of 15
   exceptions share the same `settlement_id` — likely one systemic issue, not 12
   separate ones") — this is closer to the "verification capacity" framing in the
   track's own "Why Now" section.

**Testing approach for the AI layer (different from unit tests, must be planned, not
improvised):**
- **Structured output validation**: every LLM call must return schema-conformant JSON
  (match/confidence/reasoning) — validate this in code, treat a malformed response as a
  hard failure, not a silent pass-through.
- **Consistency checks**: run the same ambiguous pair through the LLM multiple times;
  flag/measure variance in the match decision and confidence score. Report this
  variance honestly in the results — it's part of "measured accuracy," not something to
  hide.
- **Ground-truth evaluation**: since the dataset generator labels each record's true
  trap category (Section 4), evaluate the AI layer's decisions against that hidden
  ground truth the same way the deterministic tiers are evaluated — precision/recall,
  not vibes.
- **Cost/latency logging**: log every LLM call's token usage and latency in the audit
  trail. If the AI layer is only invoked for the ambiguous ~20% (not all records), this
  should be a small, defensible number — worth stating explicitly in the results report
  ("AI layer invoked on N of 150 records; deterministic tiers resolved the rest").

---

## 4b. Dataset Design (build right after the AI layer is scoped, before the matching engine)

Target: ~150 total records across the 3 sources combined (comfortably clears the 50+
minimum while giving the matcher enough adversarial variety to be meaningful).

Required trap categories and target proportions:

| Trap case | Description | Target (~% of records) |
|---|---|---|
| Clean exact match | Baseline, proves exact-match tier works | ~60% |
| Fee deduction | `credit = amount - fee - tax` per real Razorpay settlement fields — must NOT be flagged as a plain amount mismatch | ~7% |
| Split transaction | 1 internal record maps to 2+ bank/gateway records | ~5% |
| Netted/batched settlement | Multiple internal records share one `settlement_id`/`settlement_utr`, settling as 1 combined bank record — mirrors how real Razorpay settlements roll up | ~3% |
| Date/timezone offset | `settled_at` lands a day after `created_at` — must not cause a false non-match if matcher is date-strict | ~6% |
| Reference ID typo/truncation | Corrupted `settlement_utr` forces the fuzzy-matching tier to actually be exercised | ~7% |
| Currency/rounding difference | Paise-level rounding differences | ~3% |
| Pending / on-hold | `settled=false` or `on_hold=true` — a distinct, honest "pending" category, not lumped with genuine mismatches | ~4% |
| Genuinely unmatched | No real counterpart exists — must surface honestly as an exception, never silently dropped | ~5% |
| Duplicate record | Same transaction appears twice in one source — ambiguous match, not a hash collision | ~3% |

The dataset generator itself is a deliverable component — build it with a fixed random
seed for reproducibility, and log which trap category each synthetic record belongs to
in a hidden "ground truth" field (used only for our own evaluation, not fed to the
matcher) so we can measure precision/recall of the matcher and exception classifier
against known-correct answers.

---

## 5. Architecture (target shape — refine during Milestone 1 in Claude Code)

```
                Canonical Transaction Model
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
internal_ledger      bank_settlement       gateway_report
   (loader)              (loader)              (loader)
      │                    │                    │
      └────────────────────┼────────────────────┘
                           │
                 Normalization Layer
                           │
          Deterministic Matching Tiers
              (exact → fuzzy → rule, confidence-scored)
              (records near-miss info, not just pass/fail)
                           │
              ┌────────────┴────────────┐
              │                         │
      High-confidence           Below-threshold /
      matches (auto-accept)     ambiguous candidates
              │                         │
              │                         ▼
              │              ┌─────────────────────────┐
              │              │  AI Reasoning Layer (LLM) │
              │              │  - Resolves ambiguous pairs│
              │              │  - Categorizes exceptions  │
              │              │  - Settlement Q&A           │
              │              │  - Cross-exception patterns │
              │              │    (stretch)                │
              │              └─────────────────────────┘
              │                         │
              └────────────┬────────────┘
                           │
                  Audit Trail (append-only log,
                  no crypto — see Section 3; includes
                  AI call cost/latency/consistency logs)
                           │
              Results Report (match rate %,
              per-tier breakdown incl. AI-resolved count,
              exception list with AI-generated reasoning,
              confidence distribution)
                           │
        (Stretch: split/netted detector,
         cross-exception pattern detection)
```

---

## 6. Milestone Plan (adapted from the 7-day Zetheta cadence to this scope/timeline)

1. **Repo scaffold + canonical data model** — project structure, canonical transaction
   schema, source loaders (simple CSV/JSON, not banking formats)
2. **Dataset generator** — build per Section 4b, with hidden ground-truth labels for
   evaluation
3. **Deterministic matching tiers** — exact → fuzzy → rule-based, confidence scoring,
   near-miss recording (records that fall below threshold get flagged for the AI layer,
   not silently dropped)
4. **AI Reasoning Layer, core build** — ambiguous-match resolution + exception
   categorization/explanation (Section 4, items 1–2). Includes structured-output
   validation and ground-truth evaluation from the start, not bolted on later.
5. **Settlement Q&A agent** — natural-language Q&A grounded in settlement data
   (Section 4, item 3) — this is the clearest agentic, judge-recognizable component;
   treat as core, not stretch.
6. **Audit trail + reporting layer** — match rate %, per-tier breakdown (including
   AI-resolved count), full exception list with AI-generated reasoning, AI call
   cost/latency/consistency logs; optional lightweight results viewer
7. **(Stretch, in priority order)** — (a) cross-exception pattern detection, (b)
   split/netted transaction detection via subset-sum
8. **Testing pass + documentation + interview prep** — architecture doc explicitly
   stating the AI-vs-deterministic boundary and why, README, decision log writeup, and
   a prep pass on likely panel questions including "why isn't everything AI?" (per
   Section 0, Rule 10 / protocol Section 16)

Proceed through these automatically, milestone by milestone, applying Section 0's rules
throughout. Start with Milestone 1.
