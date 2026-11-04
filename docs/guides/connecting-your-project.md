# Connecting your project to Sentinel AI

This guide connects any service (small app or large production system, any language) to Sentinel's telemetry ingestion and incident detection. You need three things: structured logs, release/config events, and one `sentinel.yaml` describing your project. No code changes to Sentinel are needed.

> **What Sentinel does at this stage:**
> - Ingests your logs.
> - Fingerprints errors.
> - Detects incidents with deterministic rules.
> - Correlates them with deploys and config changes.
> - Classifies them as a release regression, latent code defect, configuration, infrastructure, external dependency or performance problem, and records the reasons.
>
> Investigation and fixes are later milestones. No data is sent to any model provider at this stage.

## 1. Run Sentinel

```sh
cp .env.example .env    # set SENTINELAI_DEV_DB_PASSWORD, SENTINELAI_INGEST_TOKEN, SENTINELAI_RELEASE_WEBHOOK_SECRET
docker compose --profile core --profile sentinel up -d --build
```

This starts:
- PostgreSQL (with pgvector).
- A one-shot migration.
- `sentinel-ingest`, the HTTP API on `127.0.0.1:8090`.
- `sentinel-detect`, which evaluates the rules every 10 s.

Point `SENTINELAI_TARGET_CONFIG` at your project's `sentinel.yaml`; it defaults to the demo's.

**Production database roles.** Create one login per service that inherits its group role, and give each service its own URL:

```sql
CREATE ROLE sentinel_telemetry LOGIN PASSWORD '...' IN ROLE r_telemetry;
```

The services also drop to `r_telemetry` in every transaction, so even an administrator URL is restricted at transaction level.

## 2. Describe your project: `sentinel.yaml`

Put this file in your repository (Appendix C.1 of the design has the full schema):

```yaml
version: 1
service: checkout-api                # name used in your logs (service.name)
source_roots: [src/checkout/]        # your code; frames elsewhere are libraries
endpoints:                           # criticality tier per route: 0 = revenue-critical
  - {method: POST, path: /checkout, tier: 0}
  - {method: GET,  path: "/orders/{id}", tier: 1}
telemetry:
  app_root: /srv/app                 # path prefix in production stack traces
  environment: production
  info_sample_rate: 0.01             # share of healthy info logs stored raw (aggregates count all)
  fields:                            # only if your logs use unusual field names
    message: [log_line]
    exception_type: [err_class]
```

**Which frames are "yours".** Stack frames under `source_roots` are your code; frames in `site-packages`, `node_modules`, JDK, Spring and similar are libraries. That distinction is what separates a code bug from an infrastructure failure, so set `source_roots` accurately. If `app_root` is wrong, a source root still matches as a path segment (`/srv/app/src/checkout/x.py`).

## 3. Send logs (choose one)

Sentinel needs **JSON logs**, one object per line. It understands OpenTelemetry semantic conventions, ECS (`@timestamp`, `error.*`), pino/bunyan (`msg`, numeric `level`) and common variants. Request logs should include method, route template, status and duration. Error logs should include exception type, message and stack trace.

| Stack | Turn on JSON logs |
|---|---|
| Python | `python-json-logger`, `structlog`, or a JSON `logging.Formatter` |
| Java/Kotlin | `logstash-logback-encoder` (Logback) or the Log4j2 `JsonTemplateLayout` |
| Node.js | `pino` or `bunyan` (JSON by default) |
| Go | `log/slog` with `slog.NewJSONHandler`, or `zap` |
| .NET | Serilog with `CompactJsonFormatter` |

### a) OpenTelemetry Collector (recommended for production)

```yaml
exporters:
  otlphttp/sentinel:
    logs_endpoint: http://sentinel-host:8090/v1/otlp/logs
    encoding: json
    headers:
      Authorization: "Bearer ${env:SENTINELAI_INGEST_TOKEN}"
service:
  pipelines:
    logs: {receivers: [otlp, filelog], processors: [batch], exporters: [otlphttp/sentinel]}
```

### b) Vector / Fluent Bit / Logstash (NDJSON over HTTP)

```toml
# Vector
[sinks.sentinel]
type = "http"
inputs = ["app_logs"]
uri = "http://sentinel-host:8090/v1/ingest/logs?source=vector"
encoding.codec = "json"
framing.method = "newline_delimited"
request.headers.Authorization = "Bearer ${SENTINELAI_INGEST_TOKEN}"
```

```ini
# Fluent Bit
[OUTPUT]
    Name   http
    Match  app.*
    Host   sentinel-host
    Port   8090
    URI    /v1/ingest/logs?source=fluentbit
    Format json_lines
    Header Authorization Bearer ${SENTINELAI_INGEST_TOKEN}
```

Retries are safe. A batch with the same `Idempotency-Key` header, or, without one, the same body, is counted once.

### c) Tail a log file (simplest; single host)

```sh
sentinelai ingest file /var/log/checkout/app.jsonl --follow --target-config sentinel.yaml
```

The file cursor is stored with each batch, so restarts resume exactly where they stopped. Rotation (rename or copy-truncate) is detected.

## 4. Send release and config events from CI/CD

These events let Sentinel tell "broke after a deploy" from "broke after a config change" from "nothing changed". Send one per deploy, rollback and config change. Send config **key names** only, never values.

```sh
BODY='{"kind":"deploy","service":"checkout-api","environment":"production","version":"v2.4.1",
       "git_sha":"'"$GIT_SHA"'","previous_version":"v2.4.0",
       "occurred_at":"'"$(date -u +%FT%TZ)"'","idempotency_key":"'"$CI_PIPELINE_ID"'"}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SENTINELAI_RELEASE_WEBHOOK_SECRET" -hex | sed 's/^.* //')
curl -fsS http://sentinel-host:8090/v1/events/releases \
  -H "X-Sentinel-Timestamp: $TS" -H "X-Sentinel-Signature: sha256=$SIG" \
  -H 'Content-Type: application/json' --data "$BODY"
```

- For a configuration change: `"kind":"config_change","changed_keys":["PAYMENT_TIMEOUT_S"]`.
- Signatures older than 5 minutes are rejected.
- Repeating an `idempotency_key` is a no-op.
- Alternatively, append the same JSON lines to a file and run `sentinelai ingest events FILE --follow`.

## 5. See incidents

```sh
sentinelai incidents list                 # open incidents with class prior and patch candidacy
sentinelai incidents show INC-1000        # reasons, suspected release, signals, fingerprints
sentinelai detect run --at 2026-11-04T18:00:00Z --once   # replay detection at an instant
```

| Classification | Meaning | Code fix appropriate? |
|---|---|---|
| `regression_from_release` | An app-code exception first seen after the latest deploy | Candidate |
| `code_defect` | An app-code exception with no recent change | Candidate |
| `configuration` | The latest change before onset was a config change, or a connection setting changed before connection failures | No: revert or fix the config |
| `infrastructure` | Connection-type failures raised inside drivers/libraries across routes | No: restore the dependency |
| `external_dependency` | Downstream timeouts or 502/504 without a local change | No |
| `performance` | Latency rose without errors | Depends |
| `unknown` | No rule matched | No: needs a human |

## 6. Operating at scale

- **Storage scales with problems, not traffic.** Every record updates the 10 s aggregates, but raw rows are kept only for warnings, errors, 5xx responses and a deterministic sample. Run `SELECT retention_purge(now() - interval '7 days')` as `r_maint` daily.
- **Ingestion is stateless and idempotent:** run several `sentinel-ingest` replicas behind a load balancer.
- **Detection runs as one process per deployment.** It is replay-safe: a restarted detector reaches the same decisions, and re-running a tick changes nothing.
- **Defaults:**
  - 5xx ratio above max(5%, baseline + 4σ) with at least 20 requests in 30 s.
  - A new fingerprint with at least 10 occurrences in 2 min.
  - p95 latency above max(2 × baseline, baseline + 500 ms).
  - Every rule must hold on consecutive 10 s evaluations.
  - Baselines exclude windows owned by open incidents.
- **Tenancy:** each deployment starts with one `default` tenant. Row-level security fails closed. It guards against application bugs, not against a compromised Sentinel process (see `docs/design/04` §9).

## 7. Try it on the demo

```sh
uv run python demo/democtl.py init
docker compose --profile core --profile sentinel --profile sentinel-demo --profile demo up -d --build
uv run python demo/democtl.py traffic --count 300
uv run python demo/democtl.py inject S01        # or S22 (config), S33 (database outage)
uv run python demo/democtl.py traffic --count 600
uv run sentinelai incidents list                # with SENTINELAI_DATABASE_URL pointing at the stack
```

The `sentinel-demo` profile tails the demo's log and event files exactly as it would tail yours.
