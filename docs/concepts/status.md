# Project status and maturity

AdvisorAI V3 contains a substantial set of contracts, local data/ledger primitives, paper execution controls, optional adapters, acceptance fixtures, and an operator console. It is not a turnkey live-trading deployment.

## Capability status

| Area | Status | What that means |
| --- | --- | --- |
| Typed contracts, config loaders, local Parquet lake, SQLite WAL ledgers | Implemented | Available in the base Python environment and covered by repository tests |
| Point-in-time snapshots and evidence gate | Implemented | Enforced by `SnapshotBuilder`, `EvidenceGraph`, and the mission service |
| Paper runtime, risk kernel, OMS, kill switch, reconciliation | Implemented | The deterministic transition path is paper/testnet constrained |
| React/FastAPI operator console | Implemented locally | The dashboard can show a clearly labelled synthetic fixture or configured ledger projection |
| Native venue adapters and remote/provider transports | Optional / experimental | Source seams and qualification scripts exist; availability does not imply admission |
| Model gateway and model candidates | Optional / phase-gated | Routes and adapters are policy checked; no model is silently treated as authoritative |
| PydanticAI/Prefect/Hamilton/LiteLLM/Nautilus runtime wrappers | Optional / phase-gated | Install extras only for the relevant qualification or development workflow |
| Multi-process service topology | Target architecture | `ServiceRegistry` is an ownership/dependency manifest, not a supervisor |
| Session lifecycle / crash recovery | Planned target | Start/stop/checkpoint/reconciliation/data-gap lifecycle is not yet represented as a complete runtime |
| `DecisionModelPort` / typed Decision Model proposal | Planned target | Architecture boundary is specified; local adapter and admission are not claimed as complete |
| Live capital operation | Not enabled by the quickstart | Requires external timed evidence, readiness, authorization, and phase admission |
| Production deployment, certificates, monitoring, and incident response | Not supplied as a turnkey package | Operators must provide and review those controls outside this checkout |

## How to read the phase documents

The roadmap separates implementation readiness from operational admission. A passing unit or acceptance test does not create an admission record, human approval, provider credential, or live authorization. Gate records can also expire and must be re-evaluated. A pending earlier operational gate does not prevent safe, typed, quarantined implementation of later components.

The intended normal product is a session-oriented workstation used for about
5–8 hours when the owner starts it. Recovery, paper/testnet reconciliation,
permitted data catch-up, PIT correctness, and clean shutdown are first-class
future requirements. Historical 24-hour and 60-day records remain unchanged;
continuous uptime is no longer the primary admission criterion for the
workstation. Optional always-on deployments have a separate profile. See the
[current phase plan](../plans/README.md) and
[session lifecycle plan](../plans/session-oriented-runtime.md).

The [phase status dossier](../plans/status.md) is the current evidence record and may change as work proceeds. The [gate matrix](../plans/gate-matrix.md) explains prerequisites. The architecture dossier describes a target end state and staged build plan; this page is the concise implementation-oriented summary.

## Explicitly absent or incomplete

- There is no tracked `LICENSE` file or documented redistribution license in this checkout.
- There is no repository-provided Dockerfile, compose stack, deployment installer, or process supervisor.
- There is no general-purpose package console entry point; commands are module or script based.
- The dashboard screenshots in this repository use the local synthetic fixture and must not be read as market performance, live account state, or a completed deployment.
- A public security contact, support address, governance policy, and release policy are not defined in the repository.

These omissions are documented rather than filled with invented promises. See [Security](../../SECURITY.md), [Contributing](../../CONTRIBUTING.md), and [Installation](../getting-started/installation.md).
