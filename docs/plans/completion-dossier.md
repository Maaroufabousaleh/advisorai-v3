# Final completion dossier — AdvisorAI V3 Integrated Alpha (PR #211)

Branch: `codex/roadmap-completion-20261112`. Base: main @ `9d98ec1`.
Pre-change suite: **1213 passed**. Post-change suite: **1229 passed**.
Acceptance runner: **11/11 phases passed**.
No merge to main. Phase 10 sealed. No live orders, credentials, or approvals.

## 1. P0 corrections implemented and verified (current HEAD)

| Finding | Implementation | Verification |
|---|---|---|
| Proposal authority boundary | `build_decision` derives executable targets from the validated proposal (identity/expiry/evidence/stance); caller targets never flow through; combined evidence+proposal effective gate; LONG/SHORT only via validated stance | `tests/decision/test_authority_boundary.py`: smuggle/mismatch/expired/failure → abstain + no-trade + risk rejection |
| Crash detection | `_session_history` + `_classify_history`: any unmatched `session_started` is a crash, even with an older clean end | `test_unmatched_start_is_crash_despite_older_clean_end` |
| Real recovery hooks | `SessionRecoveryChecks` (ledger integrity, order reconciliation, source freshness, PIT rebuild) gate NORMAL/RECOVERY/ABSTAIN; first start passes honestly on empty history | gap/without-checkpoint restarts enter recovery/abstain |
| Real stop verification | `SessionStopChecks` force UNCLEAN + `unfinished_work` entries on any failed check; `leases_released` reflects registry truth; dead-PID leases refused at acquire | `test_live_worker_blocks_clean_stop`, `test_dead_pid_lease_is_refused` |
| Startup wiring | `python -m advisorai.app start/stop/status` — supervised session distinct from dashboard UI | `tests/session/test_app_entry.py` + CLI smoke |
| Strong e2e | Nonzero LONG order: CREATED→RISK_APPROVED→ROUTED→ACKNOWLEDGED→FILLED→RECONCILED with exact assertions; ambiguous-crash recovery without duplicates; outcome linkage by ID | `tests/session/test_strong_order_lifecycle.py` (2 passed) |
| Per-session projections | `_project_sessions` scopes checkpoints/gaps/leases per session ID; ABSTAIN tone | `tests/api/test_session_projections.py` |
| Verified identities | checkpoint hash over real state (mismatch refused); code hash = git HEAD + tree digest; config hash = canonical configs tree | smoke-tested via CLI |
| TRADE_FAST policy | Documented: 2-role budget can never satisfy the 3-family gate → abstains by design; nonzero path uses STANDARD budget | covered in both e2e modules |

## 2. P1 research infrastructure implemented and verified

- `ResearchRegistry` (SQLite WAL, immutable, lineage, chain walk, retire): tested.
- Factor DSL (restricted ops, caps, canonical hash, duplicates, past-only eval): tested.
- `ResearchCoordinator` (bounded 1-2 workers, budgets, cancel/checkpoint): tested.
- Full paper→hypothesis→factor→experiment→validation→promotion workflow: tested.
- Leaky-candidate rejection, retained: tested.
- 28/28 research tests pass.

## 3. Tested HEAD and commands

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q`: **1253 passed** (1229 + 24 new P0/P1 tests), 48 warnings.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py`: **11/11 phases passed** (Phase 7: 41 passed incl. strong order lifecycle, per-session projections, authority suites).
- `uv run ruff check .`: clean. `uv lock --check`: clean.
- `npm run build --prefix dashboard`: success.
- `python -m advisorai.app start/stop/status`: smoke-tested end to end.
- Dashboard reads the same ledgers; session/research projections verified by test.

## 4. E2E scenarios proven (deterministic fixtures, exact states)

1. Clean start → operate → stop → restart, no duplicate orders.
2. Nonzero LONG order full lifecycle with exact per-step states + reconciliation clean.
3. Crash with open/ambiguous order → recovery, reconcile, no duplicate submission.
4. Evidence-gate failure → abstain, zero orders; caller quantity smuggling → no-trade.
5. Invalid/missing/stale/mismatched proposals → fail closed + risk rejection.
6. Unbackfilled gaps / missing checkpoints → recovery/abstain, never clean.
7. Outcome attaches only after horizon close; immutable replay.
8. Research: paper→promotion chain + leaky rejection, retained.

## 5. Resources

New P0/P1 modules: session/decision/api/research suites run in seconds, no
GPU, no network. Full suite ~4min. One global GPU lease preserved (reference
adapter is CPU-deterministic). No parallel GPU work.

## 6. Security review

- No credentials/tokens/keys/URLs added; authority-denial docstrings only.
- Session lock via fcntl fails closed on non-POSIX; PID liveness via signal 0.
- Dashboard changes are read projections; no new command endpoints; CSRF/step-up/idempotency untouched.
- DSL forbids eval/imports/I/O/future refs by construction; sandbox fail-closed preserved.
- Pre-push: rerun secret scan + outgoing diff review per AGENTS.md.

## 7. Launch / operate / stop / recover

See `docs/runbooks/session-operations.md`. Application entry:
`python -m advisorai.app start --reason ...` → supervised session with
verified recovery; `stop --outcome clean`; `status` for history audit.
The dashboard launcher (`scripts/launch_dashboard.sh`) starts ONLY the UI +
API inspection surface — it does not start the trading system.

## 8. Remaining external actions (owner gates)

1. Review the outgoing commit range + PR #211 diff; push; keep unmerged.
2. Human gates unchanged: Phase 10 approval, model/source admission, live credentials.
3. Prospective-duration evidence (Levels 5-7), unattested sandbox hardware, challenger qualification: externally blocked, isolated from core.
