# Phase-4 shutdown and post-seal operator runbook

This runbook is read-only until the explicit post-seal commands are run after
the source generation is durably terminal. It never stops a process, changes a
deadline, rewrites an evidence root, starts a model, submits an order, or
loads credentials.

## Safe shutdown check for the current generation

Run this after the frozen deadline, not merely after the source reaches its
64/64 target. The command uses fixed roots and the fixed v5 preregistration;
it does not select a latest artifact.

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON="${ADVISORAI_PYTHON:-python}"
SOURCE="$REPO_ROOT/artifacts/phase4/v3core-forward/20260817T193400Z-operator-interrupted-replacement-r1"
RESOURCE="$REPO_ROOT/artifacts/phase4/v3core-forward-resource/20260817T193400Z-operator-interrupted-replacement-r1-sidecar-r1"
CANDIDATE="$REPO_ROOT/artifacts/phase4/v3core-forward-predictions/20260817T193400Z-operator-interrupted-replacement-r1-chronos-2-small-r1"
PYTHONPATH="$REPO_ROOT/src" "$PYTHON" \
  "$REPO_ROOT/scripts/check_phase4_shutdown_readiness.py" \
  --source-root "$SOURCE" \
  --resource-root "$RESOURCE" \
  --resource-pid-file "$REPO_ROOT/artifacts/phase4/v3core-forward-resource/20260817T193400Z-operator-interrupted-replacement-r1-sidecar-r1.pid" \
  --resource-process-token "$SOURCE" \
  --candidate-root "$CANDIDATE"
```

Only `SAFE_TO_SHUT_DOWN` permits the operator to power off the laptop. Any
other output is `NOT_SAFE_TO_SHUT_DOWN` with refusal reasons. The checker
requires the wall-clock deadline, terminal source/resource/candidate states,
dead or command-matching PIDs, readable append-only files, no temporary
atomic-write file, a probeable unlocked collector lock, and false credential /
order-write flags. It does not perform the terminal integrity audit.

The current source root's immutable identities are:

- source code commit: `0e23c0b6a94ac87df7e5cc9fa0e552cb9adb50c5`;
- preregistration SHA-256: `5a867b9c68f9a90593990a820f612bf3fd66670933d680a75ddd521762da1ffd`;
- Phase-3 gate SHA-256: `4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232`;
- source snapshot SHA-256: `f41af27a93dfbee5b4c67cff2570cb80de09004133b84e2eb0f0ffd2546b0b9a`.

## Post-seal processing after the laptop is available again

Do not run these commands while the source status is `running`. Use the
isolated integrity-auditor checkout at the exact current draft head until that
PR is reviewed or merged:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
AUDITOR="${ADVISORAI_PHASE4_AUDITOR_ROOT:?set this to the reviewed PR #190 checkout}"
PYTHON="${ADVISORAI_PYTHON:-python}"
PYTHONPATH="$AUDITOR:$AUDITOR/src"
SOURCE="$REPO_ROOT/artifacts/phase4/v3core-forward/20260817T193400Z-operator-interrupted-replacement-r1"
RESOURCE="$REPO_ROOT/artifacts/phase4/v3core-forward-resource/20260817T193400Z-operator-interrupted-replacement-r1-sidecar-r1"
PREREG="$REPO_ROOT/artifacts/phase4/v3core-cadence-preregistration/20260812T204444Z-v3core-1h-5m-reobserve-fix-v5/phase4-v3core-cadence-preregistration.json"
PHASE3_GATE_SHA=4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232
AUDIT_COMMIT=ca0497cf268eed855ad1295053e34cf079b6c1b5
TERMINAL_AT=2026-08-22T20:00:00Z
OUTPUT="$REPO_ROOT/artifacts/phase4/v3core-terminal-review/20260822T193400Z-current-r1"
```

First verify the exact auditor checkout and source identity:

```bash
test "$(git -C "$AUDITOR" rev-parse HEAD)" = "$AUDIT_COMMIT"
test "$(sha256sum "$SOURCE/manifest.json" | cut -d' ' -f1)" != ""
jq -e --arg commit 0e23c0b6a94ac87df7e5cc9fa0e552cb9adb50c5 \
  --arg prereg 5a867b9c68f9a90593990a820f612bf3fd66670933d680a75ddd521762da1ffd \
  --arg collector 9713692cd64d2aa987ff3cdffd18c48e2cb62f531f79015f3ba4e1aa03b7bd0b \
  --arg forward 2080857b56b1e641cb648cfd97bec7bd7590584606014afe27dc8227b80c98f5 \
  --arg gate "$PHASE3_GATE_SHA" \
  '.code_commit == $commit and .collector_script_sha256 == $collector and .forward_module_sha256 == $forward and .preregistration_sha256 == $prereg and .phase3_gate_record_sha256 == $gate' \
  "$SOURCE/manifest.json"
```

Then run the immutable terminal workflow. The output root must be new; do not
reuse or place it under an input root:

```bash
PYTHONPATH="$PYTHONPATH" "$PYTHON" "$AUDITOR/scripts/qualify_phase4_v3core_forward.py" \
  --run-directory "$SOURCE" \
  --resource-root "$RESOURCE" \
  --preregistration "$PREREG" \
  --phase3-gate-sha256 "$PHASE3_GATE_SHA" \
  --terminal-observed-at "$TERMINAL_AT" \
  --output-root "$OUTPUT"
```

The current generation has zero scorable Chronos predictions. Do not pass a
retrospective or substitute ledger to change that fact. The workflow may
produce integrity/resource evidence while leaving admission and materialization
closed. For this collector generation, a missing source-root `config.json` is
expected: the reviewed run contract binds provenance in `manifest.json`. An
incomplete manifest or any other unverifiable source identity must remain a
refusal, not a reason to synthesize evidence inside the old root.

If and only if the workflow produces a materialized evaluation input, run the
strictly post-seal causal baseline pass into a new root using the frozen
PR #192 hardening checkout and its exact reviewed head:

```bash
BASELINE_OUTPUT="$REPO_ROOT/artifacts/phase4/causal-baselines/20260822T193400Z-current-r1"
BASELINE_REPO="${ADVISORAI_PHASE4_BASELINE_ROOT:?set this to the reviewed PR #192 checkout}"
BASELINE_PYTHON="${ADVISORAI_PYTHON:-python}"
test "$(git -C "$BASELINE_REPO" rev-parse HEAD)" = "24e82dc7615c6653225abf91585fc0ee0c493bd6"
test "$(sha256sum "$BASELINE_REPO/scripts/regenerate_phase4_v3core_baselines.py" | cut -d' ' -f1)" = "bcdf13316210d93b4ba9bcd7138bc5bd0c5c75db93223bbe631d3d4537eb3dff"
test "$(sha256sum "$BASELINE_REPO/src/advisorai/phase4/v3core_baseline_regeneration.py" | cut -d' ' -f1)" = "396c85e76da6246b1ed5f3229ffe635a08b97c98df2c63757393170f4bcb84c6"
PYTHONPATH="$BASELINE_REPO/src" \
"$BASELINE_PYTHON" \
"$BASELINE_REPO/scripts/regenerate_phase4_v3core_baselines.py" \
  --input "$OUTPUT/materialized/phase4-v3core-evaluation-input.json" \
  --output-root "$BASELINE_OUTPUT" \
  --repository-root "$BASELINE_REPO" \
  --repository-commit 24e82dc7615c6653225abf91585fc0ee0c493bd6 \
  --materialized-at "$TERMINAL_AT"
```

That baseline output is explicitly `post_seal_causal_regeneration`; it is not
prospective prediction evidence. Candidate coverage must still be complete for
every model included in the same-case materialization. The current zero-
Chronos generation cannot satisfy the frozen non-baseline admission gate.

No command in this runbook starts Phase 5, Phase 6, Phase 7, a corrected
candidate, or a new data collection generation.

LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.

## Reviewed dependency order

The executable dependency order is:

```text
main + merged PR #191
  -> PR #193 readiness-report fingerprint validation
  -> fresh-generation preflight / launch decision
  -> sealed source + terminal resource roots
  -> PR #190 terminal integrity workflow
  -> integrity-aware materialized Phase-4 input
  -> PR #192 post-seal causal baseline identity hardening
  -> causal mandatory baseline regeneration
  -> complete same-case candidate/baseline coverage
  -> utility measurement and formal Phase-4 review
```

The code dependency is not a merge-order requirement between #193 and #190:
#193 is a pre-launch trust boundary, while #190 is a post-seal input boundary.
#192 is independently mergeable but is consumed after #190 produces a valid
materialized input. The current review disposition is therefore:

- #193: still required; merge-safe after human review before a fresh launch;
- #190: still required; leave draft until the current generation and its
  evidence provenance are terminally reviewed;
- #192: still required; merge-safe after human review before causal
  regeneration;
- #188: preserve as evidence history for the quarantined TTM path; do not merge;
- #189: preserve as evidence history because the protected original Chronos
  process was launched from that exact worker lineage; do not merge or close
  before its evidence is sealed.
