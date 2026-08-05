# AdvisorAI V3

AdvisorAI V3 is being built as a federated research and paper-trading system with
one deterministic safety/execution spine. The full implementation authority is
[the architecture plan](advisorai-federated-multi-agent-quant-architecture-v3.md).

The repository now contains executable, gate-controlled implementations for
Phases 0–10. The deterministic foundation includes immutable contracts and PIT data,
manifest-managed Parquet, SQLite WAL ledgers, resource/config/observability
controls, a paper/testnet execution core, baseline forecasting, typed evidence
fusion, institutional risk/research checks, soak/recovery records, capability
isolation, controlled expansion and a closed limited-live guard.

External admission gates remain explicit: no live venue credentials or live order
submission are enabled, named model/runtime bake-offs are still quarantined until
measured, and the 24-hour Phase 0, 60-day Phase 7 and Phase 10 approval evidence
must be supplied before those gates can pass.

## Local development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

Run the local acceptance evidence in the same gated order as the architecture
plan. Each phase uses a fresh pytest process so the optional data/execution
runtimes do not accumulate memory on the target laptop:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py --phase 2
```

This command proves local executable controls. It does not manufacture the
required 24-hour Phase 0, 60-day Phase 7, or explicit human Phase 10 evidence.

Implementation sequencing and non-negotiable phase gates are in
[docs/plans](docs/plans/README.md).
