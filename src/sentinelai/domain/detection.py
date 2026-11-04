"""Deterministic detection: window aggregates, baselines, rules, severity, classification.

Everything here is a pure function of its inputs, so detection is restart-safe and replayable:
evaluating stored windows at time ``t`` always yields the same signals (D2 section 6, ADR-004).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sentinelai.domain.telemetry import LogRecord

WINDOW_S = 10
LATENCY_BOUNDS_MS: tuple[float, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
BUCKETS = len(LATENCY_BOUNDS_MS) + 1  # final bucket: above the last bound

SignalKind = Literal["ERROR_RATE", "EXCEPTION_SPIKE", "LATENCY"]


def window_start(ts: datetime) -> datetime:
    epoch = int(ts.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch - epoch % WINDOW_S, UTC)


def latency_bucket(duration_ms: float) -> int:
    for index, bound in enumerate(LATENCY_BOUNDS_MS):
        if duration_ms <= bound:
            return index
    return len(LATENCY_BOUNDS_MS)


def percentile_ms(buckets: Sequence[int], q: float) -> float | None:
    """Upper bound of the bucket holding the q-quantile (conservative), or None if empty."""
    total = sum(buckets)
    if total == 0:
        return None
    rank = math.ceil(q * total)
    running = 0
    for index, count in enumerate(buckets):
        running += count
        if running >= rank:
            return LATENCY_BOUNDS_MS[index] if index < len(LATENCY_BOUNDS_MS) else math.inf
    return math.inf


def add_buckets(a: Sequence[int], b: Sequence[int]) -> list[int]:
    return [x + y for x, y in zip(a, b, strict=True)]


# --------------------------------------------------------------------------- aggregation


@dataclass
class RequestCounts:
    requests: int = 0
    errors_5xx: int = 0
    errors_4xx: int = 0
    latency: list[int] = field(default_factory=lambda: [0] * BUCKETS)

    def add(self, other: RequestCounts) -> None:
        self.requests += other.requests
        self.errors_5xx += other.errors_5xx
        self.errors_4xx += other.errors_4xx
        self.latency = add_buckets(self.latency, other.latency)


@dataclass
class FingerprintSeen:
    exception_type: str
    sample_message: str
    top_frames: tuple[str, ...]
    library_only: bool
    innermost_in_app: bool | None
    first_seen: datetime
    last_seen: datetime
    occurrences: int = 0
    routes: set[str] = field(default_factory=set)


@dataclass
class Aggregate:
    """Additive deltas from one batch of records (merged into storage with upserts)."""

    requests: dict[tuple[str, str, datetime], RequestCounts] = field(default_factory=dict)
    exceptions: Counter[tuple[str, str, datetime]] = field(default_factory=Counter)
    fingerprints: dict[tuple[str, str], FingerprintSeen] = field(default_factory=dict)


def aggregate(records: Iterable[LogRecord]) -> Aggregate:
    records = list(records)
    route_of_trace = {r.trace_id: r.http_route for r in records if r.trace_id and r.http_route}
    out = Aggregate()
    for record in records:
        start = window_start(record.ts)
        if record.http_route is not None and record.http_status is not None:
            status = record.http_status
            counts = out.requests.setdefault(
                (record.service, record.http_route, start), RequestCounts()
            )
            counts.requests += 1
            counts.errors_5xx += status >= 500
            counts.errors_4xx += 400 <= status < 500
            if record.duration_ms is not None:
                counts.latency[latency_bucket(record.duration_ms)] += 1
        if record.fingerprint is not None:
            fp = record.fingerprint.value
            out.exceptions[(record.service, fp, start)] += 1
            seen = out.fingerprints.get((record.service, fp))
            if seen is None:
                seen = FingerprintSeen(
                    exception_type=record.exception_type or "unknown",
                    sample_message=(record.exception_message or record.message)[:500],
                    top_frames=record.fingerprint.top_frames,
                    library_only=record.fingerprint.library_only,
                    innermost_in_app=record.innermost_in_app,
                    first_seen=record.ts,
                    last_seen=record.ts,
                )
                out.fingerprints[(record.service, fp)] = seen
            seen.first_seen = min(seen.first_seen, record.ts)
            seen.last_seen = max(seen.last_seen, record.ts)
            seen.occurrences += 1
            route = record.http_route or route_of_trace.get(record.trace_id or "")
            if route and len(seen.routes) < 20:
                seen.routes.add(route)
    return out


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class DetectionConfig:
    eval_interval_s: int = 10
    error_window_s: int = 30
    error_min_requests: int = 20
    error_min_ratio: float = 0.05
    error_sigma: float = 4.0
    exception_window_s: int = 120
    exception_min_count: int = 10
    new_fingerprint_s: int = 900
    latency_window_s: int = 30
    latency_min_requests: int = 50
    latency_factor: float = 2.0
    latency_min_increase_ms: float = 500
    baseline_window_s: int = 3600
    warmup_windows: int = 90  # 15 minutes of 10 s windows
    warmup_error_ratio: float = 0.005
    hysteresis: Mapping[str, int] = field(
        default_factory=lambda: {"ERROR_RATE": 2, "EXCEPTION_SPIKE": 2, "LATENCY": 3}
    )


@dataclass(frozen=True)
class RequestWindow:
    route: str
    start: datetime
    counts: RequestCounts


@dataclass(frozen=True)
class ExceptionWindow:
    fingerprint: str
    start: datetime
    occurrences: int


@dataclass(frozen=True)
class ServiceSnapshot:
    """Everything the rules need for one service, read from storage."""

    service: str
    requests: Sequence[RequestWindow]
    exceptions: Sequence[ExceptionWindow]
    first_seen: Mapping[str, datetime]
    excluded: Sequence[tuple[datetime, datetime]] = ()  # windows owned by open incidents


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    service: str
    key: str  # route or fingerprint
    window_start: datetime
    window_end: datetime
    observed: float
    baseline: float
    details: Mapping[str, float | int | str]

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind, self.service, self.key)


def _excluded(ts: datetime, ranges: Sequence[tuple[datetime, datetime]]) -> bool:
    return any(a <= ts < b for a, b in ranges)


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _error_rate(at: datetime, snap: ServiceSnapshot, cfg: DetectionConfig) -> list[Signal]:
    signals = []
    recent_start = at - timedelta(seconds=cfg.error_window_s)
    base_start = at - timedelta(seconds=cfg.baseline_window_s)
    by_route: dict[str, list[RequestWindow]] = {}
    for window in snap.requests:
        by_route.setdefault(window.route, []).append(window)
    for route, windows in by_route.items():
        recent = RequestCounts()
        baseline_ratios = []
        for w in windows:
            if recent_start <= w.start < at:
                recent.add(w.counts)
            elif (
                base_start <= w.start < recent_start
                and w.counts.requests > 0
                and not _excluded(w.start, snap.excluded)
            ):
                baseline_ratios.append(w.counts.errors_5xx / w.counts.requests)
        if recent.requests < cfg.error_min_requests:
            continue
        ratio = recent.errors_5xx / recent.requests
        if len(baseline_ratios) < cfg.warmup_windows:
            mean, std = cfg.warmup_error_ratio, 0.0
        else:
            mean, std = _mean_std(baseline_ratios)
        threshold = max(cfg.error_min_ratio, mean + cfg.error_sigma * std)
        if ratio > threshold:
            signals.append(Signal("ERROR_RATE", snap.service, route, recent_start, at, ratio, mean,
                                  {"requests": recent.requests, "errors_5xx": recent.errors_5xx,
                                   "threshold": round(threshold, 4)}))  # fmt: skip
    return signals


def _exception_spike(at: datetime, snap: ServiceSnapshot, cfg: DetectionConfig) -> list[Signal]:
    signals = []
    recent_start = at - timedelta(seconds=cfg.exception_window_s)
    base_start = at - timedelta(seconds=cfg.baseline_window_s)
    recent: Counter[str] = Counter()
    baseline: Counter[str] = Counter()
    for w in snap.exceptions:
        if recent_start <= w.start < at:
            recent[w.fingerprint] += w.occurrences
        elif base_start <= w.start < recent_start and not _excluded(w.start, snap.excluded):
            baseline[w.fingerprint] += w.occurrences
    base_windows = (cfg.baseline_window_s - cfg.exception_window_s) / cfg.exception_window_s
    for fp, count in recent.items():
        first = snap.first_seen.get(fp, at)
        is_new = first >= at - timedelta(seconds=cfg.new_fingerprint_s)
        expected = baseline[fp] / base_windows
        threshold = (
            cfg.exception_min_count
            if is_new
            else max(
                cfg.exception_min_count, expected + cfg.error_sigma * math.sqrt(max(expected, 1))
            )
        )  # Poisson-style spread for counts
        if count >= threshold:
            signals.append(Signal("EXCEPTION_SPIKE", snap.service, fp, recent_start, at, count,
                                  expected, {"new_fingerprint": int(is_new),
                                             "first_seen": first.isoformat()}))  # fmt: skip
    return signals


def _latency(at: datetime, snap: ServiceSnapshot, cfg: DetectionConfig) -> list[Signal]:
    signals = []
    recent_start = at - timedelta(seconds=cfg.latency_window_s)
    base_start = at - timedelta(seconds=cfg.baseline_window_s)
    recent: dict[str, RequestCounts] = {}
    base: dict[str, RequestCounts] = {}
    for w in snap.requests:
        if recent_start <= w.start < at:
            recent.setdefault(w.route, RequestCounts()).add(w.counts)
        elif base_start <= w.start < recent_start and not _excluded(w.start, snap.excluded):
            base.setdefault(w.route, RequestCounts()).add(w.counts)
    for route, counts in recent.items():
        if counts.requests < cfg.latency_min_requests or route not in base:
            continue
        p95, base_p95 = (
            percentile_ms(counts.latency, 0.95),
            percentile_ms(base[route].latency, 0.95),
        )
        if p95 is None or base_p95 is None or math.isinf(base_p95):
            continue
        threshold = max(cfg.latency_factor * base_p95, base_p95 + cfg.latency_min_increase_ms)
        if p95 > threshold:
            signals.append(Signal("LATENCY", snap.service, route, recent_start, at, p95, base_p95,
                                  {"requests": counts.requests}))  # fmt: skip
    return signals


def evaluate_once(at: datetime, snap: ServiceSnapshot, cfg: DetectionConfig) -> list[Signal]:
    return [*_error_rate(at, snap, cfg), *_exception_spike(at, snap, cfg), *_latency(at, snap, cfg)]


def evaluate(at: datetime, snap: ServiceSnapshot, cfg: DetectionConfig) -> list[Signal]:
    """Signals whose condition held on N consecutive evaluations ending at ``at`` (hysteresis).

    Stateless: earlier evaluations are recomputed from the same stored windows, so a restarted
    detector reaches exactly the same decision.
    """
    current = evaluate_once(at, snap, cfg)
    confirmed = []
    for signal in current:
        needed = cfg.hysteresis.get(signal.kind, 2)
        held = all(
            any(s.identity == signal.identity
                for s in evaluate_once(at - timedelta(seconds=cfg.eval_interval_s * k), snap, cfg))
            for k in range(1, needed)
        )  # fmt: skip
        if held:
            confirmed.append(signal)
    return confirmed


# --------------------------------------------------------------------------- severity


def severity(
    signals: Sequence[Signal], route_tiers: Mapping[str, int], default_tier: int = 2
) -> str:
    """D2 section 6.4: the highest applicable level (sev1 most severe)."""
    level = 4
    for s in signals:
        tier = route_tiers.get(s.key, default_tier) if s.kind != "EXCEPTION_SPIKE" else default_tier
        if s.kind == "ERROR_RATE":
            if tier == 0 and s.observed >= 0.5:
                level = min(level, 1)
            elif (tier <= 1 and s.observed >= 0.10) or s.observed >= 0.25:
                level = min(level, 2)
            elif s.observed >= 0.05:
                level = min(level, 3)
        elif (
            s.kind == "EXCEPTION_SPIKE" and s.details.get("new_fingerprint")
        ) or s.kind == "LATENCY":
            level = min(level, 3)
    return f"sev{level}"


# --------------------------------------------------------------------------- classification

_INFRA_PATTERN = re.compile(
    r"(?i)operationalerror|interfaceerror|connectionrefused|connection refused|could not connect|"
    r"connect(ion)?error|econnrefused|econnreset|connection reset|no route to host|"
    r"name or service not known|getaddrinfo|dns|certificate|ssl|pooltimeout|too many connections|"
    r"server closed the connection|broken pipe|ehostunreach"
)
_DEPENDENCY_PATTERN = re.compile(
    r"(?i)timeout|timed out|unavailable|bad gateway|gateway|upstream|circuit|readtimeout"
)
_CONNECTION_KEYS = re.compile(
    r"(?i)database|db_|_db|dsn|host|url|endpoint|address|port|redis|broker"
)


@dataclass(frozen=True)
class ChangeEvent:
    kind: Literal["deploy", "config_change", "rollback", "scale"]
    version: str
    occurred_at: datetime
    changed_keys: tuple[str, ...] = ()
    ref: str | None = None  # storage id of the release event, if any


@dataclass(frozen=True)
class FingerprintEvidence:
    fingerprint: str
    exception_type: str
    message: str
    innermost_in_app: bool | None
    library_only: bool
    first_seen: datetime
    occurrences: int
    routes: tuple[str, ...]


@dataclass(frozen=True)
class IncidentEvidence:
    onset: datetime
    signals: Sequence[Signal]
    fingerprints: Sequence[FingerprintEvidence]
    changes: Sequence[ChangeEvent]
    status_codes: Mapping[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassPrior:
    incident_class: str
    code_patch_candidate: bool
    rule: str
    reasons: tuple[str, ...]
    suspected_change: ChangeEvent | None = None


CORRELATION_WINDOW = timedelta(minutes=30)


def classify(evidence: IncidentEvidence) -> ClassPrior:  # noqa: PLR0911 - one return per rule
    """Deterministic triage prior (D2 section 8.11). The investigator may override it only
    by citing evidence; both values are kept."""
    fps = sorted(evidence.fingerprints, key=lambda f: -f.occurrences)
    dominant = fps[0] if fps else None
    window_open = evidence.onset - CORRELATION_WINDOW
    recent = [c for c in evidence.changes if window_open <= c.occurred_at <= evidence.onset]
    latest = max(recent, key=lambda c: c.occurred_at, default=None)
    routes = {r for f in fps for r in f.routes} | {
        s.key for s in evidence.signals if s.kind == "ERROR_RATE"
    }
    reasons: list[str] = []

    infra = [f for f in fps if _INFRA_PATTERN.search(f"{f.exception_type} {f.message}")
             and f.innermost_in_app is not True]  # fmt: skip
    if infra and (len(routes) >= 2 or len(infra) == len(fps)):
        reasons.append(f"{infra[0].exception_type} raised inside a library/driver, not app code")
        reasons.append(f"connection-type failure across {len(routes)} route(s)")
        config = [c for c in recent if c.kind == "config_change"
                  and any(_CONNECTION_KEYS.search(k) for k in c.changed_keys)]  # fmt: skip
        if config:
            reasons.append(
                f"connection setting changed before onset: {', '.join(config[-1].changed_keys)}"
            )
            return ClassPrior("configuration", False, "infra_after_connection_config_change",
                              tuple(reasons), config[-1])  # fmt: skip
        reasons.append("no connection-related change precedes the onset")
        return ClassPrior("infrastructure", False, "connection_failure", tuple(reasons))

    if latest is not None and latest.kind in ("deploy", "rollback") and dominant is not None:
        after = dominant.first_seen >= latest.occurred_at
        if dominant.innermost_in_app and after:
            minutes = (dominant.first_seen - latest.occurred_at).total_seconds() / 60
            reasons += [
                f"{dominant.exception_type} first seen {minutes:.1f} min "
                f"after deploy {latest.version}",
                "the exception is raised in application code (innermost frame in-app)",
            ]
            return ClassPrior("regression_from_release", True, "release_correlated_app_exception",
                              tuple(reasons), latest)  # fmt: skip

    if latest is not None and latest.kind == "config_change":
        minutes = (evidence.onset - latest.occurred_at).total_seconds() / 60
        keys = ", ".join(latest.changed_keys) or "unknown"
        reasons.append(f"configuration changed {minutes:.1f} min before onset (keys: {keys})")
        reasons.append("it is the most recent change before the onset; no deploy is closer")
        return ClassPrior(
            "configuration", False, "config_change_precedes_onset", tuple(reasons), latest
        )

    statuses = evidence.status_codes
    total_5xx = sum(n for code, n in statuses.items() if code >= 500)
    gateway = statuses.get(502, 0) + statuses.get(504, 0)
    mostly_gateway = total_5xx == 0 or gateway * 2 >= total_5xx
    if (
        dominant is not None
        and _DEPENDENCY_PATTERN.search(f"{dominant.exception_type} {dominant.message}")
        and mostly_gateway
    ):
        reasons.append(f"{dominant.exception_type}: a downstream call timed out or was unavailable")
        return ClassPrior("external_dependency", False, "downstream_failure", tuple(reasons))

    if dominant is not None and dominant.innermost_in_app:
        reasons.append(f"{dominant.exception_type} raised in application code; no recent change")
        return ClassPrior("code_defect", True, "app_exception_without_change", tuple(reasons))

    if evidence.signals and all(s.kind == "LATENCY" for s in evidence.signals):
        return ClassPrior("performance", False, "latency_only", ("latency rose without errors",))
    return ClassPrior("unknown", False, "insufficient_evidence",
                      ("no rule matched the available evidence",))  # fmt: skip
