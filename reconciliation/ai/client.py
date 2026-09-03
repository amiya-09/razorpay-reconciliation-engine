"""Thin wrapper around the Groq API's structured-output path (`groq` SDK,
verified against the installed version — see docs/decision_log.md D20).

Two responsibilities beyond a bare SDK call, both required by BUILD_BRIEF
Section 4's "testing approach for the AI layer":
  - every call's token usage and latency is logged (`call_log`), so the
    results report can state exactly how many of N records needed the AI
    layer and what it cost — not a hand-waved estimate.
  - a missing/malformed structured response is a hard failure (raises),
    never a silent pass-through into downstream logic.

`StructuredReasoningClient` is a Protocol so callers (ambiguous_match.py,
exception_explain.py, ...) can be tested against a fake client with no
network access or API key — only the one GroqReasoningClient
implementation actually talks to the API.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "openai/gpt-oss-20b"  # supports strict JSON-schema structured outputs
MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 15.0
# A single suggested retry-after above this is treated as a longer-duration
# cap (e.g. a daily quota) rather than routine per-minute throttling — not
# worth retrying inside one process run. Lesson from the Gemini integration
# (D19): blindly retrying every 429 the same way burns wall-clock time on a
# wait that can't actually resolve a daily cap. See D20.
NON_RETRYABLE_DELAY_THRESHOLD_SECONDS = 120.0

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class AICallLog:
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class StructuredReasoningClient(Protocol):
    call_log: list[AICallLog]

    def call_structured(self, system: str, user: str, output_format: type[T], max_tokens: int = 2048) -> T: ...


class GroqReasoningClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None):
        from groq import Groq  # deferred: keeps the dependency out of code paths that never call the AI layer

        self.model = model
        # Groq() with no api_key picks up GROQ_API_KEY from the environment automatically.
        self._client = Groq(api_key=api_key) if api_key else Groq()
        self.call_log: list[AICallLog] = []

    def call_structured(self, system: str, user: str, output_format: type[T], max_tokens: int = 2048) -> T:
        from groq import RateLimitError

        attempt = 0
        while True:
            try:
                start = time.monotonic()
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": output_format.__name__,
                            "strict": True,
                            "schema": output_format.model_json_schema(),
                        },
                    },
                    max_completion_tokens=max_tokens,
                    # Keep reasoning-token spend bounded and predictable —
                    # this task is a lightweight classification/judgment
                    # call, not one that needs deep step-by-step reasoning.
                    reasoning_effort="low",
                )
                break
            except RateLimitError as e:
                retry_after_header = e.response.headers.get("retry-after") if e.response is not None else None
                delay = float(retry_after_header) + 1.0 if retry_after_header else DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
                if delay > NON_RETRYABLE_DELAY_THRESHOLD_SECONDS or attempt >= MAX_RATE_LIMIT_RETRIES:
                    raise RuntimeError(
                        f"Groq rate limit not recoverable within this run (suggested wait {delay:.0f}s, "
                        f"{attempt} retries already attempted). Original error: {e}"
                    ) from e
                attempt += 1
                print(f"[GroqReasoningClient] rate limited, retry {attempt}/{MAX_RATE_LIMIT_RETRIES} in {delay:.1f}s")
                time.sleep(delay)

        latency_ms = (time.monotonic() - start) * 1000
        usage = response.usage
        self.call_log.append(AICallLog(
            model=self.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        ))

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise ValueError(
                f"LLM call returned no content conforming to {output_format.__name__} "
                "— treated as a hard failure, not a silent pass-through (BUILD_BRIEF Section 4)."
            )
        try:
            return output_format.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as e:
            raise ValueError(
                f"LLM call returned output that failed to parse/validate against {output_format.__name__}: {e}"
            ) from e
