# Autonomous development backlog — machine-readable work queue

Status values: IMPLEMENTED / PARTIAL / MISSING / EXTERNALLY BLOCKED.
Admission is tracked separately and never implied by implementation.

| ID | Work package | Owner | Files | Tests | Status | Next action |
|---|---|---|---|---|---|---|
| A-runtime | App entry + supervision | session | `src/advisorai/app.py`, `src/advisorai/session/` | `tests/session/` | IMPLEMENTED | maintain |
| D-authority | Proposal-bound targets | api | `src/advisorai/api/service.py` | `tests/decision/test_authority_boundary.py` | IMPLEMENTED | maintain |
| E-registry | Research registry | research | `src/advisorai/research/registry.py` | `tests/research/test_registry_dsl_jobs.py` | IMPLEMENTED | expose via API |
| E-inspect | Registry inspection API + UI | api/dashboard | `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/api/test_research_inspection.py` (3 passed) | IMPLEMENTED | VERIFIED |
| E-retrieval | FTS retrieval over registry | research | `src/advisorai/research/retrieval.py` | `tests/research/test_retrieval.py` (3 passed) | IMPLEMENTED | VERIFIED |
| I-research-ui | Research browser views | dashboard | `dashboard/src/` | build | IMPLEMENTED | VERIFIED |
| G-replay | Nautilus replay qualification | execution | `src/advisorai/execution/nautilus.py` | Phase 0 gate | EXTERNALLY BLOCKED (production) / IMPLEMENTED (comparison) | pinned runtime still needs Phase 0 gate; boundary semantics proven by `tests/execution/test_cross_engine_replay.py` (2 passed) |
| H-sandbox | Kata/gVisor attestation | capabilities | `src/advisorai/capabilities/sandbox.py` | host tests | MEASURED (boundary holds on this host; formal admission separate) | hardware present, probe `kata_boundary_measured: true` (PR #207 remains draft) |
| B-sources | New source adapters | collectors | per-source | qualification | EXTERNALLY BLOCKED | needs rights + review per source |
| C-models | Challenger qualification | models/research | `challengers/` | prospective | EXTERNALLY BLOCKED | needs elapsed market time |
| J-live | Phase 10 approval | governance | `docs/plans/phase-10*` | human gate | EXTERNALLY BLOCKED | owner decision, never autonomous |

| I-portfolio order views | paper orders/fills API + dashboard panel | `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/api/test_paper_orders.py` (5 passed incl. performance) | IMPLEMENTED | VERIFIED |
| I-traceability chain | decision→fill→outcome joined view + mission panel | `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/api/test_traceability.py` (2 passed) | IMPLEMENTED | VERIFIED |
| J-tamper-evidence | hash-chained ledger audit + endpoint + panel | `src/advisorai/ledger/audit.py`, `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/ledger/test_audit.py`, `tests/api/test_traceability.py` | IMPLEMENTED | VERIFIED (deletion/reorder/mutation detection) |
| F-monitor | paper performance monitor + panel | `src/advisorai/learning/monitor.py`, `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/learning/test_monitor.py`, `tests/api/test_paper_orders.py` | IMPLEMENTED | VERIFIED (slices, comparison, expiry flags) |

| H-kata probe | Kata adversarial boundary probe, live host run | `scripts/probe_kata_adversarial.py` (read-only `scripts/probe_kata_sandbox.py` untouched) | `tests/capabilities/test_kata_sandbox_probe.py` (4 passed) + live run `kata_boundary_measured: true` | IMPLEMENTED | VERIFIED (boundary holds); formal Phase 8 admission remains separate review (EXTERNALLY BLOCKED) |
| J-credential audit | static import scan + deny-list hardening | `src/advisorai/contracts/core.py`, `tests/security/test_credential_scopes.py` | audit tests (4) | IMPLEMENTED | VERIFIED (plural-credential + withdraw gaps closed) |
| I-strategy comparison | baseline vs challenger on shared slices + dashboard panel | `src/advisorai/learning/monitor.py`, `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/learning/test_monitor.py`, `tests/api/test_paper_orders.py` | IMPLEMENTED | VERIFIED (comparison, no-trade baseline, expiry flags) |
