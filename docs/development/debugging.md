# Debugging and troubleshooting

## The dashboard API will not start

Install the optional web dependencies:

```bash
uv sync --extra dashboard
uv run --extra dashboard python -m advisorai.api.dashboard_server
```

The API is bound to `127.0.0.1:8787`. Check it before debugging the Vite UI:

```bash
curl http://127.0.0.1:8787/api/v1/health
```

If the API is healthy but the UI is not, install the frontend lockfile and run `npm run build --prefix dashboard` to separate a frontend build issue from a runtime proxy issue.

## The UI shows synthetic data

This is expected when the API is unavailable or when no ledger-backed dashboard projection is configured. The UI labels the source as `SYNTHETIC SNAPSHOT`; it is a fixture for local development. Configure `ADVISORAI_DASHBOARD_LEDGER_PATH` only after reviewing the state root and the projection behavior. A synthetic dashboard is never evidence of market health, P&L, or venue state.

## Protected mode rejects login

Check the auth status endpoint and confirm that the process has both `ADVISORAI_DASHBOARD_PASSWORD_HASH` and `ADVISORAI_DASHBOARD_TOTP_SECRET` without printing their values:

```bash
curl http://127.0.0.1:8787/api/v1/auth/status
```

Generate fresh material with `scripts/bootstrap_dashboard_auth.py`; use a password of at least 12 characters and a valid six-digit TOTP code. If the browser has a stale CSRF value, sign out/clear the session and log in again.

## Configuration validation fails

Run the typed bundle check from [Configuration](../getting-started/configuration.md). For transition identity/endpoint issues, use:

```bash
uv run python scripts/check_transition_config.py --secrets secrets.env
```

The secrets parser accepts a restricted `export NAME=value` format and never executes the file. Do not fix a parse failure by sourcing it.

## An optional runtime is installed but not usable

Installed dependency availability and phase admission are intentionally separate. Inspect the relevant Phase 0/8 evidence, gate record, runtime pin, and runbook. A missing or expired gate should produce a refusal even when an import succeeds. This behavior is a safety boundary, not an installation bug.

## Tests fail only with the normal pytest command

Use the repository's isolated command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
```

The environment may contain unrelated pytest plugins. If a targeted test fails, run its architectural area first, then the full suite.

## State and artifacts are hard to find

Evidence scripts default to ignored paths under `artifacts/`. The data lake and SQLite ledger roots are constructor/configuration inputs rather than a single global directory. Search the command invocation or the configured `ADVISORAI_DASHBOARD_LEDGER_PATH`/bundle path. Do not inspect or attach a populated secrets file when reporting a problem.

## A cycle abstains or an order is not routed

Inspect the recorded `RuntimeStage` and reasons. Common intentional reasons are a tripped kill switch, cadence not ready, snapshot after cutoff, invalid data quality, an evidence gate deficit, an expired decision, risk rejection, order-limit breach, or ambiguous acknowledgement pending reconciliation. The correct debugging path is to inspect the relevant evidence and ledger event, not to bypass the check.
