# Binance Spot Testnet paper connector

This runbook covers the provider-specific Binance Spot Testnet REST adapter.
It preserves the canonical execution chain:

```text
target → RiskKernel → OMS → BinanceSpotTestnetTransport → reconciliation
```

The reviewed paper boundary is:

- REST: `https://testnet.binance.vision`
- WebSocket API: `wss://ws-api.testnet.binance.vision/ws-api/v3`
- public stream: `wss://stream.testnet.binance.vision/ws`

The adapter rejects production Binance hosts, live environments, `/sapi`,
futures/margin API families, transfer paths, and withdrawal paths. It is
constructed only from `CredentialResolver.resolve(CredentialScope.PAPER_VENUE)`.
Do not add a second secrets file or a second venue ledger.

## Official protocol

Review the pinned [Binance Spot API documentation](https://github.com/binance/binance-spot-api-docs)
and [Spot Testnet documentation](https://github.com/binance/binance-spot-api-docs/tree/master/testnet)
before changing the adapter. Private REST requests use the Binance API key
header and an HMAC-SHA256 signature over the sorted query string. Signed writes
are never retried automatically because a timeout leaves acknowledgement state
ambiguous; the OMS must reconcile venue truth before any retry decision.

## Zero-network configuration check

Use the canonical repo-local operator file and review names/identities only:

```bash
./.venv/bin/python scripts/check_transition_config.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --venue-allowed-host testnet.binance.vision
```

The check must identify `binance_spot_testnet`, `paper_testnet`, and the exact
reviewed REST host. It must make zero network calls. Never source the file
wholesale and never print any credential value.

## Public product truth

The bounded public qualifier performs no authenticated operation and never
writes an order:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/qualify_binance_spot_testnet_public.py \
  --evidence-dir artifacts/phase2/binance-spot-testnet/public-truth
```

The required product mappings are provider-truth assertions for `BTCUSDT` and
`ETHUSDT`; they are not hard-coded order permission. Preserve each immutable
run and its SHA-256.

## Authenticated read-only smoke

After the zero-network check has been reviewed, run this explicit opt-in smoke
with the current configuration hash:

```bash
ADVISORAI_RUN_NETWORK_SMOKE=1 \
  ./.venv/bin/python scripts/smoke_binance_spot_testnet.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --configuration-hash <zero-network-binance-config-sha256> \
  --evidence-dir artifacts/phase2/binance-spot-testnet/read-only-smoke
```

The runner verifies, in order:

1. server time and exact reviewed host;
2. the live product catalogue;
3. `BTCUSDT` and `ETHUSDT` mappings, status, filters, and common quote asset;
4. authenticated account state;
5. balances and spot-position projection;
6. open orders; and
7. fills for the admitted symbols.

It persists only schema/count/latency/error-class information, provider
identity, adapter/config hashes, credential reference names, and network-call
count. It never persists account values, account identifiers, API headers,
signatures, or response bodies. If authentication, product truth, or any
read-only operation fails, the runner writes sanitized evidence and sends no
order.

## Paper lifecycle gate

No Binance order is allowed until a fresh immutable read-only evidence record
passes all operations. The first supervised lifecycle must use fake testnet
funds and the minimum practical post-only/limit size. It must persist intent,
pass the deterministic order-level `RiskKernel` check, enter the OMS before
submission, use the deterministic client identity, reconcile authoritative
venue state, cancel or ingest an actual fill, and persist sanitized TCA and
attribution evidence. Ambiguous acknowledgement, duplicate identity, changed
payload, reconnect, cancel race, stale state, divergence, restart, and kill
switch cases must be real-provider evidence where safely inducible and typed
fault-injection evidence otherwise.

Run the supervised qualification only after inspecting the read-only evidence;
both opt-in guards are required:

```bash
ADVISORAI_RUN_NETWORK_SMOKE=1 \
ADVISORAI_RUN_PAPER_LIFECYCLE=1 \
  ./.venv/bin/python scripts/qualify_binance_spot_testnet_lifecycle.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --configuration-hash <zero-network-binance-config-sha256> \
  --read-only-evidence artifacts/phase2/binance-spot-testnet/read-only-smoke \
  --evidence-dir artifacts/phase2/binance-spot-testnet/paper-lifecycle
```

The runner validates the immutable read-only pointer and performs exactly one
signed submission and, if the passive order remains open, one signed
cancellation. It never retries a signed write after an ambiguous result; it
queries venue truth first. The passing lifecycle run
(`20261114T014319.347437Z`, 1 submission, 1 cancellation, 0 fills) passed
restart recovery, TCA, attribution, duplicate protection, ambiguous-ack
handling, cancel-race, divergence, interruption, and kill-switch drills, but
it observed no fill itself. A real venue fill WAS observed in the earlier
failed run `20261114T005138.117654Z`, where the venue reproduced the
fill-during-cancel race (`order_cancel_pending` → `fill_recorded` → terminal
`filled`, zero cancel-ack events) and final reconciliation failed closed.
That failure is preserved evidence for the P03 race label, not a pass: a
post-fix fill match is still required, so certified fill ingestion remains
an open claim, not fixture evidence alone.

## Bounded testnet canary

After the lifecycle gate passes, the owner may run a bounded canary session
under an enforced wall-clock deadline and venue-boundary submission cap.
Run from a pinned commit on `review/testnet-launch-path` (check out the
exact SHA, never a moving branch tip, so the evidence maps to reviewed
code): fetch, check out the SHA named in the current handoff, then run.
Both bounds are explicit and required for any testnet run:

```bash
ADVISORAI_STATE_DIR=var/advisorai-testnet \
  uv run python scripts/launch_testnet_paper.py \
  --provider binance_spot_testnet --instrument BTCUSDT --quote USDT \
  --reason "owner testnet canary" --interval-seconds 10 \
  --max-runtime-seconds 120 --max-submissions 0
```

`--max-submissions 0` hard-refuses any submission at the venue boundary and
is the correct first canary: it proves SESSION READY, deadline stop, clean
reconciliation, and lease release with zero venue writes. Expect
`stop_reason=runtime_deadline_reached` followed by `outcome=clean`,
`reconciliation=reconciled`, and `leases_released=True`. Record the actual
submission count from the run output; zero is valid for the first canary.
Known limit at `b231632`: the quiet canary proves session mechanics only.
Cycles go FAILED before learning because the testnet reference decision
factory builds artifacts with short non-digest hashes, and fixing it alone
unmasks a second latent fault (outcome quality requires per-cycle HTTP
samples the fixture path never makes). Both must be fixed together before
the canary can exercise the decision path; see the P02 tracker line.
The launcher verifies the instrument and quote on the same transport before
hydrating the funded baseline, and fails closed on any out-of-profile
balance (`ValueError: testnet account holds out-of-profile balances; clear
them first`). If the canary stops there, run the read-only diagnostic before
retrying — it issues one `GET /api/v3/account` through the `PAPER_VENUE`
scope and reports only the rejected asset symbols plus which leg (`free` /
`locked`) is nonzero. It prints no credentials, amounts, prices, or full
account data:

```bash
uv run python scripts/diagnose_testnet_baseline.py --secrets secrets.env
```

Exit 0 means profile-compatible (compatible setup: BTC+USDT in any amounts,
every other asset exactly `free=0` and `locked=0` — check stuck open orders
for locked legs); exit 1 names the assets to clear first; exit 2 is an
input/transport error. Clear the flagged assets, then re-run the canary.
The coinbase sandbox profile has the same helper with its own legs
(`available` / `hold` over `GET /accounts`):

```bash
uv run python scripts/diagnose_testnet_baseline.py --secrets secrets.env --provider coinbase_exchange_sandbox
```

Never start the loop without both bounds.

## Fill-evidence validation (P03 prep)

Validate any paper-lifecycle run directory offline before claiming a venue
fill. The validator is read-only, credential-free, and network-free:

```bash
uv run python scripts/validate_binance_fill_evidence.py \
  --evidence-dir artifacts/phase2/binance-spot-testnet/paper-lifecycle/<run_id>
```

It checks manifest schema, single submission, fill linkage and quantity
match between OMS and account ledgers, fee agreement, the terminal
filled-to-reconciled chain, clean reconciliation, restart without
duplicate submission, and event-sequence consistency. Exit 0 certifies a
fill pass; exit 3 reports a cancellation-only pass, which can never
certify P03. Ledger reads go through a temporary copy, so evidence
directories are never modified.

## Canary-evidence validation (P02 prep)

Validate a stopped bounded-canary state directory offline. The validator
is read-only, credential-free, and network-free:

```bash
uv run python scripts/validate_testnet_canary_evidence.py \
  --state-dir var/advisorai-testnet --run-log canary.log
```

It checks the testnet session profile, a single clean `session_ended`
record, reconciled state, zero open orders, released leases, no
unfinished work, a recorded submission count from durable order events,
and the run-log markers (`SESSION READY`, deadline stop, clean outcome).
Exit 0 certifies the canary; any failure names the check. Ledger reads
go through a temporary copy, so state directories are never modified.

No model, LLM, Hermes task, research agent, browser task, dashboard, or venue
provider can bypass `RiskKernel` or mutate OMS truth. Transfers, withdrawals,
and production endpoints are prohibited.

## Read-only restart and configuration rollback

After the authenticated read-only smoke passes, the provider-specific recovery
check can measure configuration rollback and process-boundary hydration without
placing another order:

```bash
ADVISORAI_RUN_NETWORK_SMOKE=1 \
  ./.venv/bin/python scripts/qualify_binance_spot_testnet_recovery.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --configuration-hash <zero-network-binance-config-sha256> \
  --repository-root /mnt/c/projects/advisorai-v3 \
  --evidence-dir artifacts/phase1/binance-spot-testnet/recovery
```

The runner uses only the scoped `PAPER_VENUE` resolver. It records aggregate
read schema/count information, immutable bundle hashes, and a fresh child
process result. It never persists credential values, response bodies, headers,
signatures, account values, or order mutations. A passing report is measured
provider read-only recovery evidence, not full paper deployment rollback or
open-order/fill recovery evidence.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
