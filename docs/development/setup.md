# Development setup

The repository is a Python package with an optional React/TypeScript dashboard. The supported Python version is declared as `>=3.12` in [`pyproject.toml`](../../pyproject.toml). The local development workflow uses `uv` and the checked-in lockfile.

## Base checkout

```bash
uv sync --group dev
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
uv run ruff check .
```

The environment includes the deterministic contracts, Parquet/DuckDB/Polars lake, SQLite ledgers, config loaders, test fixtures, and development checks.

## Optional environments

Install only the extras needed for the area you are changing:

```bash
uv sync --extra dashboard     # FastAPI, Uvicorn, Argon2id
uv sync --extra transition    # websocket transition probes
uv sync --extra models        # LightGBM and Transformers adapters
uv sync --extra runtimes      # optional orchestration/model/runtime seams
```

The dashboard also needs the locked frontend dependencies:

```bash
npm ci --prefix dashboard
npm run build --prefix dashboard
```

Do not assume that installing an extra admits the corresponding provider or runtime. The phase gate and policy layers remain authoritative.

## Local state and secrets

Use ignored paths such as `artifacts/` for evidence, test databases, Parquet roots, and generated bundles. Use a local copy of the ignored `secrets.env` template only when a workflow explicitly needs it, and pass it to the safe parser; do not source it. Never commit credentials, model weights, provider responses containing secrets, or generated local databases.

## Dashboard development

```bash
./scripts/launch_dashboard.sh
```

The launcher runs the API and Vite console together and cleans up both on exit. It is deliberately local and synthetic by default. Read [Operator console](../guides/operator-console.md) before using protected mode or a ledger-backed projection.

## Contribution boundaries

Start with [Components](../reference/components.md) and [Extending](extending.md). Keep probabilistic/provided work behind typed ports; keep risk/order authority in `execution`/`runtime`; keep operator controls in the dashboard projection/API rather than duplicating authority in the UI.
