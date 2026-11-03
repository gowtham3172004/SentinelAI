"""Provider capability probe (AT-LLM-09, Phase 0 task P0-12).

Checks reachability and authentication, lists models, and for each requested model tests
whether the provider accepts JSON-schema and JSON-object response formats. It also captures
the rate-limit headers. Calls run sequentially with tiny prompts so the probe stays well
inside free-tier quotas.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError

from sentinelai.domain.llm import (
    AuthenticationError,
    ChatMessage,
    CompletionRequest,
    GenerationRejectedError,
    ModelNotFoundError,
    ProviderError,
    RateLimitedError,
    RateLimitSnapshot,
    Role,
    StructuredMode,
    UnsupportedFeatureError,
    Usage,
    extract_json_object,
)
from sentinelai.services.ports import LLMProvider

# Reasoning models spend output tokens on hidden reasoning; leave room for the answer.
PROBE_MAX_OUTPUT_TOKENS = 512


class ProbeAnswer(BaseModel):
    answer: int


_PROBE_MESSAGES = (
    ChatMessage(
        role=Role.SYSTEM,
        content='Reply only with a JSON object of the form {"answer": <integer>}.',
    ),
    ChatMessage(role=Role.USER, content="What is 2 + 3?"),
)


class FeatureProbe(BaseModel):
    supported: bool | None = None  # None: not tested (skipped or aborted)
    valid_output: bool | None = None
    detail: str | None = None
    latency_ms: int | None = None
    usage: Usage | None = None


class ModelProbe(BaseModel):
    """``listed``: appears in the provider's catalogue. ``callable``: actually served a request.

    They differ in practice: providers list models they refuse to serve (retired for new
    users, access-restricted). Neither says anything about model quality.
    """

    model: str
    listed: bool | None = None
    callable: bool | None = None
    json_schema: FeatureProbe = FeatureProbe()
    json_object: FeatureProbe = FeatureProbe()
    rate_limit: RateLimitSnapshot | None = None
    error: str | None = None


class ProviderProbe(BaseModel):
    provider: str
    probed_at: datetime
    reachable: bool = False
    authenticated: bool | None = None
    listed_models: list[str] = Field(default_factory=list)
    models: list[ModelProbe] = Field(default_factory=list)
    error: str | None = None


def _output_valid(content: str) -> bool:
    snippet = extract_json_object(content)
    if snippet is None:
        return False
    try:
        return ProbeAnswer.model_validate_json(snippet).answer == 5
    except ValidationError:
        return False


async def _probe_feature(
    provider: LLMProvider, model: str, mode: StructuredMode, probe: ModelProbe
) -> FeatureProbe:
    request = CompletionRequest(
        model=model,
        messages=_PROBE_MESSAGES,
        max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
        mode=mode,
        json_schema=ProbeAnswer.model_json_schema() if mode is StructuredMode.JSON_SCHEMA else None,
        schema_name="ProbeAnswer",
    )
    try:
        result = await provider.complete(request)
    except UnsupportedFeatureError as exc:
        return FeatureProbe(supported=False, detail=str(exc))
    except GenerationRejectedError as exc:
        return FeatureProbe(supported=True, valid_output=False, detail=str(exc))
    probe.rate_limit = result.rate_limit
    return FeatureProbe(
        supported=True,
        valid_output=_output_valid(result.content),
        latency_ms=result.latency_ms,
        usage=result.usage,
    )


async def probe_provider(
    provider: LLMProvider,
    models: Sequence[str],
    *,
    live_calls: bool = True,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProviderProbe:
    report = ProviderProbe(provider=provider.name, probed_at=now())
    try:
        report.listed_models = sorted(await provider.list_models())
        report.reachable = True
        report.authenticated = True
    except AuthenticationError as exc:
        report.reachable = True
        report.authenticated = False
        report.error = str(exc)
        return report
    except ProviderError as exc:
        report.error = str(exc)
        return report

    listed = set(report.listed_models)
    for model in models:
        probe = ModelProbe(model=model, listed=(model in listed) if listed else None)
        report.models.append(probe)
        if not live_calls:
            continue
        try:
            probe.json_schema = await _probe_feature(
                provider, model, StructuredMode.JSON_SCHEMA, probe
            )
            if probe.json_schema.supported:
                probe.callable = True
            probe.json_object = await _probe_feature(
                provider, model, StructuredMode.JSON_OBJECT, probe
            )
            if probe.json_object.supported:
                probe.callable = True
        except ModelNotFoundError as exc:
            probe.callable = False  # may still be listed: catalogues include unservable models
            probe.error = str(exc)
        except RateLimitedError as exc:
            probe.rate_limit = exc.snapshot
            probe.error = f"rate limited; stopping probe to protect quota ({exc})"
            break
        except ProviderError as exc:
            probe.error = str(exc)
    return report
