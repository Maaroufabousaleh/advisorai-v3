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

## Durable multi-source qualification

The restartable Phase-3 runner is
[`scripts/run_phase3_public_data_qualification.py`](../../scripts/run_phase3_public_data_qualification.py).
It is public/read-only, keeps each provider and symbol in its own raw spool,
hash-chains samples and health transitions, and never calls the execution
transport. A multi-hour run is started explicitly:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/run_phase3_public_data_qualification.py \
  --real \
  --run-directory artifacts/phase3/public-market-data-durable/<new-run-id> \
  --duration-hours 4 \
  --cycle-seconds 90 \
  --window-seconds 10
```

Each cycle records provider/local timestamps, clock offset and drift, event
age distributions, connection/reconnect/resubscription counts, sequence and
snapshot recovery results, raw-spool hashes, replay equivalence, source-health
transitions, and sanitized failure classes. When a source exposes event
timestamps, the cross-source disagreement record also measures the difference
between the two source freshness ages. If a source does not expose a usable
event timestamp, freshness remains explicitly unmeasured; it is never replaced
with a local timestamp. Severe disagreement or missing clock confidence stays
fail-closed and triggers no-trade/tighter-confidence policy.

Do not resume a root after changing its code identity or bounds. Review its
immutable summary with the separate validator and admission evaluator; a
completed window remains evidence for review until the gate record is
independently passed.

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

A fresh post-clock-offset sample on 2026-08-10 used one BTCUSDT and one ETHUSDT
connection for the bounded 20-second window. Both connections failed closed
before the first message with the sanitized `WebSocketTransportError` class;
the run made zero REST calls, captured zero raw messages, and still passed all
deterministic fault drills. Its immutable report is
`artifacts/phase3/binance-spot-testnet-depth/20260810T185425.534127Z/phase3-binance-spot-testnet-depth.json`
with SHA-256
`daee289fd1373477c5c22f4b792ff4e07b452c93e4544e21f757dde7080e9831`.
This is preserved as a post-change provider/runtime availability failure; it
does not create clock-synchronized freshness, reconnect, or Phase-3 admission
evidence.

## Binance Spot Testnet WebSocket layer diagnosis

Use the credential-free diagnostic before interpreting a WSS failure as a
provider outage:

```bash
PYTHONPATH=. /tmp/advisorai-v3-full-verify-20260809/bin/python \
  scripts/qualify_phase3_binance_wss_diagnostic.py \
  --real \
  --evidence-dir artifacts/phase3/binance-wss-diagnostic
```

The diagnostic checks DNS, TCP, TLS, direct public streams, valid subscription
acknowledgements, first-message timing, close/error classes, and bounded
reconnect behavior. It does not send malformed subscriptions, load
credentials, or call an order endpoint. The `.venv` may lack the transition
WebSocket dependency; use the locked transition environment and preserve that
local-runtime result separately rather than installing into the active model
worker environment.

The latest locked-runtime evidence is
`artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json`
with SHA-256
`8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b`.
DNS/TCP/TLS passed; successful BTC/ETH attempts received public messages and
valid subscriptions were acknowledged, while one ETH attempt timed out. The
classification is intermittent `websocket_connection_timeout`, not a generic
provider-unavailable result. The earlier local missing-library report is
`bc08d878e70193368bea67981a24ba3033704314e61626f7c796951caa13da9f`.

## Separate public market-data plane

The V3-Core public-data connector is intentionally separate from execution. It
uses reviewed, credential-free HTTPS/WSS cards and exposes no account, order,
cancel, transfer, or withdrawal operation. The selection runner is:

```bash
PYTHONPATH=. /tmp/advisorai-v3-full-verify-20260809/bin/python \
  scripts/qualify_phase3_public_market_data.py \
  --real \
  --duration-seconds 15 \
  --connection-rounds 2 \
  --evidence-dir artifacts/phase3/public-market-data-qualification
```

The current selected primary is Binance public market data for BTCUSDT and
ETHUSDT. Its immutable selection evidence is
`artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`
with SHA-256
`14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`.
That report proves four full public BTC/ETH WSS windows, two reconnects per
symbol, adjusted freshness after provider/local clock correction, and
cross-source top-of-book observations in addition to product/filter, book,
trade, and server-time truth. It does not admit unattended source operation.
Longer
freshness, reconnect/resubscription, sequence-gap and snapshot recovery,
stale-feed fail-closed, REST/WSS outage recovery, duplicate/out-of-order
handling, independent-source disagreement, and explicit failover without
silent substitution remain required.

The execution chain remains
`public read-only source -> normalized V3-Core data -> models/council/target ->
RiskKernel -> OMS -> Binance Spot Testnet transport`. The public source does
not receive the paper venue credentials, and the execution adapter retains its
testnet-only host guard.

## Durable source-health qualification

The longer Phase-3 qualification runner is
[`scripts/run_phase3_public_data_qualification.py`](../../scripts/run_phase3_public_data_qualification.py).
It is credential-free and read-only: its immutable config records
`credentials_loaded=false` and `order_writes_attempted=false`. It writes an
atomic heartbeat/status projection plus append-only hash-chained
`samples.jsonl`, `observations.jsonl`, `source-selection.jsonl`,
`disagreement.jsonl`, and `health-transitions.jsonl`; a run root is resumed only
when its immutable code/policy/config identity matches.

The latest completed engineering window is
`artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`.
It reached `multi_hour_window_complete` at
`2026-08-11T03:14:39.940009Z` with 63 cycles and 378 samples. The offline
validator
`artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json`
has SHA-256
`efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca` and
returned `PASS_FOR_REVIEW` / `evidence_for_review_only` with
`phase3_admission=false` and no validator issues. The run recorded 35
disconnects, 25 reconnects, 252 resubscriptions, three snapshot-recovery
attempts, zero sequence gaps, one out-of-order event, three replay failures,
and 22 severe disagreement observations. All 126 source selections failed
closed with zero silent substitutions. Final Binance sources were stale,
Coinbase sources quarantined, and Deribit sources degraded; this is measured
fail-closed behavior, not source admission.

The separate corrected v2 resource sidecar at
`artifacts/phase3/public-market-data-resource-monitor/20260811T025102Z-pid13339-v2`
reached `deadline_reached` with 32 observations and no resource errors. Its
summary SHA-256 is
`42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd`.
The qualification process, sidecar service, and PID `13339` are no longer
running; the failed v1 sidecar remains preserved for audit.

An independent one-cycle recheck at
`artifacts/phase3/public-market-data-durable/20260811T034114Z-one-cycle-recheck`
made six public connections and received 503 valid events with no credentials
or order writes. Its summary SHA-256 is
`698ad40af908757a398d19c6df83e4bfc50209bca541fe8b3acd6c314d6eff1e`.
Binance BTC/ETH again ended stale at `5.096588s`/`5.011760s` under the
5-second health policy, while Coinbase was quarantined and Deribit degraded.
This corroborates the source-health blocker; it is not an admission result and
does not justify changing the policy or silently substituting a source.

Start a fresh bounded engineering window with an explicit run root:

```bash
PYTHONPATH=. /tmp/advisorai-v3-full-verify-20260809/bin/python \
  scripts/run_phase3_public_data_qualification.py \
  --real \
  --run-directory artifacts/phase3/public-market-data-durable/<immutable-run-id> \
  --duration-hours 2 \
  --cycle-seconds 90 \
  --window-seconds 10
```

For a detached run, use a durable host-supported supervisor such as
`systemd --user` or `setsid nohup`; retain the exact command, PID/service
identity, evidence root, code hash, config hash, heartbeat, and stop/restart
procedure. Resume the same root with the same command only after confirming the
prior process is absent and the lock is recoverable. Never concatenate roots or
backdate elapsed time.

Each cycle records source/symbol provider and endpoint identity, provider and
local timestamps, offset/drift, freshness percentiles, connection and recovery
counters, sequence/duplicate/order findings, snapshot/replay hashes, health
transitions, source disagreement, failover decisions, downtime, and sanitized
failure classes. A gap invalidates the local incremental book; recovery requires
a provider-truth snapshot and a proven continuation boundary. Severe source
disagreement abstains; if no independent candidate satisfies the minimum
contract, selection fails closed. A source cannot silently become another
provider.

The runner's completion is evidence for review only. It does not open Phase-3
admission; stale, disconnected, recovering, quarantined, or severe-disagreement
outcomes remain fail-closed. The read-only dashboard/API may project the latest
sanitized `latest-health.json` through `ADVISORAI_PHASE3_HEALTH_SNAPSHOT`; it
does not expose transport write methods or execution authority.

After a root reaches `multi_hour_window_complete`, validate it offline with
[`scripts/validate_phase3_public_data_qualification.py`](../../scripts/validate_phase3_public_data_qualification.py):

```bash
PYTHONPATH=. /tmp/advisorai-v3-full-verify-20260809/bin/python \
  scripts/validate_phase3_public_data_qualification.py \
  --run-directory artifacts/phase3/public-market-data-durable/<immutable-run-id> \
  --resource-monitor artifacts/phase3/public-market-data-resource-monitor/<monitor-run-id> \
  --output-root artifacts/phase3/public-market-data-validation/<validation-run-id>
```

The validator reloads every hash-chained log, checks cycle/pair completeness,
completion timing, credential/write separation, fail-closed selection,
replay/sequence findings, raw-spool growth, and the optional OS-resource
sidecar. A successful result is explicitly
`PASS_FOR_REVIEW` / `evidence_for_review_only` with `phase3_admission=false`;
it is not a promotion mechanism.

### OS resource sidecar

The source runner's source observations are supplemented by the separate
read-only [`scripts/monitor_phase3_process_resources.py`](../../scripts/monitor_phase3_process_resources.py)
sidecar. It must write to a separate evidence root and never append to or
rewrite the qualification root. Before starting it, capture the target PID's
stable Linux `/proc/<pid>/stat` start-tick field and SHA-256 of its complete
command line, then pass both values explicitly. Start ticks are used instead
of `psutil.create_time()` because WSL boot-time reporting is not stable on all
hosts:

```bash
PYTHONPATH=. /tmp/advisorai-v3-full-verify-20260809/bin/python \
  scripts/monitor_phase3_process_resources.py \
  --pid <qualification-pid> \
  --expected-start-ticks <proc-stat-start-ticks> \
  --expected-command-sha256 <command-line-sha256> \
  --target-root artifacts/phase3/public-market-data-durable/<immutable-run-id> \
  --evidence-dir artifacts/phase3/public-market-data-resource-monitor/<monitor-run-id> \
  --until <target-end-timestamp> \
  --interval-seconds 30
```

The sidecar records sanitized RSS, VMS, CPU, threads, file descriptors,
internet connections, target-root file count/bytes, process identity, and a
hash-chained append-only observation log. It records no command text, response
bodies, credentials, or private venue state. An identity mismatch fails closed;
the sidecar is resource evidence only and cannot open Phase-3 admission.
