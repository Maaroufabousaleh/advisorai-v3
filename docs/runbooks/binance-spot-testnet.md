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
  --read-only-evidence-dir artifacts/phase2/binance-spot-testnet/read-only-smoke \
  --evidence-dir artifacts/phase2/binance-spot-testnet/paper-lifecycle \
  --ledger <new-supervised-ledger-path>
```

The runner validates the immutable read-only pointer and performs exactly one
signed submission and, if the passive order remains open, one signed
cancellation. It never retries a signed write after an ambiguous result; it
queries venue truth first. The first measured run passed the no-fill/cancel
path, restart recovery, TCA, attribution, duplicate protection, ambiguous-ack
handling, cancel-race, divergence, interruption, and kill-switch drills. It
did not observe a real fill, so fill ingestion remains fixture evidence rather
than an externally measured fill claim.

No model, LLM, Hermes task, research agent, browser task, dashboard, or venue
provider can bypass `RiskKernel` or mutate OMS truth. Transfers, withdrawals,
and production endpoints are prohibited.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
