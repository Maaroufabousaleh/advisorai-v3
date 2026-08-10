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
