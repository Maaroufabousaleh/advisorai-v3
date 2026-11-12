# Autonomous development backlog

Implementation status is separate from operational admission. External
evidence or human approval never substitutes for unfinished code.

| ID | Work package | Implementation status | Local evidence | Next action |
|---|---|---|---|---|
| A-runtime | Long-lived owner app, durable lock, scheduled local paper runtime, graceful stop, recovery | IMPLEMENTED for local deterministic profile; production adapter composition PARTIAL | `tests/session/test_app_entry.py`, `tests/session/test_lifecycle.py` | Continue with the adapter composition layer; keep local fixture as offline acceptance profile |
| A-reconcile | Latest authoritative order state, current account/open-order state, current watermark/PIT and gap coverage checks | IMPLEMENTED | `tests/session/test_lifecycle.py`, runtime reconciliation and subprocess lifecycle tests | Maintain fail-closed behavior |
| B-authority | Proposal-bound targets and decision/model no-authority boundary | IMPLEMENTED | `tests/decision`, `tests/api` | Maintain |
| B-paper | RiskKernel/OMS/venue/reconciliation/fill/outcome chain | IMPLEMENTED for local simulator and approved adapter ports | `tests/execution`, `tests/runtime`, `tests/session/test_app_entry.py` | Preserve provider-specific admission records; do not add live authority |
| E-registry | Research artifacts, lineage, SQLite registry, API inspection and dashboard views | IMPLEMENTED | `tests/research`, `tests/api`, dashboard build | Maintain |
| E-jobs | Bounded research coordinator, cancellation, budgets, restart, idempotent checkpoints | IMPLEMENTED | `tests/research/test_registry_dsl_jobs.py` | Maintain; retain cooperative worker contract |
| E-retrieval | Full-text retrieval over empirical registry | IMPLEMENTED | `tests/research/test_retrieval.py` | Maintain |
| I-orders | Paper orders/fills API and dashboard panel | IMPLEMENTED | `tests/api/test_paper_orders.py`, dashboard build | Maintain |
| I-traceability | Decision → fill → outcome joined view and mission panel | IMPLEMENTED | `tests/api/test_traceability.py`, dashboard build | Maintain |
| I-audit | Hash-chained ledger audit and tamper evidence | IMPLEMENTED | `tests/ledger/test_audit.py`, `tests/api/test_traceability.py` | Maintain |
| F-monitor | Paper performance monitor and dashboard panel | IMPLEMENTED | `tests/learning/test_monitor.py`, `tests/api/test_paper_orders.py` | Maintain |
| H-capability | Typed capability lifecycle, default-deny broker, local fixture evidence and sandbox backends | IMPLEMENTED for tested local/software boundaries | `tests/capabilities`, `scripts/run_phase8_capability_evidence.py` | Keep operational host/native isolation attestation separate from code status |
| P-CI | Full Python suite, phase acceptance, Ruff, dependency/license checks, dashboard build | IMPLEMENTED in `.github/workflows/ci.yml` | Local verification: 1,298 Python tests, 11 phase suites, Ruff, lock/dependency/license checks, dashboard audit and build passed; hosted run awaits a later push/PR event | Keep CI green |
| P9-admission | New providers, models, strategies, assets, or capabilities | QUARANTINED pending exact scope evidence | Gate matrix and existing tests | Admit one scope only after its own evidence passes |
| P10 | Live capital guard and human approval boundary | Guard IMPLEMENTED; capital admission NOT APPROVED | `tests/live` and governance contracts | Keep sealed until explicit human review |
