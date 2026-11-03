"""Built-in provider presets, suggested models and the user-extensible registry.

Model names change often (Groq in particular retires models). The suggested models are
**candidates to probe**, not commitments: `sentinelai providers probe` reports which still
exist and what they support. Users add or override providers and models in
``providers.yaml`` in the configuration directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from sentinelai.domain.providers import ModelSpec, ProviderSpec

PRESET_PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        docs_url="https://console.groq.com/docs/rate-limits",
    ),
    ProviderSpec(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        model_id_prefix="models/",
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
    ),
    ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        docs_url="https://openrouter.ai/docs",
    ),
    ProviderSpec(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        docs_url="https://inference-docs.cerebras.ai",
    ),
    ProviderSpec(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        docs_url="https://docs.mistral.ai",
    ),
    ProviderSpec(
        name="ollama",
        base_url="http://localhost:11434/v1",
        docs_url="https://github.com/ollama/ollama/blob/main/docs/openai.md",
    ),
    ProviderSpec(
        name="lmstudio",
        base_url="http://localhost:1234/v1",
        docs_url="https://lmstudio.ai/docs",
    ),
)

# Candidates only; verify with `sentinelai providers probe <provider>`. Notes record the last
# live probe (docs/spikes/llm-feasibility.md). "callable" means it served a request and
# returned schema-valid JSON for a trivial prompt; it says nothing about model quality.
_PROBED = "callable, json_schema+json_object ok (probe 2026-11-03)"
SUGGESTED_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        provider="groq", model="openai/gpt-oss-20b", family="gpt-oss", tier="fast", note=_PROBED
    ),
    ModelSpec(
        provider="groq", model="openai/gpt-oss-120b", family="gpt-oss", tier="strong", note=_PROBED
    ),
    ModelSpec(
        provider="groq", model="qwen/qwen3.8-27b", family="qwen", tier="strong", note=_PROBED
    ),
    ModelSpec(
        provider="gemini", model="gemini-3.5-flash-lite", family="gemini", tier="fast", note=_PROBED
    ),
    ModelSpec(
        provider="gemini", model="gemini-3.6-flash", family="gemini", tier="judge", note=_PROBED
    ),
    ModelSpec(
        provider="gemini",
        model="gemini-3.5-flash",
        family="gemini",
        tier="judge",
        note="listed; HTTP 503 high demand at probe 2026-11-03 (unverified)",
    ),
    ModelSpec(provider="cerebras", model="gpt-oss-120b", family="gpt-oss", tier="strong"),
    ModelSpec(provider="mistral", model="mistral-small-latest", family="mistral", tier="judge"),
    # CPU-only local models (no GPU available): fast tier experiments only.
    ModelSpec(
        provider="ollama", model="qwen2.5:3b", family="qwen", tier="fast", note="CPU-feasible"
    ),
    ModelSpec(
        provider="ollama", model="llama3.2:3b", family="llama", tier="fast", note="CPU-feasible"
    ),
)


class RegistryFile(BaseModel):
    """Schema of the user ``providers.yaml``."""

    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    models: list[ModelSpec] = Field(default_factory=list)


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Registry:
    providers: dict[str, ProviderSpec]
    models: tuple[ModelSpec, ...] = field(default_factory=tuple)

    def provider(self, name: str) -> ProviderSpec:
        try:
            return self.providers[name]
        except KeyError:
            known = ", ".join(sorted(self.providers))
            raise RegistryError(f"unknown provider '{name}' (known: {known})") from None

    def models_for(self, provider: str) -> tuple[ModelSpec, ...]:
        return tuple(m for m in self.models if m.provider == provider)


def load_registry(providers_file: Path | None) -> Registry:
    providers = {spec.name: spec for spec in PRESET_PROVIDERS}
    models = {m.id: m for m in SUGGESTED_MODELS}
    if providers_file is not None and providers_file.is_file():
        try:
            raw = yaml.safe_load(providers_file.read_text(encoding="utf-8")) or {}
            parsed = RegistryFile.model_validate(raw)
            for name, overrides in parsed.providers.items():
                base = providers[name].model_dump() if name in providers else {}
                providers[name] = ProviderSpec.model_validate({**base, **overrides, "name": name})
        except (yaml.YAMLError, ValueError) as exc:
            raise RegistryError(f"invalid providers file {providers_file}: {exc}") from exc
        for model in parsed.models:
            models[model.id] = model
    unknown = sorted({m.provider for m in models.values()} - providers.keys())
    if unknown:
        raise RegistryError(f"models refer to unknown providers: {', '.join(unknown)}")
    return Registry(providers=providers, models=tuple(models.values()))
