# Real API / paper-execution operations

This runbook is the operator hand-off for the
[real API and paper-execution transition plan](../plans/real-api-paper-transition.md).
It keeps the V3-Core scope fixed at BTC/ETH, one reviewed paper/testnet venue,
real read data, one direct LLM route, and local authoritative state. It does
not authorize live capital.

## What is implemented

- `advisorai.config.secrets` parses the ignored `export NAME=value` template
  without executing shell code, rejects live environments, and masks values.
- `advisorai.integrations` provides HTTPS host/retry/rate/circuit guards, raw
  WSS spooling, OpenAI-compatible typed output, HMAC paper/testnet order
  transport, explicit reviewed-host admission, deterministic cancellation,
  connector lifecycle cards, and fixed V3-Core collector factories.
- `advisorai.runtime.PaperRuntime` enforces closed hourly snapshots, the
  evidence → target → RiskKernel → OMS → paper venue chain, and durable cycle
  records. A reconciliation mismatch trips the independent kill switch.
- `NativeVenueAdapter` rejects unknown or malformed venue open-order identities
  during reconnect/reconciliation instead of silently dropping them. Its latest
  venue projection replaces, rather than accumulates, the prior open-order set.
  Ambiguous or reconnecting orders query the venue's order-by-client-ID path
  before falling back to open orders, normalize terminal rejection states, and
  reject a response that echoes a different client-order identity. A native
  cancel path persists `CANCEL_PENDING` before requesting venue acknowledgement.
- When a native adapter exposes the read-only account snapshot, `PaperRuntime`
  hydrates cash, positions, margin, and open-order projections before running
  reconciliation. Any transport/projection error or mismatch trips the
  independent kill switch and leaves the state closed for operator review.
- `advisorai.learning.PaperLearningLoop` records the complete decision chain,
  creates incidents for material failures, replays problems on frozen inputs,
  and records scorecards only after the forecast horizon closes.
- A configured ledger path makes the dashboard ledger-backed. An absent ledger
  path intentionally keeps the clearly-labelled synthetic UI fixture.

## Safe local setup

1. Keep populated secrets on the Linux filesystem, for example
   `~/.config/advisorai-v3/secrets.env`; do not put values in Git, prompts,
   browser/Hermes tasks, artifacts, or screenshots. If a value was exposed in
   an untrusted location, rotate it at the provider before use.
2. Fill the existing ignored `secrets.env` template with paper/testnet-only
   credentials and the selected direct LLM route. Do not add production hosts,
   withdrawal/transfer keys, or live venue permissions.
3. Validate locally without network calls:

   ```bash
   uv run python scripts/check_transition_config.py \
     --secrets "$HOME/.config/advisorai-v3/secrets.env"
   ```

4. Select explicit reviewed hosts and endpoints in the connector cards. A
   credential alone does not activate a connector; its lifecycle is
   `disabled → configured → smoke-tested → shadow → active-read` for read
   connectors and `disabled → smoke-tested → paper-only` for the venue.

## Explicit read-only integration smoke test

Network tests are opt-in and must use a dedicated testnet account. Run them
from the operator shell, never from a pull request or an untrusted capability:

```bash
uv sync --extra transition
ADVISORAI_RUN_NETWORK_SMOKE=1 \
  uv run python scripts/smoke_transition_connectors.py \
  --secrets "$HOME/.config/advisorai-v3/secrets.env" \
  --venue-allowed-host sandbox.example.test \
  --evidence-dir artifacts/phase1/paper-venue-transition
```

The command performs only account, open-order, fill, position, and balance
reads. Supply the selected venue's documented paper/testnet paths explicitly
when its generic defaults differ, for example:

```bash
ADVISORAI_RUN_NETWORK_SMOKE=1 \
  uv run python scripts/smoke_transition_connectors.py \
  --secrets "$HOME/.config/advisorai-v3/secrets.env" \
  --venue-allowed-host sandbox.example.test \
  --venue-account-path /v1/account \
  --venue-orders-path /v1/orders \
  --venue-fills-path /v1/fills \
  --venue-positions-path /v1/positions \
  --venue-balances-path /v1/balances
```

The script fails closed if configuration, scoped credentials, or the explicit
reviewed host allowlist are missing,
and emits only connector identity, status, counts, error class, and a
credential-free configuration hash. It must fail if the endpoint is not the
reviewed testnet host. A smoke test never submits or cancels an order and never
calls transfer/withdrawal endpoints; cancellation and order lifecycle checks
are performed only through the deterministic OMS/adapter contract tests until
the operator has selected and reviewed one provider-specific API.

## Running the paper loop

The runtime is a library boundary so the real venue's symbol, bar, account, and
fill schemas remain owned by its adapter. Wire these ports in a supervised
process:

```python
from advisorai.integrations import build_paper_venue_transport
from advisorai.execution import NativeVenueAdapter, OrderManager
from advisorai.runtime import PaperRuntime, PaperRuntimeConfig

# This must be the operator-reviewed testnet hostname, not merely the URL's
# hostname copied from secrets.
transport = build_paper_venue_transport(
    settings,
    allowed_hosts=("reviewed-testnet.example",),
)
venue = NativeVenueAdapter(
    venue=settings.venue_name,
    environment=settings.venue_environment,
    transport=transport,
    strict_venue=True,
)
# Construct the local ledgers/account, real collector snapshot provider, and
# AdvisorService decision builder, then pass them to PaperRuntime.
runtime = PaperRuntime(
    config=PaperRuntimeConfig(),
    snapshot_provider=collect_closed_snapshot,
    market_provider=build_risk_market_state,
    decision_builder=build_advisor_decision,
    account=account,
    risk_policy=risk_policy,
    orders=OrderManager(ledgers, venue),
    ledgers=ledgers,
)
runtime.run_forever()
```

Run this as a supervised process with restart and archive policies. Keep the
SQLite WAL, raw WSS spool, Parquet lake, configuration hash, and connector card
under the local state root. The dashboard can then point at the same ledger:

```bash
export ADVISORAI_DASHBOARD_LEDGER_PATH=/path/to/state/dashboard.sqlite3
./scripts/launch_dashboard.sh --protected
```

The dashboard is read-only with respect to orders and risk limits. Its guarded
paper halt/resume commands are control-plane requests recorded in the incident
ledger; they cannot enable live capital.

## Required operator work that the agent cannot perform

- Choose the venue/provider, create accounts, complete KYC/terms/billing, and
  request testnet access or data licenses.
- Generate, restrict, and rotate provider credentials; set IP allowlists,
  quotas, no-withdrawal/no-transfer permissions, and testnet-only scopes.
- Verify the provider's exact REST/WSS paths, symbol conventions, signing rules,
  rate limits, test-order/cancel semantics, and allowed hostnames.
- Move populated secrets to a protected Linux path, set file/host permissions,
  and rotate any value copied into an unsafe location.
- Run the explicit network smoke test and inspect redacted evidence for auth,
  schema, rate-limit, timeout, reconnect, account-read, and cancel behavior.
- Keep the supervised process online with correct system time, network,
  storage, power, backups, and alerting; perform restart, corruption, and
  archive-restore drills.
- Review incidents, approve corrective regression tests, and decide whether a
  challenger may enter shadow evaluation. The learning loop never self-promotes
  a model, route, prompt, source, or risk change.
- Accumulate the existing Phase 0 stability evidence and Phase 7 60-day soak
  evidence, then make the explicit human decision required by the authoritative
  V3 plan. No agent can attest to those timed external gates.

## Stop conditions

Stop the paper loop and leave the kill switch engaged for stale/gapped/future
data, malformed or late LLM output, repeated transport failures, ambiguous
acknowledgements, reconciliation mismatch, unexpected host/endpoint, resource
exhaustion, or any credential/secret exposure. Resume requires reconciliation,
an incident record, a reproducible replay, and an operator-owned review.
