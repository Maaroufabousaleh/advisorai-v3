# AdvisorAI V3 gate matrix

## Fresh replacement forward run — 2026-08-17

The prior root is preserved as `OPERATOR_INTERRUPTED / INCOMPLETE` after the
operator intentionally stopped the laptop; this is not a crash, provider, or
implementation failure. Classification evidence is
`artifacts/phase4/v3core-forward-incidents/20260817T192126Z-operator-interrupted/classification.json`
(SHA-256
`678450a5d5e1b4c8cd79a75303ffc174e6c29ef85de9d75c2cb664f4f78fd970`). It was
not resumed, extended, backfilled, concatenated, or rewritten.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Fresh forward collector | RUNNING / PENDING TERMINAL REVIEW | `artifacts/phase4/v3core-forward/20260817T193400Z-operator-interrupted-replacement-r1/`; PID `59671`; commit `0e23c0b6a94ac87df7e5cc9fa0e552cb9adb50c5`; target end `2026-08-22T19:35:06.869338Z`; credential-free public Binance market-data GET; no writes |
| Fresh resource qualification | RUNNING / LOCALLY MEASURED | `artifacts/phase4/v3core-forward-resource/20260817T193400Z-operator-interrupted-replacement-r1-sidecar-r1/`; PID `61721`; first identity-matched sample; start ticks `811168`; command SHA-256 `a52212826f1a367d10589b0f0624ac53d8d98a8306f05db2d915148b04a5cd40` |
| Sidecar launch attempt | PRESERVED / NON-COLLECTOR ORCHESTRATION ERROR | A pre-created evidence directory caused `FileExistsError` before sampling; no collector evidence was touched. Classification: `artifacts/phase4/v3core-forward-resource-incidents/20260817T193725Z-sidecar-launch-directory-protocol/classification.json` (SHA-256 `b652681c71c9e7e34c2ffa0a2572986877207e0e7891659bdfcc0c556e1461e`); corrected sidecar is running in a new root |
| Fresh mandatory baseline ledger | RUNNING / PENDING CASES | `artifacts/phase4/v3core-forward-predictions/20260817T193400Z-operator-interrupted-replacement-r1/`; PID `60814`; same source/config/code identity; zero predictions before first eligible cutoff |
| Fresh TTM-R2 boundary | QUARANTINED / INCOMPATIBLE | `.../20260817T193400Z-operator-interrupted-replacement-r1-ttm-r2/`; exact qualified runner requires 512 values versus frozen 48 bars; zero predictions, zero network calls, credentials false |
| Chronos-2-small runtime identity | REQUALIFIED / V3-CORE COMPATIBLE / UTILITY PENDING | `artifacts/phase0/model-runtime-qualification/chronos-v3core-compatibility/20260817T194802.642906Z/chronos-2-small.json` (SHA-256 `c282864ff939c1ea7bf7dc6dcf219bc4fb48cbd99fdfa0637f86aa0472d8471a`); current runner hash `c78b8e...`; native context 32–8192 and output 30; isolated 48-value smoke passed; no utility predictions or promotion |
| Phase 4 | PENDING | Fresh 64-per-symbol forward cases and robust candidate admission are not yet available; no promotion or downstream gate was opened |

## Current post-merge forward-run continuation — 2026-08-17

PR #187 merged at `c10203e79ddea88a6f1f5034af1625438b75b8bb`; the TTM follow-up
is isolated on `agent/phase4-ttm-r2-followup` at implementation commit
`4b75dd7`. The operator confirmed that the recorded collector, resource-sidecar,
and baseline-ledger processes were intentionally stopped before the prior
continuation. The preserved root is `OPERATOR_INTERRUPTED / INCOMPLETE`, not a
crash, provider failure, or implementation failure. Existing append-only roots
were not checked out, restarted, edited, backfilled, concatenated, or extended.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Preserved forward collector root | PRESERVED / OPERATOR_INTERRUPTED / INCOMPLETE | `artifacts/phase4/v3core-forward/20260812T204505Z-first-independent-pit-r2/`; validated 734 raw records, 86 normalized bars (43 per symbol), 2 health transitions, 8 rejections, 0 failures, and 0 completed cases; no terminal admission record was manufactured |
| Preserved resource sidecar | PRESERVED / OPERATOR_INTERRUPTED / INCOMPLETE | `artifacts/phase4/v3core-forward-resource/20260812T204505Z-first-independent-pit-r2/`; last preserved heartbeat had 398 samples; operator stopped it intentionally and no restart or repair was attempted |
| Preserved baseline ledger | PRESERVED / OPERATOR_INTERRUPTED / INCOMPLETE | `artifacts/phase4/v3core-forward-predictions/20260812T211500Z-baseline-ledger-r2/`; last preserved status had 0 predictions and 5 missed cutoffs; no append was made |
| Shared prediction record identity | IMPLEMENTED / TESTED | `ForwardPredictionRecord` now carries optional source/model/checkpoint/runner/preprocessing/runtime provenance while outcome links remain a separate append-only ledger |
| Future baseline resume identity | IMPLEMENTED / TESTED | `scripts/run_phase4_v3core_baseline_predictions.py` rejects any source, preregistration, Phase-3 gate, repository, forecasting, LightGBM, roster, cadence, or horizon identity mismatch; the active root was not resumed |
| Prospective TTM-R2 worker | IMPLEMENTED / TESTED / QUARANTINED | `scripts/run_phase4_v3core_ttm_predictions.py` and `src/advisorai/phase4/v3core_ttm.py` read only normalized bars and the immutable runtime admission; the qualified runner requires 512 input values while V3-Core freezes 48, so no prediction is generated or adapted silently |
| Qualified TTM-R2 runtime identity | LOCALLY MEASURED | `artifacts/phase0/model-runtime-qualification/runtime-admission-post-format-20260810/ttm-r2/local-admission.json`; checkpoint SHA-256 `a706726a7eb01bbcb42994b7dcb3c06ea9557898dbae8d480eb04fe8ccb89710`, runner SHA-256 `5c4e3ca38512bbf4ccea3929c17b578b9d88cf80298d991c739124abc126c7b2`, runner script SHA-256 `358150e0544bb416d42eaa6ef0fc3862d69d5a3c475ef1c537cb0ba60d0c9550`; local artifact/launcher/lock identity verification passed |
| Phase 4 | PENDING | The prior root is operator-interrupted and incomplete; a fresh forward cadence generation is required. TTM-R2 remains `CHALLENGER / INCOMPATIBLE_WITH_48_BAR_ROLE`; no Phase-4 promotion or formal re-review was started |
| Phase 2 / Phase 3 | PASSED / UNCHANGED | No predecessor evidence was reopened |
| Phase 5–7 | CLOSED | No council, fill, attribution, or soak was started |

The TTM worker has no credential resolver, network client, account operation,
order operation, or OMS access. A separate 48-bar runtime qualification is
required before it can create prospective TTM-R2 admission records; padding,
interpolation, direct-model fallback, and running against the protected root
are prohibited.

## Current V3-Core forward PIT collector — 2026-08-12T21:18:35Z

The follow-on implementation is draft PR #187 on
`agent/phase4-forward-pit-collector` at branch head `5856b35`. The active
collector executable remains bound to code commit
`eeb62f0af2ecba6cfb21f79d81793963241252e0`; later commits add only offline
ledger/materialization code and documentation. The first attempted forward root
is preserved and classified as an implementation failure caused by comparing
receipt-varying metadata as normalized bar identity; it is not concatenated or
used for admission. The classification artifact is
`artifacts/phase4/v3core-forward-incidents/20260812T204700Z-repeated-closed-bar-normalization/incident-classification.json`.

The corrected v5 preregistration is
`artifacts/phase4/v3core-cadence-preregistration/20260812T204444Z-v3core-1h-5m-reobserve-fix-v5/`
with evidence SHA-256
`5a867b9c68f9a90593990a820f612bf3fd66670933d680a75ddd521762da1ffd` and
manifest SHA-256
`1aec860d56e9cf5d78ebb441ba5077bc93da157239c092682a76ca49be76910e`.

The fresh r2 collector is running from
`artifacts/phase4/v3core-forward/20260812T204505Z-first-independent-pit-r2/`
under PID `160717`; its separate resource sidecar is PID `161130` at
`artifacts/phase4/v3core-forward-resource/20260812T204505Z-first-independent-pit-r2/`.
The source is credential-free Binance public market data only, and the root is
bound to code commit `eeb62f0af2ecba6cfb21f79d81793963241252e0`, source
snapshot `f41af27a93dfbee5b4c67cff2570cb80de09004133b84e2eb0f0ffd2546b0b9a`,
and the frozen preregistration. At the first checkpoint: 0 completed cases per
symbol, 0 failures, 2 normalized bars, and no writes.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Prior root integrity | QUARANTINED / IMPLEMENTATION_FAILURE | `artifacts/phase4/v3core-forward/20260812T203740Z-first-independent-pit/`; repeated closed-bar receipt bug preserved unchanged; no admission use |
| Corrected repeated-receipt handling | IMPLEMENTED / TESTED | `4949b5cc5b494ab6ff79c0ff40118219773d6277`; 27 focused tests pass; raw receipt history remains separate from normalized identity |
| Resumable code identity binding | IMPLEMENTED / TESTED | `eeb62f0af2ecba6cfb21f79d81793963241252e0`; resumed roots reject code/module/collector hash changes |
| Fresh independent forward cases | PENDING / RUNNING | r2 root above; target 64 completed cases per BTCUSDT and ETHUSDT, target end `2026-08-17T20:45:11.984069Z` |
| Resource qualification sidecar | LOCALLY MEASURED / RUNNING | sidecar root above; PID identity and command hash bound; no admission claim before terminal review |
| Offline completed-root materializer | IMPLEMENTED / TESTED | `scripts/materialize_phase4_v3core_forward_input.py`; 2 refusal tests; requires target-reached status, frozen preregistration/Phase-3 hashes, validated case hashes, source identity, and forward admission flags; no network/credential/write path |
| Pre-outcome mandatory-baseline ledger | IMPLEMENTED / TESTED / RUNNING | `scripts/run_phase4_v3core_baseline_predictions.py`; PID `173057`; typed hash-chained predictions and separate outcome links; 0 network calls, credentials false, order writes false; TTM-R2/Chronos are not silently substituted |
| Phase-4 robust candidate admission | PENDING | no utility evaluation or model promotion |
| Phase 2 / Phase 3 | PASSED / UNCHANGED | no predecessor gate or Phase-3 evidence reopened |
| Phase 5–7 | CLOSED | no council, fill, attribution, or soak started |

The next legal work while the time-dependent root runs is offline preparation
of the frozen baseline/prediction-ledger path and Chronos identity review; no
holdout tuning, arbitrary order, credential use, or source substitution is
permitted.

Implementation verification at code head `ef1ec1c`: full pytest `746 passed`
with 28 warnings; acceptance phases passed
`134/152/126/117/93/34/10/11/27/18/5`; Ruff, repository format, lock check,
compilation, dashboard build, diff hygiene, and tracked secret/weight checks
passed. These checks do not close the time-dependent forward PIT gate.

## Historical V3-Core forward PIT collector contract — 2026-08-12T20:33:06Z

Main is `5514a4cac8771d23c9f7e113e922c9ba9df1ecee` after PR #186. The unmerged
collector follow-on is `80b3c5eb6c0055b81e224bbc833b8a9e240906eb` on
`agent/phase4-forward-pit-collector`.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Causal v3 cadence contract | IMPLEMENTED / TESTED / FROZEN | Context is 48 bars ending one 5m interval before cutoff; outcome is the next 12 bars; v3 schemas and adversarial tests preserve the correction |
| Credential-free acquisition boundary | IMPLEMENTED / TESTED | `scripts/collect_phase4_v3core_forward.py`; exact `data-api.binance.vision` klines GET only; no secrets, account, user-data, write, transfer, or withdrawal path |
| Raw-first immutable receipts | IMPLEMENTED / TESTED | `ForwardRawSpool` retains every receipt with payload hash and append-only hash chain; normalization occurs only after raw fsync |
| Normalized bars / rejected cutoffs | IMPLEMENTED / TESTED | `ForwardNormalizedBarSpool`, `ForwardRejectionSpool`, strict close semantics, duplicate rejection, no synthetic bars |
| Fresh independent forward cases | PENDING / NOT YET MEASURED | No network collection has started; target is 64 completed cases per BTCUSDT and ETHUSDT, with a 120-hour maximum window |
| Phase-4 robust candidate admission | PENDING | No utility evaluation or model promotion; TTM-R2 remains `CHALLENGER`, TTM-R3 `RESEARCH_ONLY` |
| Phase 2 / Phase 3 | PASSED / UNCHANGED | No predecessor gate or Phase-3 evidence was reopened |
| Phase 5–7 | CLOSED | No council, fill, attribution, or soak may start before formal Phase-4 passage |

## Historical V3-Core PIT provenance hardening — 2026-08-12T19:58:26Z

PR #186 was then draft on top of main
`13323cd2ad1fd8ae0f8690b10f5909c87ccc31ae`; its contract code was at
`6b2ed741650f9de0f51e8db921aefb507979d0d3`. The prior v1 preregistration is
preserved; the corrected v2 preregistration was active at that historical
checkpoint, and the causal v3 preregistration above is active now.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Market-data-only Phase-4 surface | IMPLEMENTED / TESTED | Exact Binance REST `https://data-api.binance.vision/api/v3/klines` and WSS `wss://data-stream.binance.vision/ws`; credentials/write capability false; standard production and testnet execution hosts rejected |
| Timestamp provenance | IMPLEMENTED / TESTED | `V3CoreBarProvenance` separates interval end, provider availability, local collection, provider event time, and availability basis |
| Forward PIT classification | IMPLEMENTED / TESTED | `forward_observed` requires provider availability `<=` collection; context requires local collection `<=` cutoff; late backfill is rejected |
| Historical development classification | IMPLEMENTED / TESTED | `historical_backfill` requires a reviewed availability-contract identifier and SHA-256; collection time is never used as historical possession proof |
| Raw/normalized/source-health lineage | IMPLEMENTED / TESTED | Every bar provenance record requires source snapshot, raw-record, normalized-record hashes, and typed source-health state |
| Causal case construction | IMPLEMENTED / TESTED | 18 focused cadence/provenance tests; missing/gapped, duplicate, source substitution, unavailable context, and future leakage fail closed |
| Corrected preregistration | LOCALLY MEASURED / IMMUTABLE | `artifacts/phase4/v3core-cadence-preregistration/20260812T195826Z-v3core-1h-5m-provenance-v2/`; evidence SHA-256 `ca09ee9d62eccbd017287eebc8864e34d339d8e2a3eb2168826853a7fdd0fed8`; manifest SHA-256 `6962c7a882a11969262484e03b3c6cdb7627e27e3d23d1ffab7ffde23f8883fd` |
| Fresh independent 5m PIT cases | EXTERNALLY BLOCKED | No collection has started; status remains `PENDING_FRESH_PIT_DATA` |
| Phase-4 robust candidate admission | PENDING | `robust_candidate_admission`; no model or policy admitted |
| Phase 2 / Phase 3 | PASSED / UNCHANGED | Existing formal records and immutable evidence remain authoritative |
| Phase 5–7 | CLOSED | No downstream council, fill, attribution, or soak started |

The active contract is frozen before acquisition. The next legal implementation
step after PR #186 merges is a dedicated credential-free collector that writes
raw records first and normalizes them offline; no `CredentialResolver`, secrets,
account route, order route, transfer route, or withdrawal route may exist in
that collector.

Contract-correction verification passes full pytest (`731 passed`, 28 warnings),
all eleven acceptance suites (`134/152/126/117/78/34/10/11/27/18/5`), Ruff,
format, lock, compileall, dashboard build, diff hygiene, and tracked
secret/model-weight checks. No Phase-4 admission record was created.

## Current V3-Core cadence Phase-4 continuation — 2026-08-12T18:58:28Z

The current merged main anchor is `13323cd2ad1fd8ae0f8690b10f5909c87ccc31ae`
(PR #185). The focused implementation is on
`agent/phase4-v3core-cadence` at
`53cdc9eba5f57ce54e87348f04320b138d82fa8d`. This work preserves the consumed
daily Phase-4 evidence and does not reopen Phase 2 or Phase 3.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| V3-Core cadence contract | IMPLEMENTED / TESTED | `src/advisorai/phase4/v3core_cadence.py`; 6 focused tests; exact 5m observations, 4h context, 1h outcome, BTCUSDT/ETHUSDT |
| Causal case construction | IMPLEMENTED / TESTED | `build_v3core_cases` rejects missing, duplicate, non-contiguous, source-switch, and future/unavailable bars; no gap filling or silent source switch |
| Pre-registered 1h evaluation | LOCALLY MEASURED / IMMUTABLE | `artifacts/phase4/v3core-cadence-preregistration/20260812T185716Z-v3core-1h-5m-prereg-v1/`; evidence SHA-256 `1bbe362240a1fb136a074117f734e270afcef3cf0be6f6af34e81dc3c2631e00`; manifest SHA-256 `ffce302f99e27317f5a9c38520d5170fb0a39b8e7332657e6c9aad87324a085c` |
| Fresh 5m PIT case input | EXTERNALLY BLOCKED | Existing r7 samples are source-health/order-book telemetry, not a contiguous OHLCV case set with 4h context and 1h outcomes; consumed daily input is not reused |
| Chronos-2-small runtime identity | QUARANTINED | Fresh offline audit SHA-256 `62b971745a7536cf45fd30944a14919b570200a0382ed1dd54512a2570f9785b`; worker/runner hash mismatch preserved, no waiver |
| TTM-R2 at V3-Core cadence | PENDING DATA | No 5m/1h cases exist; TTM-R2 remains `CHALLENGER` from the negative daily policy result |
| Mandatory baselines | PRE-REGISTERED / NOT MEASURED | naive, drift, seasonal-7, linear, LightGBM are fixed in the preregistration; measurement awaits eligible cases |
| Phase-4 robust candidate admission | PENDING | `robust_candidate_admission`; no model is admitted |
| Phase 2 / Phase 3 | PASSED / UNCHANGED | Existing formal gate records remain authoritative |
| Phase 5–7 | CLOSED | Phase 4 has not passed; no council, fill, or soak started |

No network calls, credentials, model weights, or order writes were used by the
preregistration or input-builder implementation. The next admissible action is
to obtain or accumulate a reviewed independent 5m PIT window, then build cases
with `scripts/build_phase4_v3core_cadence_input.py`. The archive/rclone and
private-route gates remain separate and are not changed here.

Focused verification passed full pytest (`719 passed`, 28 warnings), all eleven
acceptance suites (`134/152/126/117/66/34/10/11/27/18/5`), Ruff, format, lock,
compileall, dashboard build, diff hygiene, and tracked secret/model-weight
checks. These checks validate implementation only; no Phase-4 gate record was
created.

## Current Phase-4 signal-policy research — 2026-08-12T19:00:00Z

The current merged main anchor is `4b9ca30353132804eff559abd9220821493b9366`
(PR #184). The new offline policy research is implemented on a focused
continuation branch and does not alter Phase 2, Phase 3, execution, credentials,
RiskKernel, OMS, or the consumed Phase-4 holdout.

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Deterministic restricted forecast-to-signal boundary | IMPLEMENTED / TESTED | `src/advisorai/phase4/signal_policy.py`; 10 focused tests; no order or portfolio authority |
| TTM-R2 economic decomposition | REAL_MEASURED / DIAGNOSTIC_ONLY | Immutable root `artifacts/phase4/signal-policy-research/20260812T192000Z-ttm-r2-development-policy-v1/`, evidence SHA-256 `8b7ce10d0beba1562abb9f46fda3906b9094d427ffb289329297157c804e3c48` |
| Chronological policy development/validation | SATISFIED FOR RESEARCH | 32 tuning + 16 validation observations per BTC/ETH symbol; 13 pre-registered policies; no holdout selection |
| Conservative-cost policy improvement | UNSATISFIED | Best validation incremental utility is `0` bps from a flat confidence policy; no policy was frozen |
| Existing final holdout reuse | SATISFIED SAFETY CONDITION | 32 observations are `CONSUMED_AND_NOT_REUSED`; no holdout-only policy score or selection metric was produced; full-input decomposition is diagnostic-only |
| Independent candidate family | QUARANTINED | Chronos-2-small/Kronos-mini/Kronos-small runtime evidence remains quarantined; no mismatch bypass or ensemble |
| Phase-4 robust candidate admission | PENDING | Sole blocker remains `robust_candidate_admission`; independent future/PIT evidence is required |

TTM-R2's full-input diagnostic records 108 turnover units, 55 signal changes,
conservative net utility `2834.3394` bps, and modeled net-zero break-even
`49.243883` bps/turnover. The diagnostic includes the consumed holdout only as
non-selection decomposition; it does not change the formal review or admit a
policy. Phase 5–7 remain closed.

## Current corrected Phase-4 reviewer audit — 2026-08-12T16:25:00Z

Historical checkpoint superseded by the current signal-policy row above; PR
#184 was subsequently merged.

Draft PR #184 remains unmerged; `main` remains at
`056a39d5641c81330dd89668e117108e1fa1bf5c`. The reviewer correction is
committed on `agent/phase4-formal-robustness-review` at
`52342b1093dc95dd0358257cdd8999cb2935479b`. Phase 2 and Phase 3 were not
reopened, no new data was acquired, no credentials were loaded, and no order
was submitted. The selected-model stability roots and the prior robustness-v2
root remain untouched.

The old robustness-v2 root remains immutable and historical:
`artifacts/phase4/formal-review/20260812T045000Z-btc-eth-64x2-robustness-v2/`.
It is superseded for current calibration and latency interpretation because
its signed-residual implementation was corrected and its daily next-bar result
was reclassified as severe signal-decay stress rather than normal operational
latency.

The fresh independent v2 review uses the same immutable input and measurement:

- root: `artifacts/phase4/formal-review/20260812T162500Z-btc-eth-64x2-reviewer-v2-final/`;
- review SHA-256 `c5117a011dc118687bfa2b1aea55e5b0cc76c42929e6e360ce86fb063880c867`;
- checklist SHA-256 `50ef16346c73fc0d64247114dad73ecde28143e0a48468f55b7524fb4463b58b`;
- pending PhaseGateRecord SHA-256 `18a05e1769a5356b860800ea2c7a84fb241385ba7884bf7e9c7d3865ebc28a18`.

The corrected requirement result is:

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Point-in-time BTC/ETH input, sample, symbols, holdout | SATISFIED | Same immutable 128-observation input; 64 BTCUSDT, 64 ETHUSDT, 32 untouched holdout observations |
| Chronological/PIT methodology | SATISFIED | Case contexts end at their cutoffs; simple baselines and LightGBM are evaluated from cutoff-only context; TTM-R2/R3 are frozen checkpoint inference; no holdout tuning/retraining |
| Past-only rolling calibration | SATISFIED | `rolling_abs_residual_quantile_v1`; 88 derived intervals per model; same instrument/model history; residuals are absolute and strictly earlier; TTM-R2 full coverage `0.73863636`, holdout `0.75`, nominal `0.80` |
| Native/derived interval boundary | SATISFIED | Native intervals are preserved and validated; partial/invalid native bounds are rejected; derived intervals are explicit and non-negative |
| Latency sensitivity | SATISFIED as measured | Runtime p50 `7.758473 ms`; daily 10s/1h scenarios are zero-bar operational proxies; +1/+2 bars are severe signal-decay stress only; no sub-bar decay claim |
| Cost stress/break-even | SATISFIED as measured | All-in assumptions: optimistic 7, base 14, conservative 23, severe 41 bps/turnover; TTM-R2 model net-zero break-even `49.243883` bps/turnover, but conservative incremental utility is `-181.04` bps |
| Robust candidate admission | UNSATISFIED | TTM-R2 remains `CHALLENGER`; TTM-R3 remains `RESEARCH_ONLY`; no model is admitted |
| Intraday latency resolution | EXTERNALLY_BLOCKED, non-gating | The reviewed input is daily; no new data was acquired |
| Global Phase-0 private/archive gates | EXTERNALLY_BLOCKED, non-gating here | Phase-4 measurement dependency remains separate from global Phase 0 |

Phase 4 therefore remains `PENDING`, with the sole current gating blocker
`robust_candidate_admission`. Phase 5–7 remain closed. RiskKernel and OMS
authority are unchanged, and live capital remains prohibited.

## Current Phase-4 formal review checkpoint — 2026-08-12T04:50:00Z

The review started from main `056a39d5641c81330dd89668e117108e1fa1bf5c`.
The implementation is committed as `bc163bc` on
`agent/phase4-formal-robustness-review` in draft PR #184; main remains at the
review-start SHA until merge.
Phase 2 and Phase 3 remain formally passed; neither predecessor was reopened,
no Phase-3 durability run was collected, and no Binance order or network call
was made by this review. The selected Phase-0 model runtime roles remain
qualified, while global Phase 0 remains separately pending its private-route
and archive prerequisites.

Final verification for this working tree: full pytest `692 passed` with 28
warnings; acceptance suites `134/152/126/117/39/34/10/11/27/18/5`; Ruff,
repository format, lock check, compilation, dashboard build, and `git diff
--check` passed.

The measurement-only Phase-4 input is the frozen Binance public BTC/ETH
snapshot (`0f84a34fb0537ecb0305cd8e5fd07e5d2dfa14500dbb61961168ee5351f55546`)
with 128 observations (64 per symbol), 96 chronological training observations,
32 final holdout observations, and 896 predictions:

- input: `artifacts/phase4/real-utility-input/20260812T032000Z-btc-eth-daily-64x2-walk-forward/phase4-paper-utility-input.json`
  (SHA-256 `38fa4aef19d9e3c030749083e3c85cb1b7ba9fec99e86ea01d5a59b798e0067c`);
- generation evidence SHA-256 `0d8ce592c558e27b1bf0de8607c6b288677ddee7d168f8aa386336fc2be78ece`;
- measurement report: `artifacts/phase4/utility-evaluation/20260812T033000Z-btc-eth-daily-64x2-base/phase4-paper-utility-evidence.json`
  (SHA-256 `2a79d08314717f18c4d4286f40e623c90ddf800a2dbbc06a7b25f89ae369d7fe`).

The immutable offline formal review is
`artifacts/phase4/formal-review/20260812T045000Z-btc-eth-64x2-robustness-v2/`:

- review evidence SHA-256 `64b9080176109ab12ce58cbd68b5e2160115537e5e4f75cba175c0051515bee3`;
- checklist SHA-256 `16e485072f23ffbdccea463b82fa0765d7691d380db57679e38d4cb173b65154`;
- pending PhaseGateRecord SHA-256 `a7a99bc18d52e8bcbd49c9ecb625564c4b363b497b4a4708ba89fc40f989d36c`.

The review is `PENDING`, not `PASSED`. Its requirement matrix is:

| Requirement | State | Evidence/result |
| --- | --- | --- |
| Phase-3 predecessor, point-in-time input, sample size, walk-forward/holdout | SATISFIED | Passed Phase-3 record and immutable 128-observation input |
| Mandatory baselines, provenance/resources, BTC/ETH and regime slices | SATISFIED | Same-input deterministic recomputation |
| Past-only rolling calibration | UNSATISFIED | 88 derived intervals; TTM-R2 coverage `0.56818` vs nominal `0.80` |
| Causal latency sensitivity | SATISFIED as measured | Next-bar TTM-R2 incremental utility `-1950.94` bps; this fails the candidate robustness check |
| Cost stress and break-even | SATISFIED as measured | Conservative TTM-R2 incremental utility `-181.04` bps; break-even all-in cost `49.24` bps/turnover |
| Holdout incremental utility | SATISFIED as measured | TTM-R2 holdout incremental utility `+129.89` bps |
| Robust candidate admission | UNSATISFIED | No candidate passes all utility, calibration, cost, latency, and holdout checks |
| Global Phase-0 route/archive | EXTERNALLY_BLOCKED, non-gating here | The Phase-4 dependency explicitly opens measurement without closing global Phase 0 |
| Initial GPU family challenger | OPTIONAL | Chronos input generation preserved an old pinned-worker hash mismatch; no bypass or promotion |

TTM-R2 is retained as `CHALLENGER`, not promoted. TTM-R3 is `RESEARCH_ONLY`
after negative full and holdout incremental utility. The evidence also shows
TTM-R2's advantage is regime-concentrated and disappears under the measured
next-bar delay and conservative modeled costs. These costs are stress assumptions,
not historical Binance fills. Phase 5, Phase 6, and Phase 7 remain closed; a
real fill/TCA/attribution remains a Phase-6 requirement. RiskKernel and OMS
authority are unchanged, and live capital remains prohibited.

## Current Phase-2/3 admission and Phase-4 measurement checkpoint — 2026-08-12T01:42:00Z

The implementation/evidence source anchor for this checkpoint is
`cd2b09066096977ac38ddb6dd756339fea9a4330`. No Phase-3 durability root was
reopened, no selected-model stability process was changed, and no additional
Binance order was submitted.
The work is merged in main at `b6a72834398465271f9be08e372d292286671fb8`
(PR #182); this checkpoint branch contains documentation-only follow-up.

Verification after this implementation boundary passed full pytest (`687
passed`, 28 warnings), acceptance suites
`134/152/126/117/34/34/10/11/27/18/5`, Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked-secret/model-weight checks.

Phase 2 is formally `PASSED` from the existing Binance Spot Testnet evidence;
the no-fill/cancel lifecycle remains Phase-2 evidence and real fill attribution
remains Phase 6. The machine-readable checklist has 21 mandatory rows, all
`SATISFIED`, plus one non-gating `NOT_APPLICABLE` real-fill row and one
non-gating `EXTERNALLY_BLOCKED` provider-deployment/archive context row:

`artifacts/phase2/formal-admission/20260812T013500Z-post-phase2-commit/phase2-admission-checklist.json`
(SHA-256 `c417e52d8a38e496b234c397fe2a1600ccca00d6bdfc70a7f60e93445300a2e8`).
The passed `PhaseGateRecord` is
`artifacts/phase2/formal-admission/20260812T013500Z-post-phase2-commit/phase2-gate-record.json`
(SHA-256 `efb9d678e72f9785c3d9162660ead6cd434af6108249de73a42a08dd9a64bdae`,
canonical hash `5ea9a4ff9a51b3e5f79eab3946a34cc0eca65df9717b2d8b01189dc2a64171fc`).

The unchanged preserved r7 evidence was re-evaluated offline with that passed
predecessor. Phase 3 is formally `PASSED`; its checklist is
`artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-admission-checklist.json`
(SHA-256 `57b3c32984320d53cf889fb77d4238907c13a38c93727bc7f7d0d55dc5dbee45`),
and its passed `PhaseGateRecord` is
`artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json`
(SHA-256 `4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232`,
canonical hash `a8f646d49edf716f201b9da015080872b0f14170128138d569446ec63119c4e3`).
The checklist records 16 `SATISFIED` mandatory evidence items; Coinbase Sandbox
ETH-USD and GDELT HTTP 429 remain non-gating external blockers, and the
required primary Binance public BTC/ETH source remains explicit.

The Phase-4 dependency decision is `OPEN_FOR_MEASUREMENT`, not admission:
`artifacts/phase4/formal-dependency/20260812T014100Z-phase3-and-role-contract-v2/phase4-predecessor-dependency.json`
has SHA-256 `8c2154feac271a9f6e6744d755a90d4c56873a784498ad6ff5b77d710c25dd28`.
It binds the passed Phase-3 record, qualified TTM-R2/Finance DeBERTa-v3/
FinBERT-MiniLM roles, and the complete mandatory baseline roster. Global Phase 0
remains separately pending private-route/archive evidence and is not silently
closed or used as a Phase-4 predecessor.

The canonical real, measurement-only Phase-4 input uses the frozen Binance
public BTC/ETH daily snapshot (content hash
`0f84a34fb0537ecb0305cd8e5fd07e5d2dfa14500dbb61961168ee5351f55546`), 64
point-in-time observations, mandatory baselines, and measured TTM-R2 and
TTM-R3 outputs. Input SHA-256 is
`e95d3937e966902f452754f764ea50c59add4852158bf4457607c66fab36a036` at
`artifacts/phase4/real-utility-input/20260812T023000Z-btc-eth-daily-snapshot-ttm-r2-r3-v3/phase4-paper-utility-input.json`;
the generation report SHA-256 is
`c820d707d5e91bdc230006de10939f8f74bd9a78f7fe1eceb1e417a68e427d9d`.
The immutable utility report is
`artifacts/phase4/utility-evaluation/20260812T023015Z-btc-eth-daily-ttm-r2-r3-baselines-v3/phase4-paper-utility-evidence.json`
with SHA-256 `2da6b6576a4679fa688920de41a360a8d5f865664e3f608e3ba4410e2c26a2aa`.
It is `measured_pending_review`, has `phase4_admission_opened=false`, and made
no network calls or execution writes. Results were:

| Model | MAE bps | RMSE bps | Directional accuracy | Net utility bps | Incremental vs strongest baseline |
|---|---:|---:|---:|---:|---:|
| LightGBM | 1235.14 | 1520.00 | 0.4375 | 2485.98 | baseline |
| TTM-R2 | 327.50 | 491.99 | 0.6250 | 5248.14 | +2762.16 |
| TTM-R3 | 346.67 | 501.74 | 0.53125 | 1468.25 | -1017.73 |

The remaining baseline results and full regime/cost fields are in the report.
TTM-R2 added value in this measured historical window; TTM-R3 did not exceed
the strongest baseline and is not eligible for promotion from this evidence.
The cost scenario uses conservative 10 bps fee, 2 bps spread, and 2 bps
slippage assumptions rather than historical tick-level costs. Runtime latency
was measured for both candidates, but latency sensitivity and interval-based
calibration remain pending review. Finance sentiment roles and TSPulse were
not fabricated into price forecasts.

Phase 4 is therefore `IMPLEMENTED / TESTED / REAL_MEASURED / PENDING_REVIEW`,
not admitted. Phase 5–7 remain closed until the authoritative utility review,
real typed council, Phase-6 fill/TCA/attribution, and all other prerequisites
are satisfied. Archive/rclone, Phase 8 quarantine, Phase 9/Alpha plan-only
state, and Phase-10 human approval remain unchanged.

## Current formal-admission checkpoint — 2026-08-12T00:45:13Z

Clean `main` before this docs refresh is `d9f2bb9d6738b3850ebd7798821b31e662b1d263`.
The offline formal evaluator is implemented at
[`scripts/evaluate_phase3_gate.py`](../../scripts/evaluate_phase3_gate.py), with
regression coverage in
[`tests/phase3/test_phase3_formal_gate.py`](../../tests/phase3/test_phase3_formal_gate.py).
It reads immutable evidence only and never loads credentials, performs network
I/O, starts a collector, or creates order authority.

The machine-readable checklist is
`artifacts/phase3/formal-admission/20260812T004513Z-contract-review/phase3-admission-checklist.json`
with SHA-256
`0cd305d79d70a7427100437b977ce028cb643fc885d680a113312b11d3a0a79c`.
It has 21 classified requirements: all 16 mandatory evidence requirements are
`SATISFIED` except the formal dependency item
`phase_2_formal_predecessor`, which is `UNSATISFIED`. GDELT availability is
`EXTERNALLY_BLOCKED` but non-gating because the contract requires its dependent
path to abstain rather than silently substitute; Coinbase Sandbox ETH-USD is
provider-truth `EXTERNALLY_BLOCKED` and non-gating because it is not the selected
primary source. LSE is `OPTIONAL`; equity SEC/ALFRED sources are
`NOT_APPLICABLE` to Phase 3.

The immutable pending `PhaseGateRecord` is
`artifacts/phase3/formal-admission/20260812T004513Z-contract-review/phase3-gate-record.json`
with SHA-256
`0ee4b783c1afa943fb8a9e94ca29ea2c358d6b7e68ba097fd224fd96614d4bbe` and
canonical hash
`55e9df60b1947fb3bd30e7c184b4bf1c48c7cbbfdf4623483525e2eb316b14d`.
Its sole blocking reason is `phase_2_formal_predecessor`; no r8/r9 durability
run was launched. Phase 3 is therefore precisely `PENDING_EXTERNAL_EVIDENCE /
PENDING_BASE_GATES`, not admitted, and Phase 4 remains unstarted. Archive/rclone,
private-route, Phase 8, Phase 9/Alpha, and Phase 10 remain separate.

## Current terminal-review checkpoint — 2026-08-11T23:17:07Z

Current executable main anchor is `d5bfde76ed3cacaba365f3d7981db5a756eaf314`
after PR #174; review began from clean `main`
`6913f2b4feaf71f4fada05a5e9611d7601dd5e8d`. No selected-model stability or
archive/rclone process was touched. The Phase-0 selected-model r3 terminal
review remains independently `QUALIFIED` for TTM-R2, Finance DeBERTa-v3, and
FinBERT-MiniLM, while global Phase 0 remains pending its separate route and
archive gates.
The review-boundary change passes full pytest (`663` tests, 28 warnings), all
eleven acceptance suites (`134/152/126/111/27/34/10/11/27/18/5`), Ruff,
format, lock, compilation, dashboard build, diff hygiene, and tracked-secret/
weight checks.

The immutable Phase-3 r7 public-data root
`artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix`
has a four-hour terminal window, 129 contiguous cycles, 774 samples, config
SHA-256 `87abe16e4f16c24bd34e49381915005e0c73f826239c0412c3a81922debaf4c6`,
summary SHA-256
`8839afbdeae42cf587ae9522d2119943f8f5e431fcd4bd6a17a0d26e2449bfde`, and
code identity
`b928650c279502b1f759c1584769f881f6c9a7f015ba34896d18c0501463fd0b`.
Credential/write separation, source identity, raw-chain integrity, replay,
sequence, recovery, disagreement, explicit failover/fail-closed selection,
and resource-sidecar checks are structurally valid. Three Binance stale
samples were external source-health events; each selected BTC/ETH path
failed closed. No sequence gap, duplicate, ordering, or replay failure was
observed.

The independent structural review is `PASS_FOR_REVIEW` at
`artifacts/phase3/public-market-data-validation/20260811T230500Z-four-hour-r7-validator-fix-codex-terminal-review/phase3-qualification-validation.json`
with SHA-256
`dbd9bdc6c96af82ef33ccfbb22557786de6c9d3e72cbfdc97a956cb91c7f32e4`. The
corrected admission-policy review is `QUALIFIED_FOR_REVIEW` at
`artifacts/phase3/public-market-data-admission/20260811T231500Z-four-hour-r7-validator-fix-codex-policy-review-final/phase3-admission-evaluation.json`
with SHA-256
`d4ad647e1f668b88c78604bd9cd75b94ae925bb9b943117e6adb07cfc8ae7aaa`;
the evaluator source SHA-256 is
`0ea2a57e1a4d7135d8a65bbcf87f4ea5eb288d9b131ec6a56178431d5a4d235e`.
The evaluator now distinguishes provider stale intervals from actual replay
continuity failures and enforces stale-source fail-closed/recomputed-failover
behavior. This qualifies the measured public BTC/ETH data component for
formal review; it does not silently promote the broader Phase-3 V3-Core
source spine or create a gate record. Phase 3 remains `PENDING_EXTERNAL_EVIDENCE`
until its remaining authoritative source-scope and formal admission
requirements are met. Phase 4–7 remain unstarted.

## Current terminal-review checkpoint — 2026-08-11T18:46:29Z

Clean `main` is `ced5d9301a816d89428616d1eb6ce0de48318cf7`, aligned with
`origin/main` after PR #171. PR #169 fixes a validator defect that treated
valid successful source selections (`fail_closed=false` with an identity bound
to the actual provider) as invalid. The fix is implemented and focused-tested;
it does not rewrite any immutable evidence root. PR #171 updates both
machine-readable model rosters to bind the terminal per-role results without
promoting the overall Phase-0 gate. Full pytest now passes `660` tests with 28
warnings; all eleven acceptance suites pass
`133/152/126/108/27/34/10/11/27/18/5`. Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked secret/weight checks pass.

The Phase-0 selected-model r3 terminal review is
`PASS_FOR_REVIEW` with `269` continuous cycles and `24.003634` elapsed hours.
The separate immutable review report
`artifacts/phase0/model-runtime-qualification/stability-validation/phase0-selected-24h-terminal-sample-20260810-r3-review-codex-postrun/phase0-model-stability-validation.json`
has SHA-256
`1a6ab92c4f28d456776eac0c89ab099b0c1ef579c729fa8e458e4d5192b06949`. Its
per-role results are `QUALIFIED` for `ttm-r2`, `finsentiment-deberta-v3`, and
`finbert-minilm`; the overall Phase-0 gate remains closed because other
Phase-0 prerequisites, including the deferred archive and unavailable private
route, are not admitted. PID `70598` is terminal and was not modified.

The immutable Phase-3 r6 root remains non-admitted. Its corrected offline
structural review is `PASS_FOR_REVIEW` at
`artifacts/phase3/public-market-data-validation/20260811T124600Z-four-hour-r6-clock-confidence-setsid-postfix-audit/phase3-qualification-validation.json`
with SHA-256
`3ac2d22f3629ff97f08e8172d1fb4aa1a3044251f32b3f0419c7fb1d1feca6d8`. The
separate admission review is `phase3_admission=false` at
`artifacts/phase3/public-market-data-admission/20260811T124600Z-four-hour-r6-clock-confidence-setsid-postfix-audit/phase3-admission-evaluation.json`
with SHA-256
`e72d0bba6ad8ec9eb204ea5f0892c6972d06362db6ceeaf8b99f6b93436d510b`; its
blocker is `primary_snapshot_sequence_or_replay_failure` because real stale
intervals occurred in the primary Binance BTC/ETH history. Provider
degradation was preserved and handled fail-closed; it was not rewritten as an
implementation failure.

A fresh independent r7 root is now running from the merged validator fix at
`artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix`
under PID `32321`, with a separate resource sidecar PID `32574` at
`artifacts/phase3/public-market-data-resource-monitor/20260811T182252Z-four-hour-r7-validator-fix`.
The runner started at `2026-08-11T18:23:11.356614Z` and targets
`2026-08-11T22:23:11.356614Z`; its config SHA-256 is
`87abe16e4f16c24bd34e49381915005e0c73f826239c0412c3a81922debaf4c6`.
The root is not yet terminal and cannot open Phase-3 admission. It records
`credentials_loaded=false` and `order_writes_attempted=false`.

Phase 4–7 remain pending: no real Phase-4 utility, Phase-5 council, Phase-6
fill/attribution, or Phase-7 soak evidence is being claimed. Coinbase and
Binance execution evidence, the deferred archive gate, Hermes quarantine,
Phase 9/Alpha plan-only status, and Phase-10 human authorization remain
unchanged.

## Current recovery checkpoint — 2026-08-11T13:44:48Z

Clean `main` is `48d913d1ac1c78549b9d1c6115550308cacced19`, aligned with
`origin/main`, after PR #167, a docs-only live Phase-3 checkpoint. Full pytest
passed `657` tests with 28 warnings; all eleven acceptance suites passed
`133/152/126/106/27/34/10/11/27/18/5`. Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked secret/weight checks also passed.

The preserved r5 root
`artifacts/phase3/public-market-data-durable/20260811T083600Z-four-hour-r5-reconnect-hashfixed`
completed at `2026-08-11T12:36:19.079514Z` with `774` samples and `6` terminal
samples. Its summary SHA-256 is
`d79a00a980af649e9de228b5d56e5123fb547691f728db57821de0216de90c7c`; offline
validation is `PASS_FOR_REVIEW` with report SHA-256
`33919b778ca079b916ec61fba7faa6d32bf5555177cbdfc9828b4ae907da9e00`, and the
health projection was validated. Admission remains
`PENDING_EXTERNAL_EVIDENCE`: report SHA-256
`2e38ff83eee96791010182c0997bcd6d5aacd6817fe114dc1e779c02f95e10db`, with
blockers `no_healthy_primary_source_for_btc_eth` and
`primary_snapshot_sequence_or_replay_failure`. No Phase-3 admission was
opened.

The first corrected-r6 launch failed before status creation with the preserved
`ModuleNotFoundError: No module named 'scripts'` log at
`artifacts/phase3/public-market-data-durable/20260811T-after-r5-four-hour-r6-clock-confidence`.
A second retry root at
`artifacts/phase3/public-market-data-durable/20260811T124355Z-four-hour-r6-clock-confidence-retry`
was preserved incomplete after its non-detached supervisor exited; it was not
reused. A one-cycle foreground diagnostic completed successfully and remains
separate evidence.

The fresh corrected r6 root is active at
`artifacts/phase3/public-market-data-durable/20260811T124600Z-four-hour-r6-clock-confidence-setsid`
under PID `2943`, runner code SHA-256
`b928650c279502b1f759c1584769f881f6c9a7f015ba34896d18c0501463fd0b`, target
`2026-08-11T16:46:26.507901Z`. Its v3 resource sidecar is PID `4807` at
`artifacts/phase3/public-market-data-resource-monitor/20260811T124600Z-four-hour-r6-clock-confidence-setsid-v3`;
the v1 precreation failure and v2 command-hash mismatch are preserved as
separate sidecar attempts. At this checkpoint r6 has produced `192` samples
and no terminal sample. All observed Binance BTC/ETH rows are
replay-equivalent and sequence-continuous; Coinbase remains explicitly
quarantined, while Deribit disconnect/recovery events and severe cross-source
disagreement remain preserved as measured fail-closed states. PID `28569` is a
separate post-run watcher that will write only fresh read-only
validation/evaluation roots after both protected processes finish. PID `70598`
remains untouched at cycle `218` with terminal target
`2026-08-11T18:07:25.593600Z`; neither timed gate is admitted. Archive/rclone
remains externally deferred.

## Current repository anchor — 2026-08-11

Clean `main` is `fbb010809598d6096b42309f16b5e13dd3e1acb8`, aligned with
`origin/main`, after PR #160. PR #160 adds offline validation that the
sanitized `latest-health.json` dashboard projection matches the latest
append-only source samples, preserves actual provider identity, and contains
no widened fields; the report records its projection SHA-256 and keeps
`phase3_admission=false`. Full pytest passed `655` tests with 28 warnings and
all eleven acceptance suites passed
`133/152/126/104/27/34/10/11/27/18/5`; no external gate was opened.

Read-only inspection at `2026-08-11T11:49:14Z` found selected-model PID
`70598` still `running` at cycle `196`, with genuine terminal target
`2026-08-11T18:07:25.593600Z`. Preserved Phase-3 r5 PID `46864` remained
`running` at `612` samples with zero terminal samples and target
`2026-08-11T12:35:59.156509Z`; its resource sidecar is PID `47392`. The exact
r5 review watcher is PID `91664`; the corrected-r6 controller is still waiting
and has not created its root, and the model terminal validator is still
waiting. No protected process or immutable evidence root was modified.
Archive/rclone remains externally deferred and untouched.

Clean `main` is `513a4fae65ddbf8f15b00eac52b8ebc390c6b5b1`, aligned with
`origin/main`, after PR #158. The attachment checkpoint’s PID `13339` and
two-hour r3 root are stale; the live protected Phase-0 model process is PID
`70598`, and the live Phase-3 r5 process/sidecar are PIDs `46864`/`47392`.
All immutable roots remain unchanged.

## Current continuation update — PR #156 timestamp projection

PR #156 merged the Phase-3 durable sample projection fix at
`85724aa5f3dece9ef7eb6f51a455d9ef03f6e871`. Future roots use runner code
identity
`f9f8c20aa33db840f1a930cc7a04f56b1c06b4a3a382130503e42462fa7e27c1` and
persist, per source/symbol sample, the latest provider event timestamp, latest
local receipt timestamp, and provider timestamp count. Raw-spool replay and
source identity remain unchanged; no admission gate was opened.

At `2026-08-11T11:22:33Z`, r5 PID `46864` remained `running` at 528 samples
with zero terminal samples and target `2026-08-11T12:35:59.156509Z`; model PID
`70598` remained `running` at 191 cycles with terminal target
`2026-08-11T18:07:25.593600Z`. The corrected r6 root has not started.
Archive/rclone remains externally deferred and untouched.

## Current continuation update — PR #158 validator hardening

PR #158 merged offline validation for the Phase-3 provider/local timestamp
projection at `513a4fae65ddbf8f15b00eac52b8ebc390c6b5b1`. Future projected roots
must contain complete timezone-aware provider and receipt timestamps with a
consistent provider-timestamp count; old roots are classified explicitly as
`legacy_unprojected` and are not rewritten. Full pytest passed 653 tests and
all eleven acceptance suites passed; no gate was opened.

At `2026-08-11T11:33:35Z`, r5 PID `46864` remained `running` at 564 samples
with zero terminal samples and target `2026-08-11T12:35:59.156509Z`; model PID
`70598` remained `running` at 193 cycles with terminal target
`2026-08-11T18:07:25.593600Z`. Corrected r6 has not started. Archive/rclone
remains externally deferred and untouched.

## Current continuation update — PR #154 provider freshness evidence

PR #154 merged the Phase-3 implementation fix at
`c1444364148be231585a96ced91ace223a8f989a`. Future durable roots use runner
code identity
`55562365731ff8e079d3b28cdb283464134c164b54c48a35351bbea2ef3a4d47` and pass
the latest timestamped public-stream event per source/symbol into the
cross-source disagreement calculation. When available, the immutable
disagreement record now contains the measured freshness-age difference;
missing provider timestamps remain explicitly unmeasured. The policy remains
fail-closed for severe disagreement or untrusted clock evidence. Focused Phase-3
tests passed 33 tests, full pytest passed 650 tests, and no external gate was
opened by this implementation.

At `2026-08-11T11:09:45Z`, the preserved Phase-3 r5 root remained `running` at
486 samples with zero terminal samples and target
`2026-08-11T12:35:59.156509Z`; its exact process and sidecar identities remain
unchanged. The selected-model r3 process PID `70598` remained `running` at 188
cycles with latest sample `2026-08-11T11:04:52.237058Z`; its genuine terminal
target remains `2026-08-11T18:07:25.593600Z`. The corrected-code r6 root has not
started. Archive/rclone remains externally deferred and untouched; Phase-0 and
Phase-3 admission remain closed.

## Current continuation update — Phase-4 offline measurement boundary

PR #152 adds the fail-closed offline
[`scripts/run_phase4_paper_utility.py`](../../scripts/run_phase4_paper_utility.py)
entrypoint and
[`docs/runbooks/phase4-paper-utility.md`](../runbooks/phase4-paper-utility.md).
It requires a currently valid passed Phase-3 `PhaseGateRecord` and a strict
typed observation/prediction envelope before writing one immutable
`measured_pending_review` report. It records the input and gate hashes,
explicitly records zero network/credential/weight activity, and cannot create
a gate record, open Phase-4 admission, promote a model, or submit an order.
No real Phase-4 report exists yet because Phase 3 has not been admitted.

At `2026-08-11T10:48:08Z`, PID `70598` remained `running` at cycle 184 with
terminal target `2026-08-11T18:07:25.593600Z`. The preserved r5 public-data
root remained `running` at 414 samples with terminal marker count `0` and
target `2026-08-11T12:35:59.156509Z`; its post-run validator and corrected-code
r6 controller remain separate. No timed root was modified and Phase-0/3
admission remains closed. Locked verification for PR #152 passed full pytest
`649 passed` with 28 warnings, acceptance suites
`133/152/126/98/27/34/10/11/27/18/5`, Ruff, format, compileall, lock check,
dashboard build, diff hygiene, and tracked-secret/weight checks.

## Current continuation observation — protected timed roots

Read-only inspection at `2026-08-11T09:54:45Z` found PID `70598` still
`running` with 175 passing cycles; its latest sample is
`2026-08-11T09:54:45.558492Z`, and its genuine terminal target remains
`2026-08-11T18:07:25.593600Z`. No terminal sample, summary, or Phase-0
admission is claimed. The process cwd, command, and evidence root were not
changed.

Read-only inspection of the preserved Phase-3 r5 root at
`2026-08-11T10:13:34Z` found 306 samples, 48 health transitions, state
`running`, and no terminal summary; its target remains
`2026-08-11T12:35:59.156509Z`. The root remains under its recorded earlier
runner identity `f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`
and cannot be retroactively credited with later age-reporting changes. No
process or evidence root was modified, and Phase-3 admission remains closed.

## Current continuation update — provider-clock confidence hardening

PR #148 merged at `3f7bfa26f0db1f15680576d09e0246525f3db8fd`. Future durable
Phase-3 roots use runner code identity
`9fc1875357437df27dfe3d0ae3a64c7e6ec957c6ba770c0569197281322ee684` and derive
cross-source quote timestamp confidence from the measured provider server-time
probe. A failed or out-of-policy clock probe now makes disagreement severe,
abstaining, and fail-closed; healthy probes preserve the normal comparison
path. Focused Phase-3 coverage and full pytest passed locally, but the active
r5 root remains immutable under its earlier identity and no Phase-3 gate state
changed.

## Current continuation update — offline model-stability validator

PR #145 merged the read-only terminal validator at
`f8802a8a5e4b1d605b07843e27a0d19a28d7e55f`. The new
`scripts/validate_model_stability.py` replays the model cycle hash chain,
requires a real sample at or beyond the 24-hour boundary, recomputes the
summary, binds each role to its approved checkpoint/runtime admission bundle,
and emits per-role `QUALIFIED` or `REJECTED / QUARANTINED` results in a
separate immutable review root. It cannot mutate the runner or open Phase-0
admission. Phase-0 remains `PENDING_STABILITY` while PID `70598` runs; Phase-0
acceptance passed `133` tests for this change.

## Current continuation update — Phase-3 non-negative age metrics

PR #143 merged the reporting-boundary fix at
`3404a0b649eeff960de0dced95efe5f7c1593bea`. Future durable Phase-3 roots use
runner code identity
`30d9fb147e6ce4a7204aaa0f2867d8c8e8e5200ec2d7a34ff2c3e037100d36eb` and clamp
clock-corrected event-age distributions at zero while retaining signed future
event counts as an explicit degraded-clock, fail-closed diagnostic. The active
r5 root remains under its earlier code identity and is not retroactively
credited with this fix; a fresh corrected-code root is required after r5
terminal review. Full pytest passed `640` tests with 28 warnings, the Phase-3
suite passed `72`, and repository quality checks passed. This is
`IMPLEMENTED / TESTED`, not Phase-3 admission.

## Current continuation update — Phase-3 measurement boundary

PR #140 merged into main at `335114ba73156cb75e44465a4d21ff27f86299e1`.
The durable public-data runner code identity is now
`17bed912495868062c6a7a79e515d5a29a8b65b40cf138b8845e837ba3ec280d`; it
separates the measured feed-window end from asynchronous WebSocket teardown and
uses the former for freshness/health evidence. Full pytest, all eleven
acceptance suites, static checks, compilation, dashboard build, and tracked
secret/weight checks passed. The active r5 root remains under its prior code
identity and was not disturbed. This is `IMPLEMENTED / TESTED`, not external
Phase-3 admission; a fresh root must measure the corrected boundary.

## Current continuation update — r4 terminal review and r5 active root

The prior four-hour root
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
ended at `2026-08-11T08:24:45.638125Z` with state
`multi_hour_window_complete`, 810 samples, and no explicit terminal marker
because it was launched before that runner fix. Its summary SHA-256 is
`53b1b77192dc77360b63b12208a445cc889c6bd0ff570fe4bd08ef37d8753fe2`; it
records Binance BTC/ETH stale at the terminal observation, Coinbase quarantined,
and Deribit degraded. The separate sidecar summary SHA-256 is
`75cc73ca44400d59df9f28037d5037ff8ca3c456c459f7d5e34ffe06e3168d47`, but its
final sparse target-exited observation has an invalid record hash and a
`process:FileNotFoundError` resource error. The old root and sidecar remain
unchanged and are quarantined; no admission was opened.

After PR #138 merged the sparse-observation sealing fix at
`1335bfabe93bdd990f9512430ae843a9795a7ebf`, a fresh current-code root is
active under PID `46864`:
`artifacts/phase3/public-market-data-durable/20260811T083600Z-four-hour-r5-reconnect-hashfixed`.
Its runner code identity is
`f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`, target
end is `2026-08-11T12:35:59.156509Z`, and its independent resource sidecar is
PID `47392` at
`artifacts/phase3/public-market-data-resource-monitor/20260811T083600Z-four-hour-r5-reconnect-hashfixed-v2`.
The first cycle is real public/read-only evidence; phase admission remains
closed pending terminal validation.

## Current continuation update — bounded Binance public-data reconnect

PR #136 merged into clean main at `350d6b55ac36251750e0459dc4e24b3507ca865c`.
Future durable Phase-3 roots use code identity
`f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8` and make
one bounded second Binance public WSS attempt after a failed first-message or
snapshot window. Each attempt remains separately spooled and counted, and
provider-truth snapshot recovery uses only the latest successful attempt's
updates; a failed attempt is not silently merged into a later continuity
chain. The change is credential-free, read-only, and adds no execution or OMS
authority. The active r4 root retains its earlier immutable code identity and
is not retroactively upgraded; Phase-3 admission remains pending until a
fresh terminal root satisfies the existing continuity and health policy.

## Current continuation update — Binance provider read-only recovery

PR #128 merged at `aa4cdcb86a9bd0c1ca749f0ded5524b8cb842c9c`; PR #133 is the
current executable anchor `083798403323e18f2cc6577103d7b81c36454279`.
Subsequent main changes are documentation-only follow-ups.
The new provider-specific recovery qualification uses only the scoped
`PAPER_VENUE` resolver and the existing Binance Spot Testnet transport. It
activates an immutable non-secret configuration revision, rolls back to the
original bundle, reopens that pointer in a fresh subprocess, and performs the
same authenticated read contract before and after the process boundary. It
does not submit, cancel, transfer, withdraw, mutate OMS state, or open an
admission gate.

The real report is
`artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json`
with SHA-256
`acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`.
It passed with 18 total read-only calls, provider-truth `BTCUSDT`/`ETHUSDT`,
configuration hash
`b41638ffc13149796f29676826b54097d2e7c417d9e4b1ff4d72be6d12f87286`, initial
and restored bundle hash
`0a44fe86c6cd7a65c316886f93848147aa3b75fd3a1eb3c31ae2579eaf7dc691`, and
`writes_attempted=false`. The measured state is
`EXTERNALLY_MEASURED / PROVIDER_READ_ONLY_RESTART_AND_CONFIG_ROLLBACK_MEASURED`;
admission remains `NOT_ADMITTED`. Full paper deployment rollback, open-order
recovery, Bronze rebuild, and archive restore remain separate evidence.

## Current continuation update — Phase-3 admission evaluator entrypoint

PR #133 merged the direct-entrypoint fix into executable main
`083798403323e18f2cc6577103d7b81c36454279`; subsequent documentation-only
follow-ups do not change this executable anchor. Direct
`scripts/evaluate_phase3_admission.py --help` now works from the repository
root without `PYTHONPATH`, and a subprocess regression proves the command is
offline. No admission logic or gate state changed. Locked verification passed
full pytest `635 passed` with 28 warnings and Phase-3 acceptance `91`.
The fixed entrypoint was then exercised against the immutable two-hour r3 root;
the fresh offline review is
`artifacts/phase3/public-market-data-admission/20260811T072500Z-two-hour-r3-entrypoint-recheck/phase3-admission-evaluation.json`
with SHA-256
`8c308ec39497ef962ea9dcb8fbbea611797bb2f0a488d08585923ae2fe7d131f`.
It remains `PENDING_EXTERNAL_EVIDENCE` with
`qualification_window_incomplete`, `no_healthy_primary_source_for_btc_eth`,
and `primary_snapshot_sequence_or_replay_failure`; `phase3_admission=false`.

## Current continuation update — post-PR #125 Phase-3 source identity integrity

The Phase-3 source-health ledger now binds each `(source_id, symbol)` stream to
one provider identity and endpoint, and verifies every appended transition's
declared predecessor state. A provider cannot silently replace another source
under the same stream identity. The change is isolated in candidate commit
`4abf2ce` is merged into clean main `3d3242c`; focused Phase-3 coverage passes
`64 tests` with one environment-only FastAPI skip, and all historical Phase-3
health ledgers reopen successfully. This is an implementation and local-test
result; it does not promote Phase-3 admission or alter the active r4 evidence
root.

## Current continuation update — Phase-3 resumable configuration bounds

Candidate commit `49b3283` makes the durable qualification runner reject a
resume that changes its persisted `max_cycles` bound, while same-configuration
hydration is verified not to append duplicate samples or health transitions.
The focused Phase-3 suite passes `66 tests`; this is local implementation and
test evidence only. The active r4 root predates the change and remains
untouched, and Phase-3 admission remains closed.

Checkpoint refreshed 2026-08-11 from the clean executable `main` anchor
`083798403323e18f2cc6577103d7b81c36454279` (PRs #86–#128 and #133 carry the executable
implementation/evidence; subsequent PRs are documentation-only follow-ups; PRs #95–#96
are documentation-only follow-ups to the #94 implementation/evidence anchor;
PR #103 adds the offline Phase-3 qualification validator, PR #105 records the
independent Phase-3 availability recheck, and PR #108 adds the durable Phase-7
runner boundary; PR #109 adds the offline Phase-3 admission evaluator, PR #110
adds the terminal-sample runner fix, PR #112 requires the explicit terminal
marker during review, PR #114 adds the closed Phase-4 utility preparation
boundary, PR #115 refreshes the evidence anchors, PR #116 adds the sanitized
Phase-3 failure-class projection, PR #118 points the model roster at the active
stability root, PR #119 records the post-roster verification, and PR #120
completes Phase-3 acceptance coverage and hardens failure-label validation; PRs
#121–#122 refresh the current evidence and continuation anchors, and PR #125
adds source-health provider identity/state-chain enforcement.)
The
Phase-3 durable source-health implementation, bounded snapshot resource fix,
concurrent symbol collection, accurate connection accounting, resource sidecar,
offline validation, and separate offline admission evaluator are merged. The
completed corrected real qualification root is recorded below and is not an
admission record.
Future durable windows now include an explicit post-boundary terminal sample;
the active r4 root predates that fix and remains separately identified by its
recorded code hash.
This matrix separates implementation, tests, local measurements, external
measurements, qualification, and admission. A passing test suite does not open
an external, timed, or human gate.

## Current Phase-3 review evidence

The immutable two-hour r3 root was revalidated offline after PR #120. The fresh
validator report is
`artifacts/phase3/public-market-data-validation/20260811T060000Z-two-hour-r3-v3/phase3-qualification-validation.json`
with SHA-256
`40b08077112092df4531175063d8c514ab58a65d31c317bb562e0d14ad8f1753`; it is
`PASS_FOR_REVIEW`, not admission. The corresponding admission review is
`artifacts/phase3/public-market-data-admission/20260811T060000Z-two-hour-r3-v3/phase3-admission-evaluation.json`
with SHA-256
`26ae8d2e7a209b71ce36fb1707a3183dff0840f14220d7df617874a1e8a80a26` and
remains `PENDING_EXTERNAL_EVIDENCE` because the root predates the terminal
marker, lacks a healthy BTC/ETH primary, and has primary snapshot/sequence/
replay failures. The active r4 and Phase-0 r3 processes remain untouched.

## Current active Phase-3 r4 observation

The independent four-hour root
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
is still running under PID `87421` toward
`2026-08-11T08:24:40.271709Z`. At the latest read-only observation it had 384
samples, 384 connection attempts/disconnects, 128 snapshot-recovery attempts,
zero successful connections, zero valid events, and all six source/symbol pairs
fail-closed. The separate resource sidecar PID `88019` had no resource error.
This root is in flight and cannot yet be judged as terminal evidence; its
recorded code identity predates the newer failure-label projection, so no
retroactive fields will be added.

## Current Phase-3 evidence projection update

The durable public-data runner now records sanitized `failure_classes` and
`failure_layers` per source/symbol sample, aggregates them in the summary, and
exposes the latest values through the read-only health snapshot/API. The projection
is limited to collector exception classes and deterministic layer labels; it
does not persist response bodies, headers, messages, or credentials. Focused
coverage passes `14` tests. The active r4 root under PID `87421` predates this
change and is preserved without retroactive modification; its current records
therefore remain evidence as recorded. This is an implementation/evidence
quality improvement and does not change `phase3_admission_opened=false`.

Locked verification for this continuation passed full pytest `624 passed` with
28 warnings and acceptance suites
`129/152/126/69/24/34/10/11/27/18/5`; repository static, build, lock,
compilation, secret, weight, and diff checks passed. No external gate was
opened by this verification.
The machine-readable roster's stability pointer is the active r3 root; this is
metadata only and does not alter the running process or its evidence.

## Current Phase-3 validator and acceptance coverage

The offline validator now checks optional `failure_classes` and `failure_layers`
for bounded sanitized labels, duplicate labels, and safe backward-compatible
omission on older immutable roots. It reports label coverage without opening
admission. The Phase-3 acceptance manifest now includes the complete
`tests/phase3` directory; the prior explicit list omitted WSS diagnostics,
admission-evaluator, validator, and resource-monitor tests. The complete
Phase-3 suite collects `87` tests and remains local evidence only.

Post-#119 locked verification passed full pytest `626 passed` with 28 warnings;
the complete acceptance results are
`129/152/126/87/24/34/10/11/27/18/5`.

## Latest Phase-4 utility preparation

PR #114 adds the offline, fail-closed Phase-4 paper-utility boundary under
`src/advisorai/phase4/paper_utility.py` and its preparation command under
`scripts/prepare_phase4_utility_evaluation.py`. The contract is ready to
consume admitted BTC/ETH paper observations, but rejects unadmitted Phase-3
input and cannot open Phase-4 admission. Its preparation manifest is
`artifacts/phase4/utility-evaluation-preparation/20260811T051344.190783212Z-offline-contract-v1/phase4-utility-preparation.json`
with SHA-256
`620f2ce32bb19aed8ce64ed0c12cddd4e0684db9f5b78add11e5b8ce6445456b`.
This is implementation/local evidence only; no real paper utility or model
promotion is claimed.

Current Phase-0 stability addendum (superseding the earlier baseline row): the
r2 root recorded eight passing cycles and then failed closed on the same
`FileNotFoundError` working-directory loss as its predecessor; its immutable
interruption record SHA-256 is
`4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`. The
absolute-path runner fix is implemented and regression-tested; a one-cycle
cwd-fix smoke passed with all three candidates, while fresh r3 is active under
PID `70598` from `2026-08-10T18:07:25.593600Z`; a read-only observation recorded
sequence 107 at `2026-08-11T03:47:10.345140Z` with record SHA-256
`c3e9e65afe59a78c80687ca19243e28cbf70f227131e4f207c1a05c8bd34b02f`. State remains
`PENDING_STABILITY`; no predecessor cycles are concatenated and no roster role
is promoted. The prompt-named non-r3 root remains append-only and is not
modified; its status file still names PID `12973`, while that PID is no longer
present in the host process table. The separately active r3 root is preserved
under PID `70598` with the same read-only observation; this is not a terminal
sample and does not open the timed gate.

| Stage / requirement | Authoritative source | Implementation present? | Automated tests? | Local deterministic evidence? | Real external evidence? | Timed evidence? | Human action? | Current gate state | Blocker | Next admissible action |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| Phase 0 contracts, ports, policy gateway, model/runtime harness | architecture §11; phase-00 plan | yes | yes | yes | no | no | no | TESTED / LOCALLY MEASURED | none for local boundary | Preserve accepted local records; do not treat them as admission |
| Phase 0 selected-model stability: TTM-R2, Finance DeBERTa-v3, FinBERT-MiniLM | phase-00 plan; model-runtime runbook | yes, including terminal-sample boundary, immutable runtime identity, and append-only validation | yes | yes, per-role terminal review | no remote route admission | 24.003634-hour review complete | no | QUALIFIED per role / PHASE 0 PENDING | immutable r3 review has 269 cycles and no issues; `ttm-r2`, `finsentiment-deberta-v3`, and `finbert-minilm` are each `QUALIFIED`, but remote-route and archive prerequisites keep overall Phase 0 closed | Keep the role evidence bound to the roster without selecting/promoting a model globally; preserve all predecessor roots and finish the remaining Phase-0 prerequisites |
| Phase 0 remote route bake-off | phase-00 plan; remote-model runbook | yes, including resumable stability runner with stop-on-failure | yes | short live route evidence plus preserved hash-chained failures | yes, exact provider/model/endpoint identity on earlier successful samples; current retries failed closed | pending 24-hour window | provider availability/time-dependent | EXTERNALLY MEASURED / QUARANTINED | Novita and DigitalOcean roots are quarantined after shared-pool HTTP 429, deadline exhaustion, and earlier runner-integrity incidents; corrected root `artifacts/phase0/remote-route-stability/20260810T053600Z` stopped after its first failed probe and has no eligible duration evidence | Preserve incident roots; retry the reviewed exact route only after provider availability returns, using a fresh systemd-backed root; never concatenate failed samples |
| Phase 0 Nautilus / Prefect / Hamilton seams | phase-00 plan | yes | yes | yes, credential-free component drill | no provider-specific evidence | no | no | TESTED / QUARANTINED | external Nautilus qualification and operational use remain governed by Phase 0 | Keep local seam evidence; qualify only through the selected gate |
| Phase 0 Parquet-manifest vs DuckLake comparison | architecture §4.2; phase-00 plan | manifest/DuckDB baseline yes | baseline yes | yes | yes, isolated challenger review | no | no | QUALIFIED / REJECTED | DuckLake snapshot/reopen worked, but the second catalog added measurable footprint and relocation override complexity without enough incremental value | Keep manifest-managed Parquet + DuckDB + SQLite WAL; preserve the immutable comparison report |
| Phase 0 external Hermes coordinator/subagent review | architecture §8; phase-00 plan | repository harness and pinned external runtime reviewed | local security tests yes | yes | yes, synthetic loopback route only | no | no | EXTERNALLY MEASURED / QUARANTINED | real provider/model route and complete native/filesystem OS attestation remain absent | Preserve the pinned review; formal admission remains closed and no runtime enters AdvisorAI core |
| Phase 0 rclone-crypt upload/verify/restore | architecture §4.2; phase-00 plan; rclone archive qualification runbook | typed adapter, scoped process environment, backward-compatible singular config, explicit A/B provider pairs, and bounded raw-list timeout yes | `tests/expansion/test_rclone.py`, `tests/config/test_secrets.py`, `tests/phase0/test_rclone_qualification.py`, and qualification runner pass | in-memory restore yes; fresh real sanitized roots | yes, independent A/B crypt upload/restore and three-way SHA equality; Provider A raw-layer check passed, Provider B recursive raw enumeration failed | no | provider-B raw-listing recovery/configuration review | EXTERNALLY MEASURED / PARTIAL / NOT QUALIFIED | Latest root `artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z` report SHA-256 `be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf` has Provider B raw-layer command failure; no plaintext exposure is claimed for the incomplete listing | Diagnose or remediate the reviewed Provider B raw listing, then run a fresh explicit A/B qualification; never promote the three-way restore alone to archive admission |
| Phase 0 resource/privacy/failure behavior | phase-00 plan; resource and gateway runbooks | yes | yes | yes | partial route observations | model stability review complete; route duration pending | no | TESTED / PENDING_EXTERNAL_EVIDENCE | selected local roles are stable-qualified, but route repetition and archive evidence remain incomplete | Preserve quarantined route incidents; do not retry unhealthy providers or touch the deferred archive gate |
| Phase 1 deterministic foundation and local rollback/Bronze rebuild | phase-01 plan | yes | yes | immutable local report | no provider deployment | no | no | QUALIFIED LOCALLY | real paper deployment rollback and archive restore remain external | Preserve local report and use the provider-specific recovery row below for measured external evidence |
| Phase 1 provider-specific Binance read-only restart/configuration recovery | phase-01 plan; real-api-paper-transition.md; Binance runbook | yes; `scripts/qualify_binance_spot_testnet_recovery.py` uses `ConfigBundleStore`, scoped `PAPER_VENUE`, and the existing `BinanceSpotTestnetTransport` | `tests/recovery/test_binance_readonly_recovery.py`, config-bundle tests, Binance adapter/lifecycle tests | immutable bundle activation/rollback/reopen and no-write probe tests | report `artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json`, SHA-256 `acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`; two authenticated read projections and fresh child-process hydration passed; zero order writes | no | no | EXTERNALLY MEASURED / PARTIAL / NOT ADMITTED | this is read-only provider recovery only; open-order/in-flight paper deployment rollback, Bronze rebuild under provider state, and long-lived restore remain unmeasured | Preserve the report; qualify full supervised paper deployment/restart/rollback only after the admitted paper runtime is wired, without repeating an order merely for evidence |
| Phase 2 deterministic paper core and Coinbase Exchange Sandbox transport | phase-02 plan; real-api-paper-transition.md | yes; Coinbase-specific `CB-ACCESS-*` signer, schema mapper, exact sandbox host guard, and read-only smoke runner | yes, including `tests/integrations/test_coinbase_exchange.py` | replay/failure fixtures plus signer/product/OMS boundary tests | partial: real Coinbase `/time`, `/products`, `/accounts`, `/orders`, and `/fills` requests reached the reviewed sandbox; account/balance/position/open-order reads passed, but product mapping and fills did not | no | provider catalogue/profile and fills-permission action | EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | the returned 13-product sandbox catalogue contained `BTC-USD` but not the required `ETH-USD`; the product-filtered fills read returned HTTP 401; no order writes were attempted | Preserve the Coinbase evidence and rerun only if a reviewed sandbox profile genuinely exposes both required products and grants the documented fills read permission; never fall back to the generic smoke or production |
| Phase 2 selected BTC/ETH paper venue candidate — Binance Spot Testnet | phase-02 plan; real-api-paper-transition.md; paper-venue-selection.md | yes; provider-specific HMAC signer, exact testnet host/path guard, product/filter mapper, account/balance/position/order/fill schema mapper, top-of-book read, scoped read-only smoke, supervised lifecycle runner, and existing `NativeTransport` boundary | yes, `tests/integrations/test_binance_spot.py`, `tests/integrations/test_binance_spot_lifecycle.py`, `tests/integrations/test_paper_venue_bakeoff.py`, formal Phase-2 tests, plus offline config/path tests | signer, provider schema, idempotent write, restart-query, production/transfer rejection fixtures, deterministic lifecycle/failure-drill tests, and credential-free ordered Binance/Bybit comparison runner | Fresh authenticated read-only evidence passed all eight required operations at `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`, SHA-256 `c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72`; one supervised fake-funds `LIMIT_MAKER` BTCUSDT lifecycle with one signed POST, one signed DELETE, authoritative query/reconciliation, restart hydration, TCA/zero-residual attribution, and no real fill at `artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`, SHA-256 `db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70`; the formal Phase-2 checklist and passed record bind these artifacts and preserve the no-fill limitation | no | no new operator action for this venue-specific paper qualification; real fill and external attribution remain Phase-6 evidence | EXTERNALLY MEASURED / QUALIFIED / PASSED | passed record `artifacts/phase2/formal-admission/20260812T013500Z-post-phase2-commit/phase2-gate-record.json`, SHA-256 `efb9d678e72f9785c3d9162660ead6cd434af6108249de73a42a08dd9a64bdae`; no transfer/withdrawal/production call | Preserve the immutable evidence and use Binance only through the existing RiskKernel → OMS chain; do not repeat a signed order merely to obtain a fill |
| Phase 3 V3-Core source spine | phase-03 plan; real-api-paper-transition.md; formal Phase-3 checklist | yes, including raw-first public BTC/ETH source qualification, typed health/fail-closed selection, lineage, disagreement, replay, and formal checklist/record boundary | yes, Phase-3 parser/replay suites plus `tests/phase3/test_phase3_formal_gate.py` | immutable r7 validator/admission review and broader source pass | r7 structural review and policy review pass; Binance public BTC/ETH is credential-free and write-free, stale intervals fail closed, source identity is preserved, and no silent substitution occurred. Broader source qualification preserves Coinbase Sandbox ETH-USD HTTP 404 and GDELT HTTP 429 as non-gating external outcomes with zero observations. Phase-2 predecessor was formalized offline and the unchanged r7 evidence was re-evaluated. | no new durability window required | no; reviewed public endpoints only | EXTERNALLY MEASURED / QUALIFIED / PASSED | passed checklist `artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-admission-checklist.json`, SHA-256 `57b3c32984320d53cf889fb77d4238907c13a38c93727bc7f7d0d55dc5dbee45`; passed record SHA-256 `4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232`; canonical hash `a8f646d49edf716f201b9da015080872b0f14170128138d569446ec63119c4e3` | preserve r7 and proceed to the Phase-4 measurement boundary; do not launch another durability root for already-satisfied evidence |
| Phase 3 latest REST retry | phase-03 plan; real-api-paper-transition.md | raw-first public REST qualification runner | `tests/phase3/test_source_qualification.py` | replay/duplicate-append/freshness fixtures | Fresh root `artifacts/phase3/source-qualification/20260810T201653.611706Z/phase3-v3-core-source-qualification.json`, SHA-256 `60cac1ba77fa31735c87b02e29125985e9d4e69b2e592886e317b0ed61ecca01`, made seven public calls: Coinbase BTC-USD ticker, Deribit index, and SEC RSS passed; Coinbase ETH-USD returned HTTP 404 and GDELT HTTP 429 | no | no | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | the provider failures remain real external availability/product truth; continuous freshness, reconnect/recovery, and independent-source disagreement remain unmeasured | Preserve the root; do not substitute ETH or GDELT data, and collect the next independent Phase-3 evidence only when available |
| Phase 3 current evidence addendum | phase-03 plan; real-api-paper-transition.md | Binance public depth qualifier, raw snapshot/update replay, and bounded provider/local clock-offset measurement are implemented | `tests/phase3/test_binance_depth_qualification.py`; Phase-3 acceptance includes it | reducer, replay, clock-offset, and deterministic fault-drill fixtures | bounded root `artifacts/phase3/binance-spot-testnet-depth/20260810T173135.489992Z/phase3-binance-spot-testnet-depth.json`, SHA-256 `b794c7fd2c014c89928c7bf2ad4b73fde253a615818dddd27a4da53a025c76c0`: four BTC/ETH snapshots and 289 updates replay-equivalent; all four connections completed; provider event timestamps were ahead of local receipt. A fresh requested 120-second root `artifacts/phase3/binance-spot-testnet-depth/20260810T182011.404029Z/phase3-binance-spot-testnet-depth.json`, SHA-256 `7b249a125c78e346c7b9d028850e2b7cbf004c890e005bad6f6f8d70b92ddd08`, failed closed before the first message on all four WSS attempts with `WebSocketTransportError`; both reports predate the offset implementation, and no REST snapshot or write was attempted in the fresh root | no continuous window | no | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | prior real freshness failure plus a subsequent provider/runtime WSS availability failure; independent source disagreement remains unmeasured | preserve both immutable roots; retry only after availability is reviewed, then run the offset-aware qualifier and collect recovery and independent-source disagreement |
| Phase 3 public market-data plane and execution separation | phase-03 plan; real-api-paper-transition.md | yes; credential-free reviewed public REST/WSS source cards, raw-first bake-off, provider-time metadata, product/filter truth, and explicit no-write separation from Binance Spot Testnet execution | yes, `tests/data/test_public_market_data.py` and WSS/depth regression suites | deterministic source-card/selection tests and replay boundaries | v2 root `artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`, SHA-256 `14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`: Binance public BTCUSDT/ETHUSDT completed four full read-only windows, two reconnects per symbol, adjusted freshness passed after a measured 0.794-second provider/local offset, and real Coinbase-vs-Binance BTC/ETH top-of-book observations were recorded; Coinbase had one adjusted-future session and Deribit remained context-only | no continuous unattended window | no | EXTERNALLY MEASURED / PARTIAL / PENDING_EXTERNAL_EVIDENCE | longer unattended operation, sequence/snapshot recovery, source disagreement policy, and provider failover are not yet admitted; Binance Testnet WSS remains intermittent and is not silently substituted | Run longer independent source windows, recovery, and explicit failover drills; keep execution writes confined to the admitted Binance transport |
| Phase 4 quantitative baseline council | phase-04 plan | yes; offline paper-utility boundary requires a passed Phase-3 `PhaseGateRecord`, explicit provenance, and closed admission | yes, `tests/models/test_paper_utility.py`, `tests/models/test_phase4_predecessor.py`, and `tests/models/test_phase4_real_utility_input.py` | frozen Binance public BTC/ETH snapshot, mandatory baselines, measured TTM-R2 control, and measured TTM-R3 challenger | 64 point-in-time BTC/ETH observations and 448 predictions; TTM-R2 net utility `5248.14` bps versus strongest measured baseline LightGBM `2485.98` bps; TTM-R3 net utility `1468.25` bps and incremental `-1017.73` bps; conservative scenario, measurement-only | no | no | IMPLEMENTED / TESTED / REAL_MEASURED / PENDING_REVIEW | input SHA-256 `e95d3937e966902f452754f764ea50c59add4852158bf4457607c66fab36a036`; report SHA-256 `2da6b6576a4679fa688920de41a360a8d5f865664e3f608e3ba4410e2c26a2aa`; TTM-R3 is not eligible from this window; interval calibration and latency sensitivity remain open; no model promotion or Phase-4 admission | review the immutable report, extend only the required past-only calibration/latency or challenger evidence, and create a formal Phase-4 decision only if the authoritative exit gate passes; do not treat one historical window as sufficient by default |
| Phase 5 typed evidence council | phase-05 plan | yes | yes | independence/authority fixtures | no real V3-Core scored council | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real source/model/provider route and data are absent | Exercise with admitted real V3-Core data after earlier gates |
| Phase 6 institutional controls and attribution | phase-06 plan | yes | yes | deterministic risk/attribution fixtures | no real paper order sample | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real paper fills and residual incidents are absent | Run the complete paper chain and reconcile exact attribution |
| Phase 7 unattended paper soak and recovery | phase-07 plan | yes; `DurablePaperSoakRunner` now provides immutable run identity, fsync'd hash-chained samples, PID/heartbeat status, lock ownership, restart hydration, and terminal-sample enforcement | yes, `tests/recovery/test_durable_soak.py` plus existing soak/recovery tests | bounded resume/tamper/failure evidence only | no | 60 calendar days required | operator supervision | PENDING_TIME_GATE | Phase 0–6 real prerequisites and venue operation are not ready; no real root launched | Wire the admitted paper runtime and launch one supervised durable root only after earlier gates pass |
| Phase 8 Hermes capability lifecycle | phase-08 plan | yes, including disposable Docker boundary probe | yes, including `tests/capabilities/test_os_sandbox_probe.py` | immutable fixture active-read report plus real local Docker boundary measurement | partial: pinned external runtime/synthetic task; no real model route or real Hermes capability task | no | review required for active-write only | EXTERNALLY MEASURED / QUARANTINED | Docker measured network denial, read-only root denial, zero effective capabilities, and bounded process controls, but native syscall/C-extension containment and credential/production-tree isolation are not attested; earlier gates remain closed | Preserve the OS-boundary report; evaluate a real isolated Hermes capability only after earlier phase gates and stronger containment evidence permit it |
| Phase 9 controlled expansion | phase-09 plan | yes | yes | challenger/source boundaries | no marginal-value challenger evidence | no | no | QUARANTINED | Phase 0–7 and E0 are not satisfied | Keep additions quarantined; reject challengers with evidence when evaluated |
| Phase 10 bounded-live readiness guards | phase-10 plan | yes | yes | readiness/AI-offline fixtures | no live validation | no | explicit human approval required | PENDING_OPERATOR_ACTION | Phase 7 and all prerequisites incomplete; no human authorization | Keep live closed; do not create approval or enable production |
| Real API/paper transition bridge | real-api-paper-transition.md; Coinbase and Binance connector runbooks | yes | yes | offline config/adapter evidence, Coinbase and Binance contract tests, supervised Binance lifecycle tests | Coinbase private reads remain partial; Binance authenticated read-only, provider-filtered BTC/ETH mapping, and one supervised fake-funds cancellation lifecycle are externally measured and preserved | no | no new venue operator action; later fill/soak gates remain supervised | EXTERNALLY MEASURED / QUALIFIED / PENDING_BASE_GATES | Coinbase cannot satisfy the required ETH-USD/fills gate; Binance is usable for the observed no-fill paper path, while real fill, Phase 0 stability, Phase 3 operational source, and later Phase 4–7 evidence remain open | Keep Coinbase quarantined; continue Phase 3 independently and use the selected Binance adapter only behind deterministic RiskKernel and authoritative OMS |
| Alpha E0 — V3-Core prerequisite | alpha-team-extension.md | plan-only | none | none beyond base evidence | no | inherits Phase 0–7 | no | BLOCKED | Phase 0–7 paper/recovery/data/risk/resource gates are not complete | Do not implement Alpha runtime; continue base gates |
| Alpha E1 — Research Brain | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0 and Phase 7 prerequisite | Wait; only maintain plan/traceability |
| Alpha E2 — Controlled Alpha Lab | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0/E1 gates | Do not build DSL or candidate runtime early |
| Alpha E3 — first V3 strategy challenger | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E2 plus real V3-Core paper evidence | Wait for admission |
| Alpha E4 — optional capability adapters | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E3 and Phase 8/9 authority | Keep external challengers quarantined |
| Alpha E5 — equities / long horizon | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E4 and point-in-time equity gate | No equity expansion |
| Alpha E6 — controlled candidate expansion | alpha-team-extension.md | no, plan-only | no | no | 60+ healthy paper days required | no | BLOCKED | E5 and per-scope soak | No candidate activation |
| Alpha E7 — bounded-live scope | alpha-team-extension.md | no, plan-only | no | no | no | human approval required | BLOCKED | Phase 10 explicit go-live review | No live scope or approval |

## Latest Phase-3 Binance WSS availability retry

A further bounded 20-second retry at
`artifacts/phase3/binance-spot-testnet-depth/20260810T201946.533716Z/phase3-binance-spot-testnet-depth.json`
has SHA-256
`ce402b7bdd67513c90b1cc5bf744d0a8d455a6f1b7f927610a84f997699b8415`.
All four public WSS connections failed closed before their first message with
`WebSocketTransportError`, made zero REST calls, and passed deterministic
fault drills. This is provider/runtime availability evidence, not a
freshness, reconnect, or Phase-3 admission pass.

## Latest Phase-3 WSS layer diagnosis

The credential-free diagnostic at
`artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json`
has SHA-256
`8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b`.
DNS resolved, TCP connectivity succeeded, and TLS negotiated TLS 1.3. The
isolated locked transition runtime reported `websockets` 16.1.1; direct BTC
and ETH attempts reached first public market messages on successful attempts,
valid subscriptions received acknowledgements, and BTC reconnect passed. ETH
had one connection timeout before a later successful attempt. The final
classification is `websocket_connection_timeout`, not provider-unavailable.
The earlier `.venv` probe is preserved separately as a local missing-library
classification with SHA-256
`bc08d878e70193368bea67981a24ba3033704314e61626f7c796951caa13da9f`.
Malformed subscriptions were not sent.

The post-diagnostic depth run at
`artifacts/phase3/binance-spot-testnet-depth/20260810T211531.293435Z/phase3-binance-spot-testnet-depth.json`
has SHA-256
`f75f4e25ba48d923df4cba4e29d7ccf4b45e7382a05b5f63bb3a500b8b59fcde`.
It captured an ETH stream with live/replay equivalence and preserved a BTC
connection failure plus adjusted-future fail-closed results on other streams.
The preserved report is partial
operational evidence, not a Phase-3 admission.

## Public market-data plane selection

The credential-free public bake-off selected Binance public market data as the
current primary candidate at
`artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`
with SHA-256
`14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`.
It verified public product truth, filters, books, trades, server time, four
full BTC/ETH WSS windows, two reconnects per symbol, adjusted freshness, and
cross-source top-of-book observations without credentials or write methods.
The source card is separate from the Binance Spot Testnet execution
adapter; it does not load broker credentials and cannot submit orders. Coinbase
public data remains an unselected candidate because its current product records
did not provide complete minimum-quantity fields; Deribit remains context-only.
Longer freshness, reconnect/resubscription, gap/snapshot recovery,
outage/backoff, source disagreement, and no-silent-substitution failover are
still pending.

## Current Phase-3 durable source-health gate

| Requirement | Implementation and tests | Real evidence | Current state / next action |
|---|---|---|---|
| Restartable unattended qualification | `scripts/run_phase3_public_data_qualification.py`; `src/advisorai/collectors/source_health.py`, `market_recovery.py`, `source_disagreement.py`, and `source_failover.py`; `scripts/validate_phase3_public_data_qualification.py`; `scripts/monitor_phase3_process_resources.py`; `tests/phase3/test_source_health_controls.py`, `test_phase3_qualification_validation.py`, and `test_phase3_resource_monitor.py` | Completed root `artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3` reached its target at `2026-08-11T03:14:39.940009Z` with 63 cycles/378 samples, summary SHA-256 `eb33cb5939feb5126bef3eff210c3710a95d6fbf3d85b3433bc2ad024a191ed7`, config SHA-256 `eb09ac0aa008c5a42c7e318178c79421bdf4d471b5649ddf65baa50a59f12398`, status SHA-256 `df8a7aa57aa95205636ce0e800882f6ccca0647b386a29488c83b7bba97ed5da`, and heartbeat SHA-256 `5d44ef77d3bf459f75c8141c53dbb45e6275489399d42616a1ad20ddd1fcb66`. The offline validation report at `artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json` has SHA-256 `efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca`, state `PASS_FOR_REVIEW`, `phase3_admission=false`, and no validation issues. The corrected v2 resource sidecar reached `deadline_reached` with 32 observations and no resource errors; summary SHA-256 `42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd` | IMPLEMENTED / TESTED / EXTERNALLY MEASURED / QUALIFIED FOR REVIEW / NOT ADMITTED. The root is no longer running. Final Binance sources were stale, Coinbase sources quarantined, and Deribit sources degraded; all 126 selections failed closed, silent substitution was zero, three replay failures and 22 severe disagreements were preserved. Keep admission closed and proceed only after Phase-0 stability and remaining Phase-3 criteria are satisfied. |
| Deterministic health and failover | Typed HEALTHY, DEGRADED, STALE, DISCONNECTED, RECOVERING, and QUARANTINED transitions; hash-chained transition ledger; explicit severe-disagreement abstention and fail-closed selection; sanitized read-only dashboard/API | Completed r3 validation reloaded 78 health transitions and 126 source selections. Final Binance states were `STALE`, Coinbase states `QUARANTINED`, and Deribit states `DEGRADED`; 126/126 selections failed closed, silent substitution was zero, disagreement was severe 22 times, and the root preserved three replay failures | No Phase-3 admission yet. The measured state machine is externally qualified for review; preserve source identity and fail closed until the remaining admission criteria and Phase-0 stability gate pass. |

The offline admission evaluator at
`scripts/evaluate_phase3_admission.py` is a separate, read-only review boundary;
it validates the requested duration from immutable timestamps, requires a real
terminal sample, checks public/write separation, source-card endpoint identity,
all-cycle primary-source continuity, fail-closed disagreement and selection
behavior, and a completed error-free resource sidecar. It cannot represent a
formal Phase-3 admission or write a `PhaseGateRecord`. Its focused tests are in
`tests/phase3/test_phase3_admission.py`.

Evaluation of the completed r3 root produced
`artifacts/phase3/public-market-data-admission/20260811T043711Z-two-hour-r3-v2/phase3-admission-evaluation.json`
with SHA-256
`cbb8ec53d793887f17ebeccab8db33a52051082cdd989ff780b7a5f854cf0c1b` and
recommendation `PENDING_EXTERNAL_EVIDENCE`. The exact blockers are
`qualification_window_incomplete` (the last sample preceded the target even
though the process finalized after it),
`no_healthy_primary_source_for_btc_eth`, and
`primary_snapshot_sequence_or_replay_failure`. This is a stricter review of
the existing evidence, not a policy relaxation.

A fresh four-hour root is currently running independently at
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
under PID `87421`, with resource sidecar PID `88019` at
`artifacts/phase3/public-market-data-resource-monitor/20260811T042355Z-four-hour-r4-fixed-v2`.
Its target is `2026-08-11T08:24:40.271709Z`, code SHA-256 is
`c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`, and the
public connectors are credential-free and write-free. Both processes are
durable evidence only; neither is an admission record and neither may be
restarted or concatenated.

An independent one-cycle recheck at
`artifacts/phase3/public-market-data-durable/20260811T034114Z-one-cycle-recheck`
made six public connections and received 503 valid events. Its summary SHA-256
is `698ad40af908757a398d19c6df83e4bfc50209bca541fe8b3acd6c314d6eff1e`.
Binance BTC/ETH again ended stale at `5.096588s`/`5.011760s` against the
5-second policy; Coinbase remained quarantined and Deribit degraded. This
corroborates the durable-window blocker and does not justify relaxing the
policy or silently substituting a source.

## Current external blockers

- Coinbase Exchange Sandbox configuration is present in the local ignored
  `secrets.env` and passed the zero-network reviewed-host check with configuration
  hash `138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`.
  The adapter uses only the `PAPER_VENUE` credential scope; secret values were
  not printed or persisted.
- The latest real Coinbase read-only attempt reached `/time`, `/products`,
  authenticated `/accounts` projections, `/orders`, and a product-filtered
  `/fills` read, then failed closed because the returned catalogue had `BTC-USD`
  but no required `ETH-USD`; account/balance/position/open-order reads passed,
  while fills returned sanitized HTTP 401. Immutable sanitized evidence is at
  `artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`
  with SHA-256 `79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`.
  No order, cancel, transfer, or withdrawal was attempted.
- Binance Spot Testnet is the selected BTC/ETH replacement candidate. Its
  public, credential-free qualifier measured server time and provider product
  truth for both `BTCUSDT` and `ETHUSDT`. Fresh authenticated read-only
  evidence then passed server time, products, BTC/ETH mapping, account,
  balances, positions, open orders, and fills at
  `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`
  with SHA-256
  `c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72`.
  One supervised fake-funds `LIMIT_MAKER` lifecycle then passed the
  deterministic RiskKernel → OMS → Binance transport → reconciliation chain
  with one signed submission, one cancellation, restart recovery, TCA, zero
  unexplained attribution residuals, and deterministic failure drills. Its
  report is at
  `artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`
  with SHA-256
  `db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70`.
  The real path observed no fill; fill ingestion remains fixture-tested. No
  Binance credential value was printed or persisted, and no production,
  transfer, or withdrawal endpoint was called.
- The final-source read-only rerun is immutable evidence at
  `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T201450.306674Z/binance-spot-testnet-read-only-smoke.json`
  with SHA-256
  `b3a8b54f446599b50547bab98240db0fe8e1380fd969a6a220fccac1c83fe8e7` and
  adapter source SHA-256
  `ec3077cc726a045420c714f99c5c2e026351190348fdc9779f96e21cff034e0d`.
- The current zero-network resolver check passes against the canonical
  repository-local secrets inventory with configuration hash
  `138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`; no
  second secrets inventory is maintained.
- The typed two-provider rclone qualification boundary is implemented and
  fixture-tested. The initial controlled real-run attempt found no populated
  `ARCHIVE_RCLONE` values and made zero network calls. The latest fresh root at
  `artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z/`
  measured independent A/B crypt uploads/restores, three-way SHA equality, and
  all recovery drills. Provider A raw-layer enumeration passed; Provider B raw
  recursive enumeration returned a sanitized provider command failure. The
  latest report SHA-256 is
  `be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf`; the
  manual A/B copy statement is deliberately not counted as qualification.
- The previous Phase-0 24-hour worker was interrupted by the laptop shutdown;
  its evidence is preserved. The post-format replacement root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`
  ended `short_smoke_complete` at 23.96857 hours after 273 passing cycles; its
  summary SHA-256 is
  `ec8208a4419aef1f1a85dc0d43e984feb6bb6f45b92a65fd67b1be956bad1661`. The
  runner now requires a real terminal sample at/after the duration boundary.
  Fresh root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810`
  recorded 7 passing cycles and then exited at cycle execution with a
  sanitized `FileNotFoundError` because its worker cwd was unavailable. The
  preserved interruption evidence is
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810/interruption.json`;
  the stderr-log SHA-256 is
  `482f878994c8dbf8b339cb48460ae576b37400f1209f7ce76f7d988a181f68e6`.
  Replacement root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r2`
  is preserved as interrupted after eight passing cycles; its interruption
  record SHA-256 is `4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`.
  Fresh r3 is active under PID `70598` with latest observed sequence 103 at
  `2026-08-11T03:25:18.150192Z` and last record SHA-256
  `49bb4f3ea73fce5661ec64bb546cdba08cc21ac07ba74b97834b6a656b494fb0`;
  do not concatenate roots.
- DuckLake comparison is complete and rejected with measured evidence at
  `artifacts/phase0/ducklake-comparison/20260809T162300Z/ducklake-comparison.json`.
- The pinned upstream Hermes review is complete as partial external-runtime
  evidence at
  `artifacts/phase0/external-hermes-review/20260809T162031Z/external-hermes-review.json`;
  it used a synthetic loopback provider and does not open Phase 8.
- A disposable Docker boundary probe was measured on 2026-08-10 at
  `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
  with SHA-256
  `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`.
  It recorded zero external network calls, a root-identity read-only root
  filesystem check, a constrained writable tmpfs, dropped capabilities, denied
  unshare/mount escape probes, and bounded process controls using the local
  Docker runtime. It did not mount the repository, credentials, broker, order,
  or production paths. Universal native syscall and C-extension containment
  remain `not_attested`, so this evidence is not formal Hermes or Phase-8
  admission.
- The exact Novita route stability trial is preserved as a failed/quarantined
  run after an upstream shared-pool HTTP 429. Earlier DigitalOcean roots were
  quarantined for runner-integrity defects, and the later root at
  `artifacts/phase0/remote-route-stability/20260809T173237.710604Z` recorded 62
  cycles with three immutable upstream shared-pool HTTP 429 gateway abstentions;
  its incident SHA-256 is
  `f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`.
  Root `artifacts/phase0/remote-route-stability/20260810T034500Z` then recorded
  11 passing cycles followed by an HTTP 429 and is quarantined by incident SHA
  `805d763d69841515f7beb676ec2a0dea2e2043106dbb4dbc43b292bff4350e9f`.
  Corrected root `artifacts/phase0/remote-route-stability/20260810T053600Z`
  stopped after its first deadline-exhausted probe and is quarantined by
  incident SHA `5b6d5ffe9133811a664f24151b95fcd850f130cff718bc6ed1eae9289178cff1`.
  No route window is active; failed samples are not concatenated.
- Phase 7 requires real paper/testnet operation plus an actual 60-day duration.
- Phase 10 requires explicit human approval and remains closed.

## Safety truth

No model, LLM route, Hermes task, browser task, dashboard, or Alpha Team plan
has trading authority. `RiskKernel` remains the deterministic veto and `OMS`
remains authoritative. Live-capital deployment is not approved.
