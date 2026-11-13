# Autonomous development backlog

Implementation status is separate from operational admission. External
evidence or human approval never substitutes for unfinished code.

| ID | Work package | Implementation status | Local evidence | Next action |
|---|---|---|---|---|
| A-runtime | Long-lived owner app, durable lock, scheduled local paper runtime, graceful stop, recovery, typed adapter and outcome composition, durable application backup/restore | IMPLEMENTED for the verified local owner lifecycle and isolated owner-database backup/restore/rollback | `tests/session/test_app_entry.py`, `tests/session/test_state_backup.py`, `tests/session/test_application_adapters.py`, `tests/session/test_lifecycle.py`, provider owner-adapter/profile suites, `tests/session/test_quote_aware_outcomes.py`, and `tests/session/test_provider_quality.py` | Maintain current-state verification; keep Phase 10 closed |
| A-reconcile | Latest authoritative order state, current account/open-order state, current watermark/PIT and gap coverage checks | IMPLEMENTED | `tests/session/test_lifecycle.py`, runtime reconciliation and subprocess lifecycle tests | Maintain fail-closed behavior |
| B-authority | Proposal-bound targets and decision/model no-authority boundary | IMPLEMENTED | `tests/decision`, `tests/api` | Maintain |
| B-paper | RiskKernel/OMS/venue/reconciliation/fill/outcome chain | IMPLEMENTED for local simulator and approved adapter ports | `tests/execution`, `tests/runtime`, `tests/session/test_app_entry.py` | Preserve provider-specific admission records; do not add live authority |
| E-registry | Research artifacts, lineage, SQLite registry, API inspection, dashboard views, and reviewed-source intake | IMPLEMENTED: bounded arXiv/OpenAlex/Crossref/GitHub/SEC/FRED metadata Scout; arXiv, OpenAlex, Crossref, and GitHub content use require first-party exact-version evidence plus bounded document hashes (GitHub: exact-tag source archives from codeload, root LICENSE/COPYING SPDX evidence, release binaries refused) | `tests/research/test_source_policy.py`, `tests/research/test_scout.py`, `tests/research`, `tests/api`, dashboard build | Keep SEC/FRED citation-only until source-specific use review; admit new providers one scope at a time |
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
