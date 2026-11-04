"""Foundation schema: roles, enums, tenancy, audit chain, queue and outbox.

Revision ID: 0001
Revises: (none)
Create Date: 2026-11-03

Phase 0 task P0-07. Design: docs/design/02-data-model.md sections 2-5, ADR-019.

Security properties established here (each covered by tests/db):
- Group roles per process, NOLOGIN, NOBYPASSRLS (except r_maint); objects owned by the
  non-superuser role sentinel_owner.
- Tenant tables: tenant_id NOT NULL, RLS ENABLED + FORCED, policy against current_tenant(),
  which raises when no valid tenant context is set (fail closed).
- Cross-tenant references impossible: composite foreign keys on (tenant_id, id).
- Audit log: insert-only (row and TRUNCATE triggers), one hash chain per tenant plus a
  platform chain, written only through SECURITY DEFINER functions. The tenant chain is
  derived from the session context, never chosen by the caller. Appends are serialised per
  chain, and refused outside READ COMMITTED, where a stale snapshot could fork the chain.
- PUBLIC holds no table, sequence or function privileges on these objects.

The whole migration runs in one transaction: it applies completely or not at all.
Grant statements are frozen output of `sentinelai db grants-sql` for authority.yaml v1.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

APP_ROLES = (
    "r_telemetry",
    "r_api",
    "r_orch",
    "r_codeintel",
    "r_broker",
    "r_deployer",
    "r_harness",
    "r_maint",
    "r_readonly",
)
ALL_ROLES = ("sentinel_owner", *APP_ROLES)
TENANT_TABLES = ("repositories", "services", "repo_revisions", "release_events")
INSERT_ONLY_TABLES = ("audit_log", "repo_revisions", "release_events")

# fmt: off
ENUMS: dict[str, tuple[str, ...]] = {
    "incident_status": (
        "detected", "triaging", "investigating", "awaiting_human", "mitigating", "resolved",
        "closed", "suppressed",
    ),
    "severity_level": ("sev1", "sev2", "sev3", "sev4"),
    "incident_class": (
        "code_defect", "regression_from_release", "configuration", "dependency_change",
        "resource_exhaustion", "external_dependency", "infrastructure", "performance",
        "data_issue", "unknown",
    ),
    "remediation_category": (
        "code_patch", "config_change", "rollback", "dependency_pin", "capacity",
        "external_escalation", "infra_escalation", "data_repair", "human_investigation",
    ),
    "run_state": (
        "queued", "freeze_evidence", "triage", "correlate", "hypothesize", "validate",
        "reproduce", "patch", "verify", "diagnose_failure", "risk_review", "report", "done",
    ),
    "run_outcome": (
        "proposal_ready", "report_only", "escalated_insufficient_evidence",
        "escalated_not_reproduced", "escalated_verify_failed", "escalated_policy",
        "escalated_budget", "escalated_dependency_unavailable", "cancelled", "failed_internal",
    ),
    "evidence_level": ("hypothesis", "supported", "reproduced", "verified"),
    "trust_level": ("system", "untrusted_app", "untrusted_repo", "advisory", "human"),
    "repro_outcome": ("reproduced", "inconclusive", "not_reproduced", "not_reproducible_here"),
    "patch_status": (
        "proposed", "prechecked", "verifying", "verified", "verified_with_limitations", "failed",
        "advisory", "superseded",
    ),
    "verification_status": (
        "verified", "verified_with_limitations", "failed", "unverified_advisory",
    ),
    "check_result": ("pass", "fail", "unknown", "skipped", "not_applicable"),
    "risk_level": ("low", "medium", "high"),
    "approval_decision": ("approve", "reject", "request_changes"),
    "user_role": ("viewer", "responder", "approver", "admin"),
    "delivery_state": (
        "queued", "token_verified", "branch_pushed", "ci_running", "ci_passed",
        "staging_deploying", "staging_verifying", "awaiting_promotion", "canary_5",
        "canary_25", "canary_100", "monitoring", "succeeded", "rolling_back", "rolled_back",
        "aborted", "failed_safe",
    ),
    "case_outcome": (
        "accepted", "accepted_with_changes", "rejected", "rolled_back", "abstained",
    ),
}
# fmt: on

ROLES_SQL = """
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['sentinel_owner','r_telemetry','r_api','r_orch','r_codeintel',
                           'r_broker','r_deployer','r_harness','r_maint','r_readonly'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE 'CREATE ROLE ' || quote_ident(r) || ' NOLOGIN';
    END IF;
    EXECUTE 'ALTER ROLE ' || quote_ident(r) || ' NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE'
         || CASE WHEN r = 'r_maint' THEN ' BYPASSRLS' ELSE ' NOBYPASSRLS' END;
  END LOOP;
END $$
"""

CURRENT_TENANT_SQL = """
CREATE FUNCTION current_tenant() RETURNS uuid
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE v text := current_setting('sentinel.tenant_id', true);
BEGIN
  IF v IS NULL OR v = '' THEN
    RAISE EXCEPTION 'tenant context not set' USING ERRCODE = '42501';
  END IF;
  RETURN v::uuid;  -- a malformed value raises invalid_text_representation: still fail closed
END $$
"""

FORBID_MUTATION_SQL = """
CREATE FUNCTION forbid_mutation() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'table % is insert-only', TG_TABLE_NAME USING ERRCODE = '42501';
END $$
"""

TABLES_SQL = (
    """
CREATE TABLE tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','suspended','offboarding','erased')),
  data_policy jsonb NOT NULL DEFAULT '{}',
  quotas jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (id <> '00000000-0000-0000-0000-000000000000'))
""",
    """
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  oidc_issuer text NOT NULL, oidc_subject text NOT NULL, email text NOT NULL,
  display_name text NOT NULL, platform_admin boolean NOT NULL DEFAULT false,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (oidc_issuer, oidc_subject))
""",
    """
CREATE TABLE tenant_memberships (
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  user_id uuid NOT NULL REFERENCES users(id),
  role user_role NOT NULL,
  PRIMARY KEY (tenant_id, user_id))
""",
    """
CREATE TABLE repositories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL, url text NOT NULL, default_branch text NOT NULL DEFAULT 'main',
  language text NOT NULL DEFAULT 'python', source_roots text[] NOT NULL DEFAULT '{}',
  sandbox_image text, policy jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name), UNIQUE (tenant_id, id))
""",
    """
CREATE TABLE services (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL, environment text NOT NULL,
  tier smallint NOT NULL DEFAULT 2 CHECK (tier BETWEEN 0 AND 3),
  repository_id uuid, app_root text,
  auto_investigate boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, name, environment), UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, repository_id) REFERENCES repositories (tenant_id, id))
""",
    """
CREATE TABLE repo_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  repository_id uuid NOT NULL,
  sha char(40) NOT NULL CHECK (sha ~ '^[0-9a-f]{40}$'),
  parent_shas char(40)[] NOT NULL DEFAULT '{}',
  author text, message text, committed_at timestamptz,
  UNIQUE (tenant_id, repository_id, sha), UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, repository_id) REFERENCES repositories (tenant_id, id))
""",
    """
CREATE TABLE release_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  service_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('deploy','config_change','rollback','scale')),
  version text NOT NULL, previous_version text,
  revision_id uuid,
  idempotency_key text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  occurred_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, service_id, idempotency_key),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id),
  FOREIGN KEY (tenant_id, revision_id) REFERENCES repo_revisions (tenant_id, id))
""",
    """
CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  chain_id uuid NOT NULL,
  ts timestamptz NOT NULL,
  actor_type text NOT NULL CHECK (actor_type IN ('user','service','system')),
  actor_id text NOT NULL, action text NOT NULL,
  entity_type text NOT NULL, entity_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}',
  prev_hash bytea NOT NULL, row_hash bytea NOT NULL)
""",
    "CREATE INDEX audit_chain_head ON audit_log (chain_id, id DESC)",
    """
CREATE TABLE jobs (
  id bigserial PRIMARY KEY,
  tenant_id uuid REFERENCES tenants(id),
  queue text NOT NULL, kind text NOT NULL, payload jsonb NOT NULL,
  priority int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','dead')),
  run_after timestamptz NOT NULL DEFAULT now(), attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 5, locked_by text, locked_until timestamptz,
  dedupe_key text, last_error text, created_at timestamptz NOT NULL DEFAULT now())
""",
    "CREATE UNIQUE INDEX jobs_dedupe ON jobs (dedupe_key) WHERE status IN ('queued','running')",
    "CREATE INDEX jobs_claim ON jobs (queue, status, run_after, priority DESC)",
    """
CREATE TABLE outbox_events (
  id bigserial PRIMARY KEY,
  event_id uuid NOT NULL DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id),
  event_type text NOT NULL, aggregate text NOT NULL, aggregate_id text NOT NULL,
  payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  dispatched_at timestamptz)
""",
    "CREATE INDEX outbox_pending ON outbox_events (id) WHERE dispatched_at IS NULL",
)

AUDIT_FUNCTIONS_SQL = (
    """
CREATE FUNCTION audit_write_chain(p_chain uuid, p_actor_type text, p_actor_id text,
    p_action text, p_entity_type text, p_entity_id text, p_payload jsonb) RETURNS bigint
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
  v_ts timestamptz := clock_timestamp();
  v_us bigint := (extract(epoch FROM v_ts) * 1000000)::bigint;  -- timezone-independent
  v_payload jsonb := COALESCE(p_payload, '{}'::jsonb);
  v_prev bytea; v_hash bytea; v_id bigint;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'audit appends require READ COMMITTED isolation' USING ERRCODE = '25001';
  END IF;
  PERFORM pg_advisory_xact_lock(7001, hashtext(p_chain::text));  -- serialise per chain
  SELECT row_hash INTO v_prev FROM audit_log WHERE chain_id = p_chain ORDER BY id DESC LIMIT 1;
  v_prev := COALESCE(v_prev, decode('00', 'hex'));
  v_hash := digest(v_prev || convert_to(concat_ws('|', p_chain::text, v_us::text, p_actor_type,
              p_actor_id, p_action, p_entity_type, p_entity_id, v_payload::text), 'UTF8'),
            'sha256');
  INSERT INTO audit_log (chain_id, ts, actor_type, actor_id, action, entity_type, entity_id,
                         payload, prev_hash, row_hash)
  VALUES (p_chain, v_ts, p_actor_type, p_actor_id, p_action, p_entity_type, p_entity_id,
          v_payload, v_prev, v_hash)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$
""",
    """
CREATE FUNCTION audit_append(p_actor_type text, p_actor_id text, p_action text,
    p_entity_type text, p_entity_id text, p_payload jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE v_tenant uuid := current_tenant();  -- raises without a tenant context
BEGIN
  IF v_tenant = '00000000-0000-0000-0000-000000000000'::uuid THEN
    RAISE EXCEPTION 'the platform chain is not writable through audit_append'
      USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = v_tenant) THEN
    RAISE EXCEPTION 'unknown tenant in context' USING ERRCODE = '42501';
  END IF;
  RETURN audit_write_chain(v_tenant, p_actor_type, p_actor_id, p_action, p_entity_type,
                           p_entity_id, p_payload);
END $$
""",
    """
CREATE FUNCTION audit_append_platform(p_actor_type text, p_actor_id text, p_action text,
    p_entity_type text, p_entity_id text, p_payload jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  RETURN audit_write_chain('00000000-0000-0000-0000-000000000000'::uuid, p_actor_type,
                           p_actor_id, p_action, p_entity_type, p_entity_id, p_payload);
END $$
""",
    "REVOKE ALL ON FUNCTION audit_write_chain(uuid, text, text, text, text, text, jsonb) "
    "FROM PUBLIC",
    "REVOKE ALL ON FUNCTION forbid_mutation() FROM PUBLIC",
)

# Frozen output of `sentinelai db grants-sql` (authority.yaml version 1).
GRANTS_SQL = """
REVOKE ALL ON TABLE tenants FROM PUBLIC
GRANT SELECT ON TABLE tenants TO r_telemetry
GRANT SELECT, INSERT, UPDATE ON TABLE tenants TO r_api
GRANT SELECT ON TABLE tenants TO r_orch
GRANT SELECT ON TABLE tenants TO r_codeintel
GRANT SELECT ON TABLE tenants TO r_broker
GRANT SELECT ON TABLE tenants TO r_deployer
GRANT SELECT ON TABLE tenants TO r_harness
GRANT SELECT ON TABLE tenants TO r_maint
GRANT SELECT ON TABLE tenants TO r_readonly
REVOKE ALL ON TABLE users FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE users TO r_api
GRANT SELECT ON TABLE users TO r_orch
GRANT SELECT ON TABLE users TO r_deployer
GRANT SELECT ON TABLE users TO r_readonly
REVOKE ALL ON TABLE tenant_memberships FROM PUBLIC
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tenant_memberships TO r_api
GRANT SELECT ON TABLE tenant_memberships TO r_orch
GRANT SELECT ON TABLE tenant_memberships TO r_deployer
GRANT SELECT ON TABLE tenant_memberships TO r_readonly
REVOKE ALL ON TABLE services FROM PUBLIC
GRANT SELECT ON TABLE services TO r_telemetry
GRANT SELECT, INSERT, UPDATE ON TABLE services TO r_api
GRANT SELECT ON TABLE services TO r_orch
GRANT SELECT ON TABLE services TO r_codeintel
GRANT SELECT ON TABLE services TO r_broker
GRANT SELECT ON TABLE services TO r_deployer
GRANT SELECT ON TABLE services TO r_readonly
REVOKE ALL ON TABLE repositories FROM PUBLIC
GRANT SELECT ON TABLE repositories TO r_telemetry
GRANT SELECT, INSERT, UPDATE ON TABLE repositories TO r_api
GRANT SELECT ON TABLE repositories TO r_orch
GRANT SELECT ON TABLE repositories TO r_codeintel
GRANT SELECT ON TABLE repositories TO r_broker
GRANT SELECT ON TABLE repositories TO r_deployer
GRANT SELECT ON TABLE repositories TO r_readonly
REVOKE ALL ON TABLE repo_revisions FROM PUBLIC
GRANT SELECT ON TABLE repo_revisions TO r_telemetry
GRANT SELECT ON TABLE repo_revisions TO r_api
GRANT SELECT ON TABLE repo_revisions TO r_orch
GRANT SELECT, INSERT ON TABLE repo_revisions TO r_codeintel
GRANT SELECT ON TABLE repo_revisions TO r_broker
GRANT SELECT ON TABLE repo_revisions TO r_deployer
GRANT SELECT ON TABLE repo_revisions TO r_readonly
REVOKE ALL ON TABLE release_events FROM PUBLIC
GRANT SELECT, INSERT ON TABLE release_events TO r_telemetry
GRANT SELECT ON TABLE release_events TO r_api
GRANT SELECT ON TABLE release_events TO r_orch
GRANT SELECT ON TABLE release_events TO r_codeintel
GRANT SELECT ON TABLE release_events TO r_deployer
GRANT SELECT ON TABLE release_events TO r_readonly
REVOKE ALL ON TABLE audit_log FROM PUBLIC
GRANT SELECT ON TABLE audit_log TO r_readonly
REVOKE ALL ON TABLE jobs FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE jobs TO r_telemetry
GRANT SELECT, INSERT, UPDATE ON TABLE jobs TO r_api
GRANT SELECT, INSERT, UPDATE ON TABLE jobs TO r_orch
GRANT SELECT, INSERT, UPDATE ON TABLE jobs TO r_codeintel
GRANT SELECT, INSERT, UPDATE ON TABLE jobs TO r_broker
GRANT SELECT, INSERT, UPDATE ON TABLE jobs TO r_deployer
GRANT SELECT, UPDATE ON TABLE jobs TO r_maint
GRANT SELECT ON TABLE jobs TO r_readonly
REVOKE ALL ON SEQUENCE jobs_id_seq FROM PUBLIC
GRANT USAGE ON SEQUENCE jobs_id_seq TO r_telemetry, r_api, r_orch, r_codeintel, r_broker, r_deployer
REVOKE ALL ON TABLE outbox_events FROM PUBLIC
GRANT SELECT, INSERT, UPDATE ON TABLE outbox_events TO r_telemetry
GRANT SELECT, INSERT, UPDATE ON TABLE outbox_events TO r_api
GRANT SELECT, INSERT, UPDATE ON TABLE outbox_events TO r_orch
GRANT SELECT, INSERT, UPDATE ON TABLE outbox_events TO r_codeintel
GRANT SELECT, INSERT, UPDATE ON TABLE outbox_events TO r_broker
GRANT SELECT, INSERT, UPDATE ON TABLE outbox_events TO r_deployer
GRANT SELECT, UPDATE ON TABLE outbox_events TO r_maint
GRANT SELECT ON TABLE outbox_events TO r_readonly
REVOKE ALL ON SEQUENCE outbox_events_id_seq FROM PUBLIC
GRANT USAGE ON SEQUENCE outbox_events_id_seq TO r_telemetry, r_api, r_orch, r_codeintel, r_broker, r_deployer
REVOKE ALL ON FUNCTION current_tenant() FROM PUBLIC
GRANT EXECUTE ON FUNCTION current_tenant() TO r_telemetry, r_api, r_orch, r_codeintel, r_broker, r_deployer, r_harness, r_maint, r_readonly
REVOKE ALL ON FUNCTION audit_append(text, text, text, text, text, jsonb) FROM PUBLIC
GRANT EXECUTE ON FUNCTION audit_append(text, text, text, text, text, jsonb) TO r_telemetry, r_api, r_orch, r_codeintel, r_broker, r_deployer
REVOKE ALL ON FUNCTION audit_append_platform(text, text, text, text, text, jsonb) FROM PUBLIC
GRANT EXECUTE ON FUNCTION audit_append_platform(text, text, text, text, text, jsonb) TO r_api, r_maint
"""  # noqa: E501


def _run(*statements: str) -> None:
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    # As the migrating administrator: cluster roles, extensions, schema privileges.
    _run(
        ROLES_SQL,
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "CREATE EXTENSION IF NOT EXISTS vector",
        "GRANT USAGE, CREATE ON SCHEMA public TO sentinel_owner",
        f"GRANT USAGE ON SCHEMA public TO {', '.join(APP_ROLES)}",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
    )
    # Everything else is created and owned by the non-superuser owner role.
    _run(
        "SET LOCAL ROLE sentinel_owner",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sentinel_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
    )
    _run(
        *(
            f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v) for v in values)})"
            for name, values in ENUMS.items()
        )
    )
    _run(CURRENT_TENANT_SQL, FORBID_MUTATION_SQL, *TABLES_SQL, *AUDIT_FUNCTIONS_SQL)

    for table in TENANT_TABLES:
        _run(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())",
        )
    # Audit log: RLS enabled but not forced, so the SECURITY DEFINER writer sees every chain.
    _run(
        "ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY",
        "CREATE POLICY audit_read ON audit_log FOR SELECT USING (chain_id = current_tenant())",
    )
    for table in INSERT_ONLY_TABLES:
        _run(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()",
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation()",
        )

    _run(*(line for line in GRANTS_SQL.strip().splitlines() if line.strip()))
    _run(
        "INSERT INTO tenants (slug, name) VALUES ('default', 'Default tenant'), "
        "('bench', 'Benchmark (synthetic data only)') ON CONFLICT (slug) DO NOTHING",
        "RESET ROLE",
    )


def downgrade() -> None:
    _run(
        "DROP TABLE IF EXISTS outbox_events, jobs, audit_log, release_events, repo_revisions, "
        "services, repositories, tenant_memberships, users, tenants CASCADE",
        "DROP FUNCTION IF EXISTS audit_append_platform(text, text, text, text, text, jsonb)",
        "DROP FUNCTION IF EXISTS audit_append(text, text, text, text, text, jsonb)",
        "DROP FUNCTION IF EXISTS audit_write_chain(uuid, text, text, text, text, text, jsonb)",
        "DROP FUNCTION IF EXISTS forbid_mutation()",
        "DROP FUNCTION IF EXISTS current_tenant()",
        *(f"DROP TYPE IF EXISTS {name}" for name in reversed(ENUMS)),
        "DROP EXTENSION IF EXISTS vector",
        "DROP EXTENSION IF EXISTS pgcrypto",
    )
    # Roles are cluster-wide. Remove this database's privileges and default ACLs, then drop
    # each role unless another database on the cluster still depends on it.
    _run(
        """
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['r_telemetry','r_api','r_orch','r_codeintel','r_broker',
                           'r_deployer','r_harness','r_maint','r_readonly','sentinel_owner'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE 'DROP OWNED BY ' || quote_ident(r);
      BEGIN
        EXECUTE 'DROP ROLE ' || quote_ident(r);
      EXCEPTION WHEN dependent_objects_still_exist THEN
        RAISE NOTICE USING MESSAGE = 'role kept (used by another database): ' || r;
      END;
    END IF;
  END LOOP;
END $$
"""
    )
