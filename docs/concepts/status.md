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
| Technology decision register | Planning-only inventory | External tools and sources are not installed, admitted, or authorized by appearing in the register |

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

## Governance and research PR snapshot

The following GitHub states were checked on 2026-11-11. They are open review
work, not current-main implementation:

| PR | Snapshot state | Scope |
|---|---|---|
| [#196](https://github.com/Maaroufabousaleh/advisorai-v3/pull/196) | OPEN, DRAFT, base main | Governance and live-risk policy; branch-only. |
| [#197](https://github.com/Maaroufabousaleh/advisorai-v3/pull/197) | OPEN, DRAFT, stacked on #196 | Trading scope and authority; branch-only. |
| [#198](https://github.com/Maaroufabousaleh/advisorai-v3/pull/198) | OPEN, DRAFT, stacked on #197 | Human notification and approval; branch-only. |
| [#205](https://github.com/Maaroufabousaleh/advisorai-v3/pull/205) | OPEN, non-draft, base main | Isolated TimesFM 3 challenger; does not change the frozen roster. |
| [#207](https://github.com/Maaroufabousaleh/advisorai-v3/pull/207) | OPEN, DRAFT, base main | Kata backend implementation; runtime unattested and Phase 8 not admitted per PR description. |
| [#208](https://github.com/Maaroufabousaleh/advisorai-v3/pull/208) | OPEN, DRAFT, stacked on a launch-fix branch | Optional Chronos stability profile; not a workstation prerequisite. |
| [#209](https://github.com/Maaroufabousaleh/advisorai-v3/pull/209) | MERGED | Session-oriented roadmap rebaseline. |

The current near-term target remains AdvisorAI Integrated Alpha. Human
governance concepts are included in the target plan, but no draft grants live
capital approval. The [technology decision register](../reference/technology-decision-register.md)
contains the dated source/tool decisions and links to the accepted ADRs.

## Explicitly absent or incomplete

- The tracked root [LICENSE](../../LICENSE) applies the AdvisorAI Proprietary License to original AdvisorAI work. It does not replace third-party notices or data/model rights review.
- There is no repository-provided Dockerfile, compose stack, deployment installer, or process supervisor.
- There is no general-purpose package console entry point; commands are module or script based.
- The dashboard screenshots in this repository use the local synthetic fixture and must not be read as market performance, live account state, or a completed deployment.
- No public security contact or support address is defined. Governance implementation remains in the open draft PRs above. Security and release requirements are documented as a target workstation lifecycle, not as supplied release automation or an incident-response service.

These omissions are documented rather than filled with invented promises. See [Security](../../SECURITY.md), [Contributing](../../CONTRIBUTING.md), and [Installation](../getting-started/installation.md).
