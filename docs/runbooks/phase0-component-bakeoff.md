# Phase-0 component bake-off evidence

This runbook describes the bounded local component drill. It measures the
repository's guarded seams without starting a provider, scheduler service,
browser, archive remote, or venue. It uses no credentials, network calls,
paper orders, or live-capital authority.

## Run the drill

From the repository root:

```bash
uv run python scripts/run_phase0_component_bakeoff.py \
  --output artifacts/phase0/component-bakeoff
```

Each invocation creates a new immutable report directory and atomically updates
`latest.json` as a pointer. The report includes dependency availability,
resource samples, stable probe hashes, and the explicit Phase-0 decision state.

The local probes cover:

- guarded deterministic Nautilus replay and rejection without a recorded gate;
- installed PydanticAI, Prefect, and Hamilton adapter seams using an in-memory
  synthetic gate and injected deterministic callables;
- byte-identical Parquet manifests/artifacts across a clean rebuild and a local
  DuckDB read;
- two bounded Hermes coordinator/subagent tasks under the repository's
  read-only sandbox policy; and
- upload, verification, ledger recording, and restore through two independent
  in-memory rclone adapter fixtures.

## Evidence interpretation

`local_probes_passed: true` means the local boundary probes completed. It does
not mean the component is admitted. The synthetic gate exists only to exercise
the runtime injection seam; it is not persisted as a Phase-0 gate. The report
therefore must retain:

- `phase0_gate_decision: "pending"`;
- `phase0_gate_eligible: false` and `phase0_gate_recorded: false`;
- `production_admitted: false` for every component; and
- quarantined status for any unavailable DuckLake, external Hermes, or real
  rclone/provider integration.

Provider-specific catalog/archive setup, remote gateway evidence, selected-model
24-hour stability, Phase-7 paper soak, and Phase-10 human approval remain
separate gates. A changed dependency, source tree, or component boundary
requires a fresh run; an existing report must never be edited into a pass.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
