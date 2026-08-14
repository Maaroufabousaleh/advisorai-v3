# Configuration

Configuration has two deliberate layers:

1. **Reviewed YAML** defines the V3-Core scope, risk policy, source/model roster, resource limits, execution policy, and mission modes.
2. **Operator environment/secrets** supplies optional provider identities and dashboard/runtime settings. It is parsed as data and delivered by allowlisted credential scope; it is not sourced as shell code.

## Start with the repository defaults

The base configuration is already checked into the repository:

```text
configs/
├── v3_core.yaml              # fixed BTC/ETH V3-Core boundary
├── agents/v3_core.yaml       # role roster and remote-call budgets
├── execution/v3_core.yaml    # paper/testnet venue and order policy
├── models/v3_core.yaml       # baselines and model candidates
├── resources/v3_core.yaml    # laptop-wide resource caps
├── resources/envelopes.yaml  # measured mode/profile envelopes
├── risk/v3_core.yaml         # hard limits and fail-closed policy
├── sources/v3_core.yaml      # source grade and intended use
└── modes/*.yaml              # Trade/Fast, Standard, Deep, Builder, Recovery
```

Validate all seven typed bundles:

```bash
uv run python -c "from pathlib import Path; from advisorai.config import load_v3_core_config, load_risk_config, load_agent_config, load_model_config, load_execution_config, load_source_registry_config, load_resource_config; root=Path('.'); values=(load_v3_core_config(root/'configs/v3_core.yaml'), load_risk_config(root/'configs/risk/v3_core.yaml'), load_agent_config(root/'configs/agents/v3_core.yaml'), load_model_config(root/'configs/models/v3_core.yaml'), load_execution_config(root/'configs/execution/v3_core.yaml'), load_source_registry_config(root/'configs/sources/v3_core.yaml'), load_resource_config(root/'configs/resources/v3_core.yaml')); print('validated', len(values), values[0].universe, values[0].execution)"
```

See the [configuration reference](../reference/configuration.md) for field-level behavior and environment variables.

## Optional transition inventory

The ignored repository-local `secrets.env` is a blank-valued operator inventory. Use one copy only. Never run `source secrets.env`; the parser in `advisorai.config.secrets` accepts the small `export NAME=value` format without invoking a shell.

Validate identities, endpoint policy, and credential references without printing secret values or making a request:

```bash
uv run python scripts/check_transition_config.py --secrets secrets.env
```

The loader defaults `ADVISORAI_ENVIRONMENT` and `ADVISORAI_VENUE_ENVIRONMENT` to `paper_testnet`, rejects live values, requires HTTPS/WSS endpoint shapes, and rejects production-looking venue endpoints in paper/testnet configuration. An explicit reviewed host allowlist is still required before a connector is considered ready:

```bash
uv run python scripts/check_transition_config.py \
  --secrets secrets.env \
  --venue-allowed-host testnet.example.invalid
```

Use a real hostname only after an operator has reviewed the endpoint and its paper/testnet scope. Do not put credentials in documentation, command history, issue reports, or screenshots.

## Dashboard-only runtime settings

The dashboard launcher and FastAPI application read a small set of process environment variables that are intentionally separate from the provider inventory:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADVISORAI_DASHBOARD_DEV_MODE` | `0` (`1` in the unprotected launcher path) | Disable auth only for local development |
| `ADVISORAI_DASHBOARD_PASSWORD_HASH` | unset | Argon2id password hash for protected mode |
| `ADVISORAI_DASHBOARD_TOTP_SECRET` | unset | TOTP secret for login and step-up authentication |
| `ADVISORAI_DASHBOARD_SUBJECT` | `owner` | Audit subject returned after login |
| `ADVISORAI_DASHBOARD_COOKIE_SECURE` | `1` | Mark the session cookie Secure |
| `ADVISORAI_DASHBOARD_ALLOWED_ORIGINS` | empty | Explicit comma-separated CORS allowlist |
| `ADVISORAI_DASHBOARD_SESSION_TTL` | `900` seconds | Session lifetime |
| `ADVISORAI_DASHBOARD_IDLE_TTL` | `900` seconds | Idle session lifetime |
| `ADVISORAI_DASHBOARD_STEP_UP_TTL` | `300` seconds | Step-up token lifetime |
| `ADVISORAI_DASHBOARD_LEDGER_PATH` | unset | Reviewed SQLite WAL path for durable dashboard receipts/projections |
| `ADVISORAI_CONFIG_BUNDLE_PATH` | unset | Root for content-addressed config bundles used by dashboard proposals/rollback |
| `ADVISORAI_PHASE3_HEALTH_SNAPSHOT` | unset | Sanitized Phase 3 source-health projection file |

Protected deployments must configure password/TOTP material, use TLS when exposed beyond localhost, and set a deliberate origin allowlist. The repository does not provide a deployment supervisor or certificate manager.
