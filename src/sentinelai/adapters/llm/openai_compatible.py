"""Adapter for any OpenAI-compatible chat-completions endpoint (ADR-017).

One adapter covers Groq, Gemini (OpenAI-compatible endpoint), OpenRouter, Cerebras, Mistral,
Ollama, LM Studio and vLLM. It talks HTTP directly with httpx so that rate-limit headers,
errors and retries stay under our control. It never logs request bodies, and it never puts
the API key into an exception message.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import SecretStr

from sentinelai import __version__
from sentinelai.adapters.redaction import register_secret
from sentinelai.domain.llm import (
    AuthenticationError,
    CompletionRequest,
    CompletionResult,
    GenerationRejectedError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
    RateLimitSnapshot,
    RequestTooLargeError,
    StructuredMode,
    UnsupportedFeatureError,
    Usage,
    parse_rate_limit_headers,
)
from sentinelai.domain.providers import ProviderSpec

MAX_ERROR_DETAIL_CHARS = 300
_GENERATION_REJECTED_CODES = frozenset({"json_validate_failed", "output_parse_failed"})
_FORMAT_HINTS = ("response_format", "json_schema", "json_object", "json mode", "structured output")


class OpenAICompatibleProvider:
    def __init__(
        self,
        spec: ProviderSpec,
        api_key: SecretStr | None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        if spec.requires_key and (api_key is None or not api_key.get_secret_value()):
            raise AuthenticationError(
                f"{spec.name}: no API key configured (set {spec.api_key_env} in the environment "
                "or in the secrets file shown by `sentinelai config path`)"
            )
        self._spec = spec
        self._api_key = api_key
        if api_key is not None:
            register_secret(api_key.get_secret_value())
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return self._spec.name

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ public API

    async def list_models(self) -> list[str]:
        response = await self._send("GET", "models", body=None)
        data = self._json(response)
        items = data.get("data")
        if not isinstance(items, list):
            raise ProviderUnavailableError(f"{self.name}: malformed model list")
        prefix = self._spec.model_id_prefix
        names = [str(item["id"]) for item in items if isinstance(item, dict) and "id" in item]
        if prefix:
            names = [name.removeprefix(prefix) for name in names]
        return [self._redact(name) for name in names]

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        started = time.monotonic()
        response = await self._send(
            "POST", "chat/completions", body=self._body(request), structured=request.mode
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        data = self._json(response)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderUnavailableError(f"{self.name}: response contained no choices")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        reported_model = data.get("model")
        request_id = response.headers.get("x-request-id") or data.get("id")
        # Provider-returned text is untrusted: never let it carry our credential onward.
        return CompletionResult(
            model=request.model,
            reported_model=self._redact(str(reported_model)) if reported_model else None,
            content=self._redact(str(message.get("content") or "")),
            finish_reason=choices[0].get("finish_reason"),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            rate_limit=parse_rate_limit_headers(response.headers),
            latency_ms=latency_ms,
            request_id=self._redact(str(request_id)) if request_id else None,
        )

    # ------------------------------------------------------------------ internals

    def _body(self, request: CompletionRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            self._spec.max_tokens_param: request.max_output_tokens,
        }
        if request.mode is StructuredMode.JSON_SCHEMA:
            if request.json_schema is None:
                raise InvalidRequestError("json_schema mode requires a schema")
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": False,
                },
            }
        elif request.mode is StructuredMode.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"sentinelai/{__version__}",
        }
        if self._api_key is not None and self._api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self._spec.base_url.rstrip('/')}/{path}"

    async def _send(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None,
        structured: StructuredMode | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method, self._url(path), json=body, headers=self._headers(), timeout=self._timeout_s
            )
        except httpx.TimeoutException:
            raise ProviderUnavailableError(f"{self.name}: request timed out") from None
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                f"{self.name}: connection failed ({type(exc).__name__})"
            ) from None
        if response.status_code >= 400:
            raise self._error(response, structured)
        return response

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise ProviderUnavailableError(f"{self.name}: response was not JSON") from None
        if not isinstance(data, dict):
            raise ProviderUnavailableError(f"{self.name}: unexpected response shape")
        return data

    def _redact(self, text: str) -> str:
        if self._api_key is not None:
            secret = self._api_key.get_secret_value()
            if secret:
                text = text.replace(secret, "***")
        return text

    def _error(  # noqa: PLR0911 - one return per error class reads clearest
        self, response: httpx.Response, structured: StructuredMode | None
    ) -> ProviderError:
        code, detail, failed_generation = _error_fields(response)
        detail = self._redact(detail)[:MAX_ERROR_DETAIL_CHARS]
        status = response.status_code
        message = f"{self.name}: HTTP {status}" + (f" ({detail})" if detail else "")
        snapshot: RateLimitSnapshot = parse_rate_limit_headers(response.headers)

        if status in (401, 403):
            return AuthenticationError(message, status_code=status)
        if status == 404:
            return ModelNotFoundError(message, status_code=status)
        if status == 413:
            return RequestTooLargeError(message, status_code=status)
        if status == 429:
            return RateLimitedError(
                message,
                status_code=status,
                retry_after_s=snapshot.retry_after_s,
                snapshot=snapshot,
            )
        if status >= 500:
            return ProviderUnavailableError(message, status_code=status)
        if code in _GENERATION_REJECTED_CODES:
            generation = self._redact(failed_generation) if failed_generation else None
            return GenerationRejectedError(
                message, status_code=status, failed_generation=generation
            )
        if structured is not None and any(hint in detail.lower() for hint in _FORMAT_HINTS):
            return UnsupportedFeatureError(message, status_code=status)
        return InvalidRequestError(message, status_code=status)


def _error_fields(response: httpx.Response) -> tuple[str | None, str, str | None]:
    """Extract (code, message, failed_generation) from common error body shapes."""
    try:
        payload = response.json()
    except ValueError:
        return None, response.text.strip(), None
    error = payload[0] if isinstance(payload, list) and payload else payload
    if isinstance(error, dict) and isinstance(error.get("error"), dict):
        error = error["error"]
    if not isinstance(error, dict):
        return None, str(error), None
    code = error.get("code")
    message = error.get("message") or error.get("detail") or ""
    failed = error.get("failed_generation")
    return (
        str(code) if code is not None else None,
        str(message),
        str(failed) if failed is not None else None,
    )
