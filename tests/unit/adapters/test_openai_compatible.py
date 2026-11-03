from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from sentinelai.adapters.llm.openai_compatible import OpenAICompatibleProvider
from sentinelai.domain.llm import (
    AuthenticationError,
    ChatMessage,
    CompletionRequest,
    GenerationRejectedError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
    RequestTooLargeError,
    Role,
    StructuredMode,
    UnsupportedFeatureError,
)
from sentinelai.domain.providers import ProviderSpec

REMOTE = ProviderSpec(name="remote", base_url="https://llm.example/v1", api_key_env="REMOTE_KEY")
LOCAL = ProviderSpec(name="local", base_url="http://localhost:11434/v1/")
KEY = SecretStr("sk-test-secret-123")

Handler = Callable[[httpx.Request], httpx.Response]


def make(handler: Handler, spec: ProviderSpec = REMOTE, key: SecretStr | None = KEY) -> Any:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(spec, key, client=client)


def request(mode: StructuredMode | None = None) -> CompletionRequest:
    return CompletionRequest(
        model="m1",
        messages=(ChatMessage(role=Role.USER, content="hi"),),
        mode=mode,
        json_schema={"type": "object"} if mode is StructuredMode.JSON_SCHEMA else None,
        schema_name="Answer",
        max_output_tokens=64,
    )


def ok_completion(content: str = '{"answer": 5}') -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "model": "m1-2026-01",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


async def test_completion_parses_content_usage_and_rate_limits() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json=ok_completion(),
            headers={"x-ratelimit-remaining-tokens": "5900", "x-request-id": "req_9"},
        )

    async with make(handler) as provider:
        result = await provider.complete(request(StructuredMode.JSON_SCHEMA))

    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test-secret-123"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "Answer"
    assert seen["body"]["max_tokens"] == 64
    assert result.content == '{"answer": 5}'
    assert result.reported_model == "m1-2026-01"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (11, 7)
    assert result.rate_limit.remaining_tokens == 5900
    assert result.request_id == "req_9"


async def test_json_object_mode_and_free_text_bodies() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=ok_completion())

    async with make(handler) as provider:
        await provider.complete(request(StructuredMode.JSON_OBJECT))
        await provider.complete(request(None))
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in bodies[1]


async def test_local_provider_sends_no_authorization_and_joins_trailing_slash() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": [{"id": "qwen2.5:3b"}, {"bad": 1}]})

    async with make(handler, spec=LOCAL, key=None) as provider:
        models = await provider.list_models()
    assert models == ["qwen2.5:3b"]
    assert seen["auth"] is None
    assert seen["url"] == "http://localhost:11434/v1/models"


async def test_model_id_prefix_is_stripped_from_listing() -> None:
    """Gemini lists "models/<name>" but its chat endpoint takes "<name>" (found by live probe)."""
    spec = REMOTE.model_copy(update={"model_id_prefix": "models/"})
    listing = {"data": [{"id": "models/gemini-2.5-flash"}, {"id": "other"}]}
    async with make(lambda _req: httpx.Response(200, json=listing), spec=spec) as provider:
        assert await provider.list_models() == ["gemini-2.5-flash", "other"]


def test_remote_provider_without_key_fails_fast() -> None:
    with pytest.raises(AuthenticationError, match="REMOTE_KEY"):
        OpenAICompatibleProvider(REMOTE, None)
    with pytest.raises(AuthenticationError):
        OpenAICompatibleProvider(REMOTE, SecretStr(""))


@pytest.mark.parametrize(
    ("status", "body", "headers", "mode", "expected"),
    [
        (401, {"error": {"message": "Invalid API Key"}}, {}, None, AuthenticationError),
        (403, {"error": {"message": "forbidden"}}, {}, None, AuthenticationError),
        (404, {"error": {"message": "model not found"}}, {}, None, ModelNotFoundError),
        (413, {"error": {"message": "Request too large (TPM)"}}, {}, None, RequestTooLargeError),
        (503, {"error": {"message": "over capacity"}}, {}, None, ProviderUnavailableError),
        (
            400,
            {"error": {"message": "response_format json_schema is not supported by this model"}},
            {},
            StructuredMode.JSON_SCHEMA,
            UnsupportedFeatureError,
        ),
        (
            400,
            {"error": {"message": "response_format is invalid"}},
            {},
            None,
            InvalidRequestError,
        ),
        (400, {"error": {"message": "temperature out of range"}}, {}, None, InvalidRequestError),
    ],
)
async def test_error_mapping(
    status: int,
    body: dict[str, Any],
    headers: dict[str, str],
    mode: StructuredMode | None,
    expected: type[ProviderError],
) -> None:
    async with make(lambda _req: httpx.Response(status, json=body, headers=headers)) as provider:
        with pytest.raises(expected) as caught:
            await provider.complete(request(mode))
    assert caught.type is expected
    assert caught.value.status_code == status


async def test_rate_limit_carries_retry_after_and_snapshot() -> None:
    headers = {"retry-after": "7", "x-ratelimit-remaining-tokens": "0"}
    response = httpx.Response(429, json={"error": {"message": "slow down"}}, headers=headers)
    async with make(lambda _req: response) as provider:
        with pytest.raises(RateLimitedError) as caught:
            await provider.complete(request())
    assert caught.value.retryable
    assert caught.value.retry_after_s == 7.0
    assert caught.value.snapshot.remaining_tokens == 0


async def test_generation_rejected_keeps_failed_generation() -> None:
    body = {
        "error": {
            "message": "Failed to generate JSON",
            "code": "json_validate_failed",
            "failed_generation": '{"answer": "five"}',
        }
    }
    async with make(lambda _req: httpx.Response(400, json=body)) as provider:
        with pytest.raises(GenerationRejectedError) as caught:
            await provider.complete(request(StructuredMode.JSON_OBJECT))
    assert caught.value.failed_generation == '{"answer": "five"}'


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("x")],
)
async def test_transport_failures_are_unavailable(exc: Exception) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise exc

    async with make(handler) as provider:
        with pytest.raises(ProviderUnavailableError) as caught:
            await provider.complete(request())
    assert caught.value.retryable


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json=[1, 2]),
    ],
)
async def test_malformed_success_responses_are_unavailable(response: httpx.Response) -> None:
    async with make(lambda _req: response) as provider:
        with pytest.raises(ProviderUnavailableError):
            await provider.complete(request())


@settings(max_examples=50, deadline=None)
@given(
    secret=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=12, max_size=40),
    status=st.sampled_from([400, 401, 404, 413, 429, 500]),
)
async def test_api_key_never_appears_in_errors(secret: str, status: int) -> None:
    """Even when a provider echoes the key back, it is redacted from our exceptions."""
    body = {
        "error": {
            "message": f"bad key {secret}",
            "failed_generation": secret,
            "code": "json_validate_failed",
        }
    }
    provider = make(lambda _req: httpx.Response(status, json=body), key=SecretStr(secret))
    try:
        with pytest.raises(ProviderError) as caught:
            await provider.complete(request(StructuredMode.JSON_OBJECT))
    finally:
        await provider.aclose()
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.args)
    assert secret not in (getattr(caught.value, "failed_generation", None) or "")
