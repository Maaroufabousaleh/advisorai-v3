# Phase-4 paper utility evidence

## Prospective TTM-R2 prediction boundary — current continuation

The prior forward acquisition root
`artifacts/phase4/v3core-forward/20260812T204505Z-first-independent-pit-r2/`
is sealed as `OPERATOR_INTERRUPTED / INCOMPLETE` after intentional operator
shutdown. It must not be resumed, extended, backfilled, or concatenated. Start
the collector, baseline ledger, and any compatible candidate worker only from a
new evidence generation with a new frozen identity.

The baseline and candidate ledgers share `ForwardPredictionRecord`; outcomes
are linked later through the separate append-only outcome-link ledger. Future
baseline resumes must match the exact source manifest/snapshot,
preregistration, Phase-3 gate, repository and model-code hashes, roster,
context, and horizon. The currently protected baseline root must not be
resumed or changed while its evidence identity is under operator protection.

The TTM worker is an offline, credential-free process over
`normalized-bars.jsonl` only. A future run template is:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/run_phase4_v3core_ttm_predictions.py \
  --admission artifacts/phase0/model-runtime-qualification/runtime-admission-post-format-20260810/ttm-r2/local-admission.json \
  --source-root artifacts/phase4/v3core-forward/<sealed-root> \
  --run-root artifacts/phase4/v3core-forward-predictions/<ttm-run-id> \
  --repository-root /mnt/c/projects/advisorai-v3 \
  --preregistration-sha256 <frozen-preregistration-sha256> \
  --phase3-gate-sha256 <passed-phase3-gate-sha256> \
  --until <utc-deadline>
```

The command must use a new run root and may not backfill the protected
collector. It records a missed cutoff rather than creating a retrospective
prediction. It refuses resume when any frozen identity differs and exposes no
network, credential, account, order, transfer, withdrawal, RiskKernel, or OMS
operation.

The currently approved TTM-R2 runtime admission was verified read-only. Its
exact runner requires 512 input values, while the frozen V3-Core contract
requires 48 closed five-minute context bars. The worker therefore quarantines
itself before inference and creates no prediction. Padding, interpolation,
direct-model fallback, or a new runtime identity would require a separate
reviewed 48-bar runtime qualification.

## Frozen v3 PIT provenance contract

Before any Phase-4 cadence acquisition, use the corrected v3 contract. The
active preregistration is
`artifacts/phase4/v3core-cadence-preregistration/20260812T203306Z-v3core-1h-5m-causal-v3/`
(evidence SHA-256
`fa7920ac365c63ea73ffb2a446d0ba5f19b7af5a0c1d92552ac7af891b0cded4`). The
earlier v1 and v2 roots are historical and must not be rewritten.

The credential-free Phase-4 Binance surface is fixed to the official public
market-data-only REST endpoint
`https://data-api.binance.vision/api/v3/klines` and WSS endpoint
`wss://data-stream.binance.vision/ws`. The official Spot REST documentation
directs public-only market-data requests to `data-api.binance.vision`; the
official stream documentation identifies `data-stream.binance.vision` as
market-data-only and states that user-data streams are unavailable there.
The collector must not load `secrets.env`, use `CredentialResolver`, send
account/user-data requests, or expose POST/DELETE/order/transfer/withdrawal
methods.

Each `V3CoreBar` contains a `V3CoreBarProvenance` record. For
`forward_pit_admission`, `availability_basis=forward_observed`, provider
availability must precede or equal actual local collection, and context
collection must be no later than the cutoff. The 48-bar context ends one
five-minute interval before the cutoff; a bar ending exactly at the cutoff is
not locally available at that cutoff. The following 12 bars form the future
one-hour outcome. For
`historical_development`, `availability_basis=historical_backfill` and a
reviewed availability-contract identifier plus SHA-256 are required; the late
local collection timestamp is not historical possession evidence.

The builder requires an explicit evidence class:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/build_phase4_v3core_cadence_input.py \
  --bars <immutable-v3core-bars.json> \
  --phase3-gate artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json \
  --output-root artifacts/phase4/v3core-cadence-input/<new-run-id> \
  --evidence-class forward_pit_admission \
  --source-id binance_spot_public_market_data \
  --provider-identity binance_spot_public_market_data \
  --endpoint https://data-api.binance.vision/api/v3/klines
```

## V3-Core cadence preregistration and case construction

The configured V3-Core evaluation contract is fixed at BTCUSDT/ETHUSDT, 5-minute
observations, a 4-hour context, and a 1-hour outcome. Do not adapt the contract
to the consumed daily Phase-4 input. The offline preregistration runner is:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/preregister_phase4_v3core_cadence.py \
  --output-root artifacts/phase4/v3core-cadence-preregistration/<new-run-id>
```

The immutable preregistration currently records
`PENDING_FRESH_PIT_DATA` at
`artifacts/phase4/v3core-cadence-preregistration/20260812T203306Z-v3core-1h-5m-causal-v3/`
(evidence SHA-256
`fa7920ac365c63ea73ffb2a446d0ba5f19b7af5a0c1d92552ac7af891b0cded4`). The
prior v1/v2 roots remain historical evidence. Existing r7
source-qualification telemetry is not a 5-minute OHLCV case set with four
hours of prior context and one-hour future outcomes, and the consumed daily
input must not be reused or concatenated with another root.

After a reviewed independent PIT bars artifact exists, build typed cases with:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/build_phase4_v3core_cadence_input.py \
  --bars <immutable-v3core-bars.json> \
  --phase3-gate artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json \
  --output-root artifacts/phase4/v3core-cadence-input/<new-run-id> \
  --source-id <reviewed-source-id> \
  --provider-identity <reviewed-provider> \
  --endpoint https://<reviewed-read-only-market-data-host>
```

The bars input must identify one provider/source, endpoint, snapshot,
availability time, and provenance hash for each bar. The builder rejects gaps,
duplicates, source switches, future context, invalid OHLC values, and cases
without the full 4-hour context or 1-hour outcome. The outcome is evaluation
only and is never supplied to features, normalization, regime assignment, or
model context. The builder is offline and cannot load credentials or submit
orders.

## Durable forward collection

After the v3 preregistration is frozen and the collector code is reviewed, the
credential-free forward run is:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/collect_phase4_v3core_forward.py \
  --real \
  --run-directory artifacts/phase4/v3core-forward/20260812T203306Z-first-independent-pit \
  --repository-root /mnt/c/projects/advisorai-v3 \
  --preregistration artifacts/phase4/v3core-cadence-preregistration/20260812T203306Z-v3core-1h-5m-causal-v3/phase4-v3core-cadence-preregistration.json \
  --phase3-gate-sha256 4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232 \
  --target-cases-per-symbol 64 \
  --max-duration-hours 120 \
  --poll-seconds 30
```

The command loads no secrets and issues only public GET requests to
`data-api.binance.vision`. It records `manifest.json`, `heartbeat.json`,
`status.json`, `raw-responses.jsonl`, `normalized-bars.jsonl`,
`completed-cases.jsonl`, `case-rejections.jsonl`, `failures.jsonl`, and
`source-health.jsonl`. It must run under a durable supervisor or detached
session. A case is counted only after its 12 future bars have closed; missing
bars are rejected and never synthesized. The run must not be used for model,
threshold, policy, or candidate selection.

Chronos-2-small remains quarantined until its exact worker/runtime identity is
re-established. The fresh audit is
`artifacts/phase0/model-runtime-qualification/chronos-v3core-identity-audit/20260812T185828.820414Z/chronos-2-small.json`
(SHA-256
`62b971745a7536cf45fd30944a14919b570200a0382ed1dd54512a2570f9785b`).

## Signal-policy research boundary

The current Phase-4 candidate remains `CHALLENGER` when modeled conservative
costs remove its incremental advantage. A restricted offline research runner
may explain that result without consuming the final holdout:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/evaluate_phase4_signal_policies.py \
  --input artifacts/phase4/real-utility-input/<run-id>/phase4-paper-utility-input.json \
  --measurement artifacts/phase4/utility-evaluation/<run-id>/phase4-paper-utility-evidence.json \
  --formal-review artifacts/phase4/formal-review/<run-id>/phase4-formal-review-evidence.json \
  --output-root artifacts/phase4/signal-policy-research/<new-run-id>
```

The runner is offline and measurement-only. Its bounded v1 search uses 32
chronological tuning and 16 validation observations per instrument. The final
16 observations per instrument from the current 128-observation review input
are consumed evidence: they must not be scored as a candidate policy or used to
select thresholds. Full-input diagnostics, if retained, must remain explicitly
non-selection evidence.
The runner creates target-direction signals only; it cannot call RiskKernel,
mutate the OMS, or submit an order. A positive development result would still
require an independent future/PIT evaluation and a fresh formal Phase-4 review.

The 2026-08-12 run is preserved at
`artifacts/phase4/signal-policy-research/20260812T192000Z-ttm-r2-development-policy-v1/`
(evidence SHA-256
`8b7ce10d0beba1562abb9f46fda3906b9094d427ffb289329297157c804e3c48`). It found
no positive conservative-cost incremental policy and did not create a gate
record.

## Current corrected formal review — 2026-08-12T16:25:00Z

The old `robustness-v2` review remains immutable and is not rewritten. The
current reviewer is v2 and uses the same immutable input/report without data
acquisition, credentials, network calls, order writes, or promotion:

```text
artifacts/phase4/formal-review/20260812T162500Z-btc-eth-64x2-reviewer-v2-final/
review SHA-256: c5117a011dc118687bfa2b1aea55e5b0cc76c42929e6e360ce86fb063880c867
checklist SHA-256: 50ef16346c73fc0d64247114dad73ecde28143e0a48468f55b7524fb4463b58b
gate-record SHA-256: 18a05e1769a5356b860800ea2c7a84fb241385ba7884bf7e9c7d3865ebc28a18
```

The calibration policy `rolling_abs_residual_quantile_v1` now uses absolute
forecast residual magnitude from strictly earlier observations for the same
instrument and model. Observations sharing a cutoff are processed as one time
group, so one outcome cannot calibrate another observation at the same cutoff.
No interval is produced before the deterministic minimum history. Native
intervals are preserved and validated; partial, inverted, or point-excluding
native intervals fail closed. Derived interval widths cannot be negative.

The corrected review measures TTM-R2 full coverage `0.73863636` and untouched
holdout coverage `0.75` against `0.80` nominal, within the `0.10` tolerance.
Calibration is therefore no longer a blocker. TTM-R2 remains a challenger
because conservative modeled-cost incremental utility is `-181.04` bps; TTM-R3
remains research-only. The current decision is `PENDING` solely on
`robust_candidate_admission`.

The input is daily. Runtime p50 is `7.758473 ms`. The 10-second and 1-hour
scenarios are explicitly zero-bar operational proxies; +1 and +2 bars are
reported as severe signal-decay stress, not as normal operational latency and
not as sub-bar observations. No intraday data was fabricated or acquired.

The walk-forward audit verifies cutoff-bounded cases, per-cutoff simple and
LightGBM baselines, frozen TTM checkpoint inference, and chronological holdout
handling. The generation artifact omitted source hashes; the reviewer verifies
case/baseline methodology against its recorded commit and records the current
input-preparation hash as a bounded provenance limitation.

The formal review command remains offline and writes a new output root only:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/review_phase4_utility.py \
  --input artifacts/phase4/real-utility-input/<run-id>/phase4-paper-utility-input.json \
  --measurement artifacts/phase4/utility-evaluation/<run-id>/phase4-paper-utility-evidence.json \
  --phase3-gate-record artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json \
  --phase4-dependency artifacts/phase4/formal-dependency/20260812T014100Z-phase3-and-role-contract-v2/phase4-predecessor-dependency.json \
  --output-root artifacts/phase4/formal-review/<new-run-id>
```

## Current formal-review result — 2026-08-12T04:50:00Z

The existing 64-observation report was preserved. A fresh, larger chronological
input was measured from the same frozen reviewed Binance public snapshot: 128
observations (64 BTCUSDT and 64 ETHUSDT), 96 chronological training
observations, 32 final holdout observations, and 896 predictions. This is
measurement-only evidence; it does not acquire data, load credentials, submit
orders, or promote a model.

Input:
`artifacts/phase4/real-utility-input/20260812T032000Z-btc-eth-daily-64x2-walk-forward/phase4-paper-utility-input.json`
(SHA-256 `38fa4aef19d9e3c030749083e3c85cb1b7ba9fec99e86ea01d5a59b798e0067c`).
Measurement:
`artifacts/phase4/utility-evaluation/20260812T033000Z-btc-eth-daily-64x2-base/phase4-paper-utility-evidence.json`
(SHA-256 `2a79d08314717f18c4d4286f40e623c90ddf800a2dbbc06a7b25f89ae369d7fe`).

The offline formal reviewer is:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/review_phase4_utility.py \
  --input artifacts/phase4/real-utility-input/<run-id>/phase4-paper-utility-input.json \
  --measurement artifacts/phase4/utility-evaluation/<run-id>/phase4-paper-utility-evidence.json \
  --phase3-gate-record artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json \
  --phase4-dependency artifacts/phase4/formal-dependency/20260812T014100Z-phase3-and-role-contract-v2/phase4-predecessor-dependency.json \
  --output-root artifacts/phase4/formal-review/<new-run-id>
```

The current immutable review is
`artifacts/phase4/formal-review/20260812T045000Z-btc-eth-64x2-robustness-v2/`.
It is `PENDING`, with blockers `past_only_calibration` and
`robust_candidate_admission`. TTM-R2 is `CHALLENGER`, not promoted: its full
incremental utility is `+610.96` bps and untouched holdout increment is
`+129.89` bps, but rolling coverage is `0.56818` versus `0.80` nominal,
conservative-cost increment is `-181.04` bps, and next-bar delay increment is
`-1950.94` bps. TTM-R3 is `RESEARCH_ONLY`. The holdout-positive result is
recorded separately from robust candidate admission, so a promising holdout
does not bypass calibration, cost, or latency requirements.

The review also records a truthful optional Chronos quarantine: its existing
immutable runtime-admission root pins a different worker hash. The mismatch was
not bypassed and no new model role was admitted. Phase 5–7 remain closed until
the formal Phase-4 gate passes and their own predecessors are evaluated.

## Current admitted predecessors and measured evidence

Phase 2 is formally passed from the existing Binance Spot Testnet
read-only/no-fill/cancel evidence. The immutable record is:

```text
artifacts/phase2/formal-admission/20260812T013500Z-post-phase2-commit/phase2-gate-record.json
SHA-256: efb9d678e72f9785c3d9162660ead6cd434af6108249de73a42a08dd9a64bdae
```

Phase 3 is formally passed by offline re-evaluation against that record. The
immutable predecessor for this runbook is:

```text
artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json
SHA-256: 4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232
```

The Phase-4 dependency decision is `OPEN_FOR_MEASUREMENT` at
`artifacts/phase4/formal-dependency/20260812T014100Z-phase3-and-role-contract-v2/`.
It permits measurement from qualified selected roles and mandatory baselines;
it does not admit Phase 4 or silently close the separate global Phase-0
private-route/archive gates.

The reproducible preparation boundary consumes the frozen public snapshot and
does not acquire data, credentials, or orders:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/prepare_phase4_real_utility_input.py \
  --forecast-snapshot /home/maaro/.cache/advisorai-v3/benchmark-data/public-daily-0f84a34fb0537ecb/forecast-snapshot.json \
  --snapshot-manifest artifacts/phase0/model-runtime-qualification/benchmark-data/public-daily-0f84a34fb0537ecb/forecast-snapshot-manifest.json \
  --phase3-gate-record artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json \
  --forecast-candidate ttm-r2 \
  --forecast-candidate ttm-r3 \
  --candidate-admission-root ttm-r3=artifacts/phase0/model-runtime-qualification/runtime-admission-phase4-ttm-r3-20260812 \
  --output-root artifacts/phase4/real-utility-input/<new-run-id>
```

The first control-only input has 64 BTC/ETH observations and 384 predictions.
The canonical current input explicitly adds the pinned TTM-R3 challenger and
has 448 predictions. Its SHA-256 is
`e95d3937e966902f452754f764ea50c59add4852158bf4457607c66fab36a036` at
`artifacts/phase4/real-utility-input/20260812T023000Z-btc-eth-daily-snapshot-ttm-r2-r3-v3/phase4-paper-utility-input.json`.
The TTM-R3 local admission root used for this measurement is
`artifacts/phase0/model-runtime-qualification/runtime-admission-phase4-ttm-r3-20260812/`;
its admission record SHA-256 is `9e5bfe2efa90d745a286a5d6fb739b4ca08025c2ea20ec4ba04391b0718609cb`.
The measurement command is:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/run_phase4_paper_utility.py \
  --input artifacts/phase4/real-utility-input/<run-id>/phase4-paper-utility-input.json \
  --phase3-gate-record artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json \
  --output-root artifacts/phase4/utility-evaluation/<new-run-id>
```

The canonical current report is
`artifacts/phase4/utility-evaluation/20260812T023015Z-btc-eth-daily-ttm-r2-r3-baselines-v3/phase4-paper-utility-evidence.json`
(SHA-256 `2da6b6576a4679fa688920de41a360a8d5f865664e3f608e3ba4410e2c26a2aa`).
It is `measured_pending_review` with `phase4_admission_opened=false`. TTM-R2
adds net utility over the strongest measured baseline in this historical
window; TTM-R3 does not. No model is promoted and no Phase-4 gate is created.
Interval calibration, latency sensitivity, and the authoritative review remain
open.

The Phase-4 utility entrypoint is an offline, measurement-only boundary:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/run_phase4_paper_utility.py \
  --input <typed-phase4-input.json> \
  --phase3-gate-record <passed-phase3-gate-record.json> \
  --output-root artifacts/phase4/utility-evaluation/<new-run-id>
```

The input envelope must use
`advisorai.phase4.paper-utility-input.v1` and contain only typed
`Phase4MarketObservation` and `Phase4Prediction` arrays. Every observation
must carry `phase3_admitted=true`. The gate file must validate as a passed,
currently valid `PhaseGateRecord` for Phase 3; a Phase-3 review recommendation
or a pending record is intentionally rejected.

The command performs no network calls, credential loading, model-weight
loading, promotion, gate recording, or execution. It writes one immutable
`phase4-paper-utility-evidence.json` plus its SHA-256 sidecar. The report is
`measured_pending_review`; `phase4_admission_opened` remains false. The report
records the input and gate file hashes, the gate canonical hash, baseline and
candidate utility results, and the unchanged RiskKernel/OMS authority
boundary. This is measurement evidence, not Phase-4 admission or paper/live
execution evidence.
