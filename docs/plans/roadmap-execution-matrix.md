# Roadmap implementation audit — AdvisorAI V3

Audited 2026-11-12 against code and tests on branch
`codex/roadmap-completion-20261112`, starting from base `195c0df`; the
implementation updates described below are part of this audited state. PR #211
remains unmerged.

## Status rules

This matrix reports software implementation, local integration, local test
verification, and operational admission separately. `EXTERNALLY BLOCKED` is
reserved for evidence or approval that must come from a provider, elapsed
operation, qualified host, or human owner. It must not describe unfinished
software. A fixture pass proves only the behavior named in its acceptance row;
it does not qualify a model, source, venue, sandbox, or trading scope.

| ID | Software requirement and acceptance | Implementation | Integration and verification | Operational admission / remaining external evidence |
|---|---|---|---|---|
| P0 | Contracts, qualification harnesses, and deterministic model/data comparison boundaries | IMPLEMENTED | `tests/phase0`, `tests/gates`, and contract suites run by `scripts/verify_acceptance.py` | Exact role qualification and time-based stability evidence remain separate; no model authority is implied |
| P1 | Typed safety, PIT, ledger, data, resource, and recovery controls | IMPLEMENTED | Phase 1 suites cover contracts, immutable data, PIT, resources, recovery, memory, and services | Provider rights and operational data availability remain source-specific evidence |
| P2 | RiskKernel → OMS → paper/testnet adapter → current-state reconciliation | IMPLEMENTED | `tests/execution`, `tests/runtime`, integration tests, and Phase 2 acceptance | Venue-specific operation is only admitted for the immutable scope and evidence recorded in `docs/plans/gate-matrix.md` |
| P3 | V3-Core acquisition, source health, raw-first lineage, replay, freshness, and PIT | IMPLEMENTED | `tests/phase3` is included in Phase 3 acceptance | Live continuity, source disagreement, recovery, and provider availability remain measured per source; failed providers are not silently substituted |
| P4 | Baselines, forecast contracts, forward evaluation, cost-aware utility, and challenger decision records | IMPLEMENTED | `tests/models` runs as Phase 4 acceptance | Candidate promotion remains pending exact immutable utility/calibration evidence and review |
| P5 | Evidence Council, typed Decision Model, evidence gates, proposal-bound targets, and no-authority adapter boundary | IMPLEMENTED | `tests/agents`, `tests/api`, and `tests/decision` | Real-source/model evaluation and any adapter qualification are separate admission decisions |
| P6 | Institutional controls, paper order/fill attribution, and outcome linkage by immutable ID | IMPLEMENTED | `tests/institutional`, `tests/learning`, and the strong paper lifecycle test | A real paper fill sample and external attribution remain operational evidence |
| P7 | Owner session lifecycle, current-state recovery, scheduled paper runtime, bounded resources, graceful stop, restart, verified backup/restore/rollback, and reviewed adapter composition | IMPLEMENTED for the local owner runtime, reviewed offline adapter boundaries, measured provider quality, and owner-database backup/restore into an isolated root | `tests/session/test_app_entry.py` proves subprocess start/operate/order/fill/reconciliation/outcome/stop/backup/restore/restart/rollback and rejects concurrent starts/backups. `tests/session/test_state_backup.py` verifies manifests, SQLite integrity, typed ledger/scorecard rows, corruption refusal, private file modes, and no-overwrite restore. Provider bridge/profile, recovery, and quality suites cover their respective boundaries | Real provider/model/venue operation and independent multi-session evidence remain operational admission requirements; implementation does not grant operational admission |
| P8 | Hermes/capability lifecycle, typed candidate export, default-deny broker, and bounded sandbox boundary | IMPLEMENTED for the local fixture workflow and tested backends | `tests/capabilities` and `scripts/run_phase8_capability_evidence.py` verify reproducible active-read evidence; the fixture remains unadmitted | Formal host/native isolation attestation and Phase 8 review remain separate; this is not a reason to leave local lifecycle software unimplemented |
| P9 | Research Brain records, Factor DSL, cancellable bounded jobs, outcome memory, and controlled expansion | IMPLEMENTED for quarantined local workflows | `tests/research`, `tests/expansion`, concurrency/cancellation/budget/restart/checkpoint tests, and Phase 9 acceptance | New providers, capabilities, strategies, assets, and models require their own admission evidence |
| P10 | Readiness evaluation, kill/rollback boundaries, and AI-offline controls | IMPLEMENTED as a closed guard | `tests/live` verifies refusal paths; Phase 10 acceptance does not activate capital | Human approval is mandatory and remains absent; Phase 10 stays sealed |
| CI | Repeatable automation for Python, phase acceptance, quality/dependencies, and dashboard output | IMPLEMENTED in `.github/workflows/ci.yml` | Four independent jobs install the locked environment, run full pytest and phase acceptance, check Ruff/lock/installed dependencies/licenses, and build the dashboard | GitHub execution is pending the next push/PR event; no operational admission is implied |

## Acceptance details for the recent correctness work

| Area | Explicit acceptance | Evidence |
|---|---|---|
| Owner process | `start` remains alive with its lock held; a concurrent process is refused; the managed scheduler runs cycles; stop verifies and releases resources; restart classifies clean and crashed sessions; an interrupted cycle is retained and later cutoffs continue only after state reconciliation | Real subprocesses in `tests/session/test_app_entry.py`; stale/live lease recovery in `tests/session/test_lifecycle.py` |
| Adapter composition | Data, model, paper-venue, and outcome adapters are injected only through a typed Python composition object; all remain under the same owner lock, resource envelope, OMS/RiskKernel path, and current watermark/PIT/gap/account verifiers; non-paper venue identity is rejected | `tests/session/test_application_adapters.py` runs an injected paper cycle under the supervisor, verifies current-state checks, and asserts the outcome adapter reports forecasts as unknown when no numeric forecast is present |
| Binance owner venue | Only the exact Spot Testnet host/environment and one verified symbol are accepted; provider identities persist in the venue ledger; current orders/fills map to local OMS IDs; non-quote fees, unknown fills, and nonzero out-of-profile balances fail closed | `tests/session/test_binance_spot_owner_adapter.py` uses a fake transport across restart; `tests/integrations/test_binance_spot.py` verifies all nonzero account assets remain visible to owner reconciliation |
| Coinbase owner venue and provider profile | Only the exact Exchange Sandbox host/environment and one exchange-verified product are accepted; Coinbase is currently USD-quote only; provider order/fill identities persist and account positions map to the configured canonical instrument; profile composition requires matching credential, product, base/quote, data, account, and outcome semantics | `tests/session/test_coinbase_exchange_owner_adapter.py`, `tests/session/test_provider_profiles.py`, and `tests/integrations/test_coinbase_exchange.py` exercise identity recovery, fee currency, product verification, and refusal paths offline |
| Quote-aware outcome and provider quality | Every decision fill through the outcome cutoff contributes from its execution price; fees and utility convert to USD through a positive FX rate bound to that snapshot; provider quality records measured HTTP attempt count, failures, latency, data-quality state, snapshot identity, and a SHA-256 measurement hash in durable outcome evidence | `tests/session/test_quote_aware_outcomes.py` covers conversion, late fills, stale evidence rejection, and scorecard restart identity. `tests/session/test_provider_quality.py` covers measured request windows, snapshot binding/idempotency, data-quality handling, and durable outcome serialization |
| Order recovery | Historical `routed` or ambiguous events do not veto a later authoritative terminal state; latest unresolved state does; account/open-order state is queried through a permitted verifier | `tests/session/test_lifecycle.py`, runtime reconciliation tests, and the subprocess flow |
| Recovery watermarks/PIT | Durable records alone are insufficient: non-empty watermarks and PIT snapshots require current-state verifiers; checkpoints belong to the latest session and current PIT record | `tests/session/test_lifecycle.py` and black-box restart |
| Data gaps | An unresolved or uncovered gap fails recovery; a resolved gap passes only when exact source/instrument coverage is confirmed by a current permitted-source verifier | `tests/session/test_lifecycle.py` |
| Research jobs | Parallel workers stay within the one/two-worker cap; cancellation, deadline accounting, restart state, and repeated checkpoint identity are durable and tested | `tests/research/test_registry_dsl_jobs.py` |
| Owner-state backup and restore | A stopped session can create a consistent, hash-verified backup of both owner databases; restore occurs into an empty isolated destination and is typed/SQLite-verified before normal recovery; older backup rollback leaves current and source roots untouched | `tests/session/test_app_entry.py` and `tests/session/test_state_backup.py` |
| Research source intake | Paper and literature intake accepts only HTTPS URLs on reviewed, exact source/path rules; embedded credentials, query tokens, spoofed suffix hosts, unknown licensing for content use, and unreviewed source families fail closed; citation-only metadata remains distinct from content redistribution | PARTIAL software gap: `PaperCard` stores source URL and license text, but `ResearchRegistry.store()` does not yet enforce an allowlist or license-use policy |
| Phase restrictions | Default app path is paper-only and local; no live endpoint, broker credential, or live-capital action is added. Phase 10 remains closed | `tests/session/test_app_entry.py`, `tests/live`, and capability authority tests |

## Next implementation work

The supervised owner now has exact single-symbol Binance Spot Testnet and
Coinbase Exchange Sandbox bridges, provider profiles that bind scoped
credentials and exchange-verified products, snapshot-bound quote conversion,
and provider quality from measured sandbox HTTP request windows. This is
software implementation only; no provider admission or live authority is
implied. Owner-state backup, restore, and rollback now copy both databases
under the durable owner lock, verify content hashes and typed SQLite records,
and restore only into a new root. The next software item is enforcement of
the Research Brain's source allowlist and license-use boundary at registry
ingest. Model qualification, provider credentials, venue evidence, and human
admission do not block that quarantined implementation.

Never infer implementation completion from a green test suite, and never infer
operational admission from implementation completion. Keep model/source/venue
qualification, timed operation, and Phase 10 approval in their authoritative
gate records.
