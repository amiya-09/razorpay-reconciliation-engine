"""AI Reasoning Layer, item 3: Settlement Q&A agent (BUILD_BRIEF Section 4,
item 3) — natural-language question in, answer grounded in the actual
settlement data out. Per BUILD_BRIEF's own framing, this is "the most clearly
agentic, judge-recognizable component."

Design: deterministic retrieval, then one grounded LLM call — not a
multi-step tool-use loop. BUILD_BRIEF Section 3 explicitly scopes the AI
layer to "single-shot structured reasoning calls, not multi-step autonomous
workflows," and at ~163 records a regex/lookup-based retrieval step is both
sufficient and exactly-reproducible (no risk of the model mis-searching).
The retrieval step:
  1. Look for exact identifiers (order_id/ledger_id/entity_id/UTR) mentioned
     in the question.
  2. If none found, look for amounts mentioned (handles "why did I get
     ₹9,800 instead of ₹10,000" — the brief's own example question) and match
     against records within a generous tolerance (the question is a human's
     approximation, not an exact query).
  3. Whatever records are found, expand to their related records across the
     other 2 sources via shared order_id/reference_id — so "why is this
     short" can be answered by seeing the ledger amount, the gateway
     fee/tax/credit breakdown, and the bank settlement together.
If nothing is found, the LLM is explicitly told there is no grounding data
and must say so rather than speculate — a hallucinated answer to a finance
question is worse than "I don't have a record matching that."
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from reconciliation.ai.client import StructuredReasoningClient
from reconciliation.ai.prompt_helpers import describe_side
from reconciliation.ai.schemas import QAAnswer
from reconciliation.models import CanonicalTransaction

SYSTEM_PROMPT = (
    "You are a settlement support agent answering a merchant's question about "
    "their payment reconciliation data. Answer ONLY using the record data "
    "provided in the prompt — never invent amounts, dates, or IDs. If no "
    "record data is provided, say plainly that you don't have a matching "
    "record rather than guessing. Cite the specific record IDs you used in "
    "cited_record_ids. When explaining a shortfall between what a merchant "
    "expected and what they received, be specific about the fee/tax/rounding "
    "cause if the data shows one. Use the exact `currency` field shown in "
    "the record data (e.g. write 'INR 500.00', not '$500.00' or any other "
    "symbol not present in the data)."
)

_ID_PATTERNS = [
    re.compile(r"\border_\d+\b"),
    re.compile(r"\bled_\d+\b"),
    re.compile(r"\bpay_\d+\b"),
    re.compile(r"\bsetl_\d+\b"),
    re.compile(r"\b\d{12}\b"),  # UTR
]

_AMOUNT_PATTERN = re.compile(
    r"₹\s?([\d,]+(?:\.\d{1,2})?)"          # ₹9,800 or ₹9800.00
    r"|\b(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)\b"  # 9,800 or 9,800.50 (comma-grouped)
    r"|\b(\d+\.\d{2})\b"                    # 9800.00 (explicit 2dp)
)


def extract_identifiers(text: str) -> list[str]:
    found = []
    for pattern in _ID_PATTERNS:
        found.extend(pattern.findall(text))
    return found


def extract_amounts(text: str) -> list[Decimal]:
    amounts = []
    for match in _AMOUNT_PATTERN.finditer(text):
        raw = next(g for g in match.groups() if g)
        try:
            amounts.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    return amounts


class SettlementIndex:
    """In-memory lookup over all 3 sources' canonical records, for grounding
    the Q&A agent. Not a matching engine — just retrieval."""

    def __init__(
        self,
        ledger: list[CanonicalTransaction],
        gateway: list[CanonicalTransaction],
        bank: list[CanonicalTransaction],
    ):
        self.all_records = [*ledger, *gateway, *bank]
        self._by_source_record_id = {t.source_record_id: t for t in self.all_records}
        self._by_order_id: dict[str, list[CanonicalTransaction]] = {}
        self._by_reference_id: dict[str, list[CanonicalTransaction]] = {}
        for t in self.all_records:
            if t.order_id:
                self._by_order_id.setdefault(t.order_id, []).append(t)
            if t.reference_id:
                self._by_reference_id.setdefault(t.reference_id, []).append(t)

    def find_by_identifier(self, identifier: str) -> list[CanonicalTransaction]:
        matches = []
        if identifier in self._by_source_record_id:
            matches.append(self._by_source_record_id[identifier])
        matches.extend(self._by_order_id.get(identifier, []))
        matches.extend(self._by_reference_id.get(identifier, []))
        return matches

    def find_by_amount(self, amount: Decimal, tolerance: Decimal = Decimal("50.00")) -> list[CanonicalTransaction]:
        return [
            t for t in self.all_records
            if abs(t.amount - amount) <= tolerance
            or (t.net_amount is not None and abs(t.net_amount - amount) <= tolerance)
        ]

    def expand_related(self, records: list[CanonicalTransaction]) -> list[CanonicalTransaction]:
        order_ids = {t.order_id for t in records if t.order_id}
        reference_ids = {t.reference_id for t in records if t.reference_id}
        expanded = {t.source_record_id: t for t in records}
        for t in self.all_records:
            if (t.order_id and t.order_id in order_ids) or (t.reference_id and t.reference_id in reference_ids):
                expanded[t.source_record_id] = t
        return list(expanded.values())


def find_relevant_records(question: str, index: SettlementIndex) -> list[CanonicalTransaction]:
    direct: list[CanonicalTransaction] = []
    for identifier in extract_identifiers(question):
        direct.extend(index.find_by_identifier(identifier))

    if not direct:
        for amount in extract_amounts(question):
            direct.extend(index.find_by_amount(amount))

    if not direct:
        return []

    return index.expand_related(direct)


def build_prompt(question: str, records: list[CanonicalTransaction]) -> str:
    if not records:
        return (
            f"Question: {question}\n\n"
            "No matching records were found in the settlement data for this "
            "question. Say so plainly rather than guessing an answer."
        )
    return (
        f"Question: {question}\n\n"
        f"Relevant settlement records found ({len(records)}):\n"
        f"{describe_side(tuple(records))}\n\n"
        "Answer the question using only this data, and list the source_record_id "
        "values you relied on in cited_record_ids."
    )


def answer_question(question: str, index: SettlementIndex, client: StructuredReasoningClient) -> QAAnswer:
    records = find_relevant_records(question, index)
    return client.call_structured(
        system=SYSTEM_PROMPT,
        user=build_prompt(question, records),
        output_format=QAAnswer,
    )
