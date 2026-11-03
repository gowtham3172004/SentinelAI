"""Port interfaces implemented by adapters."""

from __future__ import annotations

from typing import Protocol

from sentinelai.domain.llm import CompletionRequest, CompletionResult


class LLMProvider(Protocol):
    """One model provider endpoint. Implementations raise ``sentinelai.domain.llm`` errors."""

    @property
    def name(self) -> str: ...

    async def list_models(self) -> list[str]: ...

    async def complete(self, request: CompletionRequest) -> CompletionResult: ...
