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
| Live capital operation | Not enabled by the quickstart | Requires external timed evidence, readiness, authorization, and phase admission |
| Production deployment, certificates, monitoring, and incident response | Not supplied as a turnkey package | Operators must provide and review those controls outside this checkout |

## How to read the phase documents

The repository uses phase gates to separate implementation from operational admission. A passing unit or acceptance test does not create a timed soak record, a human approval, a provider credential, or a live authorization. Gate records can also expire and must be re-evaluated.

The [phase status dossier](../plans/status.md) is the current evidence record and may change as work proceeds. The [gate matrix](../plans/gate-matrix.md) explains prerequisites. The architecture dossier describes a target end state and staged build plan; this page is the concise implementation-oriented summary.

## Explicitly absent or incomplete

- There is no tracked `LICENSE` file or documented redistribution license in this checkout.
- There is no repository-provided Dockerfile, compose stack, deployment installer, or process supervisor.
- There is no general-purpose package console entry point; commands are module or script based.
- The dashboard screenshots in this repository use the local synthetic fixture and must not be read as market performance, live account state, or a completed deployment.
- A public security contact, support address, governance policy, and release policy are not defined in the repository.

These omissions are documented rather than filled with invented promises. See [Security](../../SECURITY.md), [Contributing](../../CONTRIBUTING.md), and [Installation](../getting-started/installation.md).
