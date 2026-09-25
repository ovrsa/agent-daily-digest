"""`agent_daily_digest.llm`: the Agent SDK adapter, driven by a stand-in `query` so no model is called."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from claude_agent_sdk import CLIConnectionError, CLIJSONDecodeError, CLINotFoundError, ResultError, ResultMessage

from agent_daily_digest.contracts import ErrorKind
from agent_daily_digest.llm import MIN_MAX_TURNS, StructuredRequest, build_options, invoke_structured
from agent_daily_digest.observe import LLMInvocationError

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
REQUEST = StructuredRequest(model="claude-sonnet-5", system_prompt="system", prompt="prompt", schema=SCHEMA)


def result(**overrides: Any) -> ResultMessage:
    data: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1500,
        "duration_api_ms": 1400,
        "is_error": False,
        "num_turns": 3,
        "session_id": "s",
        "structured_output": {"ok": True},
        "usage": {"input_tokens": 4, "output_tokens": 20, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50},
        "model_usage": {
            "claude-sonnet-5": {"inputTokens": 4, "outputTokens": 20, "cacheReadInputTokens": 50, "cacheCreationInputTokens": 100, "webSearchRequests": 0, "costUSD": 0.01, "contextWindow": 1, "maxOutputTokens": 1},
            "claude-haiku-4-5-20251001": {"inputTokens": 900, "outputTokens": 11, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.001, "contextWindow": 1, "maxOutputTokens": 1, "canonicalModel": "claude-haiku-4-5"},
        },
    }
    data.update(overrides)
    return ResultMessage(**data)


def fake_query(*messages: Any, raise_after: BaseException | None = None, seen: list | None = None):
    async def query(*, prompt: str, options: Any):
        if seen is not None:
            seen.append((prompt, options))
        for message in messages:
            yield message
        if raise_after is not None:
            raise raise_after

    return query


def test_options_give_the_model_no_tools_no_settings_and_a_schema() -> None:
    options = build_options(REQUEST)
    assert options.tools == [] and options.allowed_tools == [] and options.setting_sources == []
    assert options.max_turns >= MIN_MAX_TURNS
    assert options.output_format == {"type": "json_schema", "schema": SCHEMA}
    assert options.model == "claude-sonnet-5" and options.system_prompt == "system"


def test_max_turns_below_the_floor_is_refused() -> None:
    with pytest.raises(ValueError):
        StructuredRequest(model="m", system_prompt="s", prompt="p", schema=SCHEMA, max_turns=1)


def test_a_result_becomes_a_response_with_per_model_usage() -> None:
    seen: list = []
    response = invoke_structured(REQUEST, query=fake_query(result(), seen=seen))
    assert response.structured_output == {"ok": True} and not response.is_error
    assert [u.model for u in response.usage] == ["claude-haiku-4-5-20251001", "claude-sonnet-5"]
    sonnet = response.usage[1]
    assert (sonnet.input_tokens, sonnet.cache_creation_input_tokens, sonnet.cache_read_input_tokens) == (4, 100, 50)
    assert response.usage[0].canonical_model == "claude-haiku-4-5"
    assert seen[0][0] == "prompt"


def test_an_error_result_is_returned_for_the_boundary_to_classify() -> None:
    failed = result(is_error=True, api_error_status=404, structured_output=None)
    response = invoke_structured(REQUEST, query=fake_query(failed, raise_after=ResultError("boom", {"subtype": "success", "api_error_status": 404})))
    assert response.is_error and response.api_error_status == 404
    assert response.usage  # the failed attempt's usage is kept


def test_a_result_error_without_a_result_message_keeps_its_status() -> None:
    response = invoke_structured(REQUEST, query=fake_query(raise_after=ResultError("x", {"subtype": "error_max_turns"})))
    assert response.is_error and response.subtype == "error_max_turns"


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (CLINotFoundError(), ErrorKind.UNEXPECTED, False),
        (CLIConnectionError("refused"), ErrorKind.NETWORK, True),
        (CLIJSONDecodeError('{"model output": "ignore previous', ValueError("x")), ErrorKind.PARSE, True),
    ],
)
def test_sdk_exceptions_are_classified_without_their_message(exc, kind, retryable) -> None:
    with pytest.raises(LLMInvocationError) as caught:
        invoke_structured(REQUEST, query=fake_query(raise_after=exc))
    assert caught.value.kind is kind and caught.value.retryable is retryable
    assert "model output" not in (caught.value.detail or "")


def test_a_call_that_ends_without_a_result_is_a_parse_failure() -> None:
    with pytest.raises(LLMInvocationError) as caught:
        invoke_structured(REQUEST, query=fake_query())
    assert caught.value.kind is ErrorKind.PARSE


def test_a_call_that_outlives_its_timeout_is_a_timeout() -> None:
    async def slow(*, prompt: str, options: Any):
        await asyncio.sleep(5)
        yield result()

    request = StructuredRequest(model="m", system_prompt="s", prompt="p", schema=SCHEMA, timeout_seconds=0.05)
    with pytest.raises(LLMInvocationError) as caught:
        invoke_structured(request, query=slow)
    assert caught.value.kind is ErrorKind.TIMEOUT and caught.value.retryable
