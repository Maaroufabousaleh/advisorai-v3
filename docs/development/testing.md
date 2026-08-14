# Testing and checks

The repository uses pytest, Ruff, and the dashboard's Vite build. The test suite is organized by architectural concern rather than by one end-to-end application process.

## Standard checks

Run the base checks from the repository root:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
uv run ruff check .
npm run build --prefix dashboard
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` keeps unrelated globally installed pytest plugins from changing the local test environment. The repository's own pytest configuration remains active.

## Test areas

| Directory | Coverage focus |
| --- | --- |
| `tests/contracts` | Typed artifacts, ports, gateway policy and authority restrictions |
| `tests/config` | YAML loaders, safe environment parsing, and credential scopes |
| `tests/data`, `tests/point_in_time` | Collectors, immutable lake/query behavior, ledgers, and cutoff admission |
| `tests/agents`, `tests/api` | Mission routing, council/evidence gates, service decisions, and dashboard projection/API |
| `tests/execution`, `tests/runtime`, `tests/integrations` | Risk, OMS, paper runtime, venue adapters, market events, and recovery behavior |
| `tests/gates`, `tests/live`, `tests/recovery` | Phase admission, live guard, rollback, soak, and restart contracts |
| `tests/models`, `tests/learning` | Model authority, candidates, paper utility, and replay records |
| `tests/capabilities`, `tests/expansion` | Capability lifecycle, sandbox boundaries, archive and controlled expansion |
| `tests/resources`, `tests/services`, `tests/orchestration` | Resource admission, ownership manifest, and optional runtime wrappers |

## Phase-isolated acceptance

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py --phase 5
```

The acceptance runner starts a fresh pytest process per phase and stops at the first failure. Use a single `--phase` while debugging one boundary. A successful local phase suite is executable evidence only; it does not create the external timed Phase 0/7 records or Phase 10 human approval.

## Network and optional-provider tests

Most provider-facing tests use injected transports, fixtures, or explicit opt-in flags. Some integration/qualification scripts can contact public data, testnet venues, model providers, or archive endpoints when invoked with their real/network options or populated credentials. Review the associated runbook first and keep output under `artifacts/`.

Do not add a test that requires a network or secret to the default base suite. Prefer a deterministic fixture and assert the admission/failure behavior at the adapter boundary.

## What to verify with a change

- Contract change: targeted contract/port tests plus the full pytest suite.
- Data or snapshot change: immutable lake, query, collector, and point-in-time tests.
- Risk/order/runtime change: execution, runtime, integration, reconciliation, and gate tests.
- Dashboard/API change: `tests/api`, a local screenshot/manual smoke check, and the dashboard build.
- Documentation or config change: the config validation command, internal-link check, and any command/code example touched.
