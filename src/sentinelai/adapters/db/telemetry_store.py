"""PostgreSQL implementation of the telemetry and incident ports.

Idempotency (at-least-once delivery never double-counts):
- HTTP batches: ``ingest_batches`` row (unique batch id) in the same transaction as the
  additive window upserts; a re-delivered batch inserts nothing and returns False.
- Files: the source cursor (byte offset) is committed with the batch it covers.
- Release events: unique (service, idempotency_key).
- Detection: signals are unique per (service, kind, key, instant).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from sentinelai.adapters.db import queue
from sentinelai.adapters.db.session import tenant_transaction
from sentinelai.domain.detection import (
    ChangeEvent,
    DetectionConfig,
    ExceptionWindow,
    FingerprintEvidence,
    RequestCounts,
    RequestWindow,
    ServiceSnapshot,
    Signal,
    aggregate,
)
from sentinelai.domain.telemetry import LogRecord
from sentinelai.services.telemetry_ports import (
    ChangeIn,
    IncidentRow,
    IncidentUpdate,
    NewIncident,
    ServiceRef,
)

INVESTIGATION_QUEUE = "investigations"
DEFAULT_ENVIRONMENT = "production"
# Constant column list (never user input); interpolated into the incident queries below.
_INCIDENT_COLUMNS = (
    "i.id, i.public_id, i.status::text, i.severity::text, i.routes, i.primary_fingerprint, "
    "i.first_seen, i.last_signal_at, i.detected_at, i.resolved_at, "
    "COALESCE((SELECT array_agg(f.fingerprint) FROM incident_fingerprints f "
    "WHERE f.incident_id = i.id), '{}')"
)


def _incident(row: Sequence[Any]) -> IncidentRow:
    return IncidentRow(
        id=row[0], public_id=row[1], status=row[2], severity=row[3], routes=tuple(row[4]),
        primary_fingerprint=row[5], first_seen=row[6], last_signal_at=row[7],
        detected_at=row[8], resolved_at=row[9], fingerprints=frozenset(row[10]),
    )  # fmt: skip


class PgTelemetryTx:
    def __init__(self, conn: psycopg.Connection, tenant_id: UUID) -> None:
        self.conn = conn
        self.tenant_id = tenant_id
        self._services: dict[tuple[str, str], UUID] = {}

    # ------------------------------------------------------------------ services

    def ensure_service(self, name: str, environment: str | None) -> UUID:
        env = environment or DEFAULT_ENVIRONMENT
        if (name, env) not in self._services:
            self.conn.execute(
                "INSERT INTO services (tenant_id, name, environment) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, name, environment) DO NOTHING",
                (self.tenant_id, name, env),
            )
            row = self.conn.execute(
                "SELECT id FROM services WHERE name = %s AND environment = %s", (name, env)
            ).fetchone()
            if row is None:  # pragma: no cover - the insert above guarantees a row
                raise RuntimeError("service could not be registered")
            self._services[(name, env)] = row[0]
        return self._services[(name, env)]

    def services(self) -> list[ServiceRef]:
        rows = self.conn.execute(
            "SELECT id, name, environment FROM services ORDER BY name"
        ).fetchall()
        return [ServiceRef(*row) for row in rows]

    # ------------------------------------------------------------------ ingestion

    def write(
        self,
        source_id: UUID,
        records: Sequence[LogRecord],
        rejected: Sequence[tuple[str, str]],
        keep: Callable[[LogRecord], bool],
    ) -> None:
        agg = aggregate(records)
        service_ids = {
            name: self.ensure_service(name, env)
            for name, env in {(r.service, r.environment) for r in records}
        }  # fmt: skip
        env_of = {r.service: r.environment for r in records}
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO request_windows (tenant_id, service_id, route, window_start, "
                "requests, errors_5xx, errors_4xx, latency) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, service_id, route, window_start) DO UPDATE SET "
                "requests = request_windows.requests + EXCLUDED.requests, "
                "errors_5xx = request_windows.errors_5xx + EXCLUDED.errors_5xx, "
                "errors_4xx = request_windows.errors_4xx + EXCLUDED.errors_4xx, "
                "latency = add_int_arrays(request_windows.latency, EXCLUDED.latency)",
                [(self.tenant_id, service_ids[svc], route, start, c.requests, c.errors_5xx,
                  c.errors_4xx, c.latency) for (svc, route, start), c in agg.requests.items()],
            )  # fmt: skip
            cur.executemany(
                "INSERT INTO exception_windows (tenant_id, service_id, fingerprint, window_start, "
                "occurrences) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, service_id, fingerprint, window_start) DO UPDATE SET "
                "occurrences = exception_windows.occurrences + EXCLUDED.occurrences",
                [(self.tenant_id, service_ids[svc], fp, start, n)
                 for (svc, fp, start), n in agg.exceptions.items()],
            )  # fmt: skip
            cur.executemany(
                "INSERT INTO fingerprints (tenant_id, service_id, fingerprint, exception_type, "
                "sample_message, top_frames, library_only, innermost_in_app, first_seen, "
                "last_seen, occurrences, routes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, service_id, fingerprint) DO UPDATE SET "
                "first_seen = LEAST(fingerprints.first_seen, EXCLUDED.first_seen), "
                "last_seen = GREATEST(fingerprints.last_seen, EXCLUDED.last_seen), "
                "occurrences = fingerprints.occurrences + EXCLUDED.occurrences, "
                "routes = (SELECT COALESCE(array_agg(DISTINCT r), '{}') FROM (SELECT unnest("
                "fingerprints.routes || EXCLUDED.routes) AS r LIMIT 40) t)",
                [(self.tenant_id, service_ids[svc], fp, s.exception_type, s.sample_message,
                  Jsonb(list(s.top_frames)), s.library_only, s.innermost_in_app, s.first_seen,
                  s.last_seen, s.occurrences, sorted(s.routes))
                 for (svc, fp), s in agg.fingerprints.items()],
            )  # fmt: skip
            cur.executemany(
                "INSERT INTO log_events (tenant_id, service_id, ts, level, logger, message, "
                "trace_id, span_id, release_version, revision, environment, http_method, "
                "http_route, http_status, duration_ms, exception_type, exception_message, frames, "
                "fingerprint, attributes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s)",
                [(self.tenant_id, service_ids[r.service], r.ts, r.level, r.logger, r.message,
                  r.trace_id, r.span_id, r.version, r.revision, env_of[r.service], r.http_method,
                  r.http_route, r.http_status, r.duration_ms, r.exception_type,
                  r.exception_message, Jsonb([asdict(f) for f in r.frames]) if r.frames else None,
                  r.fingerprint.value if r.fingerprint else None, Jsonb(r.attributes))
                 for r in records if keep(r)],
            )  # fmt: skip
            cur.executemany(
                "INSERT INTO dead_letters (tenant_id, source_id, reason, raw) "
                "VALUES (%s, %s, %s, %s)",
                [(self.tenant_id, source_id, reason[:500], raw) for reason, raw in rejected],
            )

    # ------------------------------------------------------------------ detection reads

    def snapshot(
        self, service: ServiceRef, at: datetime, cfg: DetectionConfig,
        excluded: Sequence[tuple[datetime, datetime]],
    ) -> ServiceSnapshot:  # fmt: skip
        since = at - timedelta(seconds=cfg.baseline_window_s + 60)
        requests = [
            RequestWindow(route, start, RequestCounts(n, e5, e4, list(lat)))
            for route, start, n, e5, e4, lat in self.conn.execute(
                "SELECT route, window_start, requests, errors_5xx, errors_4xx, latency "
                "FROM request_windows WHERE service_id = %s AND window_start >= %s "
                "AND window_start < %s", (service.id, since, at),
            )
        ]  # fmt: skip
        exceptions = [
            ExceptionWindow(fp, start, n)
            for fp, start, n in self.conn.execute(
                "SELECT fingerprint, window_start, occurrences FROM exception_windows "
                "WHERE service_id = %s AND window_start >= %s AND window_start < %s",
                (service.id, since, at),
            )
        ]
        fps = sorted({w.fingerprint for w in exceptions})
        first_seen: dict[str, datetime] = {}
        if fps:
            first_seen = dict(self.conn.execute(
                "SELECT fingerprint, first_seen FROM fingerprints WHERE service_id = %s "
                "AND fingerprint = ANY(%s)", (service.id, fps),
            ).fetchall())  # fmt: skip
        return ServiceSnapshot(service.name, requests, exceptions, first_seen, excluded)

    def open_incidents(self, service_id: UUID) -> list[IncidentRow]:
        rows = self.conn.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents i WHERE i.service_id = %s "  # noqa: S608  # nosec B608
            "AND i.status IN ('detected','triaging','investigating','awaiting_human','mitigating') "
            "ORDER BY i.detected_at", (service_id,),
        ).fetchall()  # fmt: skip
        return [_incident(r) for r in rows]

    def recently_resolved(self, service_id: UUID, since: datetime) -> list[IncidentRow]:
        rows = self.conn.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents i WHERE i.service_id = %s "  # noqa: S608  # nosec B608
            "AND i.status = 'resolved' AND i.resolved_at >= %s ORDER BY i.resolved_at DESC",
            (service_id, since),
        ).fetchall()
        return [_incident(r) for r in rows]

    def incidents_created_since(self, service_id: UUID, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM incidents WHERE service_id = %s AND detected_at >= %s",
            (service_id, since),
        ).fetchone()
        return int(row[0]) if row else 0

    def noise_fingerprints(self, service_id: UUID, at: datetime) -> set[str]:
        rows = self.conn.execute(
            "SELECT fingerprint FROM noise_rules WHERE service_id = %s "
            "AND (expires_at IS NULL OR expires_at > %s)", (service_id, at),
        ).fetchall()  # fmt: skip
        return {r[0] for r in rows}

    def fingerprint_evidence(
        self, service_id: UUID, fingerprints: Sequence[str], routes: Sequence[str], since: datetime
    ) -> list[FingerprintEvidence]:
        rows = self.conn.execute(
            "SELECT fingerprint, exception_type, sample_message, innermost_in_app, library_only, "
            "first_seen, occurrences, routes FROM fingerprints WHERE service_id = %s "
            "AND last_seen >= %s AND (fingerprint = ANY(%s) OR routes && %s) "
            "ORDER BY occurrences DESC LIMIT 20",
            (service_id, since, list(fingerprints), list(routes)),
        ).fetchall()
        return [FingerprintEvidence(fp, et, msg, ina, lib, first, int(n), tuple(r))
                for fp, et, msg, ina, lib, first, n, r in rows]  # fmt: skip

    def changes(self, service_id: UUID, since: datetime, until: datetime) -> list[ChangeEvent]:
        rows = self.conn.execute(
            "SELECT id, kind, version, occurred_at, details FROM release_events "
            "WHERE service_id = %s AND occurred_at BETWEEN %s AND %s ORDER BY occurred_at",
            (service_id, since, until),
        ).fetchall()
        return [
            ChangeEvent(kind, version, occurred,
                        tuple((details or {}).get("changed_keys", [])), ref=str(ref))
            for ref, kind, version, occurred, details in rows
        ]  # fmt: skip

    def status_codes(self, service_id: UUID, since: datetime, until: datetime) -> dict[int, int]:
        rows = self.conn.execute(
            "SELECT http_status, count(*) FROM log_events WHERE service_id = %s "
            "AND ts BETWEEN %s AND %s AND http_status >= 500 GROUP BY http_status",
            (service_id, since, until),
        ).fetchall()
        return {int(code): int(n) for code, n in rows}

    # ------------------------------------------------------------------ incident writes

    def insert_signals(
        self, service_id: UUID, incident_id: UUID | None, signals: Sequence[Signal],
        at: datetime, *, suppressed: bool = False,
    ) -> int:  # fmt: skip
        inserted = 0
        for s in signals:
            result = self.conn.execute(
                "INSERT INTO incident_signals (tenant_id, incident_id, service_id, kind, key, "
                "suppressed, fired_at, window_start, window_end, observed, baseline, details) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, service_id, kind, key, fired_at) DO NOTHING",
                (self.tenant_id, incident_id, service_id, s.kind, s.key, suppressed, at,
                 s.window_start, s.window_end, s.observed, s.baseline, Jsonb(dict(s.details))),
            )  # fmt: skip
            inserted += result.rowcount
        return inserted

    def create_incident(self, incident: NewIncident) -> IncidentRow:
        prior = incident.prior
        suspected = prior.suspected_change.ref if prior.suspected_change and \
            prior.suspected_change.kind in ("deploy", "rollback") else None  # fmt: skip
        row = self.conn.execute(
            "INSERT INTO incidents (tenant_id, service_id, severity, title, primary_fingerprint, "
            "routes, class_prior, class_prior_rule, class_prior_reasons, code_patch_candidate, "
            "suspected_release_id, first_seen, last_signal_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, public_id",
            (self.tenant_id, incident.service_id, incident.severity, incident.title,
             incident.primary_fingerprint, list(incident.routes), prior.incident_class, prior.rule,
             Jsonb(list(prior.reasons)), prior.code_patch_candidate, suspected,
             incident.first_seen, incident.at),
        ).fetchone()  # fmt: skip
        if row is None:  # pragma: no cover
            raise RuntimeError("incident insert returned no row")
        incident_id, public_id = row
        payload = {"severity": incident.severity, "class_prior": prior.incident_class,
                   "code_patch_candidate": prior.code_patch_candidate}  # fmt: skip
        self.conn.execute(
            "SELECT audit_append('service', 'sentinel-telemetry', 'incident.opened', 'incident', "
            "%s, %s)", (public_id, Jsonb(payload)),
        )  # fmt: skip
        queue.emit(self.conn, "incident.created", "incident", str(incident_id),
                   {"public_id": public_id, "severity": incident.severity},
                   tenant_id=self.tenant_id)  # fmt: skip
        if incident.investigate:
            queue.enqueue(self.conn, INVESTIGATION_QUEUE, "investigate",
                          {"incident_id": incident_id}, tenant_id=self.tenant_id,
                          dedupe_key=f"investigate:{incident_id}")  # fmt: skip
        return IncidentRow(incident_id, public_id, "detected", incident.severity, incident.routes,
                           frozenset(), incident.primary_fingerprint, incident.first_seen,
                           incident.at, incident.at)  # fmt: skip

    def update_incident(self, incident: IncidentRow, update: IncidentUpdate) -> None:
        prior = update.prior
        suspected = prior.suspected_change.ref if prior.suspected_change and \
            prior.suspected_change.kind in ("deploy", "rollback") else None  # fmt: skip
        self.conn.execute(
            "UPDATE incidents SET severity = %s, title = %s, routes = %s, class_prior = %s, "
            "class_prior_rule = %s, class_prior_reasons = %s, code_patch_candidate = %s, "
            "suspected_release_id = COALESCE(%s, suspected_release_id), "
            "last_signal_at = GREATEST(last_signal_at, %s), "
            "signal_count = signal_count + %s WHERE id = %s",
            (update.severity, update.title, list(update.routes), prior.incident_class, prior.rule,
             Jsonb(list(prior.reasons)), prior.code_patch_candidate, suspected, update.at,
             update.added_signals, incident.id),
        )  # fmt: skip
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO incident_fingerprints (tenant_id, incident_id, fingerprint, "
                "first_seen, last_seen, occurrences) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, incident_id, fingerprint) DO UPDATE SET "
                "last_seen = GREATEST(incident_fingerprints.last_seen, EXCLUDED.last_seen), "
                "occurrences = GREATEST(incident_fingerprints.occurrences, EXCLUDED.occurrences)",
                [(self.tenant_id, incident.id, fp, first, last, n)
                 for fp, (first, last, n) in update.fingerprints.items()],
            )  # fmt: skip

    def transition(self, incident_id: UUID, to: str, reason: str) -> None:
        self.conn.execute("SELECT incident_transition(%s, %s, %s)", (incident_id, to, reason))


class PgTelemetryStore:
    """Tenant-bound store. ``role`` is set per transaction (dev/tests); production services
    connect as their own login role instead."""

    def __init__(self, conn: psycopg.Connection, tenant_id: UUID, *, role: str | None = None):
        self._conn = conn
        self._tenant = tenant_id
        self._role = role

    @contextmanager
    def transaction(self) -> Iterator[PgTelemetryTx]:
        with tenant_transaction(self._conn, self._tenant, role=self._role) as conn:
            yield PgTelemetryTx(conn, self._tenant)

    def _source(self, tx: PgTelemetryTx, name: str, kind: str) -> tuple[UUID, dict[str, Any]]:
        tx.conn.execute(
            "INSERT INTO ingest_sources (tenant_id, name, kind) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id, name) DO NOTHING", (self._tenant, name, kind),
        )  # fmt: skip
        row = tx.conn.execute(
            "SELECT id, cursor FROM ingest_sources WHERE name = %s FOR UPDATE", (name,)
        ).fetchone()
        if row is None:  # pragma: no cover
            raise RuntimeError("ingest source could not be registered")
        return row[0], dict(row[1])

    def source_cursor(self, source: str, kind: str) -> dict[str, Any]:
        with self.transaction() as tx:
            return self._source(tx, source, kind)[1]

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
    ) -> bool:
        with self.transaction() as tx:
            source_id, _ = self._source(tx, source, kind)
            if batch_id is not None:
                inserted = tx.conn.execute(
                    "INSERT INTO ingest_batches (tenant_id, source_id, batch_id, records) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (self._tenant, source_id, batch_id, len(records)),
                ).rowcount
                if inserted == 0:
                    return False  # duplicate delivery: nothing written
            tx.write(source_id, records, rejected, keep)
            tx.conn.execute(
                "UPDATE ingest_sources SET cursor = COALESCE(%s, cursor), records = records + %s, "
                "rejected = rejected + %s, updated_at = now() WHERE id = %s",
                (Jsonb(dict(cursor)) if cursor is not None else None, len(records), len(rejected),
                 source_id),
            )  # fmt: skip
        return True

    def record_change(self, change: ChangeIn) -> bool:
        with self.transaction() as tx:
            service_id = tx.ensure_service(change.service, change.environment)
            details = {"git_sha": change.git_sha, "changed_keys": list(change.changed_keys),
                       "actor": change.actor}  # fmt: skip
            inserted = tx.conn.execute(
                "INSERT INTO release_events (tenant_id, service_id, kind, version, "
                "previous_version, idempotency_key, details, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, service_id, idempotency_key) DO NOTHING",
                (self._tenant, service_id, change.kind, change.version, change.previous_version,
                 change.idempotency_key, Jsonb(details), change.occurred_at),
            ).rowcount  # fmt: skip
        return inserted == 1


def resolve_tenant(conn: psycopg.Connection, slug: str) -> UUID:
    row = conn.execute("SELECT id FROM tenants WHERE slug = %s", (slug,)).fetchone()
    conn.rollback()
    if row is None:
        raise LookupError(f"unknown tenant '{slug}'")
    tenant: UUID = row[0]
    return tenant
