# V3 implementation audit

This audit maps the authoritative architecture and its phase sub-plans to the
current executable base. “Local” means the boundary, contract, or deterministic
fixture exists in this repository. It does not convert an external, timed, or
human gate into a unit-test claim.

## Fresh replacement forward run — 2026-08-17

The former forward root is intentionally interrupted evidence, not a crash
incident: the operator shut down the laptop before continuation. Its exact
classification is preserved at
`artifacts/phase4/v3core-forward-incidents/20260817T192126Z-operator-interrupted/classification.json`
(SHA-256
`678450a5d5e1b4c8cd79a75303ffc174e6c29ef85de9d75c2cb664f4f78fd970`). The
root remains unchanged and is not eligible for backfill or admission.

The replacement collector is running from a new evidence generation at
`artifacts/phase4/v3core-forward/20260817T193400Z-operator-interrupted-replacement-r1/`
(PID `59671`, commit
`0e23c0b6a94ac87df7e5cc9fa0e552cb9adb50c5`, fixed target end
`2026-08-22T19:35:06.869338Z`). Its market-data-only Binance endpoint is
credential-free and has no order capability. Resource observation is isolated
under
`artifacts/phase4/v3core-forward-resource/20260817T193400Z-operator-interrupted-replacement-r1-sidecar-r1/`
(PID `61721`), and the matching baseline ledger is under
`artifacts/phase4/v3core-forward-predictions/20260817T193400Z-operator-interrupted-replacement-r1/`
(PID `60814`).
An initial sidecar launch attempt failed before sampling due solely to a
pre-created evidence directory; this did not touch the collector and is
preserved as
`artifacts/phase4/v3core-forward-resource-incidents/20260817T193725Z-sidecar-launch-directory-protocol/classification.json`
(SHA-256
`b652681c71c9e7e34c2ffa0a2572986877207e0e7891659bdfcc0c556e1461e`).

The TTM-R2 boundary has been exercised separately and correctly quarantined the
approved 512-value runner against the frozen 48-bar V3-Core context. This is a
model/role incompatibility, not an inference crash; no padding, alternate
checkpoint, credentials, network calls, or orders were used. Phase 4 remains
pending fresh completed cases and robust candidate admission.

The previously quarantined Chronos-2-small identity was separately requalified
from the pinned local checkpoint. Its native 32–8192 context range and 30-step
output cover the frozen 48-bar / 12-bar V3-Core role, and an isolated 48-value
worker smoke passed without network access. The immutable compatibility record
is
`artifacts/phase0/model-runtime-qualification/chronos-v3core-compatibility/20260817T194802.642906Z/chronos-2-small.json`
(SHA-256
`c282864ff939c1ea7bf7dc6dcf219bc4fb48cbd99fdfa0637f86aa0472d8471a`). This is
runtime/contract qualification only; Chronos has no utility result or
promotion side effect.

A separate prospective Chronos worker is running from draft PR #189's isolated
worktree at commit `7503db435d36f3ba8638a11ab995a79c79b33326`. PID `80779` is
bound to
`artifacts/phase4/v3core-forward-predictions/20260817T193400Z-operator-interrupted-replacement-r1-chronos-2-small-r1/`;
the immutable manifest SHA-256 is
`2787c4e5cecc140e4acc5d33c25089bee8e068c120303b1134f3192de301fc88` and the
status snapshot SHA-256 recorded with this checkpoint is
`9be8759c0fddf40fa4a07c34358cfb2df4a388fd782658cf2610d0c479bb522d`.
It has produced zero predictions and recorded two missed cutoffs without
backfill. Network calls, credentials, and order writes remain zero. This
worker adds no execution authority and cannot itself admit or promote Chronos.

The active collector's read-only health snapshot at
`2026-08-17T23:16:49Z` showed 90 normalized bars, 0 completed cases, 9
`schema_or_normalization` failures, and 4 source-health transitions. Raw
inspection found the BTCUSDT interval ending `2026-08-17T22:15:00Z` with
different closed-row content in successive HTTP-200 responses. The collector
entered `DEGRADED` and later recovered to `HEALTHY`; this is preserved as a
pending data-integrity incident, not a crash or provider-failure conclusion:
`artifacts/phase4/v3core-forward-incidents/20260817T231643Z-btc-closed-bar-revision/incident-classification.json`
(SHA-256
`0d6c402160d8bc09a763375f7e267a8ecd2afb2f5c1c7aa2f0e9fc90069004a5`). The
active evidence root remains immutable and no admission decision has been
made.

## Current post-merge forward-run continuation — 2026-08-17

PR #187 merged at `c10203e79ddea88a6f1f5034af1625438b75b8bb`. The operator
confirmed that its recorded collector, resource-sidecar, and baseline-ledger
processes were intentionally stopped before the prior continuation. Their roots
are preserved unchanged and classified `OPERATOR_INTERRUPTED / INCOMPLETE`;
there is no evidence of a collector crash, provider failure, or implementation
failure. No replacement process or backfill was used.

The baseline prediction boundary now has a complete future-resume identity,
including source root and manifest hash, source snapshot, preregistration,
Phase-3 gate, repository/code hashes, model roster, and cadence. The shared
prediction record remains immutable before outcome linkage and now binds optional
candidate runtime provenance. Conflicting reuse of a prediction ID is rejected.

The TTM-R2 worker is a separate read-only boundary. It uses the qualified local
admission record and exact isolated runtime identity, but fails closed before
inference because that qualified worker requires 512 input values and the
frozen V3-Core prospective contract provides 48. This is recorded as a runtime
contract quarantine; no unqualified 48-bar adapter is claimed. The current
qualified admission was independently verified without network or credentials.

No model, worker, dashboard, Hermes task, or research process gained execution
authority. Phase 4 remains pending a fresh forward root, and no downstream
phase was opened.

## Current V3-Core forward PIT collector audit — 2026-08-12T21:18:35Z

Draft PR #187 is on `agent/phase4-forward-pit-collector` at branch head
`5856b35`; its active collector executable remains bound to
`eeb62f0af2ecba6cfb21f79d81793963241252e0`. PR #186 remains merged at main
`5514a4cac8771d23c9f7e113e922c9ba9df1ecee`.

The first forward attempt is preserved under
`artifacts/phase4/v3core-forward/20260812T203740Z-first-independent-pit/` and
classified as `IMPLEMENTATION_FAILURE`, not external provider failure: the
collector compared later local receipt metadata for an unchanged closed bar as
part of normalized identity. The raw public response and sanitized failure
ledgers remain untouched. The machine-readable classification is preserved in
`artifacts/phase4/v3core-forward-incidents/20260812T204700Z-repeated-closed-bar-normalization/incident-classification.json`.

The correction at `4949b5cc5b494ab6ff79c0ff40118219773d6277` makes normalized
bar append idempotent for repeated closed-bar observations while retaining all
raw receipts. The follow-up at
`eeb62f0af2ecba6cfb21f79d81793963241252e0` prevents a resumed root from mixing
collector/module/code identities. The regression suite covers a later receipt
with changed `collected_at`, and the focused forward/cadence tests pass 27.

The fresh v5 pre-outcome contract is
`artifacts/phase4/v3core-cadence-preregistration/20260812T204444Z-v3core-1h-5m-reobserve-fix-v5/`
(evidence SHA-256
`5a867b9c68f9a90593990a820f612bf3fd66670933d680a75ddd521762da1ffd`). It
binds the corrected module and collector hashes, but still records zero
credentials, zero order writes, and zero network calls at preregistration.

The active public-only r2 acquisition is
`artifacts/phase4/v3core-forward/20260812T204505Z-first-independent-pit-r2/`
under PID `160717`, with resource observation isolated in
`artifacts/phase4/v3core-forward-resource/20260812T204505Z-first-independent-pit-r2/`
under PID `161130`. The collector started at
`2026-08-12T20:45:11.984069Z` and is allowed to run until
`2026-08-17T20:45:11.984069Z`. It has no credential-loading import or
execution-capable method; the exact reviewed surface is the Binance public
market-data klines GET. No case is eligible until its 4-hour context and
following 1-hour outcome are both present.

No Phase-4 admission, model promotion, Phase-5 council, Phase-6 fill, or
Phase-7 soak has been created. Phase 2/3 are unchanged and passed; archive and
private-route work remain outside this continuation.

The offline completion boundary is
`scripts/materialize_phase4_v3core_forward_input.py`. It revalidates the
completed-case hash ledger and `V3CoreForecastCase` objects, requires the
terminal target and frozen source/gate identities, and emits the typed
`V3CoreEvaluationInput` only after all 128 cases (64 per symbol) exist. It has
no credential, network, model, or execution dependency and has not yet been
run against the incomplete r2 root.

The pre-outcome mandatory-baseline ledger is implemented by
`scripts/run_phase4_v3core_baseline_predictions.py` and
`src/advisorai/phase4/v3core_prediction_ledger.py`. It is a separate offline
process reading only normalized bars; it generates the five pre-registered
baseline paths only when all 48 context bars are locally present and the
current time is not beyond the cutoff. It records a missed cutoff rather than
backdating. Prediction entries are immutable hash-chain records, and later
outcome associations use a separate append-only link ledger. TTM-R2 and
Chronos are explicitly recorded as separate candidate states, not replaced by
a baseline. The focused baseline and ledger tests pass.

Implementation verification at code head `ef1ec1c`: full pytest `746 passed`
with 28 warnings; acceptance phases passed
`134/152/126/117/93/34/10/11/27/18/5`; Ruff, repository format, lock check,
compilation, dashboard build, diff hygiene, and tracked secret/weight checks
passed. These checks do not constitute Phase-4 admission.

## Historical V3-Core forward PIT collector audit — 2026-08-12T20:33:06Z

PR #186 is merged at main `5514a4cac8771d23c9f7e113e922c9ba9df1ecee`.
The follow-on implementation is commit
`80b3c5eb6c0055b81e224bbc833b8a9e240906eb` on
`agent/phase4-forward-pit-collector` and is not yet merged.

The v3 cadence contract corrects a real forward-PIT availability defect: a
five-minute interval ending at an hourly cutoff cannot be locally collected by
that cutoff. Cases therefore use context interval ends
`cutoff - 4h` through `cutoff - 5m`, and future outcome ends `cutoff + 5m`
through `cutoff + 1h`. This is a contract correction, not a relaxation of
look-ahead rules. The v2 contract and its preregistration remain immutable
historical evidence.

The collector is a dedicated read-only acquisition boundary. It uses the
existing safe HTTPS client with an exact reviewed host, does not import the
credential resolver, and has no order-capable method. Raw responses are
fsync'd before parsing; normalized bars, rejected case cutoffs, failure
classes, health transitions, and completed cases are independently append-only
and hash-linked where applicable. No acquisition has started yet, so no
external measurement or Phase-4 admission is claimed.

## Historical V3-Core PIT provenance audit — 2026-08-12T19:58:26Z

PR #186 was then draft from main
`13323cd2ad1fd8ae0f8690b10f5909c87ccc31ae`; the contract correction is
`6b2ed741650f9de0f51e8db921aefb507979d0d3`.

The cadence boundary at that historical checkpoint was a versioned v2 contract.
`V3CoreBarProvenance`
records interval end, provider availability, actual local collection, optional
provider event time, availability basis, evidence class, source health, and
content hashes. `V3CoreForecastCase` validates one evidence class throughout,
keeps context/future bars source- and snapshot-local, requires provider
availability under a reviewed historical contract for development evidence,
and requires actual local collection no later than cutoff for forward admission
evidence. The builder retains rejected cutoffs instead of filling gaps or
silently switching sources.

`V3CoreMarketDataSurface` pins the Phase-4 acquisition boundary to Binance's
public market-data-only REST/WSS hosts and rejects production trading, Spot
Testnet execution, arbitrary, non-secure, credential-bearing, or write-capable
configuration. This is a Phase-4 surface correction; prior Phase-3 evidence is
not rewritten.

The corrected preregistration is
`artifacts/phase4/v3core-cadence-preregistration/20260812T195826Z-v3core-1h-5m-provenance-v2/`
with evidence SHA-256
`ca09ee9d62eccbd017287eebc8864e34d339d8e2a3eb2168826853a7fdd0fed8` and
manifest SHA-256
`6962c7a882a11969262484e03b3c6cdb7627e27e3d23d1ffab7ffde23f8883fd`.
It is a pre-outcome contract only and remains pending fresh forward PIT data.

Contract-correction verification passes full pytest (`731 passed`, 28 warnings),
all eleven acceptance suites (`134/152/126/117/78/34/10/11/27/18/5`), Ruff,
format, lock, compileall, dashboard build, diff hygiene, and tracked
secret/model-weight checks. No data acquisition, credential loading, model
promotion, or order write occurred.

## Current V3-Core cadence Phase-4 audit — 2026-08-12T18:58:28Z

PR #185 is merged at main `13323cd2ad1fd8ae0f8690b10f5909c87ccc31ae`. The
focused continuation commit is
`53cdc9eba5f57ce54e87348f04320b138d82fa8d` on
`agent/phase4-v3core-cadence`.

The new `v3core_cadence.py` boundary is typed and deterministic. It encodes the
V3-Core operating contract rather than adapting the contract to the consumed
daily experiment: 5-minute bars, a 4-hour (48-bar) context, a 1-hour (12-bar)
outcome, and BTCUSDT/ETHUSDT. It binds provider/source identity, availability
time, snapshot identity, point-in-time cutoff, and raw/provenance hashes. Case
construction is fail-closed for missing, duplicate, non-contiguous, future,
cross-source, and invalid bars. Regime labels use context only. The builder
does not acquire data, load credentials, fill gaps, switch sources, run models,
or call execution.

The offline preregistration runner and input builder are:

- `scripts/preregister_phase4_v3core_cadence.py`;
- `scripts/build_phase4_v3core_cadence_input.py`.

The preregistration root is
`artifacts/phase4/v3core-cadence-preregistration/20260812T185716Z-v3core-1h-5m-prereg-v1/`
with evidence SHA-256
`1bbe362240a1fb136a074117f734e270afcef3cf0be6f6af34e81dc3c2631e00` and
manifest SHA-256
`ffce302f99e27317f5a9c38520d5170fb0a39b8e7332657e6c9aad87324a085c`.
It is an immutable contract, not a utility result: the status is
`PENDING_FRESH_PIT_DATA` because no existing immutable root supplies the
required case set. The daily Phase-4 input is explicitly consumed and r7's
source-qualification telemetry is explicitly ineligible.

The first independent Chronos identity audit is preserved at
`artifacts/phase0/model-runtime-qualification/chronos-v3core-identity-audit/20260812T185828.820414Z/chronos-2-small.json`
(SHA-256
`62b971745a7536cf45fd30944a14919b570200a0382ed1dd54512a2570f9785b`). It
remains quarantined due to the exact worker/runner hash mismatch against the
preserved measured identity; no runtime mismatch was bypassed. There is no
Phase-4 utility measurement, promotion, or formal PhaseGateRecord for this
cadence yet. Phase 2/3 remain passed; Phase 4 remains pending on
`robust_candidate_admission`; Phase 5–7 remain closed.

Focused verification passed full pytest (`719 passed`, 28 warnings), all eleven
acceptance suites (`134/152/126/117/66/34/10/11/27/18/5`), Ruff, format, lock,
compileall, dashboard build, diff hygiene, and tracked secret/model-weight
checks. These are implementation checks only; they do not create a Phase-4
admission record.

## Current Phase-4 signal-policy research audit — 2026-08-12T19:00:00Z

The focused continuation from merged main `4b9ca30353132804eff559abd9220821493b9366`
adds `src/advisorai/phase4/signal_policy.py` and the offline
`scripts/evaluate_phase4_signal_policies.py` runner. The boundary contains no
network, credential, model-weight, or execution operation. It emits typed target
directions only; RiskKernel and OMS remain unchanged external authorities.

The runner has 10 focused tests and produces an append-only research root at
`artifacts/phase4/signal-policy-research/20260812T192000Z-ttm-r2-development-policy-v1/`.
The evidence SHA-256 is
`8b7ce10d0beba1562abb9f46fda3906b9094d427ffb289329297157c804e3c48`.
Working-tree code hashes bound by that evidence are
`signal_policy.py=35884c97b5d37e6b2e3d6adeab455bcc8cc65edb599fad65d1553a5b94a88af5`
and
`evaluate_phase4_signal_policies.py=1f69538c8520f20be769d5833caf5ee4b070ad6d34a8f01c5794d1edf795700d`.

The policy contract permits only six reviewed families and 13 candidates. It
keeps forecast policy state independent per instrument, applies thresholds and
cooldowns causally, accounts for direction changes separately from turnover
units, and exposes modeled fee/spread/slippage components, regime slices,
BTC/ETH slices, primary-only path comparisons, prediction/error distributions,
and break-even all-in cost. It does not consume realized outcomes while
generating a signal.

The research partition is chronological: 32 tuning and 16 validation
observations per symbol. The final 16 per symbol are frozen as consumed and are
not scored as candidate policies or used to choose a policy; the full-input
decomposition is explicitly diagnostic-only. All 13 policy attempts failed to produce
positive conservative-cost incremental validation utility; the best flat policy
was exactly `0` bps and was not frozen. This is evidence against the current
restricted policy search, not a Phase-4 pass. The formal gate remains pending
and requires independent future/PIT evidence before any candidate can be
re-reviewed.

## Current corrected Phase-4 reviewer audit — 2026-08-12T16:25:00Z

Historical checkpoint superseded by the current signal-policy audit above; PR
#184 was subsequently merged.

The Phase-4 formal reviewer correction is committed at
`52342b1093dc95dd0358257cdd8999cb2935479b` on draft PR #184; main remains
`056a39d5641c81330dd89668e117108e1fa1bf5c`. It adds v2 review semantics and
20 focused regression tests without touching Phase 2/3, execution, credentials,
data acquisition, or model promotion.

The fresh review root is
`artifacts/phase4/formal-review/20260812T162500Z-btc-eth-64x2-reviewer-v2-final/`
with review/checklist/gate-record SHA-256 values
`c5117a011dc118687bfa2b1aea55e5b0cc76c42929e6e360ce86fb063880c867`,
`50ef16346c73fc0d64247114dad73ecde28143e0a48468f55b7524fb4463b58b`, and
`18a05e1769a5356b860800ea2c7a84fb241385ba7884bf7e9c7d3865ebc28a18`.
The decision is `PENDING`; the only gating blocker is
`robust_candidate_admission`.

Implementation audit results:

- rolling calibration now derives widths from non-negative absolute residuals
  from strictly earlier same-cutoff groups for the same instrument/model;
  native intervals are validated and preserved, while partial/invalid native
  intervals fail closed;
- calibration exposes nominal/observed/signed/absolute coverage error and
  holdout counts; TTM-R2 is `0.73863636` full and `0.75` holdout at `0.80`
  nominal, so the corrected calibration requirement passes;
- daily latency semantics are explicit: 10s/1h are zero-bar operational
  proxies, while +1/+2 bars are severe signal-decay stress and cannot be used
  as sub-bar observations; the current next-bar negative result is preserved;
- the generation audit verifies cutoff-bounded cases, causal per-cutoff
  baselines/LightGBM, frozen TTM checkpoints, and no holdout tuning/retraining.
  Because the immutable generation manifest lacks source hashes, the reviewer
  records the exact methodology-source comparison and a bounded provenance
  limitation rather than claiming a stronger source identity;
- all modeled cost scenarios remain present and labeled non-historical. TTM-R2
  has 7/14/23/41 bps optimistic/base/conservative/severe all-in assumptions,
  a 49.243883 bps model net-zero break-even, and -181.04 bps conservative
  incremental utility. It is not promoted.

The prior robustness-v2 root is preserved unchanged and superseded only for
the corrected reviewer interpretation. RiskKernel remains the deterministic
veto, OMS remains authoritative, and no model/LLM/Hermes/dashboard/research
component gained order authority. Full pytest passes `703` tests with 28
warnings, all eleven acceptance suites pass
`134/152/126/117/50/34/10/11/27/18/5`, and Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked-secret/model-weight checks pass.

## Current Phase-4 formal review checkpoint — 2026-08-12T04:50:00Z

This review started from main `056a39d5641c81330dd89668e117108e1fa1bf5c` and
added the offline `scripts/review_phase4_utility.py` boundary plus four focused
regression tests in `tests/models/test_phase4_review.py`. The preparation
boundary now preserves measured runtime latency and native forecast intervals
when a worker supplies them; it never invents intervals.

Final verification passed full pytest (`692 passed`, 28 warnings), all eleven
acceptance suites (`134/152/126/117/39/34/10/11/27/18/5`), Ruff, repository
format, lock check, compilation, dashboard build, and `git diff --check`.

The new review consumes only the immutable 128-observation BTC/ETH input and
measurement report. It recomputes chronological training/holdout, BTC/ETH and
regime slices, past-only rolling calibration, causal delay scenarios, cost
stress, and break-even cost. It writes a classified checklist and a typed
pending `PhaseGateRecord` without network, credentials, model weights, order
writes, promotion, or authority changes.

Review root:
`artifacts/phase4/formal-review/20260812T045000Z-btc-eth-64x2-robustness-v2/`.
Review, checklist, and gate-record SHA-256 values are respectively
`64b9080176109ab12ce58cbd68b5e2160115537e5e4f75cba175c0051515bee3`,
`16e485072f23ffbdccea463b82fa0765d7691d380db57679e38d4cb173b65154`, and
`a7a99bc18d52e8bcbd49c9ecb625564c4b363b497b4a4708ba89fc40f989d36c`.
The reviewer bound code hashes `phase4_contract=1107b5356955fa402e575b18da1b127855a941dba133e8931885070e6b7aac10`
and `reviewer=3ce1d9cd77157db971fd1115f3f3ba488d6379a5f79a1ead9866ab50aee14329`.

The implementation is `IMPLEMENTED / TESTED / REAL_MEASURED`, while the gate is
`PENDING_REVIEW` with exact blockers `past_only_calibration` and
`robust_candidate_admission`. TTM-R2 remains `CHALLENGER`; TTM-R3 is
`RESEARCH_ONLY`. Phase 5–7 remain closed and no model authority was added.

## Current Phase-2/3 admission and Phase-4 measurement checkpoint — 2026-08-12T01:42:00Z

The implementation anchor is `cd2b09066096977ac38ddb6dd756339fea9a4330`,
merged in main at `b6a72834398465271f9be08e372d292286671fb8` (PR #182).
The continuation verification passed full pytest (`687 passed`, 28 warnings),
all eleven acceptance suites (`134/152/126/117/34/34/10/11/27/18/5`), Ruff,
format, lock, compilation, dashboard build, diff hygiene, and tracked-secret/
model-weight checks.
The new offline Phase-2 formalizer (`scripts/evaluate_phase2_gate.py`) validates
the existing Binance Spot Testnet read-only and no-fill/cancel evidence without
secrets, network, or writes. Its passed checklist and `PhaseGateRecord` are
preserved under
`artifacts/phase2/formal-admission/20260812T013500Z-post-phase2-commit/`;
the record SHA-256 is `efb9d678e72f9785c3d9162660ead6cd434af6108249de73a42a08dd9a64bdae`.
The record explicitly leaves filled-order attribution to Phase 6.

The existing r7 Phase-3 evidence was re-evaluated offline with that passed
predecessor. The passed Phase-3 checklist and record are preserved under
`artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/`;
the record SHA-256 is `4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232`.
No r8/r9 durability root was launched. Coinbase Sandbox ETH-USD product absence
and GDELT HTTP 429 remain external, non-gating outcomes with no substitution.

The Phase-4 predecessor evaluator records
`OPEN_FOR_MEASUREMENT` at
`artifacts/phase4/formal-dependency/20260812T014100Z-phase3-and-role-contract-v2/`.
It confirms that Phase 4 measurement requires the passed Phase-3 record,
qualified selected roles, and mandatory baselines, while global Phase-0
private-route/archive blockers remain separate. The canonical real utility input
and measurement are preserved under
`artifacts/phase4/real-utility-input/20260812T023000Z-btc-eth-daily-snapshot-ttm-r2-r3-v3/`
and `artifacts/phase4/utility-evaluation/20260812T023015Z-btc-eth-daily-ttm-r2-r3-baselines-v3/`:
64 point-in-time BTC/ETH observations and 448 predictions. TTM-R2 beat the
strongest measured baseline in the recorded conservative cost scenario, while
TTM-R3 did not add incremental utility in this window. Both pinned runtimes
passed their resource check and made no network attempt. The report remains
`measured_pending_review`; interval calibration, latency sensitivity, and
formal review are still required, so no admission or promotion is claimed.

## Current formal-admission checkpoint — 2026-08-12T00:45:13Z

From clean main `d9f2bb9d6738b3850ebd7798821b31e662b1d263`, the offline
`scripts/evaluate_phase3_gate.py` implementation reconciles the immutable r7
review, the broader source qualification, the formal source roles, and the
dependency-ordered `PhaseGateRecord` contract. It has four focused tests in
`tests/phase3/test_phase3_formal_gate.py` and performs no network I/O, secret
loading, collector startup, or execution operation.

The checklist at
`artifacts/phase3/formal-admission/20260812T004513Z-contract-review/phase3-admission-checklist.json`
has SHA-256
`0cd305d79d70a7427100437b977ce028cb643fc885d680a113312b11d3a0a79c` and binds
all referenced evidence by digest. R7 structural/policy review, primary
Binance BTC/ETH coverage, raw/replay/sequence integrity, timestamp/freshness,
source lineage, disagreement, fail-closed selection, Deribit context, official
RSS, and resource evidence are `SATISFIED`. The fresh source pass preserves
Coinbase Sandbox ETH-USD HTTP 404 and GDELT HTTP 429 as external failures with
zero observations and no substitution; GDELT availability is non-gating while
its dependent decision path remains abstained.

The pending `PhaseGateRecord` at
`artifacts/phase3/formal-admission/20260812T004513Z-contract-review/phase3-gate-record.json`
has SHA-256
`0ee4b783c1afa943fb8a9e94ca29ea2c358d6b7e68ba097fd224fd96614d4bbe` and sole
reason `phase_2_formal_predecessor`. No Phase-3 pass was inferred from review
recommendations, no r8/r9 run was launched, and Phase 4–7 remain unadmitted.
Archive/rclone, private-route, Hermes, Phase 9/Alpha, and Phase 10 states were
not changed.

## Current terminal-review checkpoint — 2026-08-11T23:17:07Z

Current executable main anchor is `d5bfde76ed3cacaba365f3d7981db5a756eaf314`
after PR #174; the review began from clean main
`6913f2b4feaf71f4fada05a5e9611d7601dd5e8d`. Phase-0 selected-model stability
is terminal and independently qualified for TTM-R2, Finance DeBERTa-v3, and
FinBERT-MiniLM; the global Phase-0 gate remains separate and pending. PID
`70598` was not touched.
The review-boundary change passes full pytest (`663` tests, 28 warnings), all
eleven acceptance suites (`134/152/126/111/27/34/10/11/27/18/5`), Ruff,
format, lock, compilation, dashboard build, diff hygiene, and tracked-secret/
weight checks.

R7 terminal evidence is preserved at
`artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix`.
Independent chain validation found 129 contiguous cycles/774 samples, complete
public read-only separation, explicit provider identities, zero sequence gaps,
duplicates, out-of-order events, and replay failures. The root recorded three
Binance stale samples and correctly failed closed for those selected asset
paths; Coinbase quarantine and Deribit disconnect/recovery failures are
external source/runtime outcomes, not implementation defects. The validator
report is `PASS_FOR_REVIEW` with no issues (SHA-256
`dbd9bdc6c96af82ef33ccfbb22557786de6c9d3e72cbfdc97a956cb91c7f32e4`).

The admission-review implementation previously conflated `stale_interval_count`
with replay continuity. It now has a separate deterministic
`primary_stale_intervals_fail_closed` check requiring fail-closed selection or
an identity-bound, quality-recomputed failover; it does not relax sequence,
ordering, replay, identity, disagreement, or resource checks. New positive and
negative coverage is included in `tests/phase3/test_phase3_admission.py`; the
full focused Phase-3 suite passes 87 tests. Re-evaluation of the immutable r7
root is `QUALIFIED_FOR_REVIEW` with all checks passing (SHA-256
`d4ad647e1f668b88c78604bd9cd75b94ae925bb9b943117e6adb07cfc8ae7aaa`). The
review used evaluator source SHA-256
`0ea2a57e1a4d7135d8a65bbcf87f4ea5eb288d9b131ec6a56178431d5a4d235e`.

This result qualifies the measured public BTC/ETH source-health component for
formal review only. The complete Phase-3 V3-Core source spine still requires
its remaining authoritative source-scope evidence and a formal gate record;
Phase 3 is not yet admitted, and no Phase-4 utility, Phase-5 council, Phase-6
fill attribution, or Phase-7 soak has been claimed.

## Current terminal-review checkpoint — 2026-08-11T18:46:29Z

The verified clean executable anchor is
`ced5d9301a816d89428616d1eb6ce0de48318cf7` after PR #171. PR #169 fixes the
Phase-3 source-selection validator, which now accepts only identity-preserving
successful selection or a fully identity-null fail-closed selection. PR #171
binds the terminal per-role model review to both machine-readable rosters while
keeping overall Phase-0 admission false. Full pytest passes `660` tests and
all eleven acceptance suites pass `133/152/126/108/27/34/10/11/27/18/5`;
this is `IMPLEMENTED / TESTED`, not admission.

The immutable Phase-0 r3 terminal review passed its genuine 24-hour boundary
with `269` cycles and no validation issues. The review report SHA-256 is
`1a6ab92c4f28d456776eac0c89ab099b0c1ef579c729fa8e458e4d5192b06949`; all
three requested model roles are independently `QUALIFIED`. This does not
qualify the entire Phase 0, whose external route/archive prerequisites remain
pending.

The immutable r6 Phase-3 structural review is now correctly
`PASS_FOR_REVIEW` after the validator fix, but the separate admission review
remains closed (`phase3_admission=false`) because the real root recorded stale
primary Binance intervals. This is external operational evidence insufficiency,
not a reason to rewrite r6. A fresh independent r7 root is running under PID
`32321`, with sidecar PID `32574`; both are outside the model root and use
credential-free public connectors. No Phase-3, Phase-4, Phase-5, Phase-6, or
Phase-7 admission is claimed.

## Current recovery checkpoint — 2026-08-11T13:44:48Z

The verified clean executable anchor is
`48d913d1ac1c78549b9d1c6115550308cacced19`, after PR #167, a docs-only live
Phase-3 checkpoint. The direct
Phase-3 runner self-bootstraps repository imports for detached execution; the
regression test covers a no-`PYTHONPATH` `--help` invocation. Full pytest
passed `657` tests with 28 warnings and all eleven acceptance suites passed
`133/152/126/106/27/34/10/11/27/18/5`. Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked-secret/weight checks also passed.

The r5 root completed and passed structural validation, including the
sanitized dashboard health projection, but remains unadmitted because its
terminal primary BTC/ETH health and replay/continuity checks failed. Its
summary, validation, and admission report hashes are recorded in the gate
matrix. Two failed corrected-r6/supervisor attempts are preserved as incident
evidence; the current fresh r6 root is active under PID `2943` with sidecar
PID `4807`, at `192` samples with no terminal sample. All observed Binance
BTC/ETH rows are replay-equivalent and sequence-continuous; Coinbase remains
explicitly quarantined, while Deribit disconnect/recovery events and severe
cross-source disagreement remain preserved as measured fail-closed states.
Post-run review watcher PID `28569` is separate and writes only fresh review
roots. No Phase-0/3 gate was promoted by elapsed time alone, and PID `70598`
remains untouched at cycle `218`.

## Current repository anchor — 2026-08-11

The verified clean executable anchor is
`fbb010809598d6096b42309f16b5e13dd3e1acb8`, aligned with `origin/main` after
PR #160. The Phase-3 offline validator now verifies that the read-only
`latest-health.json` projection is a complete, sanitized, identity-preserving
projection of the latest append-only source samples. It records projection
state and SHA-256 in the validation report while keeping formal admission
closed. This is `IMPLEMENTED / TESTED`; full pytest passed `655` tests and all
eleven acceptance suites passed `133/152/126/104/27/34/10/11/27/18/5`.

At `2026-08-11T11:49:14Z`, model stability PID `70598` remained active at
cycle `196`, and Phase-3 r5 PID `46864` remained active at `612` samples with
sidecar PID `47392`. The exact post-run and terminal validators remain
read-only watchers; no terminal sample, corrected-r6 root, Phase-0 admission,
or Phase-3 admission is claimed. Archive/rclone remains externally deferred.

The verified clean executable anchor is
`513a4fae65ddbf8f15b00eac52b8ebc390c6b5b1`, aligned with `origin/main` after
PR #158. The attachment’s PID `13339` reference is not the live process: the
protected model stability PID is `70598`, with Phase-3 r5 PID `46864` and
resource sidecar PID `47392`. This audit preserves the live roots and does not
rewrite historical evidence.

## Current continuation update — PR #158 validator hardening

PR #158 adds offline validation for the provider/local timestamp projection in
future Phase-3 roots. Projected rows must contain complete timezone-aware
timestamps and a consistent provider timestamp count; older roots are reported
as `legacy_unprojected` without mutation. This is `IMPLEMENTED / TESTED`; full
pytest passed 653 tests, and Phase-3 admission remains closed. At
`2026-08-11T11:33:35Z`, r5 remained pre-terminal at 564 samples and PID
`70598` remained pre-terminal at 193 cycles.

## Current continuation update — PR #156 timestamp projection

PR #156 adds explicit provider/local timestamp projection to each durable
Phase-3 source/symbol sample. Future roots use code identity
`f9f8c20aa33db840f1a930cc7a04f56b1c06b4a3a382130503e42462fa7e27c1` and retain
the latest provider event timestamp, local receipt timestamp, and provider
timestamp count alongside existing age and replay metrics. This is
`IMPLEMENTED / TESTED`; it does not alter source identity, fail-closed policy,
execution authority, or existing evidence roots. Phase-3 admission remains
closed. At `2026-08-11T11:22:33Z`, r5 remained pre-terminal at 528 samples and
PID `70598` remained pre-terminal at 191 cycles.

## Current continuation update — PR #154 provider freshness evidence

PR #154 adds measured provider-event timestamp propagation to the durable
Phase-3 disagreement boundary. Future roots use runner code identity
`55562365731ff8e079d3b28cdb283464134c164b54c48a35351bbea2ef3a4d47`; source
freshness-age differences are recorded only when both public streams expose
usable event timestamps, and missing values remain explicitly unmeasured. The
change is `IMPLEMENTED / TESTED`, with 33 focused Phase-3 tests and 650 full
tests passing. It does not alter source identity, fail-closed policy, execution
authority, or any existing evidence root; Phase-3 admission remains closed.

The `2026-08-11T11:09:45Z` read-only snapshot found the protected r5 root at
486 samples with no terminal sample and the selected-model stability PID at 188
cycles. The corrected r6 root has not started, and archive/rclone remains
externally deferred.

## Current continuation update — Phase-4 offline measurement boundary

PR #152 adds the strict offline
[`scripts/run_phase4_paper_utility.py`](../../scripts/run_phase4_paper_utility.py)
runner and runbook. It validates a current passed Phase-3 `PhaseGateRecord`
and typed Phase-4 observations/predictions, records file/canonical hashes, and
writes immutable measurement-only evidence. It has no credential, weight,
network, gate-recording, promotion, or order capability. The output remains
`measured_pending_review` and no Phase-4 admission is claimed because Phase 3
is still pending.

Read-only inspection at `2026-08-11T10:48:08Z` found model PID `70598` at cycle
184 and r5 at 414 samples, both still before their genuine terminal boundaries;
all protected roots and watchers remain untouched. PR #152 passed full pytest
`649 passed` with 28 warnings, acceptance suites
`133/152/126/98/27/34/10/11/27/18/5`, repository static/format/compile/lock
checks, dashboard build, and secret/weight hygiene.

## Current continuation observation — protected timed roots

The model stability root remains pre-terminal: PID `70598` reported 175
passing cycles at `2026-08-11T09:54:45.558492Z`, with target
`2026-08-11T18:07:25.593600Z`. The Phase-0 validator therefore remains
`PENDING_STABILITY` and no roster promotion is made.

The Phase-3 r5 root remains in flight with 306 samples and 48 health
transitions at `2026-08-11T10:13:34Z`, targeting
`2026-08-11T12:35:59.156509Z`. It is preserved under its own earlier runner
identity and is not credited with later fixes; no Phase-3 admission is claimed.

## Current continuation update — provider-clock confidence hardening

PR #148 adds a deterministic provider-clock confidence boundary to the
Phase-3 cross-source comparison. The public quote projection now marks a quote
clock-confident only when its source server-time probe passed and its measured
offset remains within the reviewed health bound. Missing or out-of-policy
clock evidence therefore produces severe disagreement, abstention, and
fail-closed behavior. The Phase-3 acceptance suite passed `98` tests and full
pytest passed `646` tests with 28 warnings. This remains local implementation
and test evidence; the active r5 root predates the patch and no Phase-3
admission was opened.

## Current continuation update — offline model-stability validator

PR #145 adds the read-only
`scripts/validate_model_stability.py` terminal review boundary. It verifies
the append-only model cycle chain, real 24-hour terminal sample, recomputed
summary, and exact checkpoint/runtime admission identities, with per-role
qualification output but no admission authority. Phase-0 remains
`PENDING_STABILITY` until PID `70598` exits with its genuine terminal sample;
the validator has passed the Phase-0 acceptance suite (`133` tests).

## Current continuation update — Phase-3 non-negative age metrics

PR #143 merged the Phase-3 reporting fix at
`3404a0b649eeff960de0dced95efe5f7c1593bea`. The future durable-runner code
identity is
`30d9fb147e6ce4a7204aaa0f2867d8c8e8e5200ec2d7a34ff2c3e037100d36eb`; signed
future-event diagnostics still force degraded/fail-closed clock handling, but
reported event-age durations are clamped at zero. The active r5 root uses the
previous code identity and remains untouched; it cannot be credited with the
fix. Full pytest `640` and Phase-3 `72` passed. No admission state changed.

## Current continuation update — Phase-3 measurement boundary

PR #140 merged into main at `335114ba73156cb75e44465a4d21ff27f86299e1` with
runner code identity
`17bed912495868062c6a7a79e515d5a29a8b65b40cf138b8845e837ba3ec280d`. The
collector persists a pre-teardown measurement timestamp and the later cleanup
timestamp, and source freshness is evaluated at the measurement boundary. This
is a local implementation/test hardening result; the currently active r5 root
uses the earlier code identity, remains untouched, and cannot be retroactively
credited with this fix. No authority boundary or gate state changed.

## Current continuation update — r4 terminal review and r5 active root

The pre-reconnect r4 root
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
finished its measured four-hour window at
`2026-08-11T08:24:45.638125Z` with 810 samples. Its summary is hashed by
`53b1b77192dc77360b63b12208a445cc889c6bd0ff570fe4bd08ef37d8753fe2` and
records stale Binance primary streams, quarantined Coinbase, and degraded
Deribit. The old root has no explicit terminal sample because it predates that
runner fix. Its resource sidecar summary
`75cc73ca44400d59df9f28037d5037ff8ca3c456c459f7d5e34ffe06e3168d47` is
quarantined after the offline validator found an invalid hash on its final
sparse process-exit record and a sanitized FileNotFoundError resource error.

PR #138 fixes that sealing defect at executable main
`1335bfabe93bdd990f9512430ae843a9795a7ebf` and adds regression coverage. The
new current-code r5 root is active under PID `46864` with code identity
`f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`; its
independent sidecar is PID `47392`. The new root, not the quarantined r4 root,
is the next admissible Phase-3 evidence candidate.

## Current continuation update — bounded Binance public-data reconnect

PR #136 merged into main at `350d6b55ac36251750e0459dc4e24b3507ca865c`.
The durable public-data runner now binds future roots to code identity
`f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`, preserves
each bounded Binance WSS attempt, and retries once after a failed first-message
or snapshot window. Snapshot/sequence recovery deliberately uses the latest
successful attempt only, so reconnect evidence cannot fabricate continuity.
The change passed the full locked suite and all acceptance suites; it remains
an implementation/test result until a fresh real terminal root is reviewed.

## Current continuation update — Binance provider read-only recovery

PR #128 merged at `aa4cdcb86a9bd0c1ca749f0ded5524b8cb842c9c`; PR #133 is the
current executable anchor `083798403323e18f2cc6577103d7b81c36454279`, with
subsequent documentation-only follow-ups. The new
`scripts/qualify_binance_spot_testnet_recovery.py` qualification uses the
existing `ConfigBundleStore`, scoped `PAPER_VENUE` resolver, and
`BinanceSpotTestnetTransport` to measure configuration rollback and a fresh
process restart. Parent and child processes perform only the existing
authenticated read contract; no order, cancel, transfer, withdrawal, OMS, or
production operation is available through this check.

Real evidence passed at
`artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json`
with SHA-256
`acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`.
It contains 18 read-only calls, provider-truth `BTCUSDT`/`ETHUSDT`, matching
initial/restored bundle hash
`0a44fe86c6cd7a65c316886f93848147aa3b75fd3a1eb3c31ae2579eaf7dc691`, and
`writes_attempted=false`. This is
`EXTERNALLY MEASURED / PROVIDER_READ_ONLY_RESTART_AND_CONFIG_ROLLBACK_MEASURED`
only; full provider deployment rollback, open-order recovery, Bronze rebuild,
and archive restore are not claimed.

## Current continuation update — Phase-3 admission evaluator entrypoint

PR #133 fixed direct repository-root execution of
`scripts/evaluate_phase3_admission.py` by adding a package-safe path bootstrap
and a zero-network `--help` subprocess regression. The executable main anchor
is `083798403323e18f2cc6577103d7b81c36454279`; admission logic and evidence
roots were unchanged. Locked full pytest passed `635` tests and the complete
Phase-3 acceptance suite passed `91` tests.
The fixed direct entrypoint was exercised against immutable r3 and produced
offline report
`artifacts/phase3/public-market-data-admission/20260811T072500Z-two-hour-r3-entrypoint-recheck/phase3-admission-evaluation.json`
with SHA-256
`8c308ec39497ef962ea9dcb8fbbea611797bb2f0a488d08585923ae2fe7d131f`;
the recommendation remains pending and `phase3_admission=false`.

## Current continuation update — Phase-3 source identity integrity

Candidate commit `4abf2ce` strengthens the append-only Phase-3 health ledger by
checking predecessor state and rejecting provider/endpoint changes for an
existing source/symbol stream. This preserves explicit source identity during
failover and prevents a dashboard or downstream consumer from inheriting a
different provider's continuity. The focused Phase-3 suite passes `64` tests
with one dependency-only skip; all nine existing Phase-3 health ledgers reopen
under the new checks. The active r4 process and Phase-0 stability process remain
untouched, and no admission state changed.

## Current continuation update — post-PR #125 anchor

PR #125 is merged into clean main
`3d3242cd07d55b2099b247b1d593a1701685f829`. The source-health ledger identity
binding and predecessor-state checks are therefore part of the current
executable base. The active model and Phase-3 evidence processes remain
independent of this code change; at the latest inspection they were still
pre-terminal and no external gate was promoted.

## Current continuation update — Phase-3 resumable configuration bounds

Candidate commit `49b3283` makes the durable runner's persisted `max_cycles`
bound immutable across restart and tests that a same-configuration resume
does not duplicate append-only records. Focused Phase-3 coverage is `66`
passing tests. The active evidence roots were not opened or rewritten, and no
Phase-3 admission state changed.

## Current continuation update — post-PR #120 Phase-3 review

Current clean `main` is `22370e95a85b0cffbf104e867ac59dee5ac4c2f6`. PR #120
merged the complete Phase-3 acceptance coverage and bounded failure-label validator;
PRs #121–#122 only refreshed measured evidence and continuation anchors. The
full repository verification remains `626 passed` with 28 warnings and the
complete acceptance results are `129/152/126/87/24/34/10/11/27/18/5`.

The immutable two-hour r3 qualification root was revalidated offline. The fresh
validator report
`artifacts/phase3/public-market-data-validation/20260811T060000Z-two-hour-r3-v3/phase3-qualification-validation.json`
has SHA-256
`40b08077112092df4531175063d8c514ab58a65d31c317bb562e0d14ad8f1753` and is
`PASS_FOR_REVIEW` with no validator issues. Its paired admission review
`artifacts/phase3/public-market-data-admission/20260811T060000Z-two-hour-r3-v3/phase3-admission-evaluation.json`
has SHA-256
`26ae8d2e7a209b71ce36fb1707a3183dff0840f14220d7df617874a1e8a80a26` and is
`PENDING_EXTERNAL_EVIDENCE`; the old root lacks the terminal marker, did not
establish a healthy BTC/ETH primary, and recorded primary snapshot/sequence/
replay failure. No gate state was promoted.

The active independent Phase-3 r4 window remains in flight at
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
under PID `87421`, with resource sidecar PID `88019`. The latest read-only
observation recorded 384 samples, 384 disconnects, 128 snapshot-recovery
attempts, zero valid events, and fail-closed states for every BTC/ETH pair.
Its target is `2026-08-11T08:24:40.271709Z`; no terminal or admission claim is
made, and the pre-projection root remains immutable.

## Current Phase-3 failure-class projection

The current clean-main base for this work package is
`51e3e6a73e620920aed98034c60adc49f0d16844` after PR #119. The Phase-3 durable
runner now projects sanitized exception classes and failure-layer identifiers
from REST/WebSocket collectors into source/symbol samples, summary aggregates,
and the read-only dashboard/API health view. It never copies response bodies,
headers, messages, or credentials into these fields. Focused Phase-3 and
dashboard coverage passed `31` tests; the full locked verification passed `626`
tests and acceptance suites
`129/152/126/87/24/34/10/11/27/18/5`. This is an implementation/evidence
completeness improvement, not a Phase-3 admission.

The offline validator additionally checks optional sanitized failure-label
fields and reports their coverage without mutating old roots. The acceptance
manifest now covers every test under `tests/phase3`; this closes a local
verification gap in which the WSS diagnostic, admission, validator, and
resource-monitor tests were not included in the Phase-3 suite.

## Current Phase-4 utility preparation boundary

PR #114 adds
[`src/advisorai/phase4/paper_utility.py`](../../src/advisorai/phase4/paper_utility.py)
and focused coverage in
[`tests/models/test_paper_utility.py`](../../tests/models/test_paper_utility.py).
The evaluator requires source observations to carry `phase3_admitted=true` and
requires an immutable Phase-3 gate SHA-256 before it will measure anything. It
keeps the current model candidates and mandatory baseline set explicit, applies
the versioned conservative Binance Spot Testnet fee schedule together with
measured spread/slippage, and reports calibration, regime, turnover, cost, net
utility, and incremental utility without changing the roster or creating a gate
record. The preparation command writes only a closed input manifest.

The manifest at
`artifacts/phase4/utility-evaluation-preparation/20260811T051344.190783212Z-offline-contract-v1/phase4-utility-preparation.json`
has SHA-256
`620f2ce32bb19aed8ce64ed0c12cddd4e0684db9f5b78add11e5b8ce6445456b` and state
`ready_for_admitted_input`. This is not real Phase-4 utility evidence and does
not promote any model.

## Current Phase-3 evidence anchor

Clean main base is `51e3e6a73e620920aed98034c60adc49f0d16844` after PR #119. The
completed durable root
`artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`
reached its two-hour target at `2026-08-11T03:14:39.940009Z` with 63 cycles
and 378 samples. Its summary SHA-256 is
`eb33cb5939feb5126bef3eff210c3710a95d6fbf3d85b3433bc2ad024a191ed7` and its
status SHA-256 is
`df8a7aa57aa95205636ce0e800882f6ccca0647b386a29488c83b7bba97ed5da`.
The offline validator report
`artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json`
has SHA-256
`efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca` and
returned `PASS_FOR_REVIEW` with `phase3_admission=false` and no validator
issues. All 126 source selections failed closed; three replay failures, 22
severe disagreements, final Binance stale states, Coinbase quarantine, and
Deribit degradation remain part of the evidence. The corrected v2 resource
sidecar completed with no resource errors; its summary SHA-256 is
`42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd`.
PID `13339` and the sidecar are no longer running. This records measured
operational behavior without promoting Phase 3.

The untouched selected-model stability process PID `70598` was observed
read-only at sequence 107 at `2026-08-11T03:47:10.345140Z`, record SHA-256
`c3e9e65afe59a78c80687ca19243e28cbf70f227131e4f207c1a05c8bd34b02f`. The
sample is before the configured 24-hour boundary, so Phase-0 remains
`PENDING_STABILITY`; no stability root was changed or combined.

An independent one-cycle recheck at
`artifacts/phase3/public-market-data-durable/20260811T034114Z-one-cycle-recheck`
then made six credential-free public connections and received 503 valid events.
Its summary SHA-256 is
`698ad40af908757a398d19c6df83e4bfc50209bca541fe8b3acd6c314d6eff1e`.
Binance BTC/ETH again ended stale at `5.096588s`/`5.011760s` against the
5-second policy; Coinbase remained quarantined and Deribit degraded. The
recheck is external corroboration only and did not open Phase 3.

The offline review boundary
[`scripts/evaluate_phase3_admission.py`](../../scripts/evaluate_phase3_admission.py)
and its focused tests
[`tests/phase3/test_phase3_admission.py`](../../tests/phase3/test_phase3_admission.py)
now validate timestamp-derived duration, terminal-sample presence, source-card
identity, public/write separation, all-cycle primary continuity, fail-closed
disagreement/selection behavior, and completed resource-sidecar evidence. It
does not perform network I/O, mutate source roots, or represent formal
admission. Evaluation of r3 is preserved at
`artifacts/phase3/public-market-data-admission/20260811T043711Z-two-hour-r3-v2/phase3-admission-evaluation.json`
with SHA-256
`cbb8ec53d793887f17ebeccab8db33a52051082cdd989ff780b7a5f854cf0c1b`.
The result is `PENDING_EXTERNAL_EVIDENCE` with blockers
`qualification_window_incomplete`, `no_healthy_primary_source_for_btc_eth`,
and `primary_snapshot_sequence_or_replay_failure`; the last r3 sample was
before the requested target, and source health/replay also failed the review.

The next independent four-hour root is active under PID `87421` at
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
with sidecar PID `88019`. Both are credential-free/read-only and are preserved
as separate evidence; neither opens Phase 3.

PR #110 fixes the durable runner's terminal boundary for future roots. It
collects one explicit cycle starting at or after the target, marks the sample
`terminal_sample=true`, and records `terminal_sample_count` in the summary.
PR #112 requires that marker in the offline evaluator.
The active r4 root predates this implementation and remains bound to its
recorded code SHA-256; it is not restarted or retroactively reclassified.

| Phase | Local implementation boundary | Automated evidence | Remaining admission evidence |
|---|---|---|---|
| 0 | Typed gateway/archive/event ports; trading-authority denylist; policy-enforced three-tier gateway; exact model acquisition; isolated runtime attestation; real frozen-data local bake-off; strict role roster; append-only stability records; durable Phase-0 gate and redacted gateway-call records; local component evidence drill; exact-route stability runner; scoped two-provider rclone-crypt qualification boundary | [`tests/phase0`](../../tests/phase0), [`tests/contracts`](../../tests/contracts), [`scripts/run_phase0_component_bakeoff.py`](../../scripts/run_phase0_component_bakeoff.py), [`tests/phase0/test_remote_stability.py`](../../tests/phase0/test_remote_stability.py), [`tests/expansion/test_rclone.py`](../../tests/expansion/test_rclone.py), [`tests/phase0/test_rclone_qualification.py`](../../tests/phase0/test_rclone_qualification.py), [`scripts/qualify_rclone_archive.py`](../../scripts/qualify_rclone_archive.py) | The selected local roles now have independent 24-hour `QUALIFIED` review evidence bound to both rosters (global report SHA-256 `1a6ab92c4f28d456776eac0c89ab099b0c1ef579c729fa8e458e4d5192b06949`); the private-route roots remain quarantined and complete two-provider raw-layer archive evidence remains externally deferred. No global Phase-0 admission is claimed. |
| 1 | Immutable contracts; PIT snapshot/lake/query boundaries; Parquet manifests; SQLite WAL ledgers/outbox; identity registry; config bundles/rollback; measured resource leases; traces, FTS5-first memory, optional deterministic hashing recall, durable flows, and service ownership | [`tests/contracts`](../../tests/contracts), [`tests/data`](../../tests/data), [`tests/point_in_time`](../../tests/point_in_time), [`tests/resources`](../../tests/resources), [`tests/recovery/test_config_bundles.py`](../../tests/recovery/test_config_bundles.py), [`tests/recovery/test_phase1_local_rebuild.py`](../../tests/recovery/test_phase1_local_rebuild.py), [`tests/memory`](../../tests/memory), [`tests/services`](../../tests/services) | Local rollback/Bronze rebuild report passed at `artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json`; provider-specific paper deployment rollback and long-lived restore evidence remain external |
| 1 provider recovery | Scoped Binance Spot Testnet read-only restart/configuration rollback qualification using the existing configuration store and provider transport | [`scripts/qualify_binance_spot_testnet_recovery.py`](../../scripts/qualify_binance_spot_testnet_recovery.py), [`tests/recovery/test_binance_readonly_recovery.py`](../../tests/recovery/test_binance_readonly_recovery.py), [`tests/recovery/test_config_bundles.py`](../../tests/recovery/test_config_bundles.py), [`tests/integrations/test_binance_spot.py`](../../tests/integrations/test_binance_spot.py) | Real report `artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json` (SHA-256 `acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`) passed two authenticated read projections, immutable bundle rollback/reopen, and fresh child-process hydration with 18 read-only calls and zero writes; state is externally measured partial and not admitted |
| 2 | Native paper/testnet adapter with optional strict venue identity; Coinbase Exchange Sandbox-specific CB-ACCESS signer and exact host guard; Binance Spot Testnet HMAC signer and exact host/path guard; provider schema mapping for products/accounts/orders/fills; raw event spool/replay; typed native trade/book/bar/funding/open-interest normalization; account/cash/margin/funding/borrow/FX/corporate-action ledger; cost-aware target builder; authoritative RiskKernel/kill switch; policy-bound order-level risk evidence; durable OMS; ambiguous/reconnect/partial-fill handling; changed-payload idempotency rejection; venue/account/open-order reconciliation; TCA; cadence-gated runtime admission | [`tests/execution`](../../tests/execution), [`tests/integrations`](../../tests/integrations), [`tests/integrations/test_coinbase_exchange.py`](../../tests/integrations/test_coinbase_exchange.py), [`tests/integrations/test_binance_spot.py`](../../tests/integrations/test_binance_spot.py), [`tests/integrations/test_binance_spot_lifecycle.py`](../../tests/integrations/test_binance_spot_lifecycle.py), [`tests/integrations/test_paper_venue_bakeoff.py`](../../tests/integrations/test_paper_venue_bakeoff.py), [`scripts/qualify_paper_venue_candidates.py`](../../scripts/qualify_paper_venue_candidates.py), [`scripts/qualify_binance_spot_testnet_lifecycle.py`](../../scripts/qualify_binance_spot_testnet_lifecycle.py), [`tests/runtime`](../../tests/runtime) | Coinbase real read-only smoke remains partial: the sandbox product catalogue returned `BTC-USD` but not required `ETH-USD`; authenticated account/balance/position/open-order reads passed, but the product-filtered fills read returned HTTP 401. Binance authenticated read-only evidence passed all required operations; one supervised fake-funds `LIMIT_MAKER` lifecycle measured one signed submission, authoritative reconciliation, cancellation, restart hydration, TCA, zero unexplained attribution residuals, and deterministic failure drills. The real no-fill path is qualified; fill ingestion remains fixture-tested, and Phase-0 admission of the real Nautilus runtime plus later paper gates remain pending |
| 3 | Native/CCXT/Deribit/LSE-context parsers; raw-first REST/WSS replay; typed native market events with provider timestamp normalization; RSS/GDELT and official source parsers; untrusted-content stripping; PIT availability/revision/origin metadata; quality findings and cutoff dashboard; bounded real-source qualification runners with WSS freshness measurement and level-2 book recovery boundary | [`tests/data/test_collectors.py`](../../tests/data/test_collectors.py), [`tests/data/test_market_events.py`](../../tests/data/test_market_events.py), [`tests/data/test_official.py`](../../tests/data/test_official.py), [`tests/data/test_acquisition.py`](../../tests/data/test_acquisition.py), [`tests/phase3/test_source_qualification.py`](../../tests/phase3/test_source_qualification.py), [`tests/phase3/test_coinbase_wss_qualification.py`](../../tests/phase3/test_coinbase_wss_qualification.py), [`tests/phase3/test_coinbase_level2_qualification.py`](../../tests/phase3/test_coinbase_level2_qualification.py), [`scripts/qualify_phase3_sources.py`](../../scripts/qualify_phase3_sources.py), [`scripts/qualify_phase3_coinbase_wss.py`](../../scripts/qualify_phase3_coinbase_wss.py), [`scripts/qualify_phase3_coinbase_level2.py`](../../scripts/qualify_phase3_coinbase_level2.py) | REST evidence remains partial at `artifacts/phase3/source-qualification/20260810T044558.818461Z/phase3-v3-core-source-qualification.json` (SHA-256 `d435e99b59d815700ccfc5d75e309632ecc91fa1aea3cd3b6c7157a02df272bf`): the bounded retry still records BTC-USD native ticker, Deribit index, and SEC RSS raw replay passes, Coinbase ETH-USD HTTP 404, and GDELT HTTP 429. Ticker WSS evidence at `artifacts/phase3/coinbase-wss-qualification/20260810T044142.351959Z/phase3-coinbase-wss-qualification.json` (SHA-256 `a41fa2367a7f940e8197d5f8e0188765f9c522086091f93df988e0b2abbde702`) completed two real connections with deterministic replay and freshness passing, but observed provider sequence gaps. Level-2 batch evidence at `artifacts/phase3/coinbase-level2-qualification/20260810T052805.696329Z/phase3-coinbase-level2-qualification.json` has SHA-256 `dc620a8fa41458fa4f89396e33687b13750461a3cd643be1b18d0588092e23de` and passed bounded snapshot/update validation and replay. Continuous freshness soak, recovery, and source-disagreement evidence remain external; no Phase-3 admission is claimed |
| 3 current evidence addendum | Binance Spot Testnet depth snapshot/update reducer and raw replay boundary | [`tests/phase3/test_binance_depth_qualification.py`](../../tests/phase3/test_binance_depth_qualification.py), [`scripts/qualify_phase3_binance_spot_testnet_depth.py`](../../scripts/qualify_phase3_binance_spot_testnet_depth.py) | The qualifier now includes fixture-tested bounded provider/local clock-offset measurement and raw future-event retention. The latest root `artifacts/phase3/binance-spot-testnet-depth/20260810T211531.293435Z/phase3-binance-spot-testnet-depth.json` has SHA-256 `f75f4e25ba48d923df4cba4e29d7ccf4b45e7382a05b5f63bb3a500b8b59fcde`; an ETH stream replayed equivalently, while BTC and other streams failed closed on WSS/runtime or adjusted-future freshness. The diagnostic root `artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json` has SHA-256 `8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b` and independently proved DNS/TCP/TLS before classifying intermittent connection timeout. Longer freshness, recovery, source disagreement, and Phase-3 admission remain pending |
| 3 public market-data separation | Credential-free public source cards and raw-first source-selection runner | [`src/advisorai/collectors/public_market_data.py`](../../src/advisorai/collectors/public_market_data.py), [`tests/data/test_public_market_data.py`](../../tests/data/test_public_market_data.py), [`scripts/qualify_phase3_public_market_data.py`](../../scripts/qualify_phase3_public_market_data.py) | v2 real public Binance REST/WSS product, filter, book, trade, server-time, four full-window, reconnect, adjusted-freshness, and cross-source observation evidence selected BTCUSDT/ETHUSDT as the current primary candidate in `artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json` (SHA-256 `14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`). The source card loads no credentials, has no write method, and is separate from Binance Spot Testnet execution. Candidate evidence remains bounded, not continuous source admission |
| 3 durable public-data qualification | Restartable append-only runner, typed source-health state machine, provider-truth snapshot recovery, disagreement/failover policy, read-only dashboard projection, offline validator, and separate OS-resource monitor | [`scripts/run_phase3_public_data_qualification.py`](../../scripts/run_phase3_public_data_qualification.py), [`scripts/validate_phase3_public_data_qualification.py`](../../scripts/validate_phase3_public_data_qualification.py), [`scripts/monitor_phase3_process_resources.py`](../../scripts/monitor_phase3_process_resources.py), [`tests/phase3/test_source_health_controls.py`](../../tests/phase3/test_source_health_controls.py), [`tests/phase3/test_phase3_qualification_validation.py`](../../tests/phase3/test_phase3_qualification_validation.py), [`tests/phase3/test_phase3_resource_monitor.py`](../../tests/phase3/test_phase3_resource_monitor.py) | The completed root produced 378 samples/63 cycles, 20,744 valid events, 35 disconnects, 25 reconnects, 252 resubscriptions, three snapshot-recovery attempts, zero gaps/duplicates, one out-of-order event, and three replay failures. The validator report has SHA-256 `efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca`; the resource sidecar had 32 observations and no resource errors | IMPLEMENTED / TESTED / EXTERNALLY MEASURED / QUALIFIED FOR REVIEW / NOT ADMITTED; Phase-0 stability, remaining Phase-3 admission criteria, and later paper gates remain open |
| 4 | Mandatory naive/drift/seasonal/linear/LightGBM boundaries; real isolated ModernFinBERT/MiniLM/DeBERTa, TTM-R2/R3, TSPulse, Chronos and Kronos workers; frozen public walk-forward and sentiment evaluation; one-family GPU lease; evidence-bound role roster | [`tests/models`](../../tests/models), [`tests/phase0`](../../tests/phase0) | Selected-role 24-hour stability and later paper net-utility evidence; TabPFN-TS waits on gated terms |
| 5 | Policy Mission Router; bounded adaptive council waves; typed role results; snapshot/mission-bound runs; ancestry-aware evidence graph; dissent/expiry/cutoff handling; target-only DecisionBundle and RiskKernel hand-off | [`tests/agents`](../../tests/agents), [`tests/api`](../../tests/api) | Real provider route selection and scored multi-factor evidence from live V3-Core data |
| 6 | Benchmark portfolio comparisons; robust covariance/factors/capacity/margin/stress; purged walk-forward, multiple-testing, sensitivity/regime checks; TCA/P&L attribution; incident/postmortem reconciliation; model challenge evidence | [`tests/institutional`](../../tests/institutional), [`tests/data/test_observability.py`](../../tests/data/test_observability.py) | Production paper-order sample proving exact attribution and unresolved-incident handling |
| 7 | Durable soak samples/gate; restartable `DurablePaperSoakRunner` with immutable run identity, fsync'd hash chain, PID/heartbeat status, lock ownership, and terminal-sample enforcement; data/model/agent/risk/execution scorecard fields; measured headroom and no-trade/benchmark net-utility checks; all required adverse scenarios; ledger-backed sample rebuild; recovery report and archive-restore boundary | [`tests/recovery/test_soak.py`](../../tests/recovery/test_soak.py), [`tests/recovery/test_durable_soak.py`](../../tests/recovery/test_durable_soak.py), [`src/advisorai/soak/durable.py`](../../src/advisorai/soak/durable.py) | At least 60 calendar days, meaningful adverse sample, stable resources, clean reconciliation, and positive net utility |
| 8 | Hermes isolation policy and concrete bounded process runner with enforced child socket/DNS, read-only filesystem, conventional sensitive-path and process-environment metadata policies, and common process-spawn denial; sensitive-environment scrubbing; typed research/strategy/collector/model/runbook/capability artifacts; permission-filtered capability registry/broker; lifecycle through active-read; disposable Docker OS-boundary probe; explicit human approval for active-write-limited | [`tests/capabilities`](../../tests/capabilities), [`scripts/run_phase8_capability_evidence.py`](../../scripts/run_phase8_capability_evidence.py), [`scripts/probe_phase8_os_sandbox.py`](../../scripts/probe_phase8_os_sandbox.py), `artifacts/phase8/capability-evidence/20260808T050150.878842Z/phase8-capability-evidence.json`, `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`, external review runbook | Local Hermes-to-active-read evidence passed with SHA-256 `d6e44c90574c5209bd658319637605a00269fe49fe9cad7120766ecdc2cd79e5`; pinned external Hermes package completed a synthetic loopback task inside WSL2 namespaces with report SHA-256 `2fcfe86c151bffe2f4c59af0f7e0e029005a4ad94675c47fc3c18348a151b51c`; the Docker boundary measured root-identity read-only-root/network denial, constrained tmpfs, zero capabilities, and denied unshare/mount probes with report SHA-256 `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`; formal Phase-8 admission remains pending because universal native syscall/C-extension containment, credential/production-tree isolation, and a real provider route are not attested |
| 9 | Vintaged SEC/ALFRED boundary; equity corporate-action/daily-council boundary; compliant browser ladder; one-at-a-time challenger registry; duplicate-provider rejection, safe archive keys, and two-provider archive verification/rclone boundary | [`tests/expansion`](../../tests/expansion), [`tests/data/test_official.py`](../../tests/data/test_official.py) | Marginal-value and headroom evidence for each real source/model/framework addition |
| 10 | Explicit human authorization artifact; fixed loss/notional budget; policy/state-hash-bound final order guard; AI-offline safety check; automatic paper-rollback readiness | [`tests/live`](../../tests/live) | Phase 7 completion, explicit human approval, and supervised bounded live validation |
| Alpha Team extension | Integrated plan and conformance boundary only; no Research Brain, DSL, candidate, experiment, validation, or promotion implementation is claimed by this row | None; future evidence must be tied to the E0-E7 gate in [`alpha-team-extension.md`](alpha-team-extension.md) | E0 is not yet satisfied; no Alpha Team runtime, paper candidate, or admission evidence is claimed |

## Phase-3 durable source qualification implementation

The current Phase-3 package adds a restartable append-only public-data runner,
typed deterministic source-health transitions and hash-chain ledger,
provider-truth snapshot/sequence recovery, versioned cross-source disagreement
policy, explicit failover/fail-closed selection, and a sanitized read-only
dashboard/API projection. The implementation is in
[`scripts/run_phase3_public_data_qualification.py`](../../scripts/run_phase3_public_data_qualification.py)
and the typed collectors under
[`src/advisorai/collectors`](../../src/advisorai/collectors), with focused
coverage in [`tests/phase3/test_source_health_controls.py`](../../tests/phase3/test_source_health_controls.py).

The runner also projects sanitized `failure_classes` and `failure_layers` from
the REST/WebSocket collector boundary into per-source/symbol samples, summary
aggregates, and the read-only dashboard/API health projection. Only bounded class or
layer identifiers are retained; response bodies, headers, messages, and
credentials remain outside the evidence projection. The regression test for
this boundary is included in the focused Phase-3 suite (`14 passed`). Existing
qualification roots are immutable and are not retrofitted; the active r4 root
predates this projection and remains evidence as recorded.

The prior real qualification root
`artifacts/phase3/public-market-data-durable/20260810T231500Z-two-hour-r2`
completed 336 samples across 56 cycles. Its summary SHA-256 is
`96aac309e23df24e090b97a99127b33d4dbb90e9b593cf76d909ef43e65f0283`; all five
append-only logs reloaded successfully, but the root used the pre-fix
sequential-symbol and connection-accounting implementation, so its result is
evidence-for-review-only and Phase-3 admission remains closed.

A fresh corrected root
`artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`
completed its target at `2026-08-11T03:14:39.940009Z` after 63 cycles and 378
samples. Its config records code SHA-256
`c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`, bounded
Binance snapshot limit `100`, no credentials, and no order writes. The offline
validator returned `PASS_FOR_REVIEW` with `phase3_admission=false`; the
validated report SHA-256 is
`efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca`. No
execution authority was added.

The first v1 resource sidecar root is preserved as failed hash evidence. The
corrected v2 systemd sidecar completed at
`artifacts/phase3/public-market-data-resource-monitor/20260811T025102Z-pid13339-v2`
with config SHA-256
`3202ad6c45f750a9b1c250336a0d7819cdcfa78486a6ee5bd78d645c544d3e08`, summary
SHA-256
`42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd`, 32
observations, and no resource errors. It has no credential or execution access
and did not open admission; the service and target process are no longer
running.

## Latest Phase-3 Binance availability evidence

A further bounded 20-second retry at
`artifacts/phase3/binance-spot-testnet-depth/20260810T201946.533716Z/phase3-binance-spot-testnet-depth.json`
failed closed before the first WSS message on all four connections, made zero
REST calls, and passed deterministic drills. Its SHA-256 is
`ce402b7bdd67513c90b1cc5bf744d0a8d455a6f1b7f927610a84f997699b8415`.
This remains provider/runtime availability evidence, not a Phase-3 pass.

## Global invariant checks

- No gateway, agent, Hermes task, browser job, capability, or model adapter has
  an order-submission or risk-limit-relaxation action.
- Risk decisions bind the immutable policy ID plus account and market state
  hashes; ordinary callers reject by default, and target reduction is explicit.
- Orders, fills, account events, reconciliations, capability/model/challenger
  transitions, incidents, and Phase-0 gate records are idempotent and replayable
  from local ledgers.
- PIT snapshots reject future availability/ingestion/event data; quality
  dashboards retain lineage, revision, origin, disagreement, and cutoff state.
- The acceptance runner executes phases in order and stops at the first failed
  phase. It reports only local executable evidence; it never opens a live gate.

## Verification record

The pre-qualification local run on 2026-08-10 passed all 570 tests in one process with every
declared optional extra active in an isolated locked verification environment, and
all eleven isolated phase suites. The latest merged-main rerun passed 607 tests
and phase suites `128/152/126/66/19/34/10/7/27/18/5`. The exact phase distribution and static checks are kept in
[`status.md`](status.md). Phase 0’s 24-hour evidence, Phase 7’s 60-day soak, and
Phase 10’s human/live approval remain intentionally pending. Repository-wide Ruff
format checking passes with all 281 Python files formatted. The Phase-1 local
rollback/Bronze rebuild report is immutable evidence with SHA-256
`6e8cd86017dacea7b4a0fff8e9ea41901ec4bb7ee02961f5811dcbb7266342b2` and does
not open any external or human gate. The Phase-0 component evidence report is
immutable evidence with SHA-256
`6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7`; it also
does not open any external or human gate. The Phase-8 capability report is
immutable evidence with SHA-256
`d6e44c90574c5209bd658319637605a00269fe49fe9cad7120766ecdc2cd79e5`; it also
records enforced child socket/DNS, read-only filesystem, conventional
sensitive-path and process-environment metadata policies, common process-spawn
denial, and does not create a formal gate record or global capability admission.
Direct native syscalls/C-extension escapes are outside this in-process evidence
and require separate OS-level sandbox attestation. A real local Docker
boundary probe subsequently measured network denial, read-only-root denial,
zero effective capabilities, and bounded process controls at
`artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
with SHA-256
`1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`; it did
not attest universal native syscall/C-extension containment or open formal
admission.

The current requirement-to-evidence matrix is
[`gate-matrix.md`](gate-matrix.md). It records the fresh detached Phase-0
stability run, the DuckLake challenger rejection, the pinned upstream Hermes
review, and the remaining operator/time-dependent gates without promoting any
of them to formal admission.

The Coinbase Exchange Sandbox adapter is implemented in
[`src/advisorai/integrations/coinbase_exchange.py`](../../src/advisorai/integrations/coinbase_exchange.py)
and bound through the `PAPER_VENUE` resolver scope only. Its sanitized real
smoke evidence is
`artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`
with SHA-256
`79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`. The
adapter and local schema/signing tests are implemented/tested; the external
read-only gate is `PENDING_OPERATOR_ACTION` because `ETH-USD` was absent from
the actual sandbox catalogue and the fills read returned HTTP 401. No Coinbase
order was submitted.

The selected Binance Spot Testnet candidate is implemented in
[`src/advisorai/integrations/binance_spot.py`](../../src/advisorai/integrations/binance_spot.py)
with positive/negative coverage in
[`tests/integrations/test_binance_spot.py`](../../tests/integrations/test_binance_spot.py).
Its public qualifier measured `BTCUSDT` and `ETHUSDT` from the live testnet
catalogue at
`artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/binance-spot-testnet-public-truth.json`
with SHA-256
`34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6`.
The authenticated smoke is
[`scripts/smoke_binance_spot_testnet.py`](../../scripts/smoke_binance_spot_testnet.py).
The fresh read-only report at
`artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`
has SHA-256
`c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72` and passed
server time, products, BTC/ETH mapping, account, balances, positions, open
orders, and fills without a write. The subsequent supervised lifecycle report
at
`artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`
has SHA-256
`db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70` and
qualified the single signed post-only submission/cancel and no-fill path.
Fill ingestion is not claimed as real-provider evidence because no fill
occurred; it remains covered by deterministic fixtures.
After the final adapter source was settled, a read-only-only rerun passed the
same gate at
`artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T201450.306674Z/binance-spot-testnet-read-only-smoke.json`
with SHA-256
`b3a8b54f446599b50547bab98240db0fe8e1380fd969a6a220fccac1c83fe8e7` and
adapter source SHA-256
`ec3077cc726a045420c714f99c5c2e026351190348fdc9779f96e21cff034e0d`; it made
nine read-only calls and no write.

The latest independent Phase-3 REST/raw-first retry is
`artifacts/phase3/source-qualification/20260810T201653.611706Z/phase3-v3-core-source-qualification.json`
with SHA-256
`60cac1ba77fa31735c87b02e29125985e9d4e69b2e592886e317b0ed61ecca01`.
It preserved the prior partial result: Coinbase BTC-USD, Deribit BTC index,
and SEC RSS passed; Coinbase ETH-USD returned HTTP 404 and GDELT HTTP 429.
Phase-3 remains pending continuous freshness/recovery and independent-source
evidence.
