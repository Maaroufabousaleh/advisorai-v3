# Configuration reference

AdvisorAI V3 loads reviewed YAML into typed, immutable configuration models. Operator/provider values are supplied separately through a parsed environment inventory and scoped credential resolver. Configuration availability is not phase admission.

## Reviewed YAML bundles

| File | Loader / purpose | Important current values |
| --- | --- | --- |
| [`configs/v3_core.yaml`](../../configs/v3_core.yaml) | Core universe and storage boundary | Crypto; `BTC`, `ETH`; 1h primary horizon; 5m observations; 4h context; `paper_testnet_only` |
| [`configs/agents/v3_core.yaml`](../../configs/agents/v3_core.yaml) | Evidence role roster and call budgets | Seven roles; minimum 3 factor families; minimum 2 source families; standard 2 remote calls; deep 4 |
| [`configs/execution/v3_core.yaml`](../../configs/execution/v3_core.yaml) | Venue and order policy | One paper/testnet venue; immediate/passive limit; GTC/IOC; reconcile ambiguous acknowledgements before retry; idempotency required |
| [`configs/risk/v3_core.yaml`](../../configs/risk/v3_core.yaml) | Hard limits and fail-closed policy | `risk-v3-core-v1`; stale-data rejects; independent kill switch; AI cannot loosen limits |
| [`configs/models/v3_core.yaml`](../../configs/models/v3_core.yaml) | Baselines and candidate model roster | Naive/drift/seasonal/linear/LightGBM baselines; CPU and GPU candidates; one GPU family at a time |
| [`configs/sources/v3_core.yaml`](../../configs/sources/v3_core.yaml) | Source identity, grade, parser, and intended use | Native venue, Deribit context, official RSS, GDELT, optional LSE corroboration |
| [`configs/resources/v3_core.yaml`](../../configs/resources/v3_core.yaml) | Host-wide resource envelope | Windows/WSL2 laptop target; approximately 11 GiB WSL; 1.5 GiB minimum headroom; bounded GPU/browser/DuckDB/CPU leases |
| [`configs/resources/envelopes.yaml`](../../configs/resources/envelopes.yaml) | Resource-envelope measurements/profile data | Reviewed envelope inputs for local admission and load shedding |
| [`configs/modes/*.yaml`](../../configs/modes/) | Operating-mode budgets | `trade_fast`, `standard`, `deep`, `builder`, `recovery` |

Validate the seven core bundles together:

```bash
uv run python -c "from pathlib import Path; from advisorai.config import load_v3_core_config, load_risk_config, load_agent_config, load_model_config, load_execution_config, load_source_registry_config, load_resource_config; root=Path('.'); values=(load_v3_core_config(root/'configs/v3_core.yaml'), load_risk_config(root/'configs/risk/v3_core.yaml'), load_agent_config(root/'configs/agents/v3_core.yaml'), load_model_config(root/'configs/models/v3_core.yaml'), load_execution_config(root/'configs/execution/v3_core.yaml'), load_source_registry_config(root/'configs/sources/v3_core.yaml'), load_resource_config(root/'configs/resources/v3_core.yaml')); print('validated', len(values), values[0].universe, values[0].execution)"
```

Expected output for the checked-in defaults is:

```text
validated 7 ('BTC', 'ETH') paper_testnet_only
```

## Risk limits

The checked-in `risk-v3-core-v1` policy defines these hard limits:

| Field | Default | Meaning |
| --- | ---: | --- |
| `max_gross_notional` | `1000` | Maximum gross notional |
| `max_net_notional` | `1000` | Maximum net notional |
| `max_order_notional` | `250` | Maximum one-order notional |
| `max_position_notional` | `500` | Maximum one-position notional |
| `max_leverage` | `1` | Maximum leverage |
| `max_turnover_notional` | `500` | Maximum turnover notional |
| `max_margin_used` | `500` | Maximum margin used |
| `price_collar_bps` | `100` | Price collar in basis points |

The policy also sets `stale_data_rejects: true`, `kill_switch: independent`, `ai_can_loosen_limits: false`, and `environment: paper_testnet`. Values are policy inputs, not observed account balances.

## Operating modes

| Mode | Memory ceiling | Remote LLM calls | GPU jobs | Primary use |
| --- | ---: | ---: | ---: | --- |
| `trade_fast` | 6.5 GiB | 0 | 1 | Low-latency paper path with no remote LLM |
| `standard` | 8.5 GiB | 2 | 1 | Normal council path |
| `deep` | 9.0 GiB | 4 | 1 | Expanded council/challenger work |
| `builder` | 9.0 GiB | 0 | 1 | Isolated Hermes/skill-foundry work |
| `recovery` | 8.0 GiB | 0 | 0 | Deterministic recovery first |

All modes declare 1.5 GiB minimum headroom. Mode admission does not change the hard risk policy or grant live authority.

## Environment and credentials

The ignored `secrets.env` template is parsed as data by `advisorai.config.secrets`; it is never sourced as shell code. Connector processes request one `CredentialScope` such as `direct_llm`, `public_data`, `paper_venue`, `archive_rclone`, or `event_bus`. There is no API that returns the complete master inventory.

Documented environment groups include:

| Group | Examples | Scope / notes |
| --- | --- | --- |
| Application | `ADVISORAI_ENVIRONMENT`, `ADVISORAI_CONFIG_DIR`, signing/encryption references | `internal_app`; values remain out of model/config representations |
| Model providers | `ADVISORAI_LLM_*`, provider API keys, LiteLLM/OpenRouter keys | `direct_llm`, `litellm`, or `omniroute` |
| Public data | `SEC_USER_AGENT`, FRED/ALFRED/BLS/BEA/Treasury/vendor keys | `public_data` |
| Paper/testnet venue | `ADVISORAI_VENUE_*`, venue-specific credentials | `paper_venue` or a narrower venue scope; environment must remain paper/testnet |
| Archive | Rclone, AWS, Azure, Google, or OmniCloud settings | The matching archive scope only |
| Event bus | NATS URL/user/password/NKey/creds file | `event_bus` |
| Dashboard | Password hash, TOTP secret, subject, cookie setting | `internal_app` plus dashboard process settings |

Use [Credential scopes](../runbooks/credential-scopes.md) and [Transition configuration](../getting-started/configuration.md) for safe validation. Never put a populated credential value in documentation.

## Dashboard process settings

These values are read directly by the optional dashboard process:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADVISORAI_DASHBOARD_DEV_MODE` | `0` (`1` in the local launcher path) | Disable auth for local UI development |
| `ADVISORAI_DASHBOARD_ALLOWED_ORIGINS` | empty | Explicit CORS origin list |
| `ADVISORAI_DASHBOARD_SESSION_TTL` | `900` seconds | Session lifetime |
| `ADVISORAI_DASHBOARD_IDLE_TTL` | `900` seconds | Idle lifetime |
| `ADVISORAI_DASHBOARD_STEP_UP_TTL` | `300` seconds | Step-up token lifetime |
| `ADVISORAI_DASHBOARD_LEDGER_PATH` | unset | Ledger-backed dashboard projection/receipts |
| `ADVISORAI_CONFIG_BUNDLE_PATH` | unset | Content-addressed config bundle root |
| `ADVISORAI_PHASE3_HEALTH_SNAPSHOT` | unset | Sanitized source-health projection |

See [Operator console](../guides/operator-console.md) before changing authentication mode.
