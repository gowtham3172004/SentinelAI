from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinelai.domain.llm import (
    ModelCapabilities,
    StructuredMode,
    extract_json_object,
    modes_to_try,
    parse_duration_s,
    parse_rate_limit_headers,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7", 7.0),
        ("7.66s", 7.66),
        ("2m59.56s", 179.56),
        ("1h2m3s", 3723.0),
        ("120ms", 0.12),
        ("  3s ", 3.0),
        ("1m500ms", 60.5),
    ],
)
def test_parse_duration_accepts_provider_formats(text: str, expected: float) -> None:
    assert parse_duration_s(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    [None, "", "abc", "-3", "nan", "inf", "3x", "s", "1m 2s", "Wed, 21 Oct 2026 07:28:00 GMT"],
)
def test_parse_duration_rejects_garbage(text: str | None) -> None:
    assert parse_duration_s(text) is None


@given(
    hours=st.integers(0, 48),
    minutes=st.integers(0, 59),
    centis=st.integers(0, 5999),
)
def test_parse_duration_composite_property(hours: int, minutes: int, centis: int) -> None:
    seconds = centis / 100
    text = f"{hours}h{minutes}m{seconds}s"
    assert parse_duration_s(text) == pytest.approx(hours * 3600 + minutes * 60 + seconds)


def test_parse_rate_limit_headers_is_case_insensitive_and_partial() -> None:
    snapshot = parse_rate_limit_headers(
        {
            "X-RateLimit-Limit-Requests": "1000",
            "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-reset-requests": "1m26.4s",
            "x-ratelimit-remaining-tokens": "not-a-number",
            "Retry-After": "2",
        }
    )
    assert snapshot.limit_requests == 1000
    assert snapshot.remaining_requests == 999
    assert snapshot.reset_requests_s == pytest.approx(86.4)
    assert snapshot.remaining_tokens is None
    assert snapshot.limit_tokens is None
    assert snapshot.retry_after_s == 2.0


@pytest.mark.at("AT-LLM-08")
def test_modes_skip_known_unsupported_and_always_end_with_prompt_only() -> None:
    assert modes_to_try(ModelCapabilities()) == (
        StructuredMode.JSON_SCHEMA,
        StructuredMode.JSON_OBJECT,
        StructuredMode.PROMPT_ONLY,
    )
    assert modes_to_try(ModelCapabilities(json_schema=False)) == (
        StructuredMode.JSON_OBJECT,
        StructuredMode.PROMPT_ONLY,
    )
    assert modes_to_try(ModelCapabilities(json_schema=False, json_object=False)) == (
        StructuredMode.PROMPT_ONLY,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('Sure! Here it is:\n```json\n{"a": {"b": [1, 2]}}\n```\nDone.', {"a": {"b": [1, 2]}}),
        ('prefix {"s": "brace } inside \\" string"} suffix', {"s": 'brace } inside " string'}),
        ('{broken {"ok": true}', {"ok": True}),
    ],
)
def test_extract_json_object(text: str, expected: dict[str, object]) -> None:
    snippet = extract_json_object(text)
    assert snippet is not None
    assert json.loads(snippet) == expected


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", '{"unterminated": '])
def test_extract_json_object_returns_none_without_an_object(text: str) -> None:
    assert extract_json_object(text) is None


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)


@given(
    obj=st.dictionaries(st.text(max_size=8), json_values, max_size=5),
    prose=st.sampled_from(["", "Here you go: ", "Answer:\n```json\n"]),
)
def test_extract_json_object_roundtrip_property(obj: dict[str, object], prose: str) -> None:
    text = prose + json.dumps(obj) + ("\n```" if "```" in prose else "")
    snippet = extract_json_object(text)
    assert snippet is not None
    assert json.loads(snippet) == obj
