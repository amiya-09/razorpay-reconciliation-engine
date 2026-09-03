"""Generic tiered matcher: exact key -> fuzzy key -> rule-based (no key at all).

Used for both joins in the pipeline (ledger<->gateway on order_id,
gateway<->bank on reference_id) by passing in different key/amount/date
accessor functions — the join logic itself doesn't know which source pair
it's matching.

Grouping resolution (what happens once a key, exact or fuzzy, ties a left
group to a right group):
  - 1:1            -> straightforward candidate; amount/date rules validate it.
  - 1:N or N:1     -> check whether the "many" side's amounts sum to the "one"
                      side's amount (within tolerance). If yes, this is a
                      split/netted group, resolved with high confidence. If no,
                      it's AMBIGUOUS — multiple candidates share a key without
                      a sound resolution, which is a distinct, honestly-named
                      failure mode (never conflated with plain "unmatched").
  - N:M            -> always AMBIGUOUS; too complex to resolve deterministically,
                      flagged for AI/manual review rather than guessed at.

Near-miss recording: any fuzzy candidate that clears NEAR_MISS_FLOOR but not
FUZZY_ACCEPT_THRESHOLD is still returned (status=NEAR_MISS), carrying its best
candidate and similarity score — never silently dropped.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from rapidfuzz import fuzz

from reconciliation.matching.rules import check_amount, check_date_lag
from reconciliation.matching.types import GroupMatchResult, MatchStatus, MatchTier
from reconciliation.models import CanonicalTransaction

KeyFn = Callable[[CanonicalTransaction], Optional[str]]
AmountFn = Callable[[CanonicalTransaction], Decimal]
DateFn = Callable[[CanonicalTransaction], datetime]

FUZZY_ACCEPT_THRESHOLD = 0.85
NEAR_MISS_FLOOR = 0.55
LOOSE_PREFILTER_AMOUNT_TOLERANCE = Decimal("50.00")  # wide net before spending fuzzy-string cost
LOOSE_PREFILTER_DATE_LAG_DAYS = 10


def _group_by_key(records: list[CanonicalTransaction], key_fn: KeyFn) -> tuple[dict[str, list], list]:
    keyed: dict[str, list[CanonicalTransaction]] = defaultdict(list)
    unkeyed: list[CanonicalTransaction] = []
    for record in records:
        key = key_fn(record)
        if key:
            keyed[key].append(record)
        else:
            unkeyed.append(record)
    return dict(keyed), unkeyed


def _within_loose_bounds(
    left: CanonicalTransaction, right: CanonicalTransaction,
    amount_fn_left: AmountFn, amount_fn_right: AmountFn,
    date_fn_left: DateFn, date_fn_right: DateFn,
) -> bool:
    amount_ok = check_amount("prefilter_amount", amount_fn_left(left), amount_fn_right(right), LOOSE_PREFILTER_AMOUNT_TOLERANCE).passed
    date_ok = check_date_lag("prefilter_date", date_fn_left(left), date_fn_right(right), LOOSE_PREFILTER_DATE_LAG_DAYS).passed
    return amount_ok and date_ok


def _resolve_group(
    left_group: list[CanonicalTransaction],
    right_group: list[CanonicalTransaction],
    tier: MatchTier,
    key_similarity: float,
    amount_fn_left: AmountFn,
    amount_fn_right: AmountFn,
    date_fn_left: DateFn,
    date_fn_right: DateFn,
    amount_tolerance: Decimal,
    max_date_lag_days: int,
) -> GroupMatchResult:
    if len(left_group) == 1 and len(right_group) == 1:
        left, right = left_group[0], right_group[0]
        amount_check = check_amount("amount_match", amount_fn_left(left), amount_fn_right(right), amount_tolerance)
        date_check = check_date_lag("settlement_lag", date_fn_left(left), date_fn_right(right), max_date_lag_days)
        rule_checks = (amount_check, date_check)

        key_ok = key_similarity >= (FUZZY_ACCEPT_THRESHOLD if tier == MatchTier.FUZZY else 0.0)
        status = MatchStatus.MATCHED if key_ok else MatchStatus.NEAR_MISS
        confidence = key_similarity * (1.0 if amount_check.passed else 0.5) * (1.0 if date_check.passed else 0.85)
        notes = () if amount_check.passed and date_check.passed else ("rule check failed on an otherwise key-matched pair — data disagreement, not a missing counterpart",)
        return GroupMatchResult(
            left=tuple(left_group), right=tuple(right_group), status=status, tier=tier,
            confidence=round(confidence, 4), key_similarity=key_similarity, rule_checks=rule_checks, notes=notes,
        )

    if len(left_group) == 1 and len(right_group) > 1:
        return _resolve_lopsided_group(
            one=left_group, many=right_group, one_is_left=True,
            tier=tier, key_similarity=key_similarity,
            amount_fn_one=amount_fn_left, amount_fn_many=amount_fn_right,
            amount_tolerance=amount_tolerance,
        )

    if len(left_group) > 1 and len(right_group) == 1:
        return _resolve_lopsided_group(
            one=right_group, many=left_group, one_is_left=False,
            tier=tier, key_similarity=key_similarity,
            amount_fn_one=amount_fn_right, amount_fn_many=amount_fn_left,
            amount_tolerance=amount_tolerance,
        )

    # N:M — too complex to resolve deterministically; escalate as-is.
    return GroupMatchResult(
        left=tuple(left_group), right=tuple(right_group), status=MatchStatus.AMBIGUOUS, tier=tier,
        confidence=0.1, key_similarity=key_similarity,
        notes=(f"{len(left_group)}:{len(right_group)} group — no deterministic resolution, needs review",),
    )


def _resolve_lopsided_group(
    one: list[CanonicalTransaction],
    many: list[CanonicalTransaction],
    one_is_left: bool,
    tier: MatchTier,
    key_similarity: float,
    amount_fn_one: AmountFn,
    amount_fn_many: AmountFn,
    amount_tolerance: Decimal,
) -> GroupMatchResult:
    one_amount = amount_fn_one(one[0])
    many_total = sum((amount_fn_many(r) for r in many), Decimal("0"))
    sum_check = check_amount("group_sum_match", one_amount, many_total, amount_tolerance)

    left, right = (one, many) if one_is_left else (many, one)

    if sum_check.passed:
        # A real split/netted group: the "many" side's amounts sum cleanly to
        # the "one" side, regardless of how similar those amounts are to each
        # other (a legitimate 50/50 split naturally has near-equal parts —
        # that alone must never veto an otherwise-passing sum check).
        status = MatchStatus.MATCHED
        confidence = round(key_similarity * 0.95, 4)
        notes = (f"resolved as a {len(many)}-way split/netted group via amount-sum rule",)
    else:
        # Sum doesn't reconcile. If the "many" side's amounts are also
        # near-identical to each other, that's the signature of a duplicate
        # record (e.g. 2x the same amount double-counted), not a real split.
        many_amounts = [amount_fn_many(r) for r in many]
        looks_like_duplicate = len(many) > 1 and (max(many_amounts) - min(many_amounts)) <= amount_tolerance
        status = MatchStatus.AMBIGUOUS
        confidence = round(key_similarity * 0.2, 4)
        notes = (
            "multiple candidates share this key but amounts don't sum to a clean match"
            + (" — candidates have near-identical amounts, possible duplicate record" if looks_like_duplicate else ""),
        )

    return GroupMatchResult(
        left=tuple(left), right=tuple(right), status=status, tier=tier,
        confidence=confidence, key_similarity=key_similarity, rule_checks=(sum_check,), notes=notes,
    )


def match_by_key(
    left: list[CanonicalTransaction],
    right: list[CanonicalTransaction],
    key_fn: KeyFn,
    amount_fn_left: AmountFn,
    amount_fn_right: AmountFn,
    date_fn_left: DateFn,
    date_fn_right: DateFn,
    amount_tolerance: Decimal = Decimal("0.05"),
    max_date_lag_days: int = 5,
    fuzzy_accept_threshold: float = FUZZY_ACCEPT_THRESHOLD,
    near_miss_floor: float = NEAR_MISS_FLOOR,
    enable_fuzzy_tier: bool = True,
) -> list[GroupMatchResult]:
    """
    enable_fuzzy_tier: set False for joins on strict structured identifiers
    (e.g. order_id) where a non-exact match should never be guessed at —
    format-similar-but-unrelated IDs (e.g. "order_00019" vs "order_00023")
    can score >0.8 on pure string similarity despite being two different
    transactions. Fuzzy matching belongs on genuinely free-text/typo-prone
    reference fields (e.g. bank UTR), not on strict IDs. See docs/decision_log.md D9.
    """
    left_groups, left_unkeyed = _group_by_key(left, key_fn)
    right_groups, right_unkeyed = _group_by_key(right, key_fn)

    results: list[GroupMatchResult] = []
    referenced_right_keys: set[str] = set()
    remaining_left_keys = set(left_groups)

    # Tier 1: exact key equality.
    for key in list(remaining_left_keys):
        if key in right_groups:
            results.append(_resolve_group(
                left_groups[key], right_groups[key], MatchTier.EXACT, 1.0,
                amount_fn_left, amount_fn_right, date_fn_left, date_fn_right,
                amount_tolerance, max_date_lag_days,
            ))
            referenced_right_keys.add(key)
            remaining_left_keys.discard(key)

    # Tier 2: fuzzy key similarity, restricted to candidates plausible on amount+date.
    available_right_keys = [k for k in right_groups if k not in referenced_right_keys]
    for key in list(remaining_left_keys) if enable_fuzzy_tier else []:
        left_group = left_groups[key]
        left_repr = left_group[0]
        best_key, best_score = None, 0.0
        for candidate_key in available_right_keys:
            right_repr = right_groups[candidate_key][0]
            if not _within_loose_bounds(left_repr, right_repr, amount_fn_left, amount_fn_right, date_fn_left, date_fn_right):
                continue
            score = fuzz.ratio(key, candidate_key) / 100.0
            if score > best_score:
                best_key, best_score = candidate_key, score

        if best_key is not None and best_score >= near_miss_floor:
            results.append(_resolve_group(
                left_group, right_groups[best_key], MatchTier.FUZZY, best_score,
                amount_fn_left, amount_fn_right, date_fn_left, date_fn_right,
                amount_tolerance, max_date_lag_days,
            ))
            referenced_right_keys.add(best_key)
            available_right_keys.remove(best_key)
            remaining_left_keys.discard(key)

    # Tier 3: rule-based matching for records with no key at all on either side.
    unclaimed_right_unkeyed = list(right_unkeyed)
    for left_record in left_unkeyed:
        best_idx, best_score = None, 0.0
        for idx, right_record in enumerate(unclaimed_right_unkeyed):
            if not _within_loose_bounds(left_record, right_record, amount_fn_left, amount_fn_right, date_fn_left, date_fn_right):
                continue
            relative_delta = abs(amount_fn_left(left_record) - amount_fn_right(right_record)) / max(
                amount_fn_left(left_record), Decimal("1")
            )
            score = 1.0 - min(float(relative_delta), 1.0)
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx is not None and best_score >= near_miss_floor:
            right_record = unclaimed_right_unkeyed.pop(best_idx)
            results.append(_resolve_group(
                [left_record], [right_record], MatchTier.RULE, best_score,
                amount_fn_left, amount_fn_right, date_fn_left, date_fn_right,
                amount_tolerance, max_date_lag_days,
            ))
        else:
            results.append(GroupMatchResult(
                left=(left_record,), right=(), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE,
                confidence=0.0, notes=("no key on either side and no amount/date candidate found",),
            ))

    # Anything left in remaining_left_keys never found even a near-miss candidate.
    for key in remaining_left_keys:
        results.append(GroupMatchResult(
            left=tuple(left_groups[key]), right=(), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE,
            confidence=0.0, notes=(f"no counterpart found for key {key!r}",),
        ))

    # Right-side keyed groups never referenced by any left group.
    for key, right_group in right_groups.items():
        if key not in referenced_right_keys:
            results.append(GroupMatchResult(
                left=(), right=tuple(right_group), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE,
                confidence=0.0, notes=(f"no counterpart found for key {key!r}",),
            ))

    # Right-side unkeyed records never claimed by the rule tier.
    for right_record in unclaimed_right_unkeyed:
        results.append(GroupMatchResult(
            left=(), right=(right_record,), status=MatchStatus.UNMATCHED, tier=MatchTier.NONE,
            confidence=0.0, notes=("no key on either side and no amount/date candidate found",),
        ))

    return results
