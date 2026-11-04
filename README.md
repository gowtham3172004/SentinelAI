# Sentinel AI

An evidence-driven production reliability engineer. It detects incidents, investigates them against the deployed code, reproduces failures in a sandbox, and prepares verified fixes that a human approves. When the evidence doesn't justify a code change, it says so.

> **Status: milestone 2 (telemetry ingestion and deterministic incident detection).**
> - **Working today:** ingest logs from any stack (JSON files, NDJSON/HTTP, OTLP), fingerprint errors, detect incidents, correlate them with deploys and config changes, and classify code vs configuration vs infrastructure failures, with reasons.
> - **Not yet built:** the investigation, reproduction and patch workflow comes in later milestones.
>
> **Connect your project:** [docs/guides/connecting-your-project.md](docs/guides/connecting-your-project.md). Roadmap: [docs/design/10-roadmap.md](docs/design/10-roadmap.md).

## Quick start (Windows, Linux, macOS)

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/). Every command below works the same in PowerShell, cmd, bash and zsh.

```sh
git clone <repo-url> SentinelAI
cd SentinelAI
uv sync                          # creates .venv with locked dependencies
uv run sentinelai doctor         # what is ready on this machine, and what to fix
uv run sentinelai config init    # creates your private secrets file (outside the repo)
uv run sentinelai config path    # shows where it is
```

Add at least one model provider key to the secrets file (or set it as an environment variable):

```sh
GROQ_API_KEY=...        # primary (free tier)
GEMINI_API_KEY=...      # second provider, for independent evaluation
```

Then check what the provider actually offers:

```sh
uv run sentinelai providers list
uv run sentinelai providers probe groq                         # suggested models
uv run sentinelai providers probe groq -m openai/gpt-oss-120b  # a specific model
uv run sentinelai providers probe groq --out out/groq-probe.json
uv run sentinelai providers try groq llama-3.1-8b-instant "Say hello"
```

`probe` uses tiny prompts, runs sequentially, and stops on the first rate-limit response to protect free-tier quotas. Every command accepts `--json` for scripting.

### Supported providers

Any OpenAI-compatible endpoint works through one adapter:

| Provider | Key variable | Notes |
|---|---|---|
| groq | `GROQ_API_KEY` | Primary free tier |
| gemini | `GEMINI_API_KEY` | Different model family; judge candidate |
| openrouter | `OPENROUTER_API_KEY` | Free `:free` models |
| cerebras | `CEREBRAS_API_KEY` | |
| mistral | `MISTRAL_API_KEY` | |
| ollama | none (local) | `http://localhost:11434`; CPU-only machines: small models only |
| lmstudio | none (local) | `http://localhost:1234` |

Add others (Together, vLLM, a self-hosted gateway) in `providers.yaml` in the config directory:

```yaml
providers:
  together:
    base_url: https://api.together.xyz/v1
    api_key_env: TOGETHER_API_KEY
models:
  - {provider: together, model: meta-llama/Llama-3.3-70B-Instruct-Turbo, family: llama, tier: strong}
```

Plain `http://` is accepted only for loopback hosts, so API keys never travel unencrypted.

## Development

```sh
uv run pytest                 # unit + integration tests
uv run pytest --cov           # with coverage
uv run ruff format . && uv run ruff check .
uv run mypy                   # strict
uv run lint-imports           # architecture layering contracts
```

Layering (enforced by import-linter): `cli > apps > config > adapters > services > domain`. `domain` is pure (no I/O) and is unit-testable without a model, database or Docker.

### Database (PostgreSQL 16 + pgvector)

```sh
cp .env.example .env            # then set SENTINELAI_DEV_DB_PASSWORD (git-ignored file)
docker compose --profile core up -d --wait     # on Windows: run inside WSL, e.g.
#   wsl -d Ubuntu-24.04 --cd /mnt/d/PROJECTS/SentinelAI -- docker compose --profile core up -d --wait
uv run pytest tests/db          # creates a throwaway database, migrates, tests, drops it
```

The database tests find the server through `SENTINELAI_TEST_ADMIN_URL`, or else through `.env`. Without either, they're skipped with a reason (CI always runs them).

Migrations and audit verification use an administrator URL, which is never printed:

```sh
export SENTINELAI_ADMIN_DATABASE_URL=postgresql://postgres:...@127.0.0.1:55432/sentinel
uv run sentinelai db upgrade      # one transaction per command
uv run sentinelai db current
uv run sentinelai audit verify    # recomputes every audit hash chain; exit 1 on tampering
uv run sentinelai db grants-sql   # GRANTs generated from the authority matrix
```

**Windows + WSL2 note:** WSL stops its VM when idle, which stops Docker. The containers restart with Docker (restart policy). To keep the VM running, set `vmIdleTimeout=-1` under `[wsl2]` in `%UserProfile%/.wslconfig` (that is, `C:\Users\<you>\.wslconfig`).

Docker, WSL2 and the Linux sandbox host are needed from Phase 1 and Phase 4. `sentinelai doctor` tells you what's missing.

## Documentation

- [docs/README.md](docs/README.md): index of the consolidated specification.
- [docs/design/13-phase-0-plan.md](docs/design/13-phase-0-plan.md): the current phase.

## Licence

Apache-2.0 (provisional, pending the dependency licence review in P0-13). See [LICENSE](LICENSE) and [NOTICE](NOTICE).
