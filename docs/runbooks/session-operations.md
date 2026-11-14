# AdvisorAI V3 — Launch, operate, stop, recover

Target profile: owner-operated workstation, one session at a time, paper/testnet only.

> The dashboard launcher starts ONLY the inspection UI + API. It does not
> start the trading system. The application entry point below starts the
> supervised paper session with verified recovery.

## 1. Launch the supervised application

```bash
uv sync --group dev
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/session tests/decision
ADVISORAI_STATE_DIR=var/advisorai uv run python -m advisorai.app start --reason "owner session"
```

To use a reviewed sandbox venue instead of the local simulator, pass the
supervised venue profile with enforced bounds. Owner shell only (reads the
operator inventory, snapshots the read-only testnet baseline, then starts
under the same lock). The deadline and submission cap are required for any
testnet run: the session stops clean at the deadline and the venue boundary
refuses submissions beyond the cap.

```bash
ADVISORAI_STATE_DIR=var/advisorai-testnet uv run python -m advisorai.app start \
  --reason "owner testnet canary" \
  --venue-profile binance_spot_testnet \
  --scenario qualification --interval-seconds 10 \
  --max-runtime-seconds 120 --max-submissions 1
```

`start` stays in the foreground for the whole owner session. It holds the
durable single-owner lock while one bounded scheduler runs paper cycles. The
default `quiet` profile is a local, no-network reference fixture that abstains
from directional orders. The `qualification` profile is a deterministic local
test fixture that exercises the ordinary evidence, decision, risk, OMS,
simulated venue, reconciliation, and outcome path; it is for integration
qualification, not strategy or source admission.

Expected output includes `classification=...`, `recovery_mode=...`, and
`SESSION READY`. A `recovery` mode means current permitted venue/account state
was reconciled before the scheduler started. `abstain` (exit 2) means the
required current-state checks failed. In another terminal, inspect history
without mutating:
`ADVISORAI_STATE_DIR=var/advisorai uv run python -m advisorai.app status`.

## 1b. Launch the inspection UI (separate surface)

```bash
./scripts/launch_dashboard.sh
```

Open <http://localhost:5173>. The launcher starts the API on `127.0.0.1:8787`
and waits for `/api/v1/health` (`{"status":"ok","environment":"paper_testnet"}`).
The Overview `SESSION LIFECYCLE` panel mirrors the supervised session above.

## 2. Operate (paper only)

- Overview shows the supervised session (`SESSION LIFECYCLE` panel), recovery
  classification, open leases, checkpoints, data gaps, and research activity
  (decisions, outcomes, proposals, abstentions, open problems).
- Guarded commands only: halt/resume paper, mode changes, config
  proposals/rollback, data refresh. Sensitive commands require confirmation
  plus a short-lived step-up token; every command is idempotent and audited.
- The Decision Model proposes; RiskKernel/OMS own execution. Evidence-gate
  failure, missing/stale data, or model failure yields typed ABSTAIN, never
  an order. The kill switch trips on reconciliation mismatch.

## 3. Stop cleanly

Request a graceful stop from another terminal. The owner stops the scheduler,
reconciles paper orders and account state against the permitted venue adapter,
persists its last checkpoint, verifies watermarks, releases worker leases, and
then appends the immutable `SessionEndRecord`:

```bash
ADVISORAI_STATE_DIR=var/advisorai uv run python -m advisorai.app stop --outcome clean
```

Ctrl-C or SIGTERM to the owner process also requests graceful shutdown. The
dashboard launcher is separate; stopping it does not stop the owner process.

## 3b. Back up, restore, and roll back owner state

After the owner has stopped, create a consistent copy of the ledger and
scorecard databases. The command takes the same durable owner lock and refuses
to run while a session is active:

```bash
ADVISORAI_STATE_DIR=var/advisorai uv run python -m advisorai.app backup
```

The backup directory contains only the two SQLite databases and a manifest
with their SHA-256 digests, typed row counts, backup time, and session
code/configuration identity. Files are private to the current OS user. Keep the
backup directory intact; restore and rollback commands never overwrite it.

Restore a verified backup into a new state directory, then start AdvisorAI
against that directory. The restore command validates hashes, SQLite
integrity, ledger events, outcomes, and scorecards before publishing the
directory. The first start still performs normal current-state recovery,
including permitted venue/account/order and current PIT checks:

```bash
uv run python -m advisorai.app restore \
  --source var/advisorai/backups/<backup-id> \
  --destination var/advisorai-restored
ADVISORAI_STATE_DIR=var/advisorai-restored uv run python -m advisorai.app start
```

If `ADVISORAI_LEDGER_PATH` points outside the state directory, set it to the
restored `ledgers.sqlite3` path as well. Roll back by restoring a selected prior
backup into another new state directory; the current and source roots remain
available for comparison or a later rollback:

```bash
uv run python -m advisorai.app rollback \
  --source var/advisorai/backups/<backup-id> \
  --destination var/advisorai-rollback
```

Restore requires a destination that does not already exist. It never replaces
an existing state tree, and it does not relax the normal recovery or admission
checks.

## 4. Recover / restart

On start the supervisor:

1. acquires the single-owner application lock (a second live session is refused);
2. verifies code/config hashes and reads the last `SessionEndRecord`;
3. classifies `clean_start` vs `crash_recovery` and enters recovery mode when needed;
4. requires current venue/account/order reconciliation and a current PIT
   snapshot verifier before any new proposal (ambiguous acknowledgements
   reconcile, never blind-retry);
5. records exact data gaps; backfills only permitted sources preserving
   first-available times; releases stale leases.

```python
start, recovery = supervisor.start(reason="owner restart")
assert recovery.recovery_mode.value in {"normal", "recovery"}
```

An interrupted runtime cycle is kept as a failed immutable record. Recovery
reconciles its durable orders, fills, and account state first, then advances to
the next scheduled cutoff; it does not replay the interrupted cycle.

## 5. What remains external (owner gates)

- Live capital: sealed by Phase 10; requires explicit human approval.
- Model admission: reference adapter + quarantined challengers (TimesFM 3,
  TTM-R3, Kronos-base) need exact qualification; none is admitted.
- Real venue/testnet operation: configure credentials out of tree via the
  reviewed `secrets.env` flow; no live endpoints exist in the default config.
