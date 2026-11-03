"""Provider and model registry types (docs/design/01-architecture.md §6, 06 §C.3)."""

from __future__ import annotations

from datetime import date
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sentinelai.domain.llm import ModelCapabilities

Tier = Literal["fast", "strong", "judge"]

_LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})


def is_local_host(host: str) -> bool:
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class ProviderTerms(BaseModel):
    """Data-handling terms recorded per provider; reviewed before real tenant data is sent."""

    model_config = ConfigDict(frozen=True)

    trains_on_inputs: Literal["yes", "no", "opt_out", "unknown"] = "unknown"
    retention: str = "unknown"
    reviewed_at: date | None = None
    notes: str = ""


class ProviderSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    adapter: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    # Some providers list models with a prefix their chat endpoint does not use
    # (Gemini lists "models/gemini-2.5-flash" but is called with "gemini-2.5-flash").
    model_id_prefix: str | None = None
    terms: ProviderTerms = ProviderTerms()
    docs_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def _transport_must_protect_keys(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parts.scheme == "http" and not is_local_host(parts.hostname):
            raise ValueError("plain http is only allowed for local providers (loopback hosts)")
        return value

    @property
    def local(self) -> bool:
        host = urlsplit(self.base_url).hostname or ""
        return is_local_host(host)

    @property
    def requires_key(self) -> bool:
        return self.api_key_env is not None


class ModelLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str = Field(min_length=1)
    family: str = Field(min_length=1)
    tier: Tier
    context_window: int | None = None
    capabilities: ModelCapabilities = ModelCapabilities()
    limits: ModelLimits = ModelLimits()
    note: str = ""

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"
