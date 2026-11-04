"""Detection ticks and incident management (FR-03, FR-04; D2 sections 6.3-6.7).

One transaction per service per tick: evaluate the rules over stored windows, then open,
attach, reopen, suppress or rate-limit, and finally self-resolve quiet incidents. Re-running a
tick for the same instant is a no-op (signals are unique per instant), so detection is
restart-safe and replayable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sentinelai.domain.detection import (
    DetectionConfig,
    FingerprintEvidence,
    IncidentEvidence,
    Signal,
    classify,
    evaluate,
    severity,
)
from sentinelai.services.telemetry_ports import (
    IncidentRow,
    IncidentUpdate,
    NewIncident,
    ServiceRef,
    TelemetryStore,
    TelemetryTx,
)

OPEN_STATUSES = ("detected", "triaging", "investigating", "awaiting_human", "mitigating")
_SEVERITY_ORDER = {"sev1": 1, "sev2": 2, "sev3": 3, "sev4": 4}


@dataclass(frozen=True)
class IncidentPolicy:
    merge_window: timedelta = timedelta(minutes=30)
    resolve_after: timedelta = timedelta(minutes=15)
    reopen_window: timedelta = timedelta(minutes=30)
    max_new_per_window: int = 3
    rate_window: timedelta = timedelta(minutes=10)
    investigate_severities: tuple[str, ...] = ("sev1", "sev2", "sev3")
    evidence_lookback: timedelta = timedelta(hours=2)
    new_failure_window: timedelta = timedelta(minutes=15)


def estimate_onset(
    signals: list[Signal],
    fingerprints: list[FingerprintEvidence],
    at: datetime,
    new_failure_window: timedelta,
) -> datetime:
    """When the failure actually began, not when the rules' lookback windows start.

    The first sighting of a *new* failure (a fingerprint first seen within the window) is the
    most precise anchor; release and configuration correlation depend on it. Without one, the
    start of the error-rate window is the best estimate. A lookback start must never be used:
    a 120 s exception window would place the onset before a deploy that caused the failure.
    """
    new = [f.first_seen for f in fingerprints if f.first_seen >= at - new_failure_window]
    if new:
        return min(new)
    windows = [s.window_start for s in signals if s.kind in ("ERROR_RATE", "LATENCY")]
    return min(windows) if windows else at - timedelta(seconds=30)


@dataclass
class TickResult:
    opened: list[str] = field(default_factory=list)
    attached: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    suppressed: int = 0
    signals: int = 0


def _worse(a: str, b: str) -> str:
    return a if _SEVERITY_ORDER[a] <= _SEVERITY_ORDER[b] else b


def _related(
    incident: IncidentRow, signals: list[Signal], routes_of: Mapping[str, set[str]]
) -> bool:
    for s in signals:
        if s.key in incident.routes or s.key in incident.fingerprints:
            return True
        if s.kind == "EXCEPTION_SPIKE" and routes_of.get(s.key, set()) & set(incident.routes):
            return True
    return False


def _title(signals: list[Signal], dominant: FingerprintEvidence | None) -> str:
    routes = sorted({s.key for s in signals if s.kind in ("ERROR_RATE", "LATENCY")})
    if not routes and dominant is not None:
        routes = sorted(dominant.routes)  # the failing requests' own route
    where = routes[0] if routes else "service"
    if dominant is not None:
        return f"{dominant.exception_type} on {where}"[:200]
    worst = max(
        (s for s in signals if s.kind == "ERROR_RATE"), key=lambda s: s.observed, default=None
    )
    if worst is not None:
        return f"5xx on {worst.key} ({worst.observed:.0%})"[:200]
    return f"{signals[0].kind.lower().replace('_', ' ')} on {where}"[:200]


class IncidentManager:
    def __init__(
        self,
        store: TelemetryStore,
        *,
        config: DetectionConfig | None = None,
        policy: IncidentPolicy | None = None,
        route_tiers: Mapping[str, Mapping[str, int]] | None = None,
    ) -> None:
        self._store = store
        self._cfg = config or DetectionConfig()
        self._policy = policy or IncidentPolicy()
        self._tiers = route_tiers or {}

    def tick(self, at: datetime) -> TickResult:
        result = TickResult()
        with self._store.transaction() as tx:
            services = tx.services()
        for service in services:
            with self._store.transaction() as tx:
                self._tick_service(tx, service, at, result)
        return result

    def _tick_service(
        self, tx: TelemetryTx, service: ServiceRef, at: datetime, result: TickResult
    ) -> None:
        policy = self._policy
        open_incidents = tx.open_incidents(service.id)
        excluded = [(i.first_seen - timedelta(seconds=60), at) for i in open_incidents]
        snapshot = tx.snapshot(service, at, self._cfg, excluded)
        signals = evaluate(at, snapshot, self._cfg)

        noise = tx.noise_fingerprints(service.id, at)
        suppressed = [s for s in signals if s.kind == "EXCEPTION_SPIKE" and s.key in noise]
        active = [s for s in signals if s not in suppressed]
        if suppressed:
            result.suppressed += tx.insert_signals(
                service.id, None, suppressed, at, suppressed=True
            )

        if active:
            evidence_fps = tx.fingerprint_evidence(
                service.id,
                [s.key for s in active if s.kind == "EXCEPTION_SPIKE"],
                [s.key for s in active if s.kind != "EXCEPTION_SPIKE"],
                at - policy.evidence_lookback,
            )
            routes_of = {f.fingerprint: set(f.routes) for f in evidence_fps}
            merge_since = at - policy.merge_window
            target = next(
                (i for i in open_incidents
                 if i.last_signal_at >= merge_since and _related(i, active, routes_of)),
                None,
            )  # fmt: skip
            if target is None:
                recent = tx.recently_resolved(service.id, at - policy.reopen_window)
                target = next((i for i in recent if _related(i, active, routes_of)), None)
                if target is not None:
                    tx.transition(target.id, "detected", "recurrence")
                    result.reopened.append(target.public_id)
            if (
                target is None
                and open_incidents
                and (
                    tx.incidents_created_since(service.id, at - policy.rate_window)
                    >= policy.max_new_per_window
                )
            ):
                target = max(open_incidents, key=lambda i: i.detected_at)  # umbrella incident
            self._record(
                tx, service, target, active, evidence_fps=evidence_fps, at=at, result=result
            )
            result.signals += len(active)

        for incident in tx.open_incidents(service.id):
            if incident.last_signal_at < at - policy.resolve_after:
                tx.transition(incident.id, "resolved", "self_resolved")
                result.resolved.append(incident.public_id)

    def _record(
        self,
        tx: TelemetryTx,
        service: ServiceRef,
        target: IncidentRow | None,
        active: list[Signal],
        *,
        evidence_fps: list[FingerprintEvidence],
        at: datetime,
        result: TickResult,
    ) -> None:
        onset = estimate_onset(active, evidence_fps, at, self._policy.new_failure_window)
        if target is not None:
            onset = min(onset, target.first_seen)
        evidence = IncidentEvidence(
            onset=onset,
            signals=active,
            fingerprints=evidence_fps,
            changes=tx.changes(service.id, onset - self._policy.evidence_lookback, at),
            status_codes=tx.status_codes(service.id, onset - timedelta(minutes=1), at),
        )
        prior = classify(evidence)
        level = severity(active, self._tiers.get(service.name, {}))
        dominant = max(evidence_fps, key=lambda f: f.occurrences, default=None)
        routes = tuple(sorted({s.key for s in active if s.kind != "EXCEPTION_SPIKE"}
                              | {r for f in evidence_fps for r in f.routes}))[:20]  # fmt: skip
        title = _title(active, dominant)
        fingerprints = {
            f.fingerprint: (f.first_seen, f.first_seen, f.occurrences) for f in evidence_fps
        }

        if target is None:
            incident = tx.create_incident(NewIncident(
                service_id=service.id, severity=level, title=title,
                primary_fingerprint=dominant.fingerprint if dominant else None,
                routes=routes, first_seen=onset, prior=prior, at=at,
                investigate=level in self._policy.investigate_severities,
            ))  # fmt: skip
            result.opened.append(incident.public_id)
        else:
            incident = target
            if target.public_id not in result.reopened:
                result.attached.append(target.public_id)
            level = _worse(level, target.severity)  # severity only rises
            routes = tuple(sorted(set(routes) | set(target.routes)))[:20]
        inserted = tx.insert_signals(service.id, incident.id, active, at)  # 0 on a re-run
        tx.update_incident(incident, IncidentUpdate(
            severity=level, title=title, routes=routes, prior=prior, at=at,
            added_signals=inserted, fingerprints=fingerprints,
        ))  # fmt: skip
