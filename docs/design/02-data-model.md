# 02. Data model amendments

Amends D2 §13. D2's DDL remains authoritative except where it's replaced here. "Tenant-scoped" means the table gets `tenant_id uuid NOT NULL REFERENCES tenants(id)` and the RLS policy in §2.

## 1. Inventory of table scopes

| Scope | Tables |
|---|---|
| Platform (no tenant) | `tenants`, `users`, `webauthn_credentials`, `jobs`, `outbox_events`, `provider_registry`, `bench_*` (system tables, run under the `bench` tenant), `schema_migrations` |
| Tenant-scoped | Everything else in D2 §13, plus all new tables below except those listed above |
| Harness only | Schema `bench_truth` (ground truth, hidden-test references). No grant to any Sentinel role. |

## 2. Tenancy primitives

```sql
CREATE TABLE tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','suspended','offboarding','erased')),
  data_policy jsonb NOT NULL DEFAULT '{}',   -- schema: 06 §B.5
  quotas jsonb NOT NULL DEFAULT '{}',        -- schema: 06 §B.5
  created_at timestamptz NOT NULL DEFAULT now());

CREATE FUNCTION current_tenant() RETURNS uuid LANGUAGE plpgsql STABLE AS $$
DECLARE v text := current_setting('sentinel.tenant_id', true);
BEGIN
  IF v IS NULL OR v = '' THEN RAISE EXCEPTION 'tenant context not set' USING ERRCODE = '42501'; END IF;
  RETURN v::uuid;
END $$;

-- applied to every tenant-scoped table by a migration helper:
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
```

- Every index on a tenant-scoped table **leads with `tenant_id`**.
- Application roles are `NOLOGIN` groups. Login users inherit them, and none has `BYPASSRLS` except `r_maint`.

## 3. Audit log: per-tenant chains and a timezone-safe hash (fixes C4)

Replaces D2 §13.7 `audit_log` and `audit_append`.

```sql
CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  chain_id uuid NOT NULL,                 -- tenant id, or the platform chain id 00000000-0000-0000-0000-000000000000
  ts timestamptz NOT NULL,
  actor_type text NOT NULL CHECK (actor_type IN ('user','service','system')),
  actor_id text NOT NULL, action text NOT NULL, entity_type text NOT NULL, entity_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}',    -- IDs, hashes and enums only (validated per action; PRV-04)
  prev_hash bytea NOT NULL, row_hash bytea NOT NULL);
CREATE INDEX audit_chain_head ON audit_log (chain_id, id DESC);

CREATE OR REPLACE FUNCTION audit_append(p_chain uuid, p_actor_type text, p_actor_id text,
    p_action text, p_entity_type text, p_entity_id text, p_payload jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_prev bytea; v_hash bytea; v_id bigint; v_ts timestamptz := clock_timestamp();
        v_us bigint := (extract(epoch FROM v_ts) * 1000000)::bigint;   -- timezone-independent
BEGIN
  -- callers may write only to their own tenant chain (or the platform chain for platform actors)
  IF p_chain <> '00000000-0000-0000-0000-000000000000' AND p_chain <> current_tenant() THEN
    RAISE EXCEPTION 'audit chain mismatch' USING ERRCODE = '42501';
  END IF;
  PERFORM pg_advisory_xact_lock(7001, hashtext(p_chain::text));    -- serialise per chain only
  SELECT row_hash INTO v_prev FROM audit_log WHERE chain_id = p_chain ORDER BY id DESC LIMIT 1;
  v_prev := COALESCE(v_prev, decode('00', 'hex'));
  v_hash := digest(v_prev || convert_to(concat_ws('|', p_chain::text, v_us::text, p_actor_type,
              p_actor_id, p_action, p_entity_type, p_entity_id, p_payload::text), 'UTF8'), 'sha256');
  INSERT INTO audit_log (chain_id, ts, actor_type, actor_id, action, entity_type, entity_id,
                         payload, prev_hash, row_hash)
  VALUES (p_chain, v_ts, p_actor_type, p_actor_id, p_action, p_entity_type, p_entity_id,
          p_payload, v_prev, v_hash) RETURNING id INTO v_id;
  RETURN v_id;
END $$;
```

- `jsonb::text` output is canonical in PostgreSQL, so it doesn't depend on the session.
- The verifier (`sentinelai audit verify [--chain]`) recomputes using **the stored `ts` converted to epoch microseconds**. AT-AUD-02 runs it under three session timezones.
- Insert-only triggers stay exactly as in D2.

## 4. New and changed tables

```sql
-- Identity (replaces users.role with per-tenant memberships; adds OIDC linkage — C9)
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  oidc_issuer text NOT NULL, oidc_subject text NOT NULL, email text NOT NULL,
  display_name text NOT NULL, platform_admin boolean NOT NULL DEFAULT false,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (oidc_issuer, oidc_subject));
CREATE TABLE tenant_memberships (
  tenant_id uuid NOT NULL REFERENCES tenants(id), user_id uuid NOT NULL REFERENCES users(id),
  role user_role NOT NULL, PRIMARY KEY (tenant_id, user_id));

-- Passkeys (ADR-015)
CREATE TABLE webauthn_credentials (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  credential_id bytea UNIQUE NOT NULL, public_key bytea NOT NULL,
  sign_count bigint NOT NULL DEFAULT 0, aaguid uuid, backup_eligible boolean,
  registered_via text NOT NULL CHECK (registered_via IN ('bootstrap','existing_passkey','admin_recovery')),
  registered_at timestamptz NOT NULL DEFAULT now(),
  usable_from timestamptz NOT NULL,          -- registered_at + cool-down (initial 24 h)
  revoked_at timestamptz);

-- Incidents: authority via functions (B3)
CREATE TABLE incident_transition_rules (
  from_status incident_status NOT NULL, to_status incident_status NOT NULL,
  actor_role text NOT NULL,                   -- r_telemetry | r_orch | r_api
  PRIMARY KEY (from_status, to_status, actor_role));
-- seeded from D2 §6.6 plus: (detected|triaging|investigating|awaiting_human) -> resolved by r_telemetry (self-resolved, B9)
-- incident_transition(p_incident uuid, p_to incident_status, p_reason text, p_actor_id text)
--   SECURITY DEFINER; checks session_user's group against the rule table, locks the row,
--   updates status/timestamps, calls audit_append, writes outbox incident.updated.
-- incident_annotate(p_incident uuid, p_classification incident_class) — r_orch only.

-- Release events: sender idempotency (B10)
--   replace UNIQUE (service_id, kind, version) with:
ALTER TABLE release_events ADD COLUMN idempotency_key text NOT NULL;
--   UNIQUE (tenant_id, service_id, idempotency_key)

-- Runs
--   investigation_runs adds: trace_id text, cancel_requested_at timestamptz,
--   parked_until timestamptz, park_reason text, parked_seconds int NOT NULL DEFAULT 0,
--   self_resolved boolean NOT NULL DEFAULT false, pinned_models jsonb NOT NULL DEFAULT '{}'
--   run_outcome enum adds 'escalated_dependency_unavailable' (R-01)
--   budget/spent JSON gain tokens_in, tokens_out, requests per provider (R-11)
--   llm_calls adds: provider_request_id text, rate_limit_snapshot jsonb,
--   egress_decision jsonb NOT NULL, failover_from uuid REFERENCES llm_calls(id)

-- Predictions (ADR-013) — replaces hypotheses.predictions jsonb
CREATE TABLE prediction_queries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  run_id uuid NOT NULL REFERENCES investigation_runs(id), revision int NOT NULL,
  query jsonb NOT NULL, query_sha256 bytea NOT NULL,         -- canonical AST and its hash
  origin text NOT NULL CHECK (origin IN ('system_battery','model')),
  result text CHECK (result IN ('true','false','insufficient_data','invalid')),
  observed jsonb, evidence_id uuid REFERENCES evidence_items(id),
  evaluator_version text, evaluated_at timestamptz,
  UNIQUE (run_id, revision, query_sha256));
CREATE TABLE prediction_expectations (
  query_id uuid NOT NULL REFERENCES prediction_queries(id),
  hypothesis_id uuid NOT NULL REFERENCES hypotheses(id), tenant_id uuid NOT NULL,
  expected text NOT NULL CHECK (expected IN ('true','false','indifferent')),
  source text NOT NULL CHECK (source IN ('class_table','model')),
  PRIMARY KEY (query_id, hypothesis_id));

-- Human notes (FR-33)
CREATE TABLE run_notes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  run_id uuid NOT NULL REFERENCES investigation_runs(id), author_id uuid NOT NULL REFERENCES users(id),
  body text NOT NULL CHECK (length(body) <= 4000), created_at timestamptz NOT NULL DEFAULT now(),
  ingested_evidence_id uuid);
-- trust_level enum adds 'human'

-- Async execution (ADR-020)
CREATE TABLE execution_requests (            -- broker-owned, mutable
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  run_id uuid NOT NULL REFERENCES investigation_runs(id),
  idempotency_key text UNIQUE NOT NULL, purpose text NOT NULL,
  command_template text NOT NULL, parameters jsonb NOT NULL,
  workspace_spec jsonb NOT NULL,             -- base revision and content hashes of supplied files/diff
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','preparing','running','completed','killed','rejected')),
  attempts int NOT NULL DEFAULT 0 CHECK (attempts <= 2),   -- "never silently retried more than once"
  execution_id uuid REFERENCES sandbox_executions(id), reject_reason text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
--   sandbox_executions adds: tenant_id, request_id uuid NOT NULL, plugin_report_sha256 bytea

-- Multiple executions per gate (D7); the CHECK on verification_checks becomes a deferred constraint trigger
CREATE TABLE verification_check_executions (
  check_id uuid NOT NULL REFERENCES verification_checks(id),
  execution_id uuid NOT NULL REFERENCES sandbox_executions(id), tenant_id uuid NOT NULL,
  role text NOT NULL,                         -- base | patched | counterfactual | repeat_<n>
  PRIMARY KEY (check_id, execution_id));
-- constraint trigger (DEFERRABLE INITIALLY DEFERRED): result='pass' requires >=1 linked execution
-- unless gate IN ('G11','G14')

-- Patch lifecycle (B4): patch_status keeps proposed, prechecked, verifying, verified,
-- verified_with_limitations, failed, advisory, superseded. Approval and delivery states come from:
-- CREATE VIEW patch_lifecycle AS SELECT p.id, p.status, (approval aggregates), (latest delivery state) ...

-- Approvals: risk recompute (C2) and passkey binding (C3)
--   approvals adds: tenant_id, risk_recomputed risk_level NOT NULL, challenge_sha256 bytea NOT NULL,
--   webauthn_credential_id uuid REFERENCES webauthn_credentials(id), webauthn_assertion jsonb
--   approval_tokens adds: tenant_id
--   min approvers for HIGH = 2, configurable upward only (R-07)

-- Proof bundles (ADR-014): insert-only; each link written by its authority
CREATE TABLE proof_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  incident_id uuid NOT NULL REFERENCES incidents(id), run_id uuid REFERENCES investigation_runs(id),
  patch_id uuid REFERENCES patches(id),
  link text NOT NULL CHECK (link IN ('investigation','verification','approval','delivery')),
  signer_role text NOT NULL, kid text NOT NULL,
  statement_sha256 bytea NOT NULL, prev_link_sha256 bytea, envelope jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (patch_id, link));
-- trigger: link<->session_user group must match (investigation:r_orch, verification:r_broker,
-- approval:r_api, delivery:r_deployer)

-- Notifications (FR-30)
CREATE TABLE notification_channels (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('webhook','slack_webhook')), url text NOT NULL,
  secret_ref text NOT NULL, events text[] NOT NULL, enabled boolean NOT NULL DEFAULT true);
CREATE TABLE notification_attempts (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, channel_id uuid NOT NULL,
  event_id uuid NOT NULL, attempt int NOT NULL, status_code int, error text,
  at timestamptz NOT NULL DEFAULT now(), UNIQUE (channel_id, event_id, attempt));

-- Alert intake (V1, FR-32)
CREATE TABLE alerts_inbound (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  source text NOT NULL, fingerprint text NOT NULL, payload jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(), incident_id uuid REFERENCES incidents(id),
  UNIQUE (tenant_id, source, fingerprint, received_at));

-- Blobs (prompts, responses, artifacts) — content-addressed per tenant
CREATE TABLE blobs (
  tenant_id uuid NOT NULL, sha256 bytea NOT NULL, size_bytes bigint NOT NULL,
  media_type text NOT NULL, classification text NOT NULL CHECK (classification IN ('P0','P1','P2')),
  storage_uri text NOT NULL, key_ref text,          -- envelope key (V1)
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id, sha256));

-- Embeddings keyed by model (R-12); replaces kb_cases.embedding / embedding_model
CREATE TABLE kb_case_embeddings (
  case_id uuid NOT NULL REFERENCES kb_cases(id), tenant_id uuid NOT NULL,
  embedding_model text NOT NULL, dims int NOT NULL, embedding vector NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, embedding_model), CHECK (vector_dims(embedding) = dims));
CREATE INDEX kb_emb_bge_small ON kb_case_embeddings
  USING hnsw ((embedding::vector(384)) vector_cosine_ops)
  WHERE embedding_model = 'bge-small-en-v1.5';

-- Provider registry (platform)
CREATE TABLE provider_registry (
  provider text PRIMARY KEY, base_url text NOT NULL, terms jsonb NOT NULL,   -- trains_on_inputs, retention, region
  reviewed_at date NOT NULL, enabled boolean NOT NULL DEFAULT false);

-- metric_points becomes range-partitioned by day like log_events (retention per A7).
```

## 5. Authority matrix (single source of truth: `src/sentinelai/adapters/db/authority.yaml`, P0-06)

Key: S = select, I = insert, U = update, F = through a SECURITY DEFINER function only, – = none.

| Table group | r_telemetry | r_api | r_orch | r_codeintel | r_broker | r_deployer | r_harness | r_maint | r_readonly |
|---|---|---|---|---|---|---|---|---|---|
| tenants, provider_registry | S | S I U (admin) | S | S | S | S | S | S | S |
| users, memberships, webauthn | – | S I U | S | – | – | S | – | – | S |
| telemetry tables, windows | S I U | S | S | – | – | S | – | DDL, delete | S |
| incidents | S F | S F | S F | – | S | S | – | – | S |
| incident_signals, fingerprints, noise_rules | S I U | S I U (noise) | S | – | – | – | – | – | S |
| release_events, alerts_inbound | S I | S | S | S | – | S | – | – | S |
| repositories, services | S | S I U | S | S | S | S | – | – | S |
| repo_revisions | S | S | S | S I | S | S | – | – | S |
| runs, steps*, llm_calls*, tool_calls*, hypotheses, predictions, evidence*, code_locations | – | S | S I U (* insert-only) | – | S (runs) | – | S (bench) | – | S |
| reproductions, patches, risk_assessments, reports, kb_* | – | S | S I U | – | S | S | S (bench) | – | S |
| run_notes | – | S I | S U (ingested_evidence_id) | – | – | – | – | – | S |
| execution_requests | – | S | S I (request only) | – | S I U | – | – | – | S |
| sandbox_executions*, verification_* | – | S | S | – | S I | S | S | – | S |
| approvals, approval_tokens | – | S I U (tokens) | S | – | – | S U (consume) | – | – | S |
| deliveries, health_checks, rollbacks | – | S | S | – | – | S I U | – | – | S |
| proof_links* | – | S I (approval) | S I (investigation) | – | S I (verification) | S I (delivery) | S | – | S |
| notification_* | – | S I U | – | – | – | – | – | – | S |
| blobs | S I | S | S I | S I | S I | S | S | delete | S |
| jobs, outbox_events | S I U | S I U | S I U | S I U | S I U | S I U | – | S U | S |
| audit_log | F | F | F | F | F | F | – | – | S |
| bench_* | – | S I (campaign start) | S | – | – | – | S I U | – | S |
| bench_truth schema | – | – | – | – | – | – | S | – | – |

A generator turns this file into `GRANT` statements and **one test per cell** (AT-DB-02). Any drift between the file, the migration and the tests fails CI.

## 6. Implementation notes: migration 0001 (2026-11-03)

These refine sections 2–5 as implemented. Each is equal to or stronger than the draft above.

| Topic | Draft | As implemented | Why |
|---|---|---|---|
| Audit chain selection | `audit_append(p_chain, …)` with a caller-supplied chain, checked against the context | `audit_append(…)` **derives** the chain from `current_tenant()` and rejects the zero UUID and unknown tenants. `audit_append_platform(…)` is a separate function, executable only by `r_api` and `r_maint`. | The caller can't pick a chain, and platform-chain writes have their own grant |
| Chain forking | Not addressed | Appends are refused outside READ COMMITTED (SQLSTATE 25001) | Under REPEATABLE READ a stale snapshot could read an old head hash and fork the chain |
| Shared chain writer | Inline | `audit_write_chain()` is internal; no application role may execute it | Single implementation, no direct access |
| Cross-tenant references | Not addressed | Composite foreign keys `(tenant_id, id)` on every tenant-to-tenant reference | FK checks bypass RLS; without this, a tenant could reference another tenant's row by ID |
| Object ownership | Not specified | All objects owned by `sentinel_owner` (NOLOGIN, non-superuser, NOBYPASSRLS); `ALTER DEFAULT PRIVILEGES` revokes PUBLIC EXECUTE | Security-definer functions don't run as superuser |
| SECURITY DEFINER hygiene | Not specified | Every function pins `search_path`; a test enforces it | Classic privilege-escalation vector |
| Insert-only tables | `audit_log` plus the D2 list | Row and TRUNCATE triggers on `audit_log`, `repo_revisions` and `release_events`; they block even superusers unless the trigger is disabled | Defence in depth beyond grants |
| `tenant_memberships` | S I U for `r_api` | S I U **D** for `r_api` | Removing a member must be possible; the change is recorded in the matrix and tested |
| Tenant transactions | Not specified | `tenant_transaction()` and `system_transaction()` refuse to run inside an open transaction | `SET LOCAL` lasts until the end of the top-level transaction, so a nested scope would leak its tenant and role |
| Tenancy limit | Not stated | The tenant context is a session setting, so RLS is a guard against bugs, not an authentication boundary (04 §9 item 8) | Honest threat model |
| Tenant erasure (V1) | Delete tenant rows | Insert-only triggers will need a controlled erasure path (a SECURITY DEFINER maintenance function). Audit rows stay, because their payloads carry no personal data. | Recorded for B60b |

