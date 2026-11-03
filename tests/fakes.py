"""Test doubles for ports."""

from __future__ import annotations

from collections.abc import Callable

from sentinelai.domain.llm import CompletionRequest, CompletionResult, Usage

Responder = Callable[[CompletionRequest], CompletionResult | Exception]


class ScriptedProvider:
    """Replies from a script of results or exceptions, recording every request."""

    def __init__(self, script: list[CompletionResult | Exception], models: list[str] | None = None):
        self._script = list(script)
        self._models = models or []
        self.requests: list[CompletionRequest] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def list_models(self) -> list[str]:
        return list(self._models)

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("ScriptedProvider ran out of scripted replies")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def reply(content: str, *, tokens: int = 10) -> CompletionResult:
    return CompletionResult(
        model="m", content=content, usage=Usage(input_tokens=tokens, output_tokens=tokens)
    )
