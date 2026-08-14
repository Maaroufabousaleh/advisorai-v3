# Installation

AdvisorAI V3 has a lightweight Python base environment and explicit optional extras. The base environment is enough for the deterministic contracts, local data/ledger code, tests, configuration validation, and most evidence fixtures.

## Requirements

- Python `>=3.12` (declared in [`pyproject.toml`](../../pyproject.toml)).
- [`uv`](https://docs.astral.sh/uv/) for dependency and command execution.
- Node.js/npm only for the React dashboard; the launcher checks for both.
- `curl` only for the dashboard launcher's local API health check.

The documented dashboard launcher is a POSIX shell script. The project design targets a Windows/WSL2 workstation, but this repository does not publish a separate platform support matrix.

## Python environment

From the repository root:

```bash
uv sync --group dev
```

This installs the base dependencies and the development group from the lockfile. Confirm the environment without contacting a provider:

```bash
uv run python -c "from pathlib import Path; from advisorai.config import load_v3_core_config; c=load_v3_core_config(Path('configs/v3_core.yaml')); print(c.universe, c.execution)"
```

Expected output:

```text
('BTC', 'ETH') paper_testnet_only
```

## Optional extras

Install an extra only for the workflow that needs it:

| Extra | Adds | Typical use |
| --- | --- | --- |
| `dashboard` | FastAPI, Uvicorn, Argon2 | Run the optional dashboard API and protected auth |
| `transition` | `websockets` | Opt-in real connector smoke/qualification commands |
| `models` | LightGBM and Transformers | Local model candidates and CPU model adapters |
| `runtimes` | PydanticAI/Graph, Prefect, Hamilton, LiteLLM, NautilusTrader | Runtime seams that remain phase-gated |

For a fully provisioned local development environment:

```bash
uv sync --group dev --extra runtimes --extra dashboard --extra transition --extra models
```

Installing an extra records availability only. The runtime wrappers still require the relevant phase admission record before they are treated as production-authoritative.

## Dashboard dependencies

The one-command launcher installs the dashboard's locked npm dependencies on first use. To install them explicitly:

```bash
uv sync --extra dashboard
npm ci --prefix dashboard
```

Then use:

```bash
./scripts/launch_dashboard.sh
```

See [Quickstart](quickstart.md) and [Operator console](../guides/operator-console.md) for the local and protected flows.

## Local state

Runtime evidence and temporary state belong under `artifacts/`, which is ignored except for its `.gitkeep`. SQLite/Parquet/database outputs are ignored by the repository's [`.gitignore`](../../.gitignore). Do not add generated artifacts, model weights, credentials, or local databases to a change.

There is no Dockerfile, compose file, package console entry point, or deployment installer in this checkout. The service registry is an ownership manifest, not a process supervisor; see [Components](../reference/components.md).
