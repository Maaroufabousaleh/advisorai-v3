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

The optional post-Core [Alpha Team extension](docs/plans/alpha-team-extension.md)
adds a governed research-intake and factor-discovery plane only after its E0
prerequisite. It cannot alter the canonical data, portfolio, risk, OMS, or
execution owners, and it is not currently admitted or implemented.

## Local secrets

The ignored [`secrets.env`](secrets.env) file is the single blank-valued
credential template for gateway providers, optional data sources, paper/testnet
venue adapters, archive remotes, checkpoint registries, and orchestration
services. Fill only the entries for components that have passed their phase
gate. Do not source this master inventory into a shell or process environment:
that would expose unrelated credentials to child processes and third-party
libraries. The transition implementation parses provider credentials through a
typed, allowlisted loader and passes them only to the owning real-data/LLM/paper
transport. It rejects live environments, transfer/withdrawal paths, and
production endpoints. Validate the file without shell execution:

```bash
uv run python scripts/check_transition_config.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env
```

Use this single repo-local ignored inventory; do not duplicate it under another
path. Never put live broker credentials in this transition file.

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
are in [dashboard/README.md](dashboard/README.md). With
`ADVISORAI_DASHBOARD_LEDGER_PATH` set, the API projects local paper/runtime
ledgers and deliberately reports unavailable P&L or exposure values as such;
without a ledger path it uses the clearly-labelled synthetic UI fixture.

## Real API / paper transition

The implementation and operator hand-off are documented in
[the transition plan](docs/plans/real-api-paper-transition.md) and
[the operations runbook](docs/runbooks/real-api-paper-operations.md). Install
the optional WSS client only when explicitly enabling real connector smoke
tests:

```bash
uv sync --extra transition
ADVISORAI_RUN_NETWORK_SMOKE=1 \
  uv run python scripts/smoke_transition_connectors.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env
```

This smoke command is read-only and opt-in. Actual venue choice, endpoint
verification, credentials, continuous supervision, timed soak evidence, and
human gate decisions remain operator responsibilities.

To install every declared optional local runtime/model extra (without enabling
providers, checkpoints, or live venues), use:

```bash
uv sync --group dev --extra runtimes --extra dashboard --extra transition --extra models
```

The model extra installs only the core CPU LightGBM/Transformers dependencies.
External model families run in separately attested environments; the Phase-0
roster and measured role state are recorded in
[`configs/models/phase0_local_roster.json`](configs/models/phase0_local_roster.json).
TTM-R2, TTM-R3, TSPulse, Chronos-2-small, Kronos-mini/small,
ModernFinBERT, FinBERT-MiniLM, and the finance DeBERTa challenger have passed
short offline qualification on the target laptop. The role winners remain
pending 24-hour stability, TabPFN-TS is waiting for user acceptance of gated
upstream access, and no model has trading authority.
The dependency-free hashing embedder is available as a non-authoritative
semantic-recall candidate; FTS5 remains the default memory retrieval path.
