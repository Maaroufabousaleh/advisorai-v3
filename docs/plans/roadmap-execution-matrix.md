# Roadmap execution matrix — AdvisorAI V3 completion (updated 2026-11-12, PR #211)

Machine-readable queue for the Integrated Alpha mission. Status values:
IMPLEMENTED / PARTIAL / MISSING / FAILING / EXTERNALLY BLOCKED.
Columns separate code implementation, end-to-end integration, test
verification, operational admission, and external blockers; no phase is
complete merely because one component passed.

| # | Phase / work package | Required behavior + acceptance | Implementing files | Tests | Code | Integrated | Verified | Admission / blocker |
|---|---|---|---|---|---|---|---|---|
| A1 | Session lifecycle records | SessionStart/Checkpoint/End/Recovery/DataGap/RuntimeLease immutable records | `src/advisorai/session/records.py` | `tests/session/test_lifecycle.py` | IMPLEMENTED | yes (supervisor + app entry) | yes (10 lifecycle tests) | not admitted |
| A2 | Session supervisor | full-history crash detection; verified recovery/stop checks; single-owner lock; per-session records | `src/advisorai/session/supervisor.py` | `tests/session/test_lifecycle.py` | IMPLEMENTED | yes (`python -m advisorai.app start/stop/status`) | yes (unmatched-start, failed-check, leak tests) | not admitted |
| A3 | Application entry points | supervised start/operate/stop/recover CLI distinct from dashboard UI | `src/advisorai/app.py` | `tests/session/test_app_entry.py` | IMPLEMENTED | yes | yes (start/stop/status + orphan detection) | not admitted |
| B1 | Decision Model port + authority boundary | Port, Proposal (LONG/SHORT/FLAT/ABSTAIN), proposal-bound targets, effective-gate fail-closed, no authority | `src/advisorai/decision/`, `src/advisorai/api/service.py` | `tests/decision/test_authority_boundary.py` | IMPLEMENTED | yes (service enforces identity/expiry/evidence match) | yes (smuggle/mismatch/expired/failure tests) | not admitted |
| B2 | Paper chain wiring | proposal → Portfolio Constructor → RiskKernel → OMS → venue → reconciliation → TCA/attribution → outcome memory | `src/advisorai/api/service.py`, `src/advisorai/runtime/paper.py` | `tests/runtime/`, `tests/session/test_strong_order_lifecycle.py` | IMPLEMENTED | yes (strong e2e) | yes (exact-state order lifecycle) | not admitted |
| B3 | Offline deterministic e2e fixture | clean start → paper operate → stop → restart with no duplicate orders, full lineage | `tests/session/test_integrated_paper_session.py` | same | IMPLEMENTED | yes | yes | not admitted |
| B4 | Strong order/fill/reconcile e2e | nonzero LONG order through FILL→RECONCILED; ambiguous-crash recovery without duplicates; outcome linkage by ID | `tests/session/test_strong_order_lifecycle.py` | same | IMPLEMENTED | yes | yes | not admitted |
| C1 | Local typed Decision Model reference | deterministic reference adapter (FLAT default; LONG/SHORT test stance); unqualified model → ABSTAIN/no-trade | `src/advisorai/decision/` | `tests/decision/` | IMPLEMENTED | yes | yes | not admitted (reference only) |
| D1 | Outcome/attribution linkage | DecisionRecord → outcome after horizon; immutable replay; reconciliation-gated | `src/advisorai/learning/paper.py` | `tests/session/test_outcome_linkage.py`, `tests/learning/` | IMPLEMENTED | yes (strong e2e links IDs) | yes | not admitted |
| E1 | Research Brain records + registry | six artifacts + SQLite WAL registry, lineage links, chain walk, retire-never-delete | `src/advisorai/research/cards.py`, `src/advisorai/research/registry.py` | `tests/research/test_registry_dsl_jobs.py` | IMPLEMENTED | yes | yes | not admitted |
| E2 | Factor DSL | restricted operators, depth/node caps, canonical hash, duplicate detection, past-only evaluation | `src/advisorai/research/factors.py` | same | IMPLEMENTED | yes (brain workflow) | yes (forbidden-op + leak tests) | not admitted |
| E3 | Bounded research jobs | coordinator, 1-2 workers, budgets, cancel/checkpoint, ledger events | `src/advisorai/research/jobs.py` | same | IMPLEMENTED | yes | yes | not admitted |
| E4 | Paper→approval workflow | Scout→hypothesis→factor→experiment→validation→promotion + leaky-candidate rejection, retained | `tests/research/test_brain_workflow.py` | same | IMPLEMENTED | yes | yes | not admitted (paper-shadow scope only) |
| F1 | Hermes/capability lifecycle | quarantine → review → active-read-only; trust classes; sandbox fail-closed | `src/advisorai/capabilities/` | `tests/capabilities/` | PARTIAL (pre-existing) | partial | yes (existing suites) | Kata unattested → fail-closed (PR #207, EXTERNALLY BLOCKED hardware) |
| G1 | Dashboard real-state wiring | per-session projections + research activity; synthetic mode separated; session UI panel | `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/api/test_session_projections.py` | IMPLEMENTED | yes (ledger-backed overview) | yes | not admitted |
| V1 | Phase 7 runner semantics | `verify_acceptance.py` Phase 7 = Integrated Paper Session Validation | `scripts/verify_acceptance.py` | runner | IMPLEMENTED | yes | yes (11/11 phases) | n/a |

Deferred / externally blocked (must not block core): TimesFM 3 / TTM-R3 /
Kronos-base challengers (quarantined, need exact qualification + prospective
evidence); real provider credentials; unattested sandbox hardware; Phase 10
human approval; multi-week prospective paper operation (Level 5-7).
