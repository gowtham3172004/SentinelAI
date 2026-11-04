"""Per-project settings read from the target repository's ``sentinel.yaml`` (06 Appendix C.1).

Only what telemetry and detection need is read here; unknown keys are ignored, so the same
file serves later phases. Every project can describe itself without code changes:
- ``source_roots``: which stack frames are the project's own code (in-app vs library)
- ``telemetry.app_root``: path prefix in production stack traces (e.g. ``/app``, ``/srv/app``)
- ``endpoints``: criticality tier per route (severity)
- ``telemetry.fields``: extra field names for unusual log formats, tried before the defaults
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sentinelai.domain.telemetry import FieldMapping, SourceDefaults


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    method: str = "GET"
    path: str
    tier: int = Field(default=2, ge=0, le=3)


class TelemetrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_root: list[str] | str | None = None
    environment: str | None = None
    fields: dict[str, list[str]] = Field(default_factory=dict)
    info_sample_rate: float = Field(default=0.01, ge=0, le=1)


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    service: str | None = None
    source_roots: list[str] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)

    def defaults(self, *, service: str | None = None) -> SourceDefaults:
        roots = self.telemetry.app_root
        app_roots = [roots] if isinstance(roots, str) else list(roots or [])
        return SourceDefaults(
            service=service or self.service,
            environment=self.telemetry.environment,
            source_roots=tuple(r.rstrip("/") for r in self.source_roots),
            app_roots=tuple(app_roots),
        )

    def field_mapping(self) -> FieldMapping:
        base = FieldMapping()
        known = {f.name for f in fields(FieldMapping)}
        unknown = set(self.telemetry.fields) - known
        if unknown:
            raise ValueError(f"unknown telemetry.fields keys: {', '.join(sorted(unknown))}")
        overrides = {name: tuple(keys) + getattr(base, name)
                     for name, keys in self.telemetry.fields.items()}  # fmt: skip
        return FieldMapping(**{**{f.name: getattr(base, f.name) for f in fields(FieldMapping)},
                               **overrides})  # fmt: skip

    def route_tiers(self) -> dict[str, int]:
        """Tier per route path; when methods share a path, the most critical tier wins."""
        tiers: dict[str, int] = {}
        for endpoint in self.endpoints:
            tiers[endpoint.path] = min(endpoint.tier, tiers.get(endpoint.path, 3))
        return tiers


def load_target_config(path: Path | None) -> TargetConfig:
    if path is None:
        return TargetConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return TargetConfig.model_validate(raw)
