"""Process settings, validated at startup (fail fast). Secrets are never part of ``repr``."""

from __future__ import annotations

from pathlib import Path

import platformdirs
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "sentinelai"


def default_config_dir() -> Path:
    """Per-user configuration directory.

    ``%APPDATA%`` on Windows, XDG on Linux, ``~/Library/Application Support`` on macOS.
    """
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINELAI_", extra="ignore")

    config_dir: Path = Field(default_factory=default_config_dir)
    secrets_file: Path | None = None
    providers_file: Path | None = None
    database_url: SecretStr | None = None
    # Migrations and audit verification: an owner/administrator connection, never a service's.
    admin_database_url: SecretStr | None = None
    http_timeout_s: float = Field(default=60.0, gt=0, le=600)

    @property
    def resolved_secrets_file(self) -> Path:
        return self.secrets_file or self.config_dir / "secrets.env"

    @property
    def resolved_providers_file(self) -> Path:
        return self.providers_file or self.config_dir / "providers.yaml"
