"""Telemetry ingestion, detection windows, fingerprints and incidents.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04

Milestone 2. Same security discipline as 0001:
- Every new table is tenant-scoped with RLS ENABLED + FORCED (fail-closed current_tenant()),
  except incident_transition_rules (platform configuration, read only by its function).
- Composite (tenant_id, id) foreign keys: no cross-tenant references.
- Idempotent ingestion: a source cursor or a batch id commits in the same transaction as the
  additive window counts, so at-least-once delivery never double-counts.
- Incident status changes only through incident_transition() (SECURITY DEFINER, lifecycle
  table per role, audited, outbox event). The telemetry role has column-level UPDATE on
  incidents that excludes `status`.
Grants are frozen output of `sentinelai db grants-sql` for authority.yaml v2.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TENANT_TABLES = (
    "ingest_sources", "ingest_batches", "dead_letters", "log_events", "request_windows",
    "exception_windows", "fingerprints", "incidents", "incident_signals",
    "incident_fingerprints", "noise_rules",
)  # fmt: skip

TABLES_SQL = (
    "CREATE SEQUENCE incident_seq START 1000",
    "ALTER TABLE release_events ADD CONSTRAINT release_events_tenant_id_key UNIQUE (tenant_id, id)",
    """
CREATE TABLE ingest_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'),
  kind text NOT NULL CHECK (kind IN ('file', 'http', 'otlp', 'events')),
  cursor jsonb NOT NULL DEFAULT '{}',
  records bigint NOT NULL DEFAULT 0, rejected bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name), UNIQUE (tenant_id, id))
""",
    """
CREATE TABLE ingest_batches (
  tenant_id uuid NOT NULL, source_id uuid NOT NULL,
  batch_id text NOT NULL CHECK (length(batch_id) BETWEEN 1 AND 128),
  records int NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, source_id, batch_id),
  FOREIGN KEY (tenant_id, source_id) REFERENCES ingest_sources (tenant_id, id))
""",
    """
CREATE TABLE dead_letters (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, source_id uuid NOT NULL,
  reason text NOT NULL, raw text NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, source_id) REFERENCES ingest_sources (tenant_id, id))
""",
    "CREATE INDEX dead_letters_recent ON dead_letters (tenant_id, received_at)",
    """
CREATE TABLE log_events (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, service_id uuid NOT NULL,
  ts timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
  level text NOT NULL CHECK (level IN ('debug', 'info', 'warn', 'error', 'fatal')),
  logger text, message text NOT NULL, trace_id text, span_id text,
  release_version text, revision text, environment text,
  http_method text, http_route text, http_status smallint, duration_ms real,
  exception_type text, exception_message text, frames jsonb, fingerprint text,
  attributes jsonb NOT NULL DEFAULT '{}',
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id))
""",
    "CREATE INDEX log_events_service_ts ON log_events (tenant_id, service_id, ts)",
    "CREATE INDEX log_events_fingerprint ON log_events (tenant_id, service_id, fingerprint, ts) "
    "WHERE fingerprint IS NOT NULL",
    "CREATE INDEX log_events_trace ON log_events (tenant_id, trace_id) WHERE trace_id IS NOT NULL",
    """
CREATE TABLE request_windows (
  tenant_id uuid NOT NULL, service_id uuid NOT NULL, route text NOT NULL,
  window_start timestamptz NOT NULL,
  requests int NOT NULL, errors_5xx int NOT NULL, errors_4xx int NOT NULL,
  latency int[] NOT NULL CHECK (cardinality(latency) = 12),
  PRIMARY KEY (tenant_id, service_id, route, window_start),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id))
""",
    """
CREATE TABLE exception_windows (
  tenant_id uuid NOT NULL, service_id uuid NOT NULL, fingerprint text NOT NULL,
  window_start timestamptz NOT NULL, occurrences int NOT NULL,
  PRIMARY KEY (tenant_id, service_id, fingerprint, window_start),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id))
""",
    """
CREATE TABLE fingerprints (
  tenant_id uuid NOT NULL, service_id uuid NOT NULL, fingerprint text NOT NULL,
  exception_type text NOT NULL, sample_message text NOT NULL, top_frames jsonb NOT NULL,
  library_only boolean NOT NULL, innermost_in_app boolean,
  first_seen timestamptz NOT NULL, last_seen timestamptz NOT NULL,
  occurrences bigint NOT NULL, routes text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, service_id, fingerprint),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id))
""",
    """
CREATE TABLE incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  public_id text NOT NULL DEFAULT ('INC-' || nextval('incident_seq')::text),
  service_id uuid NOT NULL,
  status incident_status NOT NULL DEFAULT 'detected',
  severity severity_level NOT NULL, title text NOT NULL,
  primary_fingerprint text, routes text[] NOT NULL DEFAULT '{}',
  classification incident_class,
  class_prior incident_class, class_prior_rule text,
  class_prior_reasons jsonb NOT NULL DEFAULT '[]',
  code_patch_candidate boolean NOT NULL DEFAULT false,
  suspected_release_id uuid,
  first_seen timestamptz NOT NULL, detected_at timestamptz NOT NULL DEFAULT now(),
  last_signal_at timestamptz NOT NULL, resolved_at timestamptz, closed_at timestamptz,
  signal_count int NOT NULL DEFAULT 0, reopened_count int NOT NULL DEFAULT 0,
  UNIQUE (tenant_id, public_id), UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id),
  FOREIGN KEY (tenant_id, suspected_release_id) REFERENCES release_events (tenant_id, id))
""",
    "CREATE INDEX incidents_open ON incidents (tenant_id, service_id, status)",
    """
CREATE TABLE incident_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  incident_id uuid, service_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('ERROR_RATE', 'EXCEPTION_SPIKE', 'JOB_FAILURE',
                                     'LATENCY', 'RESOURCE', 'DEPENDENCY')),
  key text NOT NULL, suppressed boolean NOT NULL DEFAULT false,
  fired_at timestamptz NOT NULL, window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL, observed double precision, baseline double precision,
  details jsonb NOT NULL DEFAULT '{}',
  UNIQUE (tenant_id, service_id, kind, key, fired_at),
  FOREIGN KEY (tenant_id, incident_id) REFERENCES incidents (tenant_id, id),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id))
""",
    """
CREATE TABLE incident_fingerprints (
  tenant_id uuid NOT NULL, incident_id uuid NOT NULL, fingerprint text NOT NULL,
  first_seen timestamptz NOT NULL, last_seen timestamptz NOT NULL,
  occurrences bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, incident_id, fingerprint),
  FOREIGN KEY (tenant_id, incident_id) REFERENCES incidents (tenant_id, id))
""",
    """
CREATE TABLE noise_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  service_id uuid NOT NULL, fingerprint text NOT NULL, reason text,
  expires_at timestamptz, created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id))
""",
    """
CREATE TABLE incident_transition_rules (
  from_status incident_status NOT NULL, to_status incident_status NOT NULL,
  actor_role text NOT NULL CHECK (actor_role IN ('r_telemetry', 'r_orch', 'r_api')),
  PRIMARY KEY (from_status, to_status, actor_role))
""",
)

# D2 section 6.6 lifecycle, plus self-resolution (B9) and recurrence reopen (D2 6.3).
TRANSITIONS: tuple[tuple[str, str, str], ...] = (
    *((s, "resolved", "r_telemetry")
      for s in ("detected", "triaging", "investigating", "awaiting_human", "mitigating")),
    ("resolved", "detected", "r_telemetry"),
    ("closed", "detected", "r_telemetry"),
    ("detected", "triaging", "r_orch"),
    ("triaging", "investigating", "r_orch"),
    ("triaging", "awaiting_human", "r_orch"),
    ("investigating", "awaiting_human", "r_orch"),
    ("awaiting_human", "mitigating", "r_api"),
    ("resolved", "closed", "r_api"),
    *((s, "suppressed", "r_api")
      for s in ("detected", "triaging", "investigating", "awaiting_human", "mitigating")),
    ("suppressed", "detected", "r_api"),
)  # fmt: skip

FUNCTIONS_SQL = (
    """
CREATE FUNCTION add_int_arrays(a integer[], b integer[]) RETURNS integer[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT array_agg(x + y ORDER BY i) FROM unnest(a, b) WITH ORDINALITY AS t(x, y, i) $$
""",
    """
CREATE FUNCTION incident_transition(p_incident uuid, p_to incident_status, p_reason text)
RETURNS incident_status
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_actor text := current_setting('role');
  v_from incident_status; v_public text; v_event bigint;
BEGIN
  IF v_actor IS NULL OR v_actor = 'none' THEN v_actor := session_user; END IF;
  IF p_reason !~ '^[a-z_]{1,40}$' THEN
    RAISE EXCEPTION 'reason must be a short code' USING ERRCODE = '22023';
  END IF;
  SELECT status, public_id INTO v_from, v_public FROM incidents WHERE id = p_incident FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'incident not found' USING ERRCODE = 'P0002';  -- includes other tenants
  END IF;
  IF v_from = p_to THEN RETURN v_from; END IF;  -- idempotent retry
  IF NOT EXISTS (SELECT 1 FROM incident_transition_rules r
                  WHERE r.from_status = v_from AND r.to_status = p_to
                    AND pg_has_role(v_actor, r.actor_role, 'MEMBER')) THEN
    RAISE EXCEPTION 'incident transition % -> % is not allowed for %', v_from, p_to, v_actor
      USING ERRCODE = '42501';
  END IF;
  UPDATE incidents
     SET status = p_to,
         resolved_at = CASE WHEN p_to = 'resolved' THEN now() ELSE resolved_at END,
         closed_at = CASE WHEN p_to = 'closed' THEN now() ELSE closed_at END,
         reopened_count = reopened_count
           + CASE WHEN v_from IN ('resolved', 'closed') AND p_to = 'detected' THEN 1 ELSE 0 END
   WHERE id = p_incident;
  PERFORM audit_append('service', v_actor, 'incident.transition', 'incident', v_public,
                       jsonb_build_object('from', v_from, 'to', p_to, 'reason', p_reason));
  INSERT INTO outbox_events (tenant_id, event_type, aggregate, aggregate_id, payload)
  VALUES (current_tenant(), 'incident.updated', 'incident', p_incident::text,
          jsonb_build_object('status', p_to, 'reason', p_reason))
  RETURNING id INTO v_event;
  PERFORM pg_notify('sentinel_outbox', v_event::text);
  RETURN p_to;
END $$
""",
    """
CREATE FUNCTION retention_purge(p_before timestamptz) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE v_tenant uuid; v_total bigint := 0; v_count bigint;
BEGIN
  FOR v_tenant IN SELECT id FROM tenants LOOP
    PERFORM set_config('sentinel.tenant_id', v_tenant::text, true);
    DELETE FROM log_events WHERE ts < p_before;
    GET DIAGNOSTICS v_count = ROW_COUNT; v_total := v_total + v_count;
    DELETE FROM request_windows WHERE window_start < p_before;
    GET DIAGNOSTICS v_count = ROW_COUNT; v_total := v_total + v_count;
    DELETE FROM exception_windows WHERE window_start < p_before;
    GET DIAGNOSTICS v_count = ROW_COUNT; v_total := v_total + v_count;
    DELETE FROM dead_letters WHERE received_at < p_before;
    GET DIAGNOSTICS v_count = ROW_COUNT; v_total := v_total + v_count;
  END LOOP;
  PERFORM set_config('sentinel.tenant_id', '', true);
  RETURN v_total;
END $$
""",
)

# Frozen output of `sentinelai db grants-sql --table ...` (authority.yaml version 2).
GRANTS_SQL = """
GRANT INSERT ON TABLE services TO r_telemetry
REVOKE ALL ON TABLE ingest_sources FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE ingest_sources TO r_telemetry
GRANT SELECT ON TABLE ingest_sources TO r_readonly
REVOKE ALL ON TABLE ingest_batches FROM PUBLIC
GRANT SELECT, INSERT ON TABLE ingest_batches TO r_telemetry
GRANT SELECT ON TABLE ingest_batches TO r_readonly
REVOKE ALL ON TABLE dead_letters FROM PUBLIC
GRANT SELECT, INSERT ON TABLE dead_letters TO r_telemetry
GRANT SELECT ON TABLE dead_letters TO r_readonly
REVOKE ALL ON SEQUENCE dead_letters_id_seq FROM PUBLIC
GRANT USAGE ON SEQUENCE dead_letters_id_seq TO r_telemetry
REVOKE ALL ON TABLE log_events FROM PUBLIC
GRANT SELECT, INSERT ON TABLE log_events TO r_telemetry
GRANT SELECT ON TABLE log_events TO r_api
GRANT SELECT ON TABLE log_events TO r_orch
GRANT SELECT ON TABLE log_events TO r_readonly
REVOKE ALL ON SEQUENCE log_events_id_seq FROM PUBLIC
GRANT USAGE ON SEQUENCE log_events_id_seq TO r_telemetry
REVOKE ALL ON TABLE request_windows FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE request_windows TO r_telemetry
GRANT SELECT ON TABLE request_windows TO r_api
GRANT SELECT ON TABLE request_windows TO r_orch
GRANT SELECT ON TABLE request_windows TO r_readonly
REVOKE ALL ON TABLE exception_windows FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE exception_windows TO r_telemetry
GRANT SELECT ON TABLE exception_windows TO r_api
GRANT SELECT ON TABLE exception_windows TO r_orch
GRANT SELECT ON TABLE exception_windows TO r_readonly
REVOKE ALL ON TABLE fingerprints FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE fingerprints TO r_telemetry
GRANT SELECT ON TABLE fingerprints TO r_api
GRANT SELECT ON TABLE fingerprints TO r_orch
GRANT SELECT ON TABLE fingerprints TO r_readonly
REVOKE ALL ON TABLE incidents FROM PUBLIC
GRANT SELECT, INSERT ON TABLE incidents TO r_telemetry
GRANT UPDATE (severity, title, routes, class_prior, class_prior_rule, class_prior_reasons, code_patch_candidate, suspected_release_id, last_signal_at, signal_count) ON TABLE incidents TO r_telemetry
GRANT SELECT ON TABLE incidents TO r_api
GRANT SELECT ON TABLE incidents TO r_orch
GRANT SELECT ON TABLE incidents TO r_deployer
GRANT SELECT ON TABLE incidents TO r_readonly
REVOKE ALL ON SEQUENCE incident_seq FROM PUBLIC
GRANT USAGE ON SEQUENCE incident_seq TO r_telemetry
REVOKE ALL ON TABLE incident_signals FROM PUBLIC
GRANT SELECT, INSERT ON TABLE incident_signals TO r_telemetry
GRANT SELECT ON TABLE incident_signals TO r_api
GRANT SELECT ON TABLE incident_signals TO r_orch
GRANT SELECT ON TABLE incident_signals TO r_readonly
REVOKE ALL ON TABLE incident_fingerprints FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE incident_fingerprints TO r_telemetry
GRANT SELECT ON TABLE incident_fingerprints TO r_api
GRANT SELECT ON TABLE incident_fingerprints TO r_orch
GRANT SELECT ON TABLE incident_fingerprints TO r_readonly
REVOKE ALL ON TABLE noise_rules FROM PUBLIC
GRANT SELECT ON TABLE noise_rules TO r_telemetry
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE noise_rules TO r_api
GRANT SELECT ON TABLE noise_rules TO r_readonly
REVOKE ALL ON TABLE incident_transition_rules FROM PUBLIC
REVOKE ALL ON FUNCTION incident_transition(uuid, incident_status, text) FROM PUBLIC
GRANT EXECUTE ON FUNCTION incident_transition(uuid, incident_status, text) TO r_telemetry, r_api, r_orch
REVOKE ALL ON FUNCTION add_int_arrays(integer[], integer[]) FROM PUBLIC
GRANT EXECUTE ON FUNCTION add_int_arrays(integer[], integer[]) TO r_telemetry
REVOKE ALL ON FUNCTION retention_purge(timestamptz) FROM PUBLIC
GRANT EXECUTE ON FUNCTION retention_purge(timestamptz) TO r_maint
"""  # noqa: E501


def _run(*statements: str) -> None:
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    _run("SET LOCAL ROLE sentinel_owner", *TABLES_SQL, *FUNCTIONS_SQL)
    for table in TENANT_TABLES:
        _run(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())",
        )
    for table in ("ingest_batches", "incident_signals"):
        _run(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()",
        )
    values = ", ".join(f"('{a}', '{b}', '{r}')" for a, b, r in TRANSITIONS)
    _run(
        # values come from the TRANSITIONS constant above, never from input
        "INSERT INTO incident_transition_rules (from_status, to_status, actor_role) "  # noqa: S608  # nosec B608
        f"VALUES {values}",
        *(line for line in GRANTS_SQL.strip().splitlines() if line.strip()),
        "RESET ROLE",
    )


def downgrade() -> None:
    _run(
        "DROP TABLE IF EXISTS incident_transition_rules, noise_rules, incident_fingerprints, "
        "incident_signals, incidents, fingerprints, exception_windows, request_windows, "
        "log_events, dead_letters, ingest_batches, ingest_sources CASCADE",
        "DROP FUNCTION IF EXISTS retention_purge(timestamptz)",
        "DROP FUNCTION IF EXISTS incident_transition(uuid, incident_status, text)",
        "DROP FUNCTION IF EXISTS add_int_arrays(integer[], integer[])",
        "ALTER TABLE release_events DROP CONSTRAINT IF EXISTS release_events_tenant_id_key",
        "DROP SEQUENCE IF EXISTS incident_seq",
        "REVOKE INSERT ON TABLE services FROM r_telemetry",
    )
