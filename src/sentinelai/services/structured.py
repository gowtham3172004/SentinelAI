"""Obtain schema-valid structured output from any provider (D2 §8.8, ADR-017).

Strategy: try the strongest structured mode the model may support, fall back on
``UnsupportedFeatureError``, validate with Pydantic, and allow exactly one repair attempt
per mode. Rate-limit and availability errors propagate unchanged: retrying and parking are
the gateway's job, not this function's.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from sentinelai.domain.llm import (
    AttemptRecord,
    ChatMessage,
    CompletionRequest,
    GenerationRejectedError,
    ModelCapabilities,
    Role,
    StructuredMode,
    UnsupportedFeatureError,
    Usage,
    extract_json_object,
    modes_to_try,
)
from sentinelai.services.ports import LLMProvider

MAX_ERROR_FEEDBACK_CHARS = 1500
MAX_ECHOED_OUTPUT_CHARS = 4000


class StructuredOutputError(Exception):
    """No schema-valid answer was obtained within the allowed attempts."""

    def __init__(self, message: str, attempts: Sequence[AttemptRecord]) -> None:
        super().__init__(message)
        self.attempts = tuple(attempts)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    mode: StructuredMode
    attempts: tuple[AttemptRecord, ...]

    @property
    def repaired(self) -> bool:
        return any(attempt.repair and attempt.ok for attempt in self.attempts)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for attempt in self.attempts:
            total = total + attempt.usage
        return total


def _instructions(json_schema: dict[str, Any], mode: StructuredMode) -> ChatMessage:
    if mode is StructuredMode.JSON_SCHEMA:
        text = "Respond only with a JSON object that conforms to the provided JSON schema."
    else:
        text = (
            "Respond only with a single JSON object, without prose or code fences, "
            "that matches this JSON Schema:\n" + json.dumps(json_schema, separators=(",", ":"))
        )
    return ChatMessage(role=Role.SYSTEM, content=text)


def _parse[T: BaseModel](content: str, schema: type[T]) -> tuple[T | None, str | None]:
    raw = content.strip()
    try:
        candidate: Any = json.loads(raw)
    except json.JSONDecodeError:
        extracted = extract_json_object(raw)
        if extracted is None:
            return None, "the reply did not contain a JSON object"
        candidate = json.loads(extracted)
    try:
        return schema.model_validate(candidate), None
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        ]
        return None, "; ".join(problems)[:MAX_ERROR_FEEDBACK_CHARS]


async def complete_structured[T: BaseModel](
    provider: LLMProvider,
    *,
    model: str,
    messages: Sequence[ChatMessage],
    schema: type[T],
    capabilities: ModelCapabilities | None = None,
    max_output_tokens: int = 1024,
    temperature: float = 0.0,
) -> StructuredResult[T]:
    json_schema = schema.model_json_schema()
    attempts: list[AttemptRecord] = []

    async def call(
        conversation: Sequence[ChatMessage], mode: StructuredMode, *, repair: bool
    ) -> str:
        request = CompletionRequest(
            model=model,
            messages=tuple(conversation),
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            mode=mode,
            json_schema=json_schema if mode is StructuredMode.JSON_SCHEMA else None,
            schema_name=schema.__name__,
        )
        try:
            result = await provider.complete(request)
        except GenerationRejectedError as exc:
            attempts.append(
                AttemptRecord(mode=mode, repair=repair, ok=False, error_kind="provider_validation")
            )
            return exc.failed_generation or ""
        attempts.append(
            AttemptRecord(
                mode=mode, repair=repair, ok=True, usage=result.usage, latency_ms=result.latency_ms
            )
        )
        return result.content

    def mark_invalid() -> None:
        last = attempts[-1]
        if last.ok:
            attempts[-1] = last.model_copy(update={"ok": False, "error_kind": "invalid_output"})

    for mode in modes_to_try(capabilities or ModelCapabilities()):
        conversation = [_instructions(json_schema, mode), *messages]
        try:
            content = await call(conversation, mode, repair=False)
        except UnsupportedFeatureError:
            attempts.append(AttemptRecord(mode=mode, ok=False, error_kind="unsupported"))
            continue

        value, problem = _parse(content, schema)
        if value is not None:
            return StructuredResult(value=value, mode=mode, attempts=tuple(attempts))
        mark_invalid()

        repair_conversation = [
            *conversation,
            ChatMessage(role=Role.ASSISTANT, content=content[:MAX_ECHOED_OUTPUT_CHARS]),
            ChatMessage(
                role=Role.USER,
                content=(
                    "Your previous reply was not valid. Problems: "
                    f"{problem}. Reply again with only a JSON object that matches the schema."
                ),
            ),
        ]
        content = await call(repair_conversation, mode, repair=True)
        value, problem = _parse(content, schema)
        if value is not None:
            return StructuredResult(value=value, mode=mode, attempts=tuple(attempts))
        mark_invalid()
        raise StructuredOutputError(
            f"model output failed validation after one repair: {problem}", attempts
        )

    raise StructuredOutputError("no structured-output mode is supported by this model", attempts)
