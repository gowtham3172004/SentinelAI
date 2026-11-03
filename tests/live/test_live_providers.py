"""Live tests against real providers. Opt-in only; never part of the default or CI run.

Run with:  SENTINELAI_LIVE_TESTS=1 uv run pytest -m live
(PowerShell: $env:SENTINELAI_LIVE_TESTS="1"; uv run pytest -m live)

They use the keys configured on this machine (environment or the user secrets file) and spend
a few hundred tokens per provider. What a pass proves: the endpoint is reachable, the key is
accepted, the model exists, and it returned schema-valid JSON for a trivial prompt.
**What a pass does not prove: anything about model quality for investigation work.**
That is measured only by the benchmark (docs/design/11, AT-EVAL-*).

Model choice per provider: ``SENTINELAI_LIVE_<PROVIDER>_MODEL`` if set, otherwise the first
suggested model that the provider currently lists.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from sentinelai.apps.wiring import Context
from sentinelai.config.settings import Settings
from sentinelai.domain.llm import ChatMessage, ModelNotFoundError, ProviderUnavailableError, Role
from sentinelai.services.structured import complete_structured

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("SENTINELAI_LIVE_TESTS") != "1",
        reason="live provider tests are opt-in: set SENTINELAI_LIVE_TESTS=1",
    ),
]


class Sum(BaseModel):
    answer: int


def _context_or_skip(provider: str) -> Context:
    ctx = Context(Settings())
    spec = ctx.registry.provider(provider)
    if spec.api_key_env and ctx.secrets.get(spec.api_key_env) is None:
        pytest.skip(f"{spec.api_key_env} not configured on this machine")
    return ctx


@pytest.mark.parametrize("provider", ["groq", "gemini"])
async def test_live_structured_round_trip(provider: str) -> None:
    ctx = _context_or_skip(provider)
    spec = ctx.registry.provider(provider)
    key = ctx.secrets.get(spec.api_key_env) if spec.api_key_env else None
    override = os.environ.get(f"SENTINELAI_LIVE_{provider.upper()}_MODEL")
    async with ctx.provider(provider) as client:
        listed = await client.list_models()
        assert listed, "provider listed no models"
        # Catalogues list models they refuse to serve, so try candidates in order.
        candidates = (
            [override]
            if override
            else [m.model for m in ctx.registry.models_for(provider) if m.model in listed]
        )
        result = None
        failures: list[str] = []
        for model in candidates:
            try:
                result = await complete_structured(
                    client,
                    model=model,
                    messages=[ChatMessage(role=Role.USER, content="What is 2 + 3?")],
                    schema=Sum,
                    max_output_tokens=512,
                )
                break
            except (ModelNotFoundError, ProviderUnavailableError) as exc:
                failures.append(f"{model}: {exc}")
        if result is None:
            pytest.fail(f"no {provider} candidate served a request: {failures}")
    assert result.value.answer == 5
    if key is not None:
        secret = key.get_secret_value()
        assert secret not in repr(result)
        assert all(secret not in m for m in listed)
