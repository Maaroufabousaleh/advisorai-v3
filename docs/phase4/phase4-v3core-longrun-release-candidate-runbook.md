# V3-Core Phase-4 long-run release-candidate runbook

This document describes the executable release candidate only. It is not a
long-run preregistration, does not select a start time, and does not authorize
launch, Phase-4 admission, Phase 5, live capital, credentials, or orders.

## Evidence lineage

The V1 warm-up canary failed because a pre-context cutoff was treated as
mandatory. The V2 bounded canary remained failed because watchdog monitoring
and terminal-audit tooling were defective, even though its candidate records
were useful for post-hoc diagnostics. The V3B bounded canary qualified as
`CANARY_QUALIFIED = TRUE` under its frozen canary contract. A qualified bounded
canary is validation evidence, not a Phase-4 pass.

The release candidate preserves the historical evidence roots and binds the
qualified Chronos-2-small model, source-finality rule, runtime locks, repaired
watchdog, chronological auditor, and 80-opportunity coordinator by identity.

## Frozen scientific shape

- Binance public market data only; BTCUSDT and ETHUSDT; five-minute receipts.
- Raw receipts are append-only. A bar is `ADMITTED_FINAL` only after its close,
  a 60-second guard, and two identical observations from distinct receipts.
- An admitted bar is immutable; a genuine later change is generation-fatal.
- Each hourly opportunity uses exactly 48 consecutive admitted-final bars, with
  the newest interval ending no later than cutoff minus ten minutes.
- The candidate is the qualified pinned Chronos-2-small path only: no padding,
  interpolation, fallback, retrospective prediction, or backfill.
- The target is 80 prospective opportunities per symbol; at least 64 clean
  certified cases per symbol are required by independent terminal audit.

For a fresh, hour-aligned UTC start `S`, the first fresh five-minute interval
ending at `S` is excluded. The required context ends are therefore
`S + 5m, S + 10m, ..., S + 48*5m`; the first legal top-of-hour cutoff is the
first `T` satisfying `T >= S + 48*5m + 10m`. The equivalent
`S + (48 - 1)*5m + 10m` expression is only a lower-bound description after
the first fresh interval has been established. The explicit interval sequence
is authoritative, and inference is gated by actual `ADMITTED_FINAL`
availability rather than arithmetic alone. Non-hour-aligned starts fail
closed.

## Recovery and host contract

An isolated source or candidate interruption may receive at most one bounded,
identity-validated recovery attempt for the whole run. Recovery must preserve
append-only state, the exact repository/component/model/runtime identities, the
original cutoff schedule, and the no-credential/no-order boundary. A missed
opportunity is excluded without backfill. Watchdog process death is
generation-fatal in this release candidate because fatal-history continuity is
not auto-recovered.

Unknown errors, identity drift, ledger conflicts, future leakage, post-admission
revision, credential detection, order capability, and mathematical
infeasibility are generation-fatal. No deadline or cutoff is extended after
sleep, suspension, outage, reboot, or recovery.

The supported operational posture for an eventual approximately 86-hour run
is a powered laptop with sleep/hibernate disabled, a live WSL/session, stable
public-data network access, and no host reboot. Seamless host-reboot, lid-close,
or WSL-suspension continuity is not claimed by this release candidate; such an
interruption must be classified by the frozen runtime contract before launch.

## Operational gates

The launch checkout must attest its actual Git HEAD, component files, scripts,
both `uv.lock` and the separately qualified `requirements.lock`, checkpoint,
Phase-3 predecessor, runtime qualification, and security flags. The canonical
terminal auditor, not runtime counters, certifies clean coverage and emits
`PHASE4_LONGRUN_CERTIFIED` or `PHASE4_LONGRUN_NOT_CERTIFIED`.

The dry-run readiness report is for human review. No immutable long-run
preregistration has been created and no long-run process should be started
from this branch until human review, code freeze, exact UTC start selection,
and final preregistration are complete.

## Review history and verification

The initial adversarial review of PR #203 was `BLOCKED` because the branch had
no executable 80-opportunity acquisition path, no durable prediction timing
boundary, and readiness could accept caller-supplied identities. Those defects
were repaired in the release-candidate implementation. The branch now
contains dedicated long-run preregistration/schema, launcher, collector,
Chronos candidate, outcome linker, coordinator, watchdog, scheduler, and
terminal-auditor entry points. Launch remains explicitly opt-in and no
runtime evidence has been created by this branch.

The implementation traces to #190 (sealed-source integrity and chronological
audit), #193 (readiness fingerprinting), #199 (corrected candidate/finality
qualification), #200 (bounded canary/source-finality machinery), #201
(warm-up and scheduler contract), and #202 (watchdog latching, snapshot
consistency, and auditor repair). The 80/64 accounting, bounded recovery
contract, dual-lock attestation, executable lifecycle, and long-run runbook
are the explicitly approved release-candidate additions. #192 remains
post-seal baseline tooling and #195 remains shutdown/readiness operational
tooling; neither is part of prospective acquisition.

The previously unavailable artifact-backed tests are resolved from the
canonical checkout without copying or changing the artifacts. The immutable
Phase-3 gate is
`artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json`
(SHA-256
`4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232`). The
qualified Phase-0 Chronos runtime record is
`artifacts/phase0/model-runtime-qualification/chronos-v3core-r1/20260817T194802.642906Z/chronos-2-small.json`
(SHA-256
`e04dc75df9bebd79f623ea32a8e815f7f15ad92cb0e343edf098ad577c896289`). The
forecast snapshot manifest used by baseline-only tests is
`artifacts/phase0/model-runtime-qualification/benchmark-data/public-daily-0f84a34fb0537ecb/forecast-snapshot-manifest.json`
(SHA-256
`d41239615c7de15d8ca1e2a940f99a2fa25c939b862c96c7f5e7f26c420e29d2`). These
are references to preserved artifacts, not release-candidate evidence.

The launch-critical long-run suites pass in the release-candidate context;
the full Phase-4 suite's three LightGBM failures remain isolated to #192's
post-seal baseline path and are `NON_BLOCKING_FOR_ACQUISITION`. The complete
canonical-context predecessor/real-input artifact suite passes. Final human
review must still approve the merge/freeze identity, select an exact future
UTC start, create the immutable preregistration, and run the final launch
preflight.
