# Phase-4 V3-Core long-run release candidate

This runbook describes the reviewed, no-launch release candidate. It is a
planning and control contract, not a long-run preregistration. No collector,
Chronos worker, watchdog, scheduler, GPU workload, broker connection, or
credential is started by this branch.

## Qualification lineage

The historical canary results are kept separate from any future long-run
evidence:

| Run | Result | Meaning |
| --- | --- | --- |
| V1 warm-up canary | Failed | The pre-warm-up cutoff was incorrectly treated as mandatory. |
| V2 monitor/auditor canary | Failed | A watchdog monitoring failure made the canary permanently unqualified. The corrected post-hoc replay found no genuine post-admission revision and linked the existing outcomes, but did not change that result. |
| `20260824T160000Z-chronos-monitor-audit-v3b` | `CANARY_QUALIFIED = TRUE` | The corrected source-finality, Chronos, watchdog, coverage, causality, and outcome path qualified for bounded canary use only. |

The V3B preregistration and terminal report remain immutable. A successful
canary is not a Phase-4 admission or PASS, and it does not authorize the
multi-day run. Live capital remains unauthorized.

V3B qualification reference:

- preregistration SHA-256: `458754d53a74def22d33d2f462f31d3701c072f7ad4beb064aa0a63f10e98916`
- terminal report SHA-256: `183dd854f5eee2418c10a27b74daa0d86044367eae461ac2c1bfc30f79d239bd`
- qualified implementation: `de31ff1a46e8c57cc299f29a6463283f3ddf2931`
- qualified Chronos model identity SHA-256:
  `c9287ccd27f52d93a71f24be2ad967d0bad6c79e7a4abd0c758e7448994b68ca`
- qualified checkpoint SHA-256:
  `492290ae82bb89f9769e3479ce90b3179de1f33e600c34daa0352531538b23cd`
- Phase-3 gate SHA-256:
  `4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232`

## Scientific contract

The eventual long run is fixed to:

- Binance public market data only, BTCUSDT and ETHUSDT, five-minute raw
  observations, and hourly prediction opportunities.
- Every raw receipt is append-only. A canonical bar is admitted only after
  interval close, a 60-second post-close guard, and two identical OHLCV
  observations from distinct raw receipts. `ADMITTED_FINAL` is immutable;
  any genuine later change is a generation-fatal post-admission revision.
- Each context has exactly 48 consecutive `ADMITTED_FINAL` bars. The newest
  interval must end at or before the hourly cutoff minus ten minutes. There is
  no padding, interpolation, fallback, backfill, or pre-start history shortcut
  in the fresh-run contract.
- The only candidate is `autogluon/chronos-2-small` at revision
  `ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a`, using the qualified checkpoint
  and preprocessing identity. Predictions are prospective and must be written
  to the immutable typed ledger before their genuine one-hour outcomes mature.
- The scheduled target is **80 opportunities per symbol**. The scientific
  clean minimum is **64 cases per symbol**, leaving a 16-case (25%) planned
  buffer. The run does not stop early at 64 and never extends its deadline
  after launch.

The first legal cutoff for a fresh UTC-hour start `S` is the first top-of-hour
`T` satisfying:

```text
T >= S + 48 * 5 minutes + 10 minutes
```

All 48 bars must originate after `S`. For example, a 16:00 UTC start derives a
21:00 UTC first cutoff. The planning duration is approximately 86 hours: 80
hourly opportunities, one hour of outcome maturity, and a one-hour terminal
margin, in addition to the four-hour context warm-up already represented by
the first-cutoff calculation.

## Identity and security boundary

Both dependency identity surfaces are required and are deliberately different:

- repository `uv.lock`: SHA-256
  `2baec5b4342fa9cef1c199ceb958621b320e8107a70abae26237d173b83ce0ac`;
- separately qualified Chronos runtime `requirements.lock`: SHA-256
  `260b47a47432d58c80ec1f850563782d8302ecf8748950c2b85200152e1dfaec`.

The release-candidate fingerprint also binds the repository commit,
collector/finality/Chronos/watchdog/auditor/scheduler code, preprocessing,
checkpoint, model identity, Phase-3 gate, source snapshot, and V3B
qualification references. A future immutable preregistration must add its own
hash and the resulting run-specific source snapshot hash; the dry-run keeps
that future source snapshot explicitly pending. This branch does not create a
long-run preregistration.

The original `20260901T120000Z-v3core-phase4-chronos-long-r1`
preregistration is immutable historical control evidence. Its start window
passed without a launch and it must never be edited, reused, or treated as
prospective evidence.

## Detached launch gate

A newly reviewed V2 preregistration binds the detached gate's code hash and an
exact 60-second launch window beginning at the frozen start. It also binds the
canonical launch-preflight code and a complete immutable verification-results
artifact; missing, failed, extra, or caller-invented checks are rejected. The
gate may be armed before that time, but its only prestart scientific state is
`PRESTART_WAITING_VALID_GATE`. It waits in a local OS process; it does not
start the coordinator, collector, candidate, watchdog, outcome linker, or
scheduler early.

At the window boundary, the gate delegates exactly once to the reviewed
launcher. The launcher recomputes checkout, component, dual-lock, model,
checkpoint, predecessor, runtime, import-path, and worktree identities before
creating any evidence root or child process. It also resolves the annotated
release tag to the preregistered commit, refuses any existing long-run
component, and requires a queryable GPU with no resident compute application.
The launch window is checked again after these dynamic checks so a slow
attestation cannot become a late launch. A refusal is recorded as
`LONGRUN_NOT_LAUNCHED_PREFLIGHT_FAILED`; expiry is recorded as
`LONGRUN_NOT_LAUNCHED_MISSED_START_WINDOW`. Neither state permits a late
launch or a shifted schedule. Legacy V1 preregistrations have no launch-gate
authority and always fail closed.

The source and candidate path has no credentials, withdrawal/transfer
capability, broker/order capability, or execution authority. The readiness
checker is a refusal boundary; it does not acquire resources or start work.

## Failure and recovery policy

An isolated unresolved context, missed cutoff, candidate rejection, or
recoverable source/candidate outage is recorded append-only as `CASE_EXCLUDED`
when the 64-case clean minimum remains mathematically attainable. It is never
backfilled or silently repaired.

A component resume is only reviewable under one bounded attempt per component
incident and only when the repository, code, model/checkpoint, both locks,
append-only state, resume identity, ledger, watchdog history, security flags,
and scientific continuity all validate. A failed validation is fatal.

The following are immediately `GENERATION_FATAL` (or the equivalent
`GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION` state): post-admission revision,
broken raw/admitted/prediction identity, identity or lock drift, future
leakage, fabricated/backfilled data, corrupted fatal history, credentials,
orders, unrecoverable CUDA/model failure, or clean cases plus remaining
opportunities below 64 for either symbol. No deadline extension, rule
relaxation, or performance-dependent stop is allowed.

## Traceability

| Requirement | Source review | Release-candidate implementation | Verification/evidence |
| --- | --- | --- | --- |
| Sealed raw/normalized integrity and chronological audit | #190 | Integrated `v3core_integrity` and terminal audit lineage | Existing integrity, materialization, and audit tests |
| Immutable readiness fingerprint | #193 | Integrated readiness hash validation; extended dual-lock release contract | Readiness fingerprint tests and dry-run report hash |
| Corrected candidate/finality qualification | #199 | V3B qualification reference and exact model/checkpoint identity | V3B terminal report; candidate contract tests |
| Bounded prospective canary machinery | #200 | Reused canary finality/context/evidence-class contracts | Canary and finality tests |
| Warm-up/scheduler contract | #201 | Reused first-cutoff geometry and prospective timing boundary | Warm-up and scheduler tests |
| Watchdog/auditor repair | #202 | Reused absorbing failure and chronological replay behavior | Watchdog, replay, and terminal workflow tests |
| 80/64 long-run accounting and recovery boundary | This release-candidate branch | `v3core_longrun.py` and long-run preflight script | Long-run accounting/recovery tests |
| Detached post-start launch gate | Follow-up launch-gate review | V2 preregistration schema, hash-bound gate, post-start launcher window | Early/missed-window, legacy refusal, and structured-failure tests |

PR #192 remains separate post-seal causal baseline tooling. PR #195 remains
separate shutdown/readiness operational tooling. Neither is required to acquire
prospective candidate evidence and neither is entangled in this launch
candidate unless a later reviewed integration proves a dependency.

## Review gate

`RELEASE_CANDIDATE_READY_FOR_HUMAN_REVIEW` means the sanitized offline
preflight and required evidence/tests passed. It does not mean
`LONG_RUN_READY`. Before any run, a human must review this branch, approve a
future start time, create a new immutable preregistration, and verify the final
launch fingerprint. No long-run preregistration exists yet.
