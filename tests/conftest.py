from __future__ import annotations

from pathlib import Path

import pytest

from sentinelai.config.providers import PRESET_PROVIDERS


@pytest.fixture(autouse=True)
def isolated_config(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Never read the developer's real configuration, secrets or provider keys in tests.

    The only exception is the opt-in ``live`` suite (tests/live), which exists precisely to
    exercise the real configured providers.
    """
    config_dir = tmp_path / "config"
    if request.node.get_closest_marker("live"):
        return config_dir
    monkeypatch.setenv("SENTINELAI_CONFIG_DIR", str(config_dir))
    for variable in ("SENTINELAI_SECRETS_FILE", "SENTINELAI_PROVIDERS_FILE"):
        monkeypatch.delenv(variable, raising=False)
    for spec in PRESET_PROVIDERS:
        if spec.api_key_env:
            monkeypatch.delenv(spec.api_key_env, raising=False)
    return config_dir
