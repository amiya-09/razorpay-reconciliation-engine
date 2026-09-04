"""Unit tests for GroqReasoningClient's own retry logic, mocking the
underlying groq SDK client directly (no network, no API key needed) —
distinct from test_client_live.py, which makes one real call.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from groq import BadRequestError

from reconciliation.ai.client import MAX_SCHEMA_REPAIR_RETRIES, GroqReasoningClient
from reconciliation.ai.schemas import AmbiguousMatchDecision


def _bad_request_error(message: str = "schema violation") -> BadRequestError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return BadRequestError(message, response=response, body=None)


def _fake_completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
    )


VALID_DECISION_JSON = (
    '{"match": true, "confidence": 0.8, "reasoning": "same transaction", '
    '"suspected_trap_category": "reference_id_typo"}'
)


def test_call_structured_retries_once_after_schema_invalid_response_then_succeeds():
    client = GroqReasoningClient(api_key="test-key")
    client._client.chat.completions.create = MagicMock(
        side_effect=[_bad_request_error(), _fake_completion(VALID_DECISION_JSON)]
    )

    result = client.call_structured(system="sys", user="user prompt", output_format=AmbiguousMatchDecision)

    assert result.match is True
    assert result.suspected_trap_category == "reference_id_typo"
    assert client._client.chat.completions.create.call_count == 2


def test_repair_retry_prompt_includes_a_reminder_to_fill_every_field():
    client = GroqReasoningClient(api_key="test-key")
    client._client.chat.completions.create = MagicMock(
        side_effect=[_bad_request_error(), _fake_completion(VALID_DECISION_JSON)]
    )

    client.call_structured(system="sys", user="original prompt", output_format=AmbiguousMatchDecision)

    first_call, second_call = client._client.chat.completions.create.call_args_list
    assert first_call.kwargs["messages"][1]["content"] == "original prompt"
    repaired_prompt = second_call.kwargs["messages"][1]["content"]
    assert repaired_prompt.startswith("original prompt")
    assert "did not conform" in repaired_prompt


def test_call_structured_only_logs_usage_for_the_successful_attempt():
    client = GroqReasoningClient(api_key="test-key")
    client._client.chat.completions.create = MagicMock(
        side_effect=[_bad_request_error(), _fake_completion(VALID_DECISION_JSON)]
    )

    client.call_structured(system="sys", user="user prompt", output_format=AmbiguousMatchDecision)

    assert len(client.call_log) == 1
    assert client.call_log[0].input_tokens == 50
    assert client.call_log[0].output_tokens == 20


def test_call_structured_raises_after_the_repair_retry_also_fails():
    client = GroqReasoningClient(api_key="test-key")
    client._client.chat.completions.create = MagicMock(
        side_effect=[_bad_request_error("first failure"), _bad_request_error("second failure")]
    )

    with pytest.raises(ValueError, match="after 1 repair retry"):
        client.call_structured(system="sys", user="user prompt", output_format=AmbiguousMatchDecision)

    assert client._client.chat.completions.create.call_count == MAX_SCHEMA_REPAIR_RETRIES + 1
    assert client.call_log == []  # neither attempt produced a usable response


def test_call_structured_retries_on_empty_content_not_just_bad_request_error():
    client = GroqReasoningClient(api_key="test-key")
    client._client.chat.completions.create = MagicMock(
        side_effect=[_fake_completion(""), _fake_completion(VALID_DECISION_JSON)]
    )

    result = client.call_structured(system="sys", user="user prompt", output_format=AmbiguousMatchDecision)

    assert result.match is True
    assert client._client.chat.completions.create.call_count == 2
