"""Ports for telemetry ingestion and incident management (implemented by adapters.db)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sentinelai.domain.detection import (
    ChangeEvent,
    ClassPrior,
    DetectionConfig,
    FingerprintEvidence,
    ServiceSnapshot,
    Signal,
)
from sentinelai.domain.telemetry import LogRecord


@dataclass(frozen=True)
class ServiceRef:
    id: UUID
    name: str
    environment: str


@dataclass(frozen=True)
class IncidentRow:
    id: UUID
    public_id: str
    status: str
    severity: str
    routes: tuple[str, ...]
    fingerprints: frozenset[str]
    primary_fingerprint: str | None
    first_seen: datetime
    last_signal_at: datetime
    detected_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class ChangeIn:
    """A validated release or configuration event (D2 section 5.7)."""

    kind: str
    service: str
    environment: str
    version: str
    occurred_at: datetime
    idempotency_key: str
    previous_version: str | None = None
    git_sha: str | None = None
    changed_keys: tuple[str, ...] = ()
    actor: str | None = None


@dataclass(frozen=True)
class NewIncident:
    service_id: UUID
    severity: str
    title: str
    primary_fingerprint: str | None
    routes: tuple[str, ...]
    first_seen: datetime
    prior: ClassPrior
    at: datetime
    investigate: bool


@dataclass(frozen=True)
class IncidentUpdate:
    severity: str
    title: str
    routes: tuple[str, ...]
    prior: ClassPrior
    at: datetime
    added_signals: int
    fingerprints: Mapping[str, tuple[datetime, datetime, int]] = field(default_factory=dict)


class TelemetryTx(Protocol):
    """Operations inside one tenant transaction."""

    def services(self) -> list[ServiceRef]: ...

    def snapshot(
        self, service: ServiceRef, at: datetime, cfg: DetectionConfig,
        excluded: Sequence[tuple[datetime, datetime]],
    ) -> ServiceSnapshot: ...  # fmt: skip

    def open_incidents(self, service_id: UUID) -> list[IncidentRow]: ...

    def recently_resolved(self, service_id: UUID, since: datetime) -> list[IncidentRow]: ...

    def incidents_created_since(self, service_id: UUID, since: datetime) -> int: ...

    def noise_fingerprints(self, service_id: UUID, at: datetime) -> set[str]: ...

    def fingerprint_evidence(
        self, service_id: UUID, fingerprints: Sequence[str], routes: Sequence[str], since: datetime
    ) -> list[FingerprintEvidence]: ...

    def changes(self, service_id: UUID, since: datetime, until: datetime) -> list[ChangeEvent]: ...

    def status_codes(
        self, service_id: UUID, since: datetime, until: datetime
    ) -> dict[int, int]: ...

    def insert_signals(
        self, service_id: UUID, incident_id: UUID | None, signals: Sequence[Signal],
        at: datetime, *, suppressed: bool = False,
    ) -> int: ...  # fmt: skip

    def create_incident(self, incident: NewIncident) -> IncidentRow: ...

    def update_incident(self, incident: IncidentRow, update: IncidentUpdate) -> None: ...

    def transition(self, incident_id: UUID, to: str, reason: str) -> None: ...


class TelemetryStore(Protocol):
    def transaction(self) -> AbstractContextManager[TelemetryTx]: ...

    def write_batch(
        self,
        source: str,
        kind: str,
        records: Sequence[LogRecord],
        rejected: Sequence[tuple[str, str]],
        *,
        keep: Callable[[LogRecord], bool],
        cursor: Mapping[str, Any] | None = None,
        batch_id: str | None = None,
    ) -> bool: ...

    def source_cursor(self, source: str, kind: str) -> dict[str, Any]: ...

    def record_change(self, change: ChangeIn) -> bool: ...
