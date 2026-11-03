from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinelai.domain.providers import ModelSpec, ProviderSpec, is_local_host


@pytest.mark.parametrize(
    "url",
    ["http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://[::1]:8000/v1"],
)
def test_plain_http_allowed_for_loopback(url: str) -> None:
    spec = ProviderSpec(name="local", base_url=url)
    assert spec.local
    assert not spec.requires_key


@pytest.mark.parametrize(
    "url", ["http://api.example.com/v1", "ftp://example.com", "not a url", "https:///v1"]
)
def test_insecure_or_invalid_urls_rejected(url: str) -> None:
    """API keys must never travel over plain http to a remote host."""
    with pytest.raises(ValidationError):
        ProviderSpec(name="remote", base_url=url, api_key_env="REMOTE_KEY")


def test_remote_https_provider() -> None:
    spec = ProviderSpec(name="groq", base_url="https://api.groq.com/openai/v1", api_key_env="K_X")
    assert not spec.local
    assert spec.requires_key


@pytest.mark.parametrize(
    ("host", "local"),
    [("localhost", True), ("127.0.0.5", True), ("::1", True), ("10.0.0.1", False), ("a.b", False)],
)
def test_is_local_host(host: str, local: bool) -> None:
    assert is_local_host(host) is local


def test_model_id() -> None:
    spec = ModelSpec(provider="groq", model="openai/gpt-oss-120b", family="gpt-oss", tier="strong")
    assert spec.id == "groq/openai/gpt-oss-120b"
