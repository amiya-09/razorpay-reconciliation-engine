"""Fake StructuredReasoningClient for tests — no network, no API key.

Implements the same Protocol as GroqReasoningClient (call_structured +
call_log) so ambiguous_match.py/exception_explain.py/pipeline.py can be tested
against real wiring without ever hitting the Groq API.
"""

from __future__ import annotations

from typing import Callable, Optional, TypeVar

from pydantic import BaseModel

from reconciliation.ai.client import AICallLog

T = TypeVar("T", bound=BaseModel)


class FakeReasoningClient:
    def __init__(self, responder: Optional[Callable[[str, str, type], BaseModel]] = None):
        self.call_log: list[AICallLog] = []
        self.calls: list[tuple[str, str, type]] = []
        self._responder = responder

    def call_structured(self, system: str, user: str, output_format: type[T], max_tokens: int = 1024) -> T:
        self.calls.append((system, user, output_format))
        self.call_log.append(AICallLog(model="fake", input_tokens=len(user) // 4, output_tokens=20, latency_ms=1.0))
        if self._responder is None:
            raise AssertionError("FakeReasoningClient was called with no responder configured")
        return self._responder(system, user, output_format)
