"""AT-INC-03 and migration 0002 invariants: incident status only via incident_transition()."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors

from sentinelai.adapters.db.authority import load_matrix
from sentinelai.adapters.db.session import verify_chain
from tests.db.conftest import SeededTenant, scoped

TRANSITION = "SELECT incident_transition(%s, %s, %s)"


def status(admin: psycopg.Connection, incident: object) -> str:
    row = admin.execute("SELECT status FROM incidents WHERE id = %s", (incident,)).fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.at("AT-INC-03")
@pytest.mark.parametrize(
    ("role", "path", "allowed"),
    [
        ("r_orch", ["triaging", "investigating", "awaiting_human"], True),
        ("r_telemetry", ["resolved", "detected"], True),  # self-resolution, then recurrence
        ("r_api", ["suppressed", "detected"], True),
        ("r_telemetry", ["triaging"], False),  # only the orchestrator starts triage
        ("r_orch", ["resolved"], False),  # only detection declares resolution
        ("r_orch", ["closed"], False),
        ("r_api", ["triaging"], False),
    ],
)
def test_transitions_follow_the_lifecycle_per_role(
    admin: psycopg.Connection,
    two_tenants: tuple[SeededTenant, SeededTenant],
    role: str,
    path: list[str],
    allowed: bool,
) -> None:
    a = two_tenants[0]
    for target in path:
        if allowed:
            with scoped(admin, role, a.id):
                admin.execute(TRANSITION, (a.incident, target, "test_step"))
            assert status(admin, a.incident) == target
        else:
            with (
                pytest.raises(errors.InsufficientPrivilege, match="not allowed"),
                scoped(admin, role, a.id),
            ):
                admin.execute(TRANSITION, (a.incident, target, "test_step"))
            assert status(admin, a.incident) == "detected"


@pytest.mark.at("AT-INC-03")
def test_full_lifecycle_counts_reopens_and_sets_timestamps(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a = two_tenants[0]
    steps = [("r_orch", "triaging"), ("r_orch", "investigating"), ("r_orch", "awaiting_human"),
             ("r_api", "mitigating"), ("r_telemetry", "resolved"), ("r_telemetry", "detected"),
             ("r_telemetry", "resolved"), ("r_api", "closed")]  # fmt: skip
    for role, target in steps:
        with scoped(admin, role, a.id):
            admin.execute(TRANSITION, (a.incident, target, "step"))
    row = admin.execute(
        "SELECT status, reopened_count, resolved_at IS NOT NULL, closed_at IS NOT NULL "
        "FROM incidents WHERE id = %s", (a.incident,)
    ).fetchone()  # fmt: skip
    assert row == ("closed", 1, True, True)


def test_transitions_are_idempotent_audited_and_announced(
    db_url: str, admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a = two_tenants[0]
    with scoped(admin, "r_orch", a.id):
        admin.execute(TRANSITION, (a.incident, "triaging", "claimed"))
        admin.execute(TRANSITION, (a.incident, "triaging", "claimed"))  # retry: no-op
    audit = admin.execute(
        "SELECT actor_id, action, payload FROM audit_log WHERE chain_id = %s", (a.id,)
    ).fetchall()
    assert audit == [("r_orch", "incident.transition",
                      {"from": "detected", "to": "triaging", "reason": "claimed"})]  # fmt: skip
    assert verify_chain(admin, a.id).ok
    events = admin.execute(
        "SELECT event_type, payload FROM outbox_events WHERE aggregate_id = %s", (str(a.incident),)
    ).fetchall()
    assert events == [("incident.updated", {"status": "triaging", "reason": "claimed"})]


def test_reason_must_be_a_short_code_not_free_text(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a = two_tenants[0]
    with pytest.raises(errors.InvalidParameterValue), scoped(admin, "r_orch", a.id):
        admin.execute(TRANSITION, (a.incident, "triaging", "customer jane@x.com said so"))


@pytest.mark.at("AT-TEN-01")
def test_cannot_transition_another_tenants_incident(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    with pytest.raises(errors.NoDataFound, match="not found"), scoped(admin, "r_orch", a.id):
        admin.execute(TRANSITION, (b.incident, "triaging", "x"))
    assert status(admin, b.incident) == "detected"


def test_telemetry_cannot_set_status_directly_but_can_update_counters(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a = two_tenants[0]
    with pytest.raises(errors.InsufficientPrivilege), scoped(admin, "r_telemetry", a.id):
        admin.execute("UPDATE incidents SET status = 'closed'")
    with scoped(admin, "r_telemetry", a.id):
        admin.execute("UPDATE incidents SET signal_count = signal_count + 1, severity = 'sev2'")
    columns = load_matrix().column_updates("r_telemetry", "incidents")
    assert "status" not in columns
    for column in columns:
        row = admin.execute(
            "SELECT has_column_privilege('r_telemetry', 'incidents', %s, 'UPDATE')", (column,)
        ).fetchone()
        assert row == (True,), column
    denied = admin.execute(
        "SELECT has_column_privilege('r_telemetry', 'incidents', 'status', 'UPDATE')"
    ).fetchone()
    assert denied == (False,)


def test_add_int_arrays_and_retention_purge(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    assert admin.execute("SELECT add_int_arrays('{1,2,3}', '{10,20,30}')").fetchone() == (
        [11, 22, 33],
    )
    with pytest.raises(errors.InsufficientPrivilege), scoped(admin, "r_telemetry", None):
        admin.execute("SELECT retention_purge(now() + interval '1 day')")
    with scoped(admin, "r_maint", None):
        purged = admin.execute("SELECT retention_purge(now() + interval '1 day')").fetchone()
    assert purged is not None
    assert purged[0] >= 2 * 4  # both tenants: log, request window, exception window, dead letter
    remaining = admin.execute("SELECT count(*) FROM log_events").fetchone()
    assert remaining == (0,)
