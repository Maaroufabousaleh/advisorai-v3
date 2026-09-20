# Chronos-2-small runtime stability qualification

This runbook describes the separate 24-hour stability profile for the
already admitted `autogluon/chronos-2-small` runtime. It does not widen the
historical `scripts/run_model_stability.py` role set (`ttm-r2`,
`finsentiment-deberta-v3`, and `finbert-minilm`) and it does not create
Phase-4 prospective forecasts.

## Review boundary

The runner is an implementation-only extension stacked on the Chronos
startup/preflight repair in PR #206. The admission and qualification evidence
remain inputs; this change does not rewrite either artifact. After PR #206 is
reviewed and a release identity is frozen, a new run must bind its final
repository commit and the unchanged Chronos admission. If a worker,
checkpoint, interpreter, dependency lock, CUDA/runtime, or qualification
artifact changes, the run fails closed and a fresh runtime admission is
required.

The runner uses the existing `run_runtime_qualification()` machinery and
`GpuModelLease`. It does not alter the current Phase-4 Chronos contract, r4
evidence, r5 preregistration, or any execution path.

## Contract

The profile is `chronos-2-small-24h-v1`:

- at least 24 actual elapsed hours;
- a default five-minute cadence;
- a real terminal cycle at or after the configured duration boundary;
- no failed or skipped cycle can contribute to a pass;
- exact admission, checkpoint/config, Python launcher and resolved binary,
  pyvenv, dependency manifest/lock, worker/runner, repository, and
  qualification-evidence identities on every cycle;
- offline model load, repeated inference, batch inference, finite
  `forecast[30]` output, and the admitted deterministic repeatability policy;
- RSS <= 4096 MiB, VRAM <= 6144 MiB, residual RSS <= 256 MiB, residual VRAM
  <= 256 MiB, and one GPU model family at a time;
- worker termination and GPU lease cleanup after every cycle;
- no credentials, order writes, execution authority, model download, or
  prospective Phase-4 prediction/outcome writes.

The cycle file is an fsync'd, append-only SHA-256 chain. `config.json` and a
terminal `summary.json` are immutable. A changed identity, worker crash,
resource violation, cleanup failure, interrupted run, or hash-chain problem
cannot be resumed under the old identity or reported as a pass.

## Prerequisites

Run only from a clean reviewed checkout with:

1. the exact Chronos local admission JSON;
2. the exact offline qualification evidence JSON;
3. the pinned model cache already present and outside the repository;
4. the qualified CUDA/runtime environment and no competing GPU process;
5. a new, empty evidence directory outside r4 and any prospective Phase-4
   evidence root.

No credentials are required. Do not install packages or download model files
as part of the run.

## Invocation

The command is designed to be independently supervised. The supervisor must
retain the exact command and process identity, and must not reuse a run
directory containing another config or chain:

```bash
setsid uv run python scripts/run_chronos_runtime_stability.py \
  --admission /absolute/path/to/local-admission.json \
  --qualification-evidence /absolute/path/to/chronos-2-small.json \
  --repository-root /absolute/path/to/advisorai-v3 \
  --run-directory /absolute/path/to/chronos-stability/<unique-run-id> \
  > /absolute/path/to/chronos-stability/<unique-run-id>.stdout 2>&1 < /dev/null &
```

The default duration is 24 hours and the default cadence is 300 seconds.
There is no short-duration production flag. A Codex/session exit must not be
used as evidence that the run ended or passed.

## Evidence layout and terminal validation

The run directory contains:

- `config.json`: immutable identity, resource, timing, dataset, and security
  binding;
- `cycles.jsonl`: append-only hash-chained cycle evidence;
- `status.json`: atomically updated operational state and process identity;
- `summary.json`: immutable runner summary when a cycle sequence terminates;
- `runner.lock`: an operational single-supervisor lock.

After the supervisor has terminated cleanly at the boundary, run the
independent terminal validator:

```bash
uv run python scripts/validate_chronos_runtime_stability.py \
  --run-directory /absolute/path/to/chronos-stability/<unique-run-id> \
  --output /absolute/path/to/chronos-stability/<unique-run-id>-terminal-validation.json
```

The validator rechecks the chain, summary, terminal timing, current admission
and runtime identity, source identity, process state, and security flags. It
writes an immutable report and `.sha256` sidecar. Only `state: PASS` is
qualification evidence; `PENDING_STABILITY` and `FAIL` require human review
and cannot be promoted.

## Failure handling

If a cycle fails, the runner records the sanitized failure and stops. If the
identity changes, the runner records `failed_identity_drift` and stops. If the
supervisor is interrupted, status is `interrupted`; the evidence is retained
and is never concatenated with a later run. A new unique run and, where the
admission contract requires it, new runtime qualification evidence are
needed. Do not backfill cycles, shorten the window, or substitute a model.

The current implementation does not start the 24-hour run. It is reviewed
and tested independently first. Successful startup smoke or eventual Kata,
TimesFM, or other challenger work has no bearing on this Chronos stability
decision.
