# Phase 3 V3-Core source qualification

This runbook performs a bounded, read-only public source pass through the
existing V3-Core collector factory. It is evidence collection only; it does
not admit Phase 3, activate a venue, or create order authority.

The runner uses these current reviewed endpoints:

- Coinbase Exchange Sandbox REST host for native BTC/ETH market reads;
- Deribit public derivatives context;
- SEC official press-release RSS;
- GDELT public document search.

It does not load `secrets.env`. Coinbase production hosts, transfer paths, and
withdrawal paths are rejected. Native source failures remain failures; the
runner never substitutes a different venue or symbol.

## Run

From the repository root:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/qualify_phase3_sources.py \
  --real \
  --evidence-dir artifacts/phase3/source-qualification
```

The explicit `--real` flag is required. The command makes a small bounded set
of HTTPS reads, writes raw response bytes to the run's ignored `raw-http/`
directory before parsing, replays successful parser results from those bytes,
and writes a sanitized summary plus `evidence-manifest.json`. The summary
contains response hashes, statuses, counts, replay state, quality findings,
and error classes/status codes only; it does not contain response bodies,
headers, credentials, or account state.

## Current evidence

The latest run is:

```text
artifacts/phase3/source-qualification/20260810T044558.818461Z/phase3-v3-core-source-qualification.json
evidence SHA-256: d435e99b59d815700ccfc5d75e309632ecc91fa1aea3cd3b6c7157a02df272bf
```

BTC-USD native ticker, Deribit BTC index, and SEC RSS passed the bounded
raw-spool/replay/quality checks. The current Coinbase Sandbox has no
`ETH-USD` product, and GDELT returned HTTP 429. The report therefore remains
`EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE`.

## Limitations

REST bootstrap evidence cannot prove WebSocket sequence continuity, reconnect
recovery, duplicate message handling under a live feed, or a continuous
freshness soak. Those must be collected separately on the reviewed source
routes. A source outage or rate limit is recorded as missing data and must fail
closed; it must not be hidden behind a fallback source.

Earlier failed runner attempts are preserved with incident records under their
immutable evidence roots and are not concatenated into the current evidence.

## Coinbase Sandbox WebSocket qualification

The bounded WSS probe is
[`scripts/qualify_phase3_coinbase_wss.py`](../../scripts/qualify_phase3_coinbase_wss.py).
It is pinned to the reviewed public sandbox feed
`wss://ws-feed-public.sandbox.exchange.coinbase.com`, subscribes only to the
public `BTC-USD` `ticker` and `heartbeat` channels, and uses the existing
`RawWebSocketFeed`/`RawMessageSpool` boundary. It does not load credentials or
open an execution path. Production WSS hosts, paths, queries, and credentials
are rejected before any network call.

Run it only with the transition extra installed:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/qualify_phase3_coinbase_wss.py \
  --real \
  --evidence-dir artifacts/phase3/coinbase-wss-qualification
```

The runner makes two bounded connections by default. Each connection has its
own append-only raw spool, records subscription acknowledgement and provider
sequence metadata, parses/replays only typed ticker events, and records
control-message/error classes without copying provider payloads into the
summary. Provider sequence gaps or reordering fail the bounded probe.

The latest real evidence is:

```text
artifacts/phase3/coinbase-wss-qualification/20260810T044142.351959Z/phase3-coinbase-wss-qualification.json
evidence SHA-256: a41fa2367a7f940e8197d5f8e0188765f9c522086091f93df988e0b2abbde702
```

Both connections completed their 12-second windows, received subscription
acknowledgements, 29 ticker messages and 23 heartbeats in total, and replayed
all 29 ticker events deterministically. Freshness passed on both connections:
maximum provider-event age was 2.078 seconds and maximum heartbeat interval
was 1.015 seconds. The provider nevertheless reported non-consecutive
sequence values on both connections (21 gaps/141 missing sequence values on
the first and 25 gaps/177 on the second), so the report is
`EXTERNALLY_MEASURED / PENDING_EXTERNAL_EVIDENCE`; no WSS qualification or
Phase-3 admission is claimed. A longer freshness soak, level-2 recovery
strategy, and source-disagreement evidence remain separate requirements.

## Coinbase Sandbox level-2 book qualification

The delivery-guaranteeing public book path is qualified separately by
[`scripts/qualify_phase3_coinbase_level2.py`](../../scripts/qualify_phase3_coinbase_level2.py).
It is pinned to the same reviewed Sandbox WebSocket host, accepts only the
reviewed `level2` and `level2_batch` channels, and uses the existing raw-first
spool. The reducer requires one snapshot before updates, validates positive
prices, non-negative sizes, valid sides, and an uncrossed best bid/ask, then
replays the raw bytes and compares the final book-state hash. The summary
contains hashes and counts only; it does not copy provider payloads.

Run with the locked transition environment and explicit public-read opt-in:

```bash
PYTHONPATH=. /tmp/advisorai-v3-full-verify-20260809/bin/python \
  scripts/qualify_phase3_coinbase_level2.py \
  --real --channel level2_batch \
  --evidence-dir artifacts/phase3/coinbase-level2-qualification
```

The direct `level2` channel delivered heartbeats but no snapshot in its
bounded run. The public `level2_batch` run produced one BTC-USD snapshot, 79
updates, and 12 heartbeats with zero validation failures, matching live/replay
book-state SHA-256, maximum event age 0.576 seconds, and maximum heartbeat
interval 1.081 seconds. Its immutable report is
`artifacts/phase3/coinbase-level2-qualification/20260810T052805.696329Z/phase3-coinbase-level2-qualification.json`
with SHA-256
`dc620a8fa41458fa4f89396e33687b13750461a3cd643be1b18d0588092e23de`.
This closes only the bounded level-2 source-smoke subcheck. Longer freshness,
reconnect/recovery, source disagreement, and the Phase-3 admission gate remain
pending.

## Binance Spot Testnet depth qualification

The credential-free Binance depth qualifier is
[`scripts/qualify_phase3_binance_spot_testnet_depth.py`](../../scripts/qualify_phase3_binance_spot_testnet_depth.py).
It uses only the reviewed public Spot Testnet REST host
`testnet.binance.vision` and stream host `stream.testnet.binance.vision`.
Each fresh BTCUSDT/ETHUSDT connection writes raw WebSocket bytes before
interpretation, captures a REST depth snapshot, validates Binance `U/u`
sequence continuity and an uncrossed book, and compares live processing with
raw-spool replay. Before interpreting the depth events it also spools the
provider `/api/v3/time` response and records a bounded midpoint provider/local
clock offset. Freshness retains both raw future-event counts and the adjusted
fail-closed result; an invalid or excessive offset aborts the connection. It
never loads credentials and never submits an order.

Run it only in the locked transition environment with explicit public-network
opt-in:

```bash
PYTHONPATH=. uv run --extra transition python \
  scripts/qualify_phase3_binance_spot_testnet_depth.py \
  --real \
  --duration-seconds 20 \
  --connections 2 \
  --evidence-dir artifacts/phase3/binance-spot-testnet-depth
```

The latest immutable run is
`artifacts/phase3/binance-spot-testnet-depth/20260810T173135.489992Z/phase3-binance-spot-testnet-depth.json`
with SHA-256
`b794c7fd2c014c89928c7bf2ad4b73fde253a615818dddd27a4da53a025c76c0`.
Four connections (two BTCUSDT and two ETHUSDT) captured four REST snapshots
and 289 depth updates with matching live/replay final-book hashes. All four
fresh connections completed. All received Binance event
timestamps were ahead of local receipt, so the freshness result failed closed;
the report records the provider/runtime hashes, WebSocket dependency version,
and sanitized failure classes. The deterministic injected REST-outage,
sequence-gap, stale-data, and snapshot-disagreement drills passed. This is
real partial source evidence, not Phase-3 admission. Clock-synchronized
freshness, recovery/resubscription, longer unattended operation, and
independent source-disagreement evidence remain pending.

The clock-offset measurement was added after the bounded report above. That
older report remains immutable evidence from the pre-offset runner; no real
post-change freshness claim exists until a new provider-available run passes
the complete raw-spool, clock, recovery, and replay checks.

A fresh requested 120-second run on 2026-08-10 was preserved separately at
`artifacts/phase3/binance-spot-testnet-depth/20260810T182011.404029Z/phase3-binance-spot-testnet-depth.json`
with SHA-256
`7b249a125c78e346c7b9d028850e2b7cbf004c890e005bad6f6f8d70b92ddd08`.
All four public WSS attempts failed closed before their first message with the
sanitized `WebSocketTransportError` class; no REST snapshot or write was
attempted. The deterministic drills still passed. This is a provider/runtime
availability failure, not a longer-operation pass; preserve it and do not
concatenate it with the earlier bounded root.
