from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinelai.domain.llm import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimitedError,
    RateLimitSnapshot,
    StructuredMode,
    UnsupportedFeatureError,
)
from sentinelai.services.probe import probe_provider
from tests.fakes import ScriptedProvider, reply

FIXED = datetime(2026, 10, 31, tzinfo=UTC)


class FailingList(ScriptedProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__([])
        self._error = error

    async def list_models(self) -> list[str]:
        raise self._error


@pytest.mark.at("AT-LLM-09")
async def test_detects_capabilities_per_model() -> None:
    provider = ScriptedProvider(
        [
            UnsupportedFeatureError("json_schema unsupported"),  # m1 json_schema
            reply('{"answer": 5}'),  # m1 json_object
            reply('{"answer": 5}'),  # m2 json_schema
            reply("five"),  # m2 json_object: accepted, but invalid output
        ],
        models=["m1", "m2", "other"],
    )
    report = await probe_provider(provider, ["m1", "m2"], now=lambda: FIXED)

    assert report.reachable
    assert report.authenticated
    m1, m2 = report.models
    assert (m1.listed, m1.json_schema.supported, m1.json_object.supported) == (True, False, True)
    assert m1.callable is True
    assert m1.json_object.valid_output is True
    assert (m2.json_schema.supported, m2.json_object.valid_output) == (True, False)
    assert [r.mode for r in provider.requests] == [
        StructuredMode.JSON_SCHEMA,
        StructuredMode.JSON_OBJECT,
    ] * 2


@pytest.mark.at("AT-LLM-09")
async def test_retired_model_is_reported_not_callable() -> None:
    provider = ScriptedProvider([ModelNotFoundError("gone")], models=["m-new"])
    report = await probe_provider(provider, ["m-old"], now=lambda: FIXED)
    assert report.models[0].listed is False
    assert report.models[0].callable is False
    assert "gone" in (report.models[0].error or "")


@pytest.mark.at("AT-LLM-09")
async def test_listed_but_unservable_model_keeps_listed_true() -> None:
    """Live finding (Gemini, 2026-11-03): catalogues list models that return 404 when called."""
    provider = ScriptedProvider([ModelNotFoundError("no longer available")], models=["old"])
    report = await probe_provider(provider, ["old"], now=lambda: FIXED)
    assert report.models[0].listed is True
    assert report.models[0].callable is False


async def test_rate_limit_stops_probe_to_protect_quota() -> None:
    snapshot = RateLimitSnapshot(remaining_tokens=0)
    provider = ScriptedProvider([RateLimitedError("429", snapshot=snapshot)], models=["a", "b"])
    report = await probe_provider(provider, ["a", "b"], now=lambda: FIXED)
    assert len(report.models) == 1
    assert report.models[0].rate_limit == snapshot
    assert len(provider.requests) == 1


async def test_authentication_failure() -> None:
    report = await probe_provider(
        FailingList(AuthenticationError("bad key")), ["a"], now=lambda: FIXED
    )
    assert report.reachable
    assert report.authenticated is False
    assert report.models == []


async def test_unreachable_provider() -> None:
    report = await probe_provider(
        FailingList(ProviderUnavailableError("down")), ["a"], now=lambda: FIXED
    )
    assert not report.reachable
    assert report.error == "down"


async def test_no_calls_mode_only_lists() -> None:
    provider = ScriptedProvider([], models=["a"])
    report = await probe_provider(provider, ["a", "b"], live_calls=False, now=lambda: FIXED)
    assert [(m.model, m.listed) for m in report.models] == [("a", True), ("b", False)]
    assert provider.requests == []
