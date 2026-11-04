from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinelai.domain.telemetry import (
    FieldMapping,
    RejectedRecord,
    SourceDefaults,
    fingerprint,
    mask_message,
    normalise,
    normalise_level,
    parse_stack,
    parse_timestamp,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
ROOTS = SourceDefaults(service="shop-api", source_roots=("shop",), app_roots=("/app",))

PY_STACK = """Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/starlette/routing.py", line 73, in app
    response = await f(request)
  File "/app/shop/api.py", line 110, in create_order
    order = service.create_order(session, request)
  File "/app/shop/orders/service.py", line {line}, in create_order
    customer_note=normalize_customer_note(request.customer_note),
  File "/app/shop/orders/normalize.py", line 10, in normalize_customer_note
    cleaned = " ".join(note.strip().split())[:MAX_NOTE_LENGTH]
AttributeError: 'NoneType' object has no attribute 'strip'
"""

JAVA_STACK = """java.lang.NullPointerException: Cannot invoke "String.trim()" because "note" is null
\tat com.acme.shop.orders.NoteNormalizer.normalize(NoteNormalizer.java:17)
\tat com.acme.shop.orders.OrderService.create(OrderService.java:42)
\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:897)
"""

JS_STACK = """TypeError: Cannot read properties of undefined (reading 'trim')
    at normalizeNote (/srv/app/src/orders/normalize.js:12:21)
    at createOrder (/srv/app/src/orders/service.js:40:9)
    at Layer.handle (/srv/app/node_modules/express/lib/router/layer.js:95:5)
"""


def test_python_stack_frames_and_ownership() -> None:
    frames = parse_stack(PY_STACK.format(line=47), source_roots=("shop",), app_roots=("/app",))
    assert [f.function for f in frames][-1] == "normalize_customer_note"
    assert frames[-1].path == "shop/orders/normalize.py"
    assert frames[-1].in_app
    assert not frames[0].in_app  # starlette in site-packages


def test_jvm_stack_is_reordered_outermost_first() -> None:
    frames = parse_stack(JAVA_STACK, source_roots=("com/acme",))
    assert frames[-1].function == "normalize"
    assert frames[-1].path == "com/acme/shop/orders/NoteNormalizer.java"
    assert frames[-1].in_app
    assert not frames[0].in_app  # spring


def test_js_stack_is_reordered_and_node_modules_are_libraries() -> None:
    frames = parse_stack(JS_STACK, source_roots=("src",), app_roots=("/srv/app",))
    assert (frames[-1].function, frames[-1].path) == ("normalizeNote", "src/orders/normalize.js")
    assert frames[-1].in_app
    assert not frames[0].in_app


def test_no_stack_gives_no_frames() -> None:
    assert parse_stack("just a message") == []


@pytest.mark.at("AT-FP-01")
@given(
    line=st.integers(1, 5000),
    order_id=st.integers(1, 10**9),
    uid=st.uuids(),
)
def test_fingerprint_stable_under_lines_ids_and_values(
    line: int, order_id: int, uid: object
) -> None:
    base_frames = parse_stack(PY_STACK.format(line=47), source_roots=("shop",), app_roots=("/app",))
    moved = parse_stack(PY_STACK.format(line=line), source_roots=("shop",), app_roots=("/app",))
    a = fingerprint(
        "KeyError", "order 17 missing key 'x' for 1b4e28ba-2fa1-11d2-883f-0016d3cca427", base_frames
    )
    b = fingerprint("KeyError", f"order {order_id} missing key 'y' for {uid}", moved)
    assert a.value == b.value


@pytest.mark.at("AT-FP-02")
def test_distinct_bugs_get_distinct_fingerprints() -> None:
    frames = parse_stack(PY_STACK.format(line=47), source_roots=("shop",), app_roots=("/app",))
    values = {
        fingerprint("AttributeError", "x", frames).value,
        fingerprint("TypeError", "x", frames).value,
        fingerprint("AttributeError", "different shape of message", frames).value,
        fingerprint("AttributeError", "x", frames[:-1]).value,
    }
    assert len(values) == 4


@pytest.mark.at("AT-FP-03")
def test_library_only_flag() -> None:
    frames = parse_stack(
        'File "/usr/lib/python3.12/site-packages/psycopg/connection.py", line 9, in connect',
        source_roots=("shop",),
    )
    assert fingerprint("OperationalError", "connection refused", frames).library_only
    assert not fingerprint("ValueError", "x", []).library_only  # no frames: unknown, not library


def test_mask_message() -> None:
    assert (
        mask_message("order 42 for 'bob' id 0xdeadbeef at 3.5s")
        == "order <n> for <str> id <hex> at <n>s"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("ERROR", "error"), ("Warning", "warn"), (50, "error"), (30, "info"), (40, "warn"),
     ("critical", "fatal"), ("nonsense", "info")],
)  # fmt: skip
def test_levels(value: object, expected: str) -> None:
    assert normalise_level(value) == expected


@pytest.mark.parametrize(
    "value",
    ["2026-11-04T12:00:00Z", "2026-11-04T12:00:00+00:00", 1793793600, 1793793600000,
     1793793600000000, 1793793600000000000],
)  # fmt: skip
def test_timestamps(value: object) -> None:
    assert parse_timestamp(value) == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    [
        # OpenTelemetry semantic conventions (the demo)
        {"ts": "2026-11-04T12:00:00Z", "level": "error", "message": "unhandled error",
         "service.name": "shop-api", "exception.type": "AttributeError",
         "exception.message": "boom", "exception.stacktrace": PY_STACK.format(line=1)},
        # ECS / Elastic style
        {"@timestamp": "2026-11-04T12:00:00Z", "log": {"level": "ERROR"},
         "message": "unhandled error",
         "service": {"name": "shop-api"}, "error": {"type": "AttributeError", "message": "boom",
                                                    "stack_trace": PY_STACK.format(line=1)}},
        # pino / bunyan style
        {"time": 1793793600000, "level": 50, "msg": "unhandled error", "app": "shop-api",
         "err": {"type": "AttributeError", "message": "boom", "stack": PY_STACK.format(line=1)}},
    ],
)  # fmt: skip
def test_normalise_reads_common_dialects(raw: dict[str, object]) -> None:
    record = normalise(raw, mapping=FieldMapping(), defaults=ROOTS, received=NOW)
    assert record.ts == NOW
    assert (record.service, record.level, record.exception_type) == (
        "shop-api",
        "error",
        "AttributeError",
    )
    assert record.fingerprint is not None
    assert record.frames[-1].function == "normalize_customer_note"


def test_normalise_access_log_and_redaction() -> None:
    raw = {"time": "2026-11-04T12:00:00Z", "msg": "POST /orders 500 token=abcdef123",
           "http.method": "POST", "http.route": "/orders", "http.status_code": 500,
           "duration": 0.25, "user_email": "a@b.co", "service.name": "shop-api"}  # fmt: skip
    record = normalise(raw, mapping=FieldMapping(), defaults=ROOTS, received=NOW)
    assert (record.http_route, record.http_status, record.duration_ms) == ("/orders", 500, 250.0)
    assert "abcdef123" not in record.message
    assert record.attributes["user_email"] == "[REDACTED:email]"


def test_record_without_service_is_rejected() -> None:
    with pytest.raises(RejectedRecord):
        normalise({"msg": "x"}, mapping=FieldMapping(), defaults=SourceDefaults(), received=NOW)
