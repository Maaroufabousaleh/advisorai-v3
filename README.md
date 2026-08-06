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

## Local secrets

The ignored [`secrets.env`](secrets.env) file is the single blank-valued
credential template for gateway providers, optional data sources, paper/testnet
venue adapters, archive remotes, checkpoint registries, and orchestration
services. Fill only the entries for components that have passed their phase gate,
then load it in the shell that starts the process:

```bash
set -a
source ./secrets.env
set +a
```

The current implementation has no live provider or venue connection and does not
read provider credentials yet; external transports are injected for tests. Keep
live broker credentials out of this file until the explicit Phase 10 approval
gate is satisfied. On WSL, prefer copying filled secrets to a Linux-filesystem
path such as `~/.config/advisorai-v3/secrets.env` rather than storing values on a
shared `/mnt/c` mount.

## Dashboard status

The operator console is in `dashboard/` with a typed optional API at
`src/advisorai/api/dashboard.py`. It reads explicit projections of the existing
deterministic services and issues only guarded paper-control commands; it does
not own account state, risk, OMS, ledgers, credentials, or live activation.

Run it locally with:

```bash
./scripts/launch_dashboard.sh
```

The launcher starts the API and Vite console together, waits for API health, and
cleans up both processes on Ctrl-C. Use protected authentication with:

```bash
./scripts/launch_dashboard.sh --protected
```

Protected-mode setup, password/MFA bootstrap, TLS, and LAN deployment guidance
are in [dashboard/README.md](dashboard/README.md). Until a live projection is
connected, synthetic paper values are deliberately labelled in the interface.
