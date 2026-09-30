"""One structured-output call through the Claude Agent SDK.

Every setting here comes from the #2 spike:

- `tools=[]` and `setting_sources=[]`: the model gets no tools and the CLI
  loads no user settings, hooks or CLAUDE.md, so a run is the same on any Mac
- `max_turns` is also the model's retry budget for the schema: a successful
  call took 3 turns and a struggling one 5, so the floor is `MIN_MAX_TURNS`
- the answer is `ResultMessage.structured_output`; `is_error` and
  `api_error_status` decide failure, and `subtype` alone never does
- authentication is the local claude.ai login; no key is read or passed

The SDK is imported lazily so importing this package does not start anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from agent_daily_digest.contracts import ErrorKind
from agent_daily_digest.llm.call import LLMInvocationError, LLMResponse, StructuredRequest, invocation_error
from agent_daily_digest.llm.pricing import ModelUsage


Query = Callable[..., AsyncIterator[Any]]
"""`claude_agent_sdk.query`, or a stand-in with the same keyword arguments."""




def build_options(request: StructuredRequest) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        model=request.model,
        system_prompt=request.system_prompt,
        tools=[],
        allowed_tools=[],
        setting_sources=[],
        max_turns=request.max_turns,
        max_budget_usd=request.max_budget_usd,
        output_format={"type": "json_schema", "schema": dict(request.schema)},
    )


def invoke_structured(request: StructuredRequest, *, query: Query | None = None) -> LLMResponse:
    """Run one request to completion and return its response.

    Raises `LLMInvocationError` with a classified kind when no response came
    back. A response that reports an error is returned, not raised, so the
    measurement boundary classifies it from its status.
    """
    return asyncio.run(run_structured(request, query=query))


async def run_structured(request: StructuredRequest, *, query: Query | None = None) -> LLMResponse:
    from claude_agent_sdk import (
        CLIConnectionError,
        CLIJSONDecodeError,
        CLINotFoundError,
        ClaudeSDKError,
        ProcessError,
        ResultError,
        ResultMessage,
    )

    if query is None:
        from claude_agent_sdk import query as sdk_query

        query = sdk_query

    result: Any = None

    async def consume() -> None:
        nonlocal result
        async for message in query(prompt=request.prompt, options=build_options(request)):
            if isinstance(message, ResultMessage):
                result = message

    try:
        await asyncio.wait_for(consume(), timeout=request.timeout_seconds)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise LLMInvocationError(
            ErrorKind.TIMEOUT, f"no result within {request.timeout_seconds:g}s", retryable=True
        ) from exc
    except ResultError as exc:
        # The CLI reported a terminal error result and exited. The result message,
        # when it arrived first, carries the usage the failed attempt still spent.
        if result is not None:
            return to_response(result)
        return LLMResponse(
            structured_output=None,
            is_error=True,
            api_error_status=exc.api_error_status,
            subtype=exc.subtype,
        )
    except CLINotFoundError as exc:
        raise invocation_error(exc, ErrorKind.UNEXPECTED, retryable=False) from exc
    except CLIConnectionError as exc:
        raise invocation_error(exc, ErrorKind.NETWORK, retryable=True) from exc
    except CLIJSONDecodeError as exc:
        raise invocation_error(exc, ErrorKind.PARSE, retryable=True) from exc
    except ProcessError as exc:
        if result is not None:
            return to_response(result)
        raise invocation_error(exc, ErrorKind.UNEXPECTED, retryable=False) from exc
    except ClaudeSDKError as exc:
        raise invocation_error(exc, ErrorKind.UNEXPECTED, retryable=False) from exc

    if result is None:
        raise LLMInvocationError(ErrorKind.PARSE, "the CLI ended without a result message", retryable=True)
    return to_response(result)


def to_response(message: Any) -> LLMResponse:
    """Convert a `ResultMessage` into the boundary's response shape."""
    return LLMResponse(
        structured_output=message.structured_output,
        is_error=bool(message.is_error),
        api_error_status=message.api_error_status,
        subtype=message.subtype,
        usage=model_usages(message.model_usage, message.usage),
    )


def model_usages(model_usage: Mapping[str, Mapping[str, Any]] | None, usage: Mapping[str, Any] | None) -> tuple[ModelUsage, ...]:
    """Per-model usage. Falls back to the top-level `usage` when no breakdown came back."""
    if model_usage:
        return tuple(
            ModelUsage(
                model=model,
                input_tokens=_int(entry.get("inputTokens")),
                output_tokens=_int(entry.get("outputTokens")),
                cache_creation_input_tokens=_int(entry.get("cacheCreationInputTokens")),
                cache_read_input_tokens=_int(entry.get("cacheReadInputTokens")),
                canonical_model=entry.get("canonicalModel"),
            )
            for model, entry in sorted(model_usage.items())
        )
    if usage:
        return (
            ModelUsage(
                model="unknown",
                input_tokens=_int(usage.get("input_tokens")),
                output_tokens=_int(usage.get("output_tokens")),
                cache_creation_input_tokens=_int(usage.get("cache_creation_input_tokens")),
                cache_read_input_tokens=_int(usage.get("cache_read_input_tokens")),
            ),
        )
    return ()


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


__all__ = [
    "Query",
    "build_options",
    "invoke_structured",
    "model_usages",
    "run_structured",
    "to_response",
]
