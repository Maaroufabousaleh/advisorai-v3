# AdvisorAI V3 — Launch, operate, stop, recover

Target profile: owner-operated workstation, one session at a time, paper/testnet only.

## 1. Launch

```bash
uv sync --group dev
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/session tests/decision
./scripts/launch_dashboard.sh
```

Open <http://localhost:5173>. The launcher starts the API on `127.0.0.1:8787`
and waits for `/api/v1/health` (`{"status":"ok","environment":"paper_testnet"}`).

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

Stop accepting missions, finish or checkpoint bounded jobs, reconcile open
paper orders and account state, flush ledgers/manifests/watermarks, release
worker/GPU leases, then append the immutable `SessionEndRecord`:

```python
supervisor.stop()  # outcome=clean by default; unfinished work stays explicit
```

Shutdown is normal operation, not an incident. Ctrl-C stops the launcher's
API and Vite processes.

## 4. Recover / restart

On start the supervisor:

1. acquires the single-owner application lock (a second live session is refused);
2. verifies code/config hashes and reads the last `SessionEndRecord`;
3. classifies `clean_start` vs `crash_recovery` and enters recovery mode when needed;
4. requires venue/account/order reconciliation and PIT snapshot rebuild before
   any new proposal (ambiguous acknowledgements reconcile, never blind-retry);
5. records exact data gaps; backfills only permitted sources preserving
   first-available times; releases stale leases.

```python
start, recovery = supervisor.start(reason="owner restart")
assert recovery.recovery_mode.value in {"normal", "recovery"}
```

## 5. What remains external (owner gates)

- Live capital: sealed by Phase 10; requires explicit human approval.
- Model admission: reference adapter + quarantined challengers (TimesFM 3,
  TTM-R3, Kronos-base) need exact qualification; none is admitted.
- Real venue/testnet operation: configure credentials out of tree via the
  reviewed `secrets.env` flow; no live endpoints exist in the default config.
