# Roadmap execution matrix — AdvisorAI V3 completion (2026-11-12)

Machine-readable queue for the Integrated Alpha mission. Status values:
IMPLEMENTED / PARTIAL / MISSING / FAILING / EXTERNALLY BLOCKED.
Operational admission is tracked separately and never implied by implementation.

| # | Phase / work package | Required behavior + acceptance | Implementing files | Tests | Status | Admission |
|---|---|---|---|---|---|---|
| A1 | Session lifecycle records | SessionStart/Checkpoint/End/Recovery/DataGap/RuntimeLease immutable records | `src/advisorai/session/records.py` | `tests/session/test_lifecycle.py` | MISSING | not admitted |
| A2 | Session supervisor | start→validate→recover→reconcile→gaps→snapshot→operate→checkpoint→stop→restart; single-owner lock; crash classification; idempotent recovery; lease cleanup | `src/advisorai/session/supervisor.py` | `tests/session/test_lifecycle.py` | MISSING | not admitted |
| B1 | Decision Model port | `DecisionModelPort`, `DecisionProposal` (LONG/SHORT/FLAT/ABSTAIN), `DecisionModelArtifact`, calibration/confidence/expiry/fallback/ABSTAIN, no authority | `src/advisorai/decision/` | `tests/decision/` | MISSING | not admitted |
| B2 | Paper chain wiring | proposal → Portfolio Constructor → RiskKernel → OMS → venue → reconciliation → TCA/attribution → outcome memory, via validated contracts | `src/advisorai/api/service.py`, `src/advisorai/runtime/paper.py` | `tests/runtime/`, `tests/agents/` | PARTIAL | not admitted |
| B3 | Offline deterministic e2e fixture | clean start → paper operate → stop → restart with no duplicate orders, full lineage | `tests/session/test_integrated_paper_session.py` | same | MISSING | not admitted |
| C1 | Local typed Decision Model reference | deterministic reference adapter behind the port; lazy load; unqualified model → safe fallback/no-trade | `src/advisorai/decision/` | `tests/decision/` | MISSING | not admitted |
| D1 | Outcome/attribution linkage | DecisionRecord → OutcomeResolution → TrainingExample lineage, horizons enforced | `src/advisorai/learning/paper.py` | `tests/learning/` | PARTIAL | not admitted |
| E1 | Research Brain records | PaperCard/HypothesisCard/CandidateFactor/ExperimentArtifact/ValidationReport/PromotionDecision + registries | `src/advisorai/research/cards.py` | `tests/research/test_cards.py` | MISSING | not admitted |
| E2 | Bounded research jobs | cancellable Scout/Librarian/Miner/Engineer/Conductor/Red Team jobs, one GPU lease | existing + new | `tests/research/` | PARTIAL | not admitted |
| F1 | Hermes/capability lifecycle | quarantine → review → active-read-only; trust classes; sandbox fail-closed | `src/advisorai/capabilities/` | `tests/capabilities/` | PARTIAL | not admitted |
| G1 | Dashboard real-state wiring | session/ledger/health/mission/risk/order/experiment/outcome projections; synthetic mode separated | `src/advisorai/api/dashboard.py`, `dashboard/src/` | `tests/api/test_dashboard.py` | PARTIAL | not admitted |
| V1 | Phase 7 runner semantics | `verify_acceptance.py` Phase 7 = Integrated Paper Session Validation, aligned coverage | `scripts/verify_acceptance.py` | runner | FAILING (stale label) | n/a |

P0 order: A1 → A2 → B1 → B2 → B3 → C1 → V1 → D1 → E1 → E2 → F1 → G1.
P1/P2 (challengers TimesFM3/TTM-R3/Kronos-base, optional providers, UI polish):
EXTERNALLY BLOCKED or deferred by roadmap; must not block core.
