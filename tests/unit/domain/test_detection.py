from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sentinelai.domain.detection import (
    ChangeEvent,
    DetectionConfig,
    ExceptionWindow,
    FingerprintEvidence,
    IncidentEvidence,
    RequestCounts,
    RequestWindow,
    ServiceSnapshot,
    Signal,
    aggregate,
    classify,
    evaluate,
    evaluate_once,
    latency_bucket,
    percentile_ms,
    severity,
    window_start,
)
from sentinelai.domain.telemetry import FieldMapping, SourceDefaults, normalise

AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
CFG = DetectionConfig()


def windows(route: str, start: datetime, end: datetime, requests: int, errors: int,
            latency_ms: float = 20) -> list[RequestWindow]:  # fmt: skip
    out, t = [], start
    while t < end:
        counts = RequestCounts(requests=requests, errors_5xx=errors)
        counts.latency[latency_bucket(latency_ms)] = requests
        out.append(RequestWindow(route, t, counts))
        t += timedelta(seconds=10)
    return out


def healthy_hour(
    route: str = "/orders", until: datetime = AT - timedelta(seconds=60)
) -> list[RequestWindow]:
    return windows(route, until - timedelta(hours=1), until, 10, 0)


def snap(
    requests: list[RequestWindow],
    exceptions: Sequence[ExceptionWindow] = (),
    first_seen: dict[str, datetime] | None = None,
    excluded: Sequence[tuple[datetime, datetime]] = (),
) -> ServiceSnapshot:
    return ServiceSnapshot("shop-api", requests, list(exceptions), first_seen or {}, excluded)


# --------------------------------------------------------------------------- windows


def test_window_start_and_histogram() -> None:
    assert window_start(AT + timedelta(seconds=17, microseconds=5)) == AT + timedelta(seconds=10)
    buckets = [0] * 12
    for ms in [5] * 90 + [800] * 10:
        buckets[latency_bucket(ms)] += 1
    assert percentile_ms(buckets, 0.5) == 5
    assert percentile_ms(buckets, 0.95) == 1000
    assert percentile_ms([0] * 12, 0.95) is None


# --------------------------------------------------------------------------- ERROR_RATE


@pytest.mark.at("AT-DET-01")
def test_error_rate_fires_on_a_sustained_rise() -> None:
    incident = windows("/orders", AT - timedelta(seconds=60), AT, 10, 3)
    signals = evaluate(AT, snap(healthy_hour() + incident), CFG)
    assert [(s.kind, s.key) for s in signals] == [("ERROR_RATE", "/orders")]
    assert signals[0].observed == pytest.approx(0.3)


@pytest.mark.at("AT-DET-01")
def test_quiet_traffic_and_low_volume_stay_silent() -> None:
    assert (
        evaluate(
            AT,
            snap(healthy_hour() + windows("/orders", AT - timedelta(seconds=60), AT, 10, 0)),
            CFG,
        )
        == []
    )
    tiny = windows(
        "/orders", AT - timedelta(seconds=60), AT, 2, 2
    )  # 100% errors, 6 requests per 30 s
    assert evaluate(AT, snap(healthy_hour() + tiny), CFG) == []


@pytest.mark.at("AT-DET-01")
def test_hysteresis_ignores_a_single_blip() -> None:
    blip = windows("/orders", AT - timedelta(seconds=30), AT, 10, 3)  # only the last 30 s
    data = healthy_hour(until=AT - timedelta(seconds=30)) + blip
    assert evaluate_once(AT, snap(data), CFG)  # condition holds once
    assert evaluate(AT - timedelta(seconds=20), snap(data), CFG) == []


@pytest.mark.at("AT-DET-03")
def test_baseline_excludes_windows_of_open_incidents() -> None:
    """A long-running incident must not become the new normal."""
    bad_since = AT - timedelta(minutes=50)
    history = healthy_hour(until=bad_since) + windows("/orders", bad_since, AT, 10, 2)
    excluded = [(bad_since, AT)]
    assert evaluate(AT, snap(history, excluded=excluded), CFG)  # still detected at 20%
    # Without exclusion the incident would have been absorbed into the baseline by now.
    assert evaluate(AT, snap(history), CFG) == []


def test_chronic_error_rate_is_the_baseline_not_an_incident() -> None:
    chronic = windows("/orders", AT - timedelta(hours=2), AT, 10, 2)  # flat 20% for two hours
    assert evaluate(AT, snap(chronic), CFG) == []


# --------------------------------------------------------------------------- EXCEPTION_SPIKE


@pytest.mark.at("AT-DET-01")
def test_new_fingerprint_spike() -> None:
    exc = [ExceptionWindow("fp1", AT - timedelta(seconds=s), 3) for s in (10, 20, 30, 40, 50)]
    first = {"fp1": AT - timedelta(seconds=50)}
    signals = evaluate(AT, snap(healthy_hour(), exc, first), CFG)
    assert [(s.kind, s.key, s.details["new_fingerprint"]) for s in signals] == [
        ("EXCEPTION_SPIKE", "fp1", 1)
    ]


def test_old_fingerprint_at_its_usual_rate_is_noise() -> None:
    usual = [ExceptionWindow("fpX", AT - timedelta(seconds=s), 1) for s in range(10, 3600, 10)]
    first = {"fpX": AT - timedelta(days=3)}
    assert evaluate(AT, snap(healthy_hour(), usual, first), CFG) == []


# --------------------------------------------------------------------------- LATENCY


def test_latency_needs_three_consecutive_evaluations() -> None:
    base = windows("/orders", AT - timedelta(hours=1), AT - timedelta(seconds=60), 20, 0, 20)
    slow = windows("/orders", AT - timedelta(seconds=60), AT, 20, 0, 3000)
    assert [s.kind for s in evaluate(AT, snap(base + slow), CFG)] == ["LATENCY"]
    short = windows("/orders", AT - timedelta(seconds=20), AT, 20, 0, 3000)  # a 20 s blip
    base_short = windows("/orders", AT - timedelta(hours=1), AT - timedelta(seconds=20), 20, 0, 20)
    assert evaluate(AT, snap(base_short + short), CFG) == []


# --------------------------------------------------------------------------- severity


def sig(kind: str, key: str, observed: float, **details: int) -> Signal:
    return Signal(kind, "shop-api", key, AT, AT, observed, 0.0, details)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("signals", "tiers", "expected"),
    [
        ([sig("ERROR_RATE", "/orders", 0.6)], {"/orders": 0}, "sev1"),
        ([sig("ERROR_RATE", "/orders", 0.2)], {"/orders": 0}, "sev2"),
        ([sig("ERROR_RATE", "/report", 0.3)], {}, "sev2"),
        ([sig("ERROR_RATE", "/report", 0.07)], {}, "sev3"),
        ([sig("EXCEPTION_SPIKE", "fp", 12, new_fingerprint=1)], {}, "sev3"),
        ([sig("EXCEPTION_SPIKE", "fp", 40, new_fingerprint=0)], {}, "sev4"),
    ],
)
def test_severity(signals: list[Signal], tiers: dict[str, int], expected: str) -> None:
    assert severity(signals, tiers) == expected


# --------------------------------------------------------------------------- classification


def fp(exc_type: str, message: str, in_app: bool | None, first_seen: datetime,
       routes: tuple[str, ...] = ("/orders",)) -> FingerprintEvidence:  # fmt: skip
    return FingerprintEvidence(
        "f", exc_type, message, in_app, in_app is False, first_seen, 30, routes
    )


ONSET = AT - timedelta(minutes=2)
OLD_DEPLOY = ChangeEvent("deploy", "v1.0.0", AT - timedelta(hours=3))


@pytest.mark.at("AT-DET-04")
def test_s01_shape_is_a_release_regression_and_a_code_candidate() -> None:
    deploy = ChangeEvent("deploy", "v1.1.0", AT - timedelta(minutes=5))
    prior = classify(IncidentEvidence(
        ONSET, [sig("ERROR_RATE", "/orders", 0.25)],
        [fp("AttributeError", "'NoneType' object has no attribute 'strip'", True, ONSET)],
        [OLD_DEPLOY, deploy], {500: 30},
    ))  # fmt: skip
    assert (prior.incident_class, prior.code_patch_candidate) == ("regression_from_release", True)
    assert prior.suspected_change == deploy


@pytest.mark.at("AT-DET-04")
def test_s22_shape_is_configuration_even_though_raised_in_app_code() -> None:
    change = ChangeEvent("config_change", "v1.0.0", AT - timedelta(minutes=3),
                         ("SHOP_PAYMENT_TIMEOUT_S",))  # fmt: skip
    prior = classify(IncidentEvidence(
        ONSET, [sig("ERROR_RATE", "/orders/{order_id}/pay", 1.0)],
        [fp("shop.payments.PaymentTimeoutError", "payment service did not respond within 0.001s",
            True, ONSET, ("/orders/{order_id}/pay",))],
        [OLD_DEPLOY, change], {504: 40},
    ))  # fmt: skip
    assert (prior.incident_class, prior.code_patch_candidate) == ("configuration", False)
    assert "SHOP_PAYMENT_TIMEOUT_S" in " ".join(prior.reasons)


@pytest.mark.at("AT-DET-04")
def test_s33_shape_is_infrastructure() -> None:
    prior = classify(IncidentEvidence(
        ONSET, [sig("ERROR_RATE", "/orders", 1.0), sig("ERROR_RATE", "/orders/{order_id}", 1.0)],
        [fp("sqlalchemy.exc.OperationalError", "connection refused", False, ONSET,
            ("/orders", "/orders/{order_id}"))],
        [OLD_DEPLOY], {503: 80},
    ))  # fmt: skip
    assert (prior.incident_class, prior.code_patch_candidate) == ("infrastructure", False)


def test_database_url_change_turns_infrastructure_into_configuration() -> None:
    change = ChangeEvent("config_change", "v1", AT - timedelta(minutes=4), ("SHOP_DATABASE_URL",))
    prior = classify(IncidentEvidence(
        ONSET, [], [fp("OperationalError", "could not connect", False, ONSET, ("/a", "/b"))],
        [change], {503: 10},
    ))  # fmt: skip
    assert prior.incident_class == "configuration"


def test_app_exception_long_after_the_last_deploy_is_a_latent_code_defect() -> None:
    prior = classify(
        IncidentEvidence(ONSET, [], [fp("KeyError", "'sku'", True, ONSET)], [OLD_DEPLOY])
    )
    assert (prior.incident_class, prior.code_patch_candidate) == ("code_defect", True)


def test_downstream_timeouts_without_change_are_external_dependency() -> None:
    prior = classify(IncidentEvidence(
        ONSET, [], [fp("ReadTimeout", "upstream timed out", True, ONSET)], [], {504: 20},
    ))  # fmt: skip
    assert prior.incident_class == "external_dependency"


def test_latency_only_and_no_evidence() -> None:
    assert (
        classify(IncidentEvidence(ONSET, [sig("LATENCY", "/x", 900)], [], [])).incident_class
        == "performance"
    )
    assert classify(IncidentEvidence(ONSET, [], [], [])).incident_class == "unknown"


def test_aggregate_links_exceptions_to_routes_through_trace_ids() -> None:
    mapping, defaults = FieldMapping(), SourceDefaults(service="shop-api", source_roots=("shop",))
    raw: list[dict[str, Any]] = [
        {"ts": "2026-11-04T12:00:01Z", "message": "boom", "trace_id": "t1",
         "exception.type": "ValueError", "exception.message": "x"},
        {"ts": "2026-11-04T12:00:01Z", "message": "POST /orders 500", "trace_id": "t1",
         "http.route": "/orders", "http.status_code": 500, "duration_ms": 12.0},
        {"ts": "2026-11-04T12:00:02Z", "message": "GET /orders 200",
         "http.route": "/orders", "http.status_code": 200, "duration_ms": 3.0},
    ]  # fmt: skip
    agg = aggregate(normalise(r, mapping=mapping, defaults=defaults, received=AT) for r in raw)
    counts = agg.requests[("shop-api", "/orders", AT)]
    assert (counts.requests, counts.errors_5xx) == (2, 1)
    seen = next(iter(agg.fingerprints.values()))
    assert seen.routes == {"/orders"}
    assert sum(agg.exceptions.values()) == 1
