from __future__ import annotations

from pathlib import Path

import pytest

from sentinelai.config.providers import PRESET_PROVIDERS, RegistryError, load_registry
from sentinelai.config.secrets import SecretResolver, parse_env_file
from sentinelai.config.settings import Settings


def test_parse_env_file() -> None:
    text = """
    # comment
    GROQ_API_KEY=gsk_abc
    export GEMINI_API_KEY="quoted value"
    EMPTY=
    malformed line
    SINGLE='x=y'
    """
    assert parse_env_file(text) == {
        "GROQ_API_KEY": "gsk_abc",
        "GEMINI_API_KEY": "quoted value",
        "EMPTY": "",
        "SINGLE": "x=y",
    }


def test_resolver_prefers_environment_and_masks_values(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("A_KEY=from-file\nB_KEY=file-only\nC_KEY=\n", encoding="utf-8")
    resolver = SecretResolver({"A_KEY": "from-env"}, secrets)

    assert resolver.source("A_KEY") == "env"
    assert resolver.source("B_KEY") == "file"
    assert resolver.source("C_KEY") is None
    assert resolver.source("MISSING") is None
    value = resolver.get("A_KEY")
    assert value is not None
    assert value.get_secret_value() == "from-env"
    assert "from-env" not in repr(value)
    assert "from-env" not in str(value)


def test_resolver_without_file() -> None:
    assert SecretResolver({}, None).get("X") is None
    assert SecretResolver({}, Path("does/not/exist.env")).get("X") is None


def test_settings_paths_follow_config_dir(isolated_config: Path) -> None:
    settings = Settings()
    assert settings.config_dir == isolated_config
    assert settings.resolved_secrets_file == isolated_config / "secrets.env"
    assert "pw@" not in repr(Settings(database_url="postgresql://u:pw@h/db"))


def test_registry_presets_cover_remote_and_local_providers() -> None:
    registry = load_registry(None)
    assert {"groq", "gemini", "openrouter", "cerebras", "mistral", "ollama", "lmstudio"} <= set(
        registry.providers
    )
    assert registry.provider("ollama").local
    assert registry.models_for("groq")
    with pytest.raises(RegistryError, match="unknown provider 'nope'"):
        registry.provider("nope")


def test_registry_file_adds_and_overrides(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(
        """
providers:
  together:
    base_url: https://api.together.xyz/v1
    api_key_env: TOGETHER_API_KEY
  groq:
    max_tokens_param: max_completion_tokens
models:
  - {provider: together, model: some/model, family: llama, tier: strong}
""",
        encoding="utf-8",
    )
    registry = load_registry(path)
    assert registry.provider("together").api_key_env == "TOGETHER_API_KEY"
    groq = registry.provider("groq")
    assert groq.max_tokens_param == "max_completion_tokens"
    assert groq.base_url == next(p for p in PRESET_PROVIDERS if p.name == "groq").base_url
    assert [m.model for m in registry.models_for("together")] == ["some/model"]


@pytest.mark.parametrize(
    "content",
    [
        "providers:\n  evil:\n    base_url: http://attacker.example/v1\n",
        "models:\n  - {provider: ghost, model: x, family: f, tier: fast}\n",
        "providers: [unclosed",
    ],
)
def test_registry_file_rejects_invalid_content(tmp_path: Path, content: str) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(path)
