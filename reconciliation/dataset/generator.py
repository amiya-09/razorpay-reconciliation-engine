"""Synthetic dataset generator for the 3 reconciliation sources.

Design: rather than sampling trap categories independently per record (which
would produce IDs that don't line up across sources at all, or line up too
perfectly), we generate one "scenario" at a time — a scenario is a single
business transaction (or small batch, for split/netted cases) and it emits a
matched set of rows across `internal_ledger` / `gateway_report` /
`bank_settlement` that are internally consistent with the trap category it's
demonstrating.

SCENARIO_PLAN below fixes how many of each scenario type to generate. This is
deliberately NOT a direct per-record-percentage match to BUILD_BRIEF Section 4b's
table: multi-record traps (split_transaction, netted_settlement) each emit
several rows per scenario, which skews record-level percentages away from
scenario-level ones no matter how counts are chosen. `describe_mix()` reports
the *actual* resulting percentages so the discrepancy is visible, not hidden —
see docs/decision_log.md D6.

Reproducibility: every scenario draws from a single seeded `random.Random`, and
IDs are assigned by a monotonic counter — same seed always produces the exact
same dataset, byte for byte.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

from reconciliation.models import TrapCategory

DEFAULT_SEED = 42
BASE_DATE = datetime(2026, 1, 5, 0, 0, 0)

STANDARD_FEE_PCT = Decimal("0.02")
LARGE_FEE_PCT = Decimal("0.07")  # deliberately too big to pass any sane amount-tolerance check
GST_ON_FEE_PCT = Decimal("0.18")


class IdFactory:
    """Monotonic, human-readable IDs — 'order_00001', etc. — except UTRs, which
    need to be high-entropy (see next_utr)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._used_utrs: set[str] = set()

    def next(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}_{self._counters[prefix]:05d}"

    def next_utr(self, rng: random.Random) -> str:
        """A 12-digit numeric string, matching real bank UTR/RRN format.

        Deliberately NOT a sequential counter ("UTR000000042"): with only
        ~60 UTRs in a tiny zero-padded ID space, a single-digit transposition
        (used by the reference-ID-typo trap) frequently landed on another
        already-used UTR by pure coincidence, silently creating a collision
        with an unrelated record instead of a clean typo. Random 12-digit
        strings make that collision probability negligible. See D10.
        """
        while True:
            candidate = "".join(str(rng.randint(0, 9)) for _ in range(12))
            if candidate not in self._used_utrs:
                self._used_utrs.add(candidate)
                return candidate


def money(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def random_amount(rng: random.Random) -> Decimal:
    return money(rng.randint(50_000, 5_000_000))  # 500.00 – 50,000.00


def random_created_at(rng: random.Random, base_date: datetime, day_span: int = 10) -> datetime:
    return base_date + timedelta(
        days=rng.randint(0, day_span),
        hours=rng.randint(9, 19),
        minutes=rng.randint(0, 59),
        seconds=rng.randint(0, 59),
    )


def compute_fee_tax(amount: Decimal, fee_pct: Decimal = STANDARD_FEE_PCT) -> tuple[Decimal, Decimal, Decimal]:
    fee = (amount * fee_pct).quantize(Decimal("0.01"))
    tax = (fee * GST_ON_FEE_PCT).quantize(Decimal("0.01"))
    credit = amount - fee - tax
    return fee, tax, credit


def corrupt_reference(reference: str, rng: random.Random) -> str:
    """Simulate a transposed-digit bank reference vs. the gateway's UTR
    (e.g. manual re-entry or OCR error) — fails exact match, forcing the
    fuzzy tier, without destroying the reference's distinguishing digits.

    Truncation was tried first and dropped, and sequential low-entropy UTRs
    ("UTR000000041") were dropped too: both let a "corrupted" reference land
    on another already-used UTR by pure coincidence, silently creating a
    collision with an unrelated record instead of a clean typo — caught by
    the engine's integration tests reporting an unexpected AMBIGUOUS group
    instead of a clean fuzzy match (D10). Random 12-digit UTRs (next_utr)
    plus digit transposition (which permutes rather than discards) make that
    collision probability negligible.

    UTRs can have repeated adjacent digits — swapping two equal digits would
    silently no-op, so only swap a pair that's actually different.
    """
    differing_pairs = [i for i in range(len(reference) - 1) if reference[i] != reference[i + 1]]
    if not differing_pairs:
        return reference  # astronomically unlikely for 12 random digits — no-op is the safe fallback
    i = rng.choice(differing_pairs)
    chars = list(reference)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


# --- row builders -----------------------------------------------------------


def ledger_row(ledger_id, order_id, amount, created_at, trap, status="captured") -> dict:
    return {
        "ledger_id": ledger_id,
        "order_id": order_id,
        "amount": str(amount),
        "currency": "INR",
        "created_at": created_at.isoformat(),
        "status": status,
        "trap_category": trap,
    }


def gateway_row(
    entity_id, order_id, amount, fee, tax, credit, settled, on_hold,
    created_at, settled_at, settlement_id, settlement_utr, trap,
) -> dict:
    return {
        "entity_id": entity_id,
        "order_id": order_id,
        "type": "payment",
        "amount": str(amount),
        "fee": str(fee),
        "tax": str(tax),
        "credit": str(credit),
        "settled": settled,
        "on_hold": on_hold,
        "created_at": created_at.isoformat(),
        "settled_at": settled_at.isoformat() if settled_at else None,
        "settlement_id": settlement_id,
        "settlement_utr": settlement_utr,
        "trap_category": trap,
    }


def bank_row(bank_ref, amount, value_date, narration, trap) -> dict:
    return {
        "bank_ref": bank_ref,
        "amount": str(amount),
        "value_date": value_date.isoformat(),
        "narration": narration,
        "account_id": "acc_main",
        "trap_category": trap,
    }


# --- scenarios ----------------------------------------------------------
# Each takes (rng, ids, base_date) and returns
# {"internal_ledger": [...], "gateway_report": [...], "bank_settlement": [...]}


def scenario_clean(rng, ids, base_date) -> dict:
    trap = TrapCategory.CLEAN_EXACT_MATCH.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    fee, tax, credit = compute_fee_tax(amount)
    created = random_created_at(rng, base_date)
    settled_at = created + timedelta(days=1)
    utr = ids.next_utr(rng)
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap)],
        "gateway_report": [
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, True, False,
                created, settled_at, None, utr, trap,
            )
        ],
        "bank_settlement": [bank_row(utr, credit, settled_at, f"NEFT RAZORPAY {order_id}", trap)],
    }


def scenario_fee_deduction(rng, ids, base_date) -> dict:
    """Same shape as clean, but fee is large enough that a naive amount-tolerance
    check would (wrongly) flag it as a mismatch — only the explicit
    amount - fee - tax == credit check resolves it correctly."""
    trap = TrapCategory.FEE_DEDUCTION.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    fee, tax, credit = compute_fee_tax(amount, fee_pct=LARGE_FEE_PCT)
    created = random_created_at(rng, base_date)
    settled_at = created + timedelta(days=1)
    utr = ids.next_utr(rng)
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap)],
        "gateway_report": [
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, True, False,
                created, settled_at, None, utr, trap,
            )
        ],
        "bank_settlement": [bank_row(utr, credit, settled_at, f"NEFT RAZORPAY {order_id}", trap)],
    }


def scenario_date_offset(rng, ids, base_date) -> dict:
    """Settlement lands 2-3 days after creation instead of the usual T+1 —
    must not be treated as an anomaly by a date-strict matcher."""
    trap = TrapCategory.DATE_TIMEZONE_OFFSET.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    fee, tax, credit = compute_fee_tax(amount)
    created = random_created_at(rng, base_date)
    settled_at = created + timedelta(days=rng.randint(2, 3), hours=rng.randint(-6, 6))
    utr = ids.next_utr(rng)
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap)],
        "gateway_report": [
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, True, False,
                created, settled_at, None, utr, trap,
            )
        ],
        "bank_settlement": [bank_row(utr, credit, settled_at, f"NEFT RAZORPAY {order_id}", trap)],
    }


def scenario_reference_typo(rng, ids, base_date) -> dict:
    """Bank's own reference is a truncated/transposed version of the gateway's
    settlement_utr — exact-match tier fails by design; fuzzy tier must catch it."""
    trap = TrapCategory.REFERENCE_ID_TYPO.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    fee, tax, credit = compute_fee_tax(amount)
    created = random_created_at(rng, base_date)
    settled_at = created + timedelta(days=1)
    utr = ids.next_utr(rng)
    corrupted_ref = corrupt_reference(utr, rng)
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap)],
        "gateway_report": [
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, True, False,
                created, settled_at, None, utr, trap,
            )
        ],
        "bank_settlement": [bank_row(corrupted_ref, credit, settled_at, f"NEFT RAZORPAY {order_id}", trap)],
    }


def scenario_rounding(rng, ids, base_date) -> dict:
    """Bank credits a few paise off the computed `credit` — real paise-level
    rounding drift, not a genuine mismatch."""
    trap = TrapCategory.CURRENCY_ROUNDING.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    fee, tax, credit = compute_fee_tax(amount)
    created = random_created_at(rng, base_date)
    settled_at = created + timedelta(days=1)
    utr = ids.next_utr(rng)
    drift_paise = rng.choice([-5, -3, -2, -1, 1, 2, 3, 5])
    bank_amount = credit + money(drift_paise)
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap)],
        "gateway_report": [
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, True, False,
                created, settled_at, None, utr, trap,
            )
        ],
        "bank_settlement": [bank_row(utr, bank_amount, settled_at, f"NEFT RAZORPAY {order_id}", trap)],
    }


def scenario_pending(rng, ids, base_date) -> dict:
    """Not yet settled / on hold — no bank_settlement row exists yet. This must
    be classified as 'pending', never lumped in with a genuine mismatch."""
    trap = TrapCategory.PENDING_ON_HOLD.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    fee, tax, credit = compute_fee_tax(amount)
    created = random_created_at(rng, base_date)
    on_hold = rng.random() < 0.5
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap)],
        "gateway_report": [
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, False, on_hold,
                created, None, None, ids.next_utr(rng), trap,
            )
        ],
        "bank_settlement": [],
    }


def scenario_unmatched_internal(rng, ids, base_date) -> dict:
    """Internal-only orphan — e.g. a voided/cancelled attempt with no gateway
    or bank counterpart. Must surface as an exception, never silently dropped."""
    trap = TrapCategory.GENUINELY_UNMATCHED.value
    order_id = ids.next("order")
    amount = random_amount(rng)
    created = random_created_at(rng, base_date)
    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, amount, created, trap, status="voided")],
        "gateway_report": [],
        "bank_settlement": [],
    }


def scenario_unmatched_bank(rng, ids, base_date) -> dict:
    """Bank-only orphan — e.g. an interest credit with no order behind it."""
    trap = TrapCategory.GENUINELY_UNMATCHED.value
    amount = random_amount(rng)
    created = random_created_at(rng, base_date)
    return {
        "internal_ledger": [],
        "gateway_report": [],
        "bank_settlement": [bank_row(ids.next_utr(rng), amount, created, "INTEREST CREDIT", trap)],
    }


def scenario_duplicate(rng, ids, base_date) -> dict:
    """Same transaction logged twice in internal_ledger — an ambiguous-match
    case (two identical candidates), not a data-quality hash collision."""
    trap = TrapCategory.DUPLICATE_RECORD.value
    base = scenario_clean(rng, ids, base_date)
    original = base["internal_ledger"][0]
    duplicate = dict(original, ledger_id=ids.next("led"))
    for row in (original, duplicate, *base["gateway_report"], *base["bank_settlement"]):
        row["trap_category"] = trap
    return {
        "internal_ledger": [original, duplicate],
        "gateway_report": base["gateway_report"],
        "bank_settlement": base["bank_settlement"],
    }


def scenario_split(rng, ids, base_date) -> dict:
    """One internal_ledger record's amount is split across 2 gateway/bank
    records (e.g. two partial captures on the same order)."""
    trap = TrapCategory.SPLIT_TRANSACTION.value
    order_id = ids.next("order")
    total_amount = random_amount(rng)
    created = random_created_at(rng, base_date)
    part1 = money(int(total_amount * 100) // 2)
    parts = [part1, total_amount - part1]

    gateway_rows, bank_rows = [], []
    for part_amount in parts:
        fee, tax, credit = compute_fee_tax(part_amount)
        settled_at = created + timedelta(days=1)
        utr = ids.next_utr(rng)
        gateway_rows.append(
            gateway_row(
                ids.next("pay"), order_id, part_amount, fee, tax, credit, True, False,
                created, settled_at, None, utr, trap,
            )
        )
        bank_rows.append(bank_row(utr, credit, settled_at, f"NEFT RAZORPAY {order_id}", trap))

    return {
        "internal_ledger": [ledger_row(ids.next("led"), order_id, total_amount, created, trap)],
        "gateway_report": gateway_rows,
        "bank_settlement": bank_rows,
    }


def scenario_netted(rng, ids, base_date) -> dict:
    """3 separate internal transactions get batched under one settlement_id/
    settlement_utr and paid out as a single combined bank credit — mirrors how
    real Razorpay settlements roll up multiple payments."""
    trap = TrapCategory.NETTED_SETTLEMENT.value
    settlement_id = ids.next("setl")
    utr = ids.next_utr(rng)
    created = random_created_at(rng, base_date)
    settled_at = created + timedelta(days=1)

    internal_rows, gateway_rows = [], []
    total_credit = Decimal("0")
    for _ in range(3):
        order_id = ids.next("order")
        amount = random_amount(rng)
        fee, tax, credit = compute_fee_tax(amount)
        total_credit += credit
        internal_rows.append(ledger_row(ids.next("led"), order_id, amount, created, trap))
        gateway_rows.append(
            gateway_row(
                ids.next("pay"), order_id, amount, fee, tax, credit, True, False,
                created, settled_at, settlement_id, utr, trap,
            )
        )

    return {
        "internal_ledger": internal_rows,
        "gateway_report": gateway_rows,
        "bank_settlement": [bank_row(utr, total_credit, settled_at, "BATCH SETTLEMENT", trap)],
    }


# --- plan + orchestration ------------------------------------------------

ScenarioFn = Callable[[random.Random, IdFactory, datetime], dict]

SCENARIO_PLAN: list[tuple[str, ScenarioFn, int]] = [
    ("clean_exact_match", scenario_clean, 25),
    ("fee_deduction", scenario_fee_deduction, 4),
    ("split_transaction", scenario_split, 3),
    ("netted_settlement", scenario_netted, 2),
    ("date_timezone_offset", scenario_date_offset, 3),
    ("reference_id_typo", scenario_reference_typo, 4),
    ("currency_rounding", scenario_rounding, 2),
    ("pending_on_hold", scenario_pending, 3),
    ("genuinely_unmatched (internal-only)", scenario_unmatched_internal, 3),
    ("genuinely_unmatched (bank-only)", scenario_unmatched_bank, 3),
    ("duplicate_record", scenario_duplicate, 2),
]


def generate_dataset(
    seed: int = DEFAULT_SEED, base_date: datetime = BASE_DATE
) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(seed)
    ids = IdFactory()
    ledger_rows: list[dict] = []
    gateway_rows: list[dict] = []
    bank_rows: list[dict] = []

    for _label, fn, count in SCENARIO_PLAN:
        for _ in range(count):
            result = fn(rng, ids, base_date)
            ledger_rows.extend(result["internal_ledger"])
            gateway_rows.extend(result["gateway_report"])
            bank_rows.extend(result["bank_settlement"])

    # Shuffle so file order doesn't trivially leak trap category via position.
    rng.shuffle(ledger_rows)
    rng.shuffle(gateway_rows)
    rng.shuffle(bank_rows)
    return ledger_rows, gateway_rows, bank_rows


def describe_mix(ledger_rows: list[dict], gateway_rows: list[dict], bank_rows: list[dict]) -> str:
    all_rows = ledger_rows + gateway_rows + bank_rows
    total = len(all_rows)
    counts = Counter(row["trap_category"] for row in all_rows)
    lines = [
        f"Total records: {total} "
        f"(internal_ledger={len(ledger_rows)}, gateway_report={len(gateway_rows)}, bank_settlement={len(bank_rows)})",
        "Trap category mix (% of total records, includes multi-record traps):",
    ]
    for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {category:<40} {count:>4}  ({count / total:5.1%})")
    return "\n".join(lines)


def write_dataset(output_dir: Path, seed: int = DEFAULT_SEED) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_rows, gateway_rows, bank_rows = generate_dataset(seed=seed)

    (output_dir / "internal_ledger.json").write_text(json.dumps(ledger_rows, indent=2))
    (output_dir / "bank_settlement.json").write_text(json.dumps(bank_rows, indent=2))
    (output_dir / "gateway_report.json").write_text(json.dumps(gateway_rows, indent=2))

    print(describe_mix(ledger_rows, gateway_rows, bank_rows))


if __name__ == "__main__":
    write_dataset(Path(__file__).resolve().parents[2] / "data" / "raw")
