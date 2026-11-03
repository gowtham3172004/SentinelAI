"""Provider-independent types and pure helpers for language-model calls (ADR-017).

Nothing here performs I/O. Adapters translate these types to and from a provider's wire
format; services decide what to ask for and how to validate the answer.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class StructuredMode(StrEnum):
    """How a structured answer is requested, strongest guarantee first."""

    JSON_SCHEMA = "json_schema"  # provider enforces or guides by a JSON schema
    JSON_OBJECT = "json_object"  # provider guarantees syntactically valid JSON only
    PROMPT_ONLY = "prompt_only"  # schema given in the prompt; JSON extracted from text


class ModelCapabilities(BaseModel):
    """What a model supports. ``None`` means unknown: the gateway tries it and learns."""

    model_config = ConfigDict(frozen=True)

    json_schema: bool | None = None
    json_object: bool | None = None
    tools: bool | None = None


class CompletionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, gt=0)
    mode: StructuredMode | None = None  # None requests free text
    json_schema: dict[str, Any] | None = None
    schema_name: str = Field(default="output", pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class RateLimitSnapshot(BaseModel):
    """Rate-limit state reported by a provider on one response (fields absent if not sent)."""

    model_config = ConfigDict(frozen=True)

    limit_requests: int | None = None
    remaining_requests: int | None = None
    reset_requests_s: float | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None
    retry_after_s: float | None = None


class CompletionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    reported_model: str | None = None
    content: str
    finish_reason: str | None = None
    usage: Usage = Usage()
    rate_limit: RateLimitSnapshot = RateLimitSnapshot()
    latency_ms: int = 0
    request_id: str | None = None


# --------------------------------------------------------------------------- errors


class ProviderError(Exception):
    """Base class for provider failures.

    ``retryable`` tells the gateway whether to retry or park instead of failing the step.
    """

    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(ProviderError):
    """Missing, invalid or unauthorised API key."""


class ModelNotFoundError(ProviderError):
    """The model does not exist or was retired by the provider."""


class InvalidRequestError(ProviderError):
    """The provider rejected the request as malformed or unacceptable."""


class UnsupportedFeatureError(InvalidRequestError):
    """The model does not support the requested feature (for example a response format)."""


class RequestTooLargeError(InvalidRequestError):
    """The request exceeds the model's context window or per-request token limit."""


class GenerationRejectedError(InvalidRequestError):
    """The provider generated output but rejected it against the requested format."""

    def __init__(
        self, message: str, *, status_code: int | None = None, failed_generation: str | None = None
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.failed_generation = failed_generation


class RateLimitedError(ProviderError):
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        retry_after_s: float | None = None,
        snapshot: RateLimitSnapshot | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after_s = retry_after_s
        self.snapshot = snapshot or RateLimitSnapshot()


class ProviderUnavailableError(ProviderError):
    """Timeouts, connection failures, 5xx responses and malformed responses."""

    retryable = True


# --------------------------------------------------------------------------- pure helpers

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def parse_duration_s(text: str | None) -> float | None:
    """Parse provider durations: ``"7"``, ``"7.66s"``, ``"2m59.56s"``, ``"1h2m"``, ``"120ms"``.

    Returns ``None`` for anything unparseable, negative or non-finite.
    """
    if text is None:
        return None
    value = text.strip().lower()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    position = 0
    total = 0.0
    for match in _DURATION_PART.finditer(value):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        position = match.end()
    if position == 0 or position != len(value):
        return None
    return total


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitSnapshot:
    """Read the de-facto standard ``x-ratelimit-*`` and ``retry-after`` headers."""
    lowered = {key.lower(): value for key, value in headers.items()}
    return RateLimitSnapshot(
        limit_requests=_int_or_none(lowered.get("x-ratelimit-limit-requests")),
        remaining_requests=_int_or_none(lowered.get("x-ratelimit-remaining-requests")),
        reset_requests_s=parse_duration_s(lowered.get("x-ratelimit-reset-requests")),
        limit_tokens=_int_or_none(lowered.get("x-ratelimit-limit-tokens")),
        remaining_tokens=_int_or_none(lowered.get("x-ratelimit-remaining-tokens")),
        reset_tokens_s=parse_duration_s(lowered.get("x-ratelimit-reset-tokens")),
        retry_after_s=parse_duration_s(lowered.get("retry-after")),
    )


def modes_to_try(capabilities: ModelCapabilities) -> tuple[StructuredMode, ...]:
    """Structured modes in order of preference, skipping those known to be unsupported."""
    modes: list[StructuredMode] = []
    if capabilities.json_schema is not False:
        modes.append(StructuredMode.JSON_SCHEMA)
    if capabilities.json_object is not False:
        modes.append(StructuredMode.JSON_OBJECT)
    modes.append(StructuredMode.PROMPT_ONLY)
    return tuple(modes)


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _matching_brace(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def extract_json_object(text: str) -> str | None:
    """Return the first complete JSON object embedded in model text, or ``None``.

    Handles code fences and surrounding prose. Only objects (not arrays or scalars) qualify.
    """
    candidates = [match.group(1) for match in _FENCE.finditer(text)]
    candidates.append(text)
    for candidate in candidates:
        start = candidate.find("{")
        while start != -1:
            end = _matching_brace(candidate, start)
            if end is not None:
                snippet = candidate[start : end + 1]
                try:
                    parsed = json.loads(snippet)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    return snippet
            start = candidate.find("{", start + 1)
    return None


class AttemptRecord(BaseModel):
    """One model call made while obtaining a structured answer."""

    model_config = ConfigDict(frozen=True)

    mode: StructuredMode
    repair: bool = False
    ok: bool
    error_kind: str | None = None  # unsupported | invalid_output | provider_validation
    usage: Usage = Usage()
    latency_ms: int = 0
