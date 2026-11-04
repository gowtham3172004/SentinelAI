from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sentinelai.domain.detection import FingerprintEvidence, Signal
from sentinelai.services.incidents import _title, estimate_onset

AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=15)


def signal(kind: str, key: str, lookback_s: int) -> Signal:
    return Signal(kind, "svc", key, AT - timedelta(seconds=lookback_s), AT, 1.0, 0.0, {})  # type: ignore[arg-type]


def fp(first_seen: datetime, routes: tuple[str, ...] = ("/pay",)) -> FingerprintEvidence:
    return FingerprintEvidence("f", "TimeoutError", "m", True, False, first_seen, 10, routes)


def test_onset_is_the_first_new_failure_not_the_lookback_start() -> None:
    first_failure = AT - timedelta(seconds=40)
    onset = estimate_onset([signal("EXCEPTION_SPIKE", "f", 120)], [fp(first_failure)], AT, WINDOW)
    assert onset == first_failure  # not AT - 120 s


def test_old_recurring_fingerprints_do_not_drag_the_onset_back() -> None:
    ancient = fp(AT - timedelta(days=3))
    onset = estimate_onset([signal("ERROR_RATE", "/pay", 30)], [ancient], AT, WINDOW)
    assert onset == AT - timedelta(seconds=30)


def test_onset_fallback_without_any_evidence() -> None:
    assert estimate_onset([], [], AT, WINDOW) == AT - timedelta(seconds=30)


def test_title_uses_the_failing_requests_route_when_only_an_exception_rule_fired() -> None:
    title = _title([signal("EXCEPTION_SPIKE", "f", 120)], fp(AT, ("/orders/{order_id}/pay",)))
    assert title == "TimeoutError on /orders/{order_id}/pay"
    assert _title([signal("ERROR_RATE", "/orders", 30)], None) == "5xx on /orders (100%)"
