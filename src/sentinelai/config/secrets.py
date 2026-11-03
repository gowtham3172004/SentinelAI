"""Secret resolution: the process environment first, then the per-user secrets file.

The secrets file lives in the user configuration directory, never in the repository, and
uses ``KEY=value`` lines. Values are returned only as ``SecretStr``.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

SecretSource = Literal["env", "file"]


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


class SecretResolver:
    def __init__(self, environ: Mapping[str, str], secrets_file: Path | None) -> None:
        self._environ = environ
        self._file_values: dict[str, str] = {}
        if secrets_file is not None and secrets_file.is_file():
            self._file_values = parse_env_file(secrets_file.read_text(encoding="utf-8"))

    def source(self, name: str) -> SecretSource | None:
        if self._environ.get(name):
            return "env"
        if self._file_values.get(name):
            return "file"
        return None

    def get(self, name: str) -> SecretStr | None:
        value = self._environ.get(name) or self._file_values.get(name)
        return SecretStr(value) if value else None


def is_private_file(path: Path) -> bool | None:
    """``True`` if only the owner can read the file (POSIX). ``None`` where not determinable."""
    if os.name != "posix" or not path.exists():
        return None
    return (path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)) == 0
