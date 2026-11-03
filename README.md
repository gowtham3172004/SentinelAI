# Sentinel AI

An evidence-driven production reliability engineer. It detects incidents, investigates them against the deployed code, reproduces failures in a sandbox, and prepares verified fixes that a human approves. When the evidence doesn't justify a code change, it says so.

> **Status: Phase 0 (foundations).** What works today is the terminal CLI, the provider-independent model gateway core, and the environment checks. The investigation workflow is built in later phases. See [docs/design/10-roadmap.md](docs/design/10-roadmap.md).

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

Docker, WSL2 and the Linux sandbox host are needed from Phase 1 and Phase 4. `sentinelai doctor` tells you what's missing.

## Documentation

- [docs/README.md](docs/README.md): index of the consolidated specification.
- [docs/design/13-phase-0-plan.md](docs/design/13-phase-0-plan.md): the current phase.

## Licence

Apache-2.0 (provisional, pending the dependency licence review in P0-13). See [LICENSE](LICENSE) and [NOTICE](NOTICE).
