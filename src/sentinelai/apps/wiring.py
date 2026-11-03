"""Construct configured adapters from settings."""

from __future__ import annotations

import os
from collections.abc import Mapping

from sentinelai.adapters.llm.openai_compatible import OpenAICompatibleProvider
from sentinelai.config.providers import Registry, load_registry
from sentinelai.config.secrets import SecretResolver
from sentinelai.config.settings import Settings


class Context:
    """Everything a command needs, resolved once."""

    def __init__(self, settings: Settings, environ: Mapping[str, str] | None = None) -> None:
        self.settings = settings
        self.secrets = SecretResolver(
            os.environ if environ is None else environ, settings.resolved_secrets_file
        )
        self.registry: Registry = load_registry(settings.resolved_providers_file)

    def provider(self, name: str) -> OpenAICompatibleProvider:
        spec = self.registry.provider(name)
        key = self.secrets.get(spec.api_key_env) if spec.api_key_env else None
        return OpenAICompatibleProvider(spec, key, timeout_s=self.settings.http_timeout_s)
