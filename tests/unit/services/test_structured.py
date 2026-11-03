from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from sentinelai.domain.llm import (
    ChatMessage,
    GenerationRejectedError,
    ModelCapabilities,
    ProviderUnavailableError,
    RateLimitedError,
    Role,
    StructuredMode,
    UnsupportedFeatureError,
)
from sentinelai.services.structured import StructuredOutputError, complete_structured
from tests.fakes import ScriptedProvider, reply


class Triage(BaseModel):
    incident_class: str
    confidence_note: str = Field(max_length=50)


MESSAGES = [ChatMessage(role=Role.USER, content="classify")]
VALID = '{"incident_class": "code_defect", "confidence_note": "trace in app frame"}'


async def run(provider: ScriptedProvider, caps: ModelCapabilities | None = None):  # type: ignore[no-untyped-def]
    return await complete_structured(
        provider, model="m", messages=MESSAGES, schema=Triage, capabilities=caps
    )


async def test_valid_first_answer_uses_json_schema_mode() -> None:
    provider = ScriptedProvider([reply(VALID)])
    result = await run(provider)
    assert result.value.incident_class == "code_defect"
    assert result.mode is StructuredMode.JSON_SCHEMA
    assert not result.repaired
    assert provider.requests[0].json_schema is not None
    assert provider.requests[0].messages[0].role is Role.SYSTEM


@pytest.mark.at("AT-LLM-01")
async def test_invalid_answer_gets_exactly_one_repair_with_errors() -> None:
    provider = ScriptedProvider([reply('{"incident_class": "code_defect"}'), reply(VALID)])
    result = await run(provider)
    assert result.repaired
    assert len(provider.requests) == 2
    repair_prompt = provider.requests[1].messages[-1].content
    assert "confidence_note" in repair_prompt
    assert [a.ok for a in result.attempts] == [False, True]
    assert result.usage.input_tokens == 20


@pytest.mark.at("AT-LLM-01")
async def test_second_invalid_answer_fails_the_step() -> None:
    provider = ScriptedProvider([reply("not json"), reply('{"incident_class": 3}')])
    with pytest.raises(StructuredOutputError) as caught:
        await run(provider)
    assert len(provider.requests) == 2
    assert all(not attempt.ok for attempt in caught.value.attempts)


async def test_repair_feedback_never_echoes_input_values() -> None:
    secretish = "x" * 60 + "TOKEN-abc"
    provider = ScriptedProvider(
        [reply(f'{{"incident_class": "a", "confidence_note": "{secretish}"}}'), reply(VALID)]
    )
    await run(provider)
    assert "TOKEN-abc" not in provider.requests[1].messages[-1].content


@pytest.mark.at("AT-LLM-08")
async def test_falls_back_from_json_schema_to_json_object() -> None:
    provider = ScriptedProvider([UnsupportedFeatureError("no json_schema"), reply(VALID)])
    result = await run(provider)
    assert result.mode is StructuredMode.JSON_OBJECT
    assert provider.requests[1].json_schema is None
    assert "JSON Schema" in provider.requests[1].messages[0].content
    assert result.attempts[0].error_kind == "unsupported"


@pytest.mark.at("AT-LLM-08")
async def test_prompt_only_extracts_json_from_prose() -> None:
    caps = ModelCapabilities(json_schema=False, json_object=False)
    provider = ScriptedProvider([reply(f"Here is the result:\n```json\n{VALID}\n```")])
    result = await run(provider, caps)
    assert result.mode is StructuredMode.PROMPT_ONLY
    assert provider.requests[0].mode is StructuredMode.PROMPT_ONLY


async def test_provider_side_validation_failure_is_repaired() -> None:
    rejected = GenerationRejectedError("bad", failed_generation='{"incident_class": 1}')
    provider = ScriptedProvider([rejected, reply(VALID)])
    result = await run(provider)
    assert result.repaired
    assert result.attempts[0].error_kind == "provider_validation"


async def test_all_modes_unsupported() -> None:
    provider = ScriptedProvider([UnsupportedFeatureError("x")] * 3)
    with pytest.raises(StructuredOutputError, match="no structured-output mode"):
        await run(provider)


@pytest.mark.parametrize(
    "error", [RateLimitedError("slow", retry_after_s=3), ProviderUnavailableError("down")]
)
async def test_retryable_errors_propagate_for_the_gateway(error: Exception) -> None:
    with pytest.raises(type(error)):
        await run(ScriptedProvider([error]))
