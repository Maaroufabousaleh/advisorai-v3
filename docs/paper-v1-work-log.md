# PAPER-V1 integration work log

Integration lead record. One entry per meaningful change: decision + why,
commit/PR, reproduction, tests/CI, evidence + hashes, still unverified.
Credentials and raw private data never enter this log.

## 2026-11-17 — status check + P03 verification + dashboard blocker

### Status (verified from repo, not chat)

- `main` = `eb9cbcc` (#214). Local checkout was detached at `1e2138c`; all
  remote heads fetched before acting.
- #215 (`omp/research-to-paper-integration` → `main`, ready): head `7b05ce6`
  (Amendment 3). Python/lint/phase CI green; Dashboard build fails on npm
  audit; no independent review yet.
- #216 (`review/testnet-launch-path` → `main`, ready): head `b64d5c0`
  (tracker: P03 DONE). Same CI shape: green except Dashboard audit; no review.
- #220 (`omp/btc-shadow-accounting` → research branch, draft): head `e37f613`
  (Amendment 10, unchanged from last known). Same dashboard failure.
- #221 (docs draft) unrelated.

### Dashboard audit: external blocker, no supported fix (recorded once)

- Failure: `npm audit --audit-level=high` flags picomatch ReDoS
  GHSA-fc5q-39xc-vjpf (high) via `vite → tinyglobby → fdir → picomatch`.
- Reproduced locally from the committed lockfile. `dashboard/` has zero diff
  between `main` and every PR branch, so this fails `main` equally on its
  next run — not a branch regression.
- `npm audit fix` moves picomatch 4.0.5 → 4.0.7 but the advisory range is
  `<=4.0.7` and 4.0.7 is the newest published picomatch: still flagged.
  Latest `tinyglobby` still depends on `picomatch ^4.0.4`. No patched
  upstream release exists, so no semver-safe bump can clear the gate.
- Decision: leave the gate intact; do not retry version bumps, do not waive
  the check, do not widen PR scope with a vite major bump or fork override.
  Merge candidates stay merge-blocked on this check until upstream ships a
  fix or the owner directs an explicit, reviewed exception.
- Prior note agrees: `98896e3` already records this as the known `main`
  advisory with the gate left intact.

### P03 DONE: independently verified (no new venue writes)

- Claim (branch `review/testnet-launch-path`, commit `b64d5c0`): owner SELL
  FOK run `20261117T184837.169951Z` from `1e2138c`, genuine venue fill
  `BTCUSDT:2994757` (0.00009 BTC, zero USDT fee), exact reconciliation,
  clean restart, offline validator `p03_certified: true` (18/18).
- Reproduction (read-only, exact pinned commit, local checkout at `1e2138c`):
  `uv run python scripts/validate_binance_fill_evidence.py --evidence-dir
  artifacts/phase2/binance-spot-testnet/fill-probe/20261117T184837.169951Z`
- Result: exit 0, 18/18 checks pass, `fill_pass: true`,
  `p03_certified: true`, manifest SHA-256
  `535cfeba547fc469784b9cb46c09246967a9ff9479b499d507aaae381b718d5a`
  matches the tracker claim byte-for-byte (`sha256sum` agrees).
- Evidence preserved in place; nothing re-run or overwritten. No redundant
  owner order needed. P03 row can close on `main` once #216 lands.

## 2026-11-17 — P04 #215 verification at `7b05ce6` (prep complete, gates external)

### What was checked (isolated worktree `/tmp/r2p-review` at exact head)

- `tests/research/`: 189 passed with CI-identical env
  (`uv sync --locked --all-extras --group dev`). First attempt without
  `--all-extras` failed one lightgbm test — local-env artifact, not branch.
- Fixture replay (`--label lead-verify-7b05ce6`): SPY/TLT totals + all six
  window splits match the PR-stated numbers to the bp.
- Scope: 44 files, all research/tests/scripts/docs + one-line `.gitignore`
  (synthetic snapshot dir). No execution/risk/admission/gate/venue/
  credential/dependency/CI changes. New scripts use only public Binance
  HTTP (archive + klines); no secrets/POSTs/Testnet. `replay.py` one-liner
  (`int(Decimal(volume))`) numerically identical — exact repro proves it.
- History: 0 Yahoo refs in the 7 new commits; 0 added blobs >20KB.
- Path-scoping fixes (`5ca728f`/`f235045`/`d1a7d9a`): reviewed diffs +
  regressions; runner-owned root, payload root rejected, traversal/escape/
  symlink/non-file rejected pre-read. Bot threads addressed in code; UI
  resolution needs a reviewer with standing.
- Amendment-3 economics reviewed: signal at day-1 close from known closes,
  fill at next open, entry open→close + held close→close + exit
  prior-close→exit-open telescopes open-to-open; 20bps per unit change;
  BH = portfolio value minus one leg. No lookahead found.

### Flags (P06 reviewer, not P04 blockers)

- Strategy accrues additively (sum of daily simple bps) while BH is
  geometric (`last/first−1`). Toy always-long: gross 360.40 vs 365.85 on
  identical prices; gap compounds with trend/volatility — material to the
  181-day `net > BH` pass rule. Disclosed in-candidate (BH 46667.66 →
  137118.90 across amendments); P06 acceptance must rule.
- Real-snapshot plumbing totals + input hashes not independently
  reproduced (out-of-tree data); producing code paths are fixture-tested.

### Posted

- #215 comment `6964298616`: full verification (comment, not approval).
- #216 comment `6964300360`: P03 offline re-certification (18/18, exit 0,
  manifest `535cfeba…b718d5a` recomputed matching).

### Gates (both external — no further prep available)

- Independent approval on #215/#216: outstanding (owner + bot COMMENTED only).
- Dashboard build: upstream picomatch, no patched release; gate intact.
  Merge only after both clear; branch protections/review requirements kept.

## 2026-11-17 — P06 end-of-window fix (Amendment 11, `86a0d27`)

### Finding (verified absent at `e37f613`, not already fixed)

- `score_window` applied a traded day's fill without checking `fill_date <=
  mark`: a fill printing after the valuation mark changed the value at the
  earlier mark. Proven red: Mar 5 entry filling Mar 7 was applied at the
  Mar 6 mark.
- Benchmark entry (`first_open_after` past the first record) had no
  `entry_day <= mark` guard: flat-only Mar 4 scored at the Mar 4 mark
  priced the benchmark off the Mar 6 open. Proven red.
- Pending fills reported only as "missing", with no pending-until date.

### Fix (branch `omp/btc-shadow-accounting`, pushed `e37f613..86a0d27`)

- `src/advisorai/research/btc_scoring.py`: refuse fills printing after the
  mark; refuse benchmark entry after the mark; missing fills report
  `pending until <expected fill date>` when the decision record time shows
  the fill is not yet observable (data-loss case keeps the plain message).
  Window never moves. Docstring rules + Amendment 11 in the candidate doc.
- `tests/research/test_btc_scoring_rehearsal.py`: three fixture-only
  regressions, written red first, all green after.
- `tests/research/`: 216 passed (was 213), ruff check + format clean.
- No forward scoring, no orders, replay economics untouched. CI reruns on
  #220 (dashboard audit still red upstream — same external blocker).

### P06 scorer properties verified (offline, fixture-only)

- Fill-after-mark impossible (new guard); benchmark shares
  `first_open_after` timing with strategy fills (Amendment 9 +
  `test_execution_timing_matches_for_strategy_and_benchmark`).
- Pending fills pinned as above. Daily path rehearsed across restart
  (pre-outage bytes preserved), missed days (hold, excluded, never
  filled), duplicates (rerun appends nothing), late/uncompleted days.
- Preregistered window 2026-12-04..2027-06-02 (181 decisions), actual
  receipt/record times pinned per record, backdating impossible by
  append-only logs + recording cutoff.
- Forward shadow itself needs future market data (first signal 12-04):
  external time dependency, not runnable now. Dry-run workflow stays
  inert (schedule-only, contents:read, collection-only).

## 2026-11-17 — P07 bounded workflow (`744cb02` on `omp/btc-shadow-accounting`)

### Gaps found (proven before fixing)

- Retry duplicated experiments: `run_experiments` claimed "duplicates reuse
  stored experiments" but every retry stored new rows (random uuid4 +
  `created_at` in the content hash). Proven: same replay twice → 2
  experiment + 2 validation rows, different ids. The old dropped-step
  resume test passed while silently duplicating.
- Negative results were dropped: per-exposure failures (e.g. FIXC) lived
  only in memory + `rehearsal.json`; `prior_experiments` hardcoded
  `succeeded=True` for every stored record, so a rejection could never be
  retrieved as one.

### Fix (pushed `86a0d27..744cb02`, #220 reruns CI)

- `replay.py`: deterministic experiment identity (uuid5 over factor,
  policy, code/data/env hashes, costs, seeds, window requirement).
  `run_exposure` reuses the stored row: success rebuilds the exact
  `ExposureResult` from full-precision metrics (no recompute); recorded
  failures re-raise the original type+message. Metrics now full precision
  (+ per-window trades, window names, indexed regime triples) to make the
  rebuild exact. `run_replay` stores per-exposure failures as REJECTED
  experiments (get-or-create, stable ids in new `failed_ids`); unresolvable
  cost overrides key off raw values so they stay per-exposure failures
  instead of aborting the run. Nondeterministic success/failure flip trips
  a loud `RuntimeError` instead of overwriting.
- `workflow.py`: failure outcomes link to the stored REJECTED id (was the
  hypothesis id); `prior_experiments` reports stored status honestly with
  the failure string as detail.
- Rehearsal report adds `prior_succeeded`/`prior_rejected` +
  `experiment_rows`/`validation_rows`; plan doc records the guarantees.
- Tests: replay idempotency (equal results, stable rows, changed-costs
  freshness), failure persistence + reuse, partial-cost isolation,
  accepted/rejected retrieval, cancel→memory→resume. Resume test now
  asserts identical ids + stable rows (would fail on old code).
- `tests/research/`: 221 passed; ruff clean. Full suite: 9 failed / 1523
  passed — byte-identical failures on the clean base (phase0 isolated-
  runtime workstation-env issues, green on CI). Acceptance: 9/143 both
  sides, same cause. No regressions.

### Demo (real CLI, `/tmp/p07demo`, fixture only)

- First run: 2 accepted + 1 rejected (FIXC), 3 experiment / 2 validation rows.
- Rerun: identical ids, no new rows. Crash resume (dropped step): identical
  ids, no new rows, rejection preserved.
- Retrieval: `KEEP FIXA`, `KEEP FIXB`, `DROP FIXC: ValueError: ...no eligible
  month-end cutoffs...`, plus cancelled jobs as `unfinished` (tested).
- Cancellation takes effect at step boundaries (cooperative `cancelled()`
  + coordinator slots); mid-computation kill is out of scope by design,
  failed/interrupted jobs stay first-class memory.

## 2026-11-17 — P08/P09 offline fault paths (PR #222)

### Survey (before building)

- `main` already integrates supervisor + runtime + decision + RiskKernel +
  OMS + paper venue + reconciliation (`test_integrated_paper_session.py`,
  3 tests) with crash→recovery and evidence-gate abstention; OMS-level
  ambiguous-crash recovery exists (`test_strong_order_lifecycle.py`).
- Gap: no runtime-level fault tests for stale data, venue outage,
  ambiguous ack at the cycle level, mismatch trip → abstain, emergency
  halt, verifier-gated restart, or duplicate-run suppression.

### Built (branch `omp/paper-v1-p08p09-offline`, PR #222 → `main`)

- `tests/session/test_integrated_fault_recovery.py`: 7 deterministic
  offline tests, zero source changes. Stale cadence abstains (fresh
  routes); outage → FAILED + orphan expire/reconcile + clean next cycle;
  ambiguous ack → reconcile without resubmission (blind retry refused);
  tampered venue cash → durable kill trip + abstain until approved resume;
  halt → scheduler + routing stop, CLEAN end, approved resume;
  UNCLEAN restart → ABSTAIN without verifier, cancel + reconcile, verified
  restart → NORMAL, 2 distinct venue orders; same-cutoff rerun routes
  nothing new.
- Verified: 7/7 new, 143/143 session+execution+runtime, ruff check +
  format clean. Findings while writing (all handled in-test, no source
  changes): FAILED cycles carry no order ids (orphan found via OMS);
  stop/start order checks require an explicit venue verifier; restart
  without one yields ABSTAIN by design.
- PR #222 opened as ready-for-review; independent approval outstanding.
  Dashboard check will fail on the same upstream picomatch blocker.

### Not claimed

- P08/P09 acceptance needs integrated evidence on the reviewed release
  revision (approved strategy + merged base) — impossible before P04
  lands. This PR is offline prep only; integrated runs come later.

## 2026-11-17 — P10 campaign prep (preparation only, campaign BLOCKED)

- `docs/paper-v1-p10-campaign-prep.md`: entry criteria, session matrix,
  per-session evidence checklist, abort rules, no-compression rules,
  tracking convention, resume trigger. No sessions run — entry needs the
  reviewed P08 revision with P09 evidence accepted.
- Blocker chain: P04 merge → P05/P06/P07 close on merged base → P08
  integrated revision → P09 integrated evidence → campaign revision pin.
- Docs branch: `omp/paper-v1-docs` (work log + P10 prep), pushed, no PR
  yet — PR once the next checkpoint lands or on owner request.
