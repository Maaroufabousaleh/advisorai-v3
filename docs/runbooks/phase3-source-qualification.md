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
artifacts/phase3/source-qualification/20260810T041104.946822Z/phase3-v3-core-source-qualification.json
evidence SHA-256: 875ba39c05cdbb11e9fd4dcaded48f43bf2701a753bfcf20fb5d53a065470962
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
