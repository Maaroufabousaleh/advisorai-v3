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

## 2026-11-18 — P06 hosted recording workflow prepared + rehearsed (live NOT enabled)

- Branch `omp/btc-shadow-accounting` (#220 line), commits `3cb4011`
  (record workflow + script + 5 tests), `bcea270` (push-triggered
  rehearsal path), `3d4fbf9` (run-2 trigger). #215/#216 untouched.
- `.github/workflows/btc-shadow-record.yml`: rehearsal mode (fixture,
  any branch) + live mode (qualified snapshot + forward + recording).
  Live is triple-gated — default branch AND `SHADOW_LIVE_ARMED==true`
  repo variable AND script `--allow-live` + env — and unreachable now.
  No scoring in either mode; artifacts retained 365d.
- Why push-triggered: `workflow_dispatch` 404s for workflows not yet
  registered on the default branch (proven 3x), and this file cannot
  reach main before #215 (script imports `btc_shadow_job`). Pushes
  touching `.github/shadow-rehearsal.json` run the identical rehearsal
  job with dates/stamp/prior from the file. Schedule/dispatch paths
  remain for post-merge use.
- Hosted rehearsal (ubuntu-latest, persistent artifacts):
  - Run 1 (#44213457255): 2024-03-05 fresh chain, success; decisions +
    state byte-identical to the local run; forward hash `88a3eacb…f42f5`.
  - Missed run: no dispatch for 03-06. Run 2 (#44213609731, pinned
    prior 44213457255): 03-06 quarantined/missed, 03-07 scored long,
    03-05 fill observed at 209.0 (matches offline economics),
    `prior_report_sha256` linked.
  - Restart: `gh run rerun 44213609731` → byte-identical decisions,
    fills, state, report.
- Offline: 226/226 `tests/research/` green; ruff clean. New tests pin
  the two-run chain, missed day, idempotent rerun, tampered-prior
  refusal, and live-mode refusal without flag+arm.
- **Activation (owner step, NOT done):** after this file is merged to
  the default branch and P04 has landed, set repo variable
  `SHADOW_LIVE_ARMED=true` (`gh variable set SHADOW_LIVE_ARMED -b true`
  or web UI Settings → Variables). The next 01:30 UTC schedule then
  records live. Until then every live trigger fails loudly at the gate.

## 2026-11-18 — Independent AI review of #215/#216 (both APPROVE, merges BLOCKED on CI)

- Owner accepted independent AI review (fresh reviewer sessions, not
  the implementers) in place of human review. Heads confirmed unchanged
  at review time: #215 `7b05ce6`, #216 `b64d5c0`.
- #215 verdict APPROVE: 189 tests; fixture/replay values exact;
  multi-asset scope with venue boundaries; both code-scanning findings
  addressed in live paths with regression tests. Full record:
  PR #215 comment 6965903294.
- #216 verdict APPROVE: P03 DONE re-certified from existing artifacts
  only (no reruns/orders) — manifest hash, validator exit 0 18/18,
  ledger fill chain all match. Full record: PR #216 comment 6965905394.
- Verdicts also recorded in the tracker on this branch (seeded from
  `b64d5c0`, the newest tracker state; rows unchanged).
- **Merge decision: NOT merged.** `main` has no branch protection, so
  no eligible-account approval is required — but both PRs fail the
  Dashboard build check (upstream picomatch advisory, no patched
  release), and the merge instruction requires green CI without
  waiving the audit. Sole blocker: red Dashboard check. Verdicts lapse
  if either head moves (recheck then).

## 2026-11-18 — #215 + #216 MERGED per owner override (green CI excepted)

- Owner ordered the merge without green CI. #216 (`b64d5c0`) merged as
  `f5d053b`, #215 (`7b05ce6`) as `536c517` (local merge into a `main`
  worktree, pushed; reviewed heads byte-identical ancestors, AI verdicts
  stand). Only conflict: `.gitignore` (both append) → union in the merge
  commit. Merged tree re-verified: 189/189 `tests/research/` with the
  reviewer's extras env (plain env skips/fails lightgbm tests —
  environmental, also true on the reviewed head).
- Docs note: tracker on `main` at `d8b5a5d` — Merge-note section (verdict
  summary, override, Dashboard exception, re-audit trigger), P04 row
  DONE-with-override, count 3/10, P04-MERGED evidence bullet. P05 stays
  OPEN pending re-verification of its accepted evidence on `536c517`.
- The tracker copy seeded on this docs branch is SUPERSEDED by `main`;
  `main` is canonical from here on. PR comments posted on both PRs.
- Next runnable: P05 closeout verification on the merged base.

## 2026-11-18 — P05 DONE on merged base (`f499709`)

- Re-verified the owner-accepted BTCUSDT handoff on `d8b5a5d`:
  `test_btc_spot.py` 8/8 (PIT/completion/freshness/gap/provenance/
  fail-closed), `test_skills.py` + `test_multi_asset_replay.py` +
  `test_synthetic.py` green (31 total with btc_spot), pins intact
  (`1ee07b5b…53dce` in both plan docs, synthetic seed/SHA/counts in
  code), model id pinned in the candidate doc. Per-cutoff LightGBM
  stays private challenger research (not release-selected).
- The handoff doc's "(merge + green CI)" gate: satisfied by the merge
  plus the same owner Dashboard exception (recorded, not hidden).
- Tracker on `main` at `f499709`: P05 DONE, count 4/10. P06/P07 now
  unblocked on dependencies (need forward data / #220 landing).

## 2026-11-18 — #220 line merged with new `main`, retargeted to `main`

- Merged `main@f499709` into `omp/btc-shadow-accounting` as `6307f68`
  (clean, no conflicts); 226/226 `tests/research/` green on the merged
  tree. Pushed; PR base retargeted to `main` via the pulls API (`gh pr
  edit --base` exits 1 with a swallowed error on this repo).
- #220 diff vs `main` is now exactly Amendments 4–11 + the recording
  workflow. Still draft; forward data gates P06, review gates the merge.

## 2026-11-18 — #220 + #221 + #222 MERGED (AI-reviewed, Dashboard excepted)

- Owner ordered all three merged correctly. Landings first: #221
  (`ee79d74` docs-only, stale vs new `main`) merged `main@f499709`
  cleanly → `8b6b173`; #222 merged `main` → `58d1152` (7/7 new tests +
  301 session/execution/runtime green, ruff clean).
- Fresh AI reviews, all APPROVE, no findings: #220@`6307f68` (226
  tests, Amendment-4 economics, live-gate refusal, no scoring);
  #221@`8b6b173` (9 md files, claims verified); #222@`58d1152` (7
  hermetic fault tests, real assertions). Verdicts posted on each PR.
- Merged via `gh pr merge --merge` in order #221 → #222 → #220:
  `ee98795`, `efaa89d`, `c12e41c` (#220 marked ready first — `gh pr
  ready` works; only `gh pr edit --base` is broken on this repo).
  Reviewed heads verified ancestors; all other CI green, Dashboard red
  under the same owner exception.
- Tracker Merge note 2 on `main` at `3847694`. No row flips: P06 needs
  12-04 forward data; P07 needs a criteria check on the merged base;
  P08/P09 need integrated evidence. Count stays 4/10.
- Live schedules now registered on `main` (dry-run collection-only by
  design; record-live triple-gated, UNARMED).
