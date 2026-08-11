# AdvisorAI V3 continuation checkpoint

## Current continuation update — Phase-3 measurement boundary

- PR #140 merged into main at
  `335114ba73156cb75e44465a4d21ff27f86299e1`; current durable-runner code
  identity is
  `17bed912495868062c6a7a79e515d5a29a8b65b40cf138b8845e837ba3ec280d`.
- The public Binance collector now distinguishes the measured feed-window end
  from asynchronous WebSocket close cleanup, preserving both timestamps and
  evaluating freshness at the former. This fixes a local teardown-latency
  measurement artifact; it does not relax staleness, change source identity, or
  affect execution authority.
- Full pytest passed `638` tests with 28 warnings; acceptance passed
  `129/152/126/94/24/34/10/11/27/18/5`; Ruff, format, lock, compilation,
  dashboard build, diff, and tracked-secret/weight checks passed.
- The active r5 root remains under its prior code identity and was not touched;
  Phase-3 admission remains pending until a fresh external root measures the
  corrected code. PID `70598` remains untouched; archive/rclone remains
  externally deferred.

## Current continuation update — r4 terminal review and r5 active root

- The preserved r4 root
  `artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
  completed at `2026-08-11T08:24:45.638125Z` with 810 samples and summary
  SHA-256 `53b1b77192dc77360b63b12208a445cc889c6bd0ff570fe4bd08ef37d8753fe2`.
  It has no terminal marker, and its final sidecar observation invalidated the
  sidecar hash chain with `process:FileNotFoundError`; sidecar summary SHA-256
  is `75cc73ca44400d59df9f28037d5037ff8ca3c456c459f7d5e34ffe06e3168d47`.
  Both artifacts are preserved and quarantined; no gate was promoted.
- PR #138 merged at
  `1335bfabe93bdd990f9512430ae843a9795a7ebf`, fixing sparse resource-record
  sealing and adding a regression test. The fresh r5 root is active under PID
  `46864` at
  `artifacts/phase3/public-market-data-durable/20260811T083600Z-four-hour-r5-reconnect-hashfixed`
  with code identity
  `f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8` and
  target `2026-08-11T12:35:59.156509Z`.
- Its resource sidecar is PID `47392` at
  `artifacts/phase3/public-market-data-resource-monitor/20260811T083600Z-four-hour-r5-reconnect-hashfixed-v2`.
  The first real cycle has valid BTC/ETH snapshot/replay data, but all current
  source selections remain fail-closed. Phase-3 admission is pending.
- Selected-model stability PID `70598` remains untouched; archive/rclone is
  unchanged and externally deferred.

## Current continuation update — bounded Binance public-data reconnect

- PR #136 merged at `350d6b55ac36251750e0459dc4e24b3507ca865c`. Future
  qualification roots use runner code identity
  `f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`.
- The durable Binance public WSS collector now allows one bounded reconnect
  after a failed first-message or snapshot window. Both attempts remain
  separately evidenced; snapshot/sequence recovery is based only on the latest
  successful provider snapshot and its updates. No credentials, order writes,
  OMS methods, execution transport, or production trading authority were added.
- Full locked pytest passed `636` tests with 28 warnings; acceptance passed
  `129/152/92/24/34/10/11/27/18/5`. Ruff, format, lock, compilation,
  dashboard build, diff, tracked-secret, and tracked-weight checks passed.
- The active Phase-3 r4 root remains under PID `87421` with sidecar PID
  `88019` and its earlier code identity; selected-model stability PID `70598`
  remains untouched. No timed or Phase-3 admission gate changed.

## Current continuation update — Binance provider read-only recovery

- PR #128 merged at `aa4cdcb86a9bd0c1ca749f0ded5524b8cb842c9c`; PR #133 is the
  current executable anchor `083798403323e18f2cc6577103d7b81c36454279`.
  Subsequent PRs are documentation-only follow-ups.
- The provider-specific recovery qualification uses the existing scoped
  `PAPER_VENUE` resolver and Binance Spot Testnet transport. It activated a
  non-secret immutable configuration revision, rolled back to the original
  bundle, reopened that pointer in a fresh child process, and repeated only
  authenticated reads. No order, cancel, transfer, withdrawal, OMS, or
  production operation was attempted.
- Real evidence is
  `artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json`
  with SHA-256
  `acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`.
  It passed with 18 read-only calls, provider-truth `BTCUSDT`/`ETHUSDT`,
  matching initial/restored bundle hash
  `0a44fe86c6cd7a65c316886f93848147aa3b75fd3a1eb3c31ae2579eaf7dc691`, and
  `writes_attempted=false`. State is externally measured partial and
  `NOT_ADMITTED`; full provider deployment rollback, open-order recovery,
  Bronze rebuild, and archive restore remain pending.
- PR #133 fixed direct repository-root execution of the offline Phase-3
  admission evaluator and added a zero-network subprocess help regression.
  Its executable merge anchor is `083798403323e18f2cc6577103d7b81c36454279`;
  no admission logic or evidence root changed. Full locked pytest is `635
  passed`; Phase-3 acceptance is `91`.
- The corrected evaluator was exercised against immutable r3. Offline report
  `artifacts/phase3/public-market-data-admission/20260811T072500Z-two-hour-r3-entrypoint-recheck/phase3-admission-evaluation.json`
  has SHA-256
  `8c308ec39497ef962ea9dcb8fbbea611797bb2f0a488d08585923ae2fe7d131f` and
  remains `PENDING_EXTERNAL_EVIDENCE` with `phase3_admission=false`.
- Selected-model stability PID `70598`, active Phase-3 PID `87421`, and its
  resource sidecar PID `88019` remain untouched. Archive/rclone remains
  externally deferred.

## Current continuation update — Phase-3 source identity integrity

- Candidate commit `4abf2ce` adds provider/endpoint identity binding and
  predecessor-state validation to the append-only Phase-3 health ledger.
- Focused Phase-3 coverage passes `64` tests with one FastAPI-only skip; all
  nine historical Phase-3 health ledgers reopen successfully.
- PR #125 merged this change; clean main is now
  `3d3242cd07d55b2099b247b1d593a1701685f829`. The active selected-model PID
  `70598`, Phase-3 PID `87421`, and resource sidecar PID `88019` were not
  stopped, restarted, or modified. Phase-3 admission and all timed gates
remain pending.

## Current continuation update — Phase-3 resumable configuration bounds

- Candidate commit `49b3283` rejects a resumed durable qualification root when
  `max_cycles` differs from its immutable config and proves same-config resume
  hydration does not duplicate samples or health transitions.
- The focused Phase-3 suite passes `66` tests. The active model and Phase-3
  processes remain untouched; no terminal or admission evidence is claimed.

## Current continuation update — post-PR #120 Phase-3 review

- Clean `main` is `22370e95a85b0cffbf104e867ac59dee5ac4c2f6`; PRs #120–#122 are
  merged.
- Full pytest remains `626 passed` with 28 warnings. Acceptance suites are
  `129/152/126/87/24/34/10/11/27/18/5`; the complete Phase-3 suite is covered.
- Offline validation of immutable r3 produced
  `artifacts/phase3/public-market-data-validation/20260811T060000Z-two-hour-r3-v3/phase3-qualification-validation.json`
  (SHA-256
  `40b08077112092df4531175063d8c514ab58a65d31c317bb562e0d14ad8f1753`),
  `PASS_FOR_REVIEW`, with no issues and `phase3_admission=false`.
- Offline admission review produced
  `artifacts/phase3/public-market-data-admission/20260811T060000Z-two-hour-r3-v3/phase3-admission-evaluation.json`
  (SHA-256
  `26ae8d2e7a209b71ce36fb1707a3183dff0840f14220d7df617874a1e8a80a26`),
  `PENDING_EXTERNAL_EVIDENCE`. The blockers are the pre-terminal-marker r3
  window, no healthy BTC/ETH primary, and primary snapshot/sequence/replay
  failure.
- PID `70598` (selected-model stability), PID `87421` (active Phase-3 r4), and
  PID `88019` (r4 resource sidecar) remain untouched. Archive/rclone remains
  externally deferred.

## Current continuation update — in-flight Phase-3 r4 observation

- The active root
  `artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
  remains under PID `87421` with target
  `2026-08-11T08:24:40.271709Z`.
- At `2026-08-11T06:11:35Z`, it had 384 samples, 384 connection
  attempts/disconnects, 128 snapshot-recovery attempts, zero successful
  connections, zero valid events, and all six source/symbol pairs fail-closed.
  This is not terminal evidence and does not open Phase 3.
- Resource sidecar PID `88019` remains active with no observed resource error;
  the active root predates the sanitized failure-label projection and will not
  be retrofitted.

Checkpoint refreshed 2026-08-11 from clean `main`
`083798403323e18f2cc6577103d7b81c36454279` (PRs #86–#128 and #133 carry the executable
implementation/evidence; subsequent PRs are documentation-only follow-ups; PRs #95–#96
are documentation-only follow-ups to the #94 implementation/evidence anchor;
PR #103 adds the offline Phase-3 qualification validator, PR #105 records the
independent Phase-3 availability recheck, and PR #108 adds the durable Phase-7
runner boundary; PR #109 adds the offline Phase-3 admission evaluator, PR #110
adds the terminal-sample runner fix, PR #112 requires the explicit terminal
marker during review, PR #114 adds the closed Phase-4 utility preparation
boundary, PR #115 refreshes the evidence anchors, PR #116 adds the sanitized
Phase-3 failure-class projection, and PR #118 points the model roster at the
active stability root; PR #119 records post-roster verification, PR #120
completes Phase-3 acceptance coverage, and PR #121 records the fresh review
evidence.)

## Current continuation update — Phase-4 utility preparation

- PR #114 is based on clean-main anchor
  `dea0d4e832143d9ae1ab6515a255e25ee1377b3f` and adds the offline
  `src/advisorai/phase4/paper_utility.py` boundary plus
  `scripts/prepare_phase4_utility_evaluation.py`.
- The boundary is ready for admitted BTC/ETH V3-Core paper observations. It
  requires an immutable Phase-3 gate hash and refuses unadmitted observations;
  it measures directional accuracy, interval/confidence calibration, regime
  slices, turnover, spread/slippage, Binance-testnet fee cost, net utility,
  and incremental value against `naive`, `drift`, `seasonal-7`, `linear`, and
  `lightgbm`. It cannot promote a model, create a gate record, or submit an
  order.
- Preparation evidence is
  `artifacts/phase4/utility-evaluation-preparation/20260811T051344.190783212Z-offline-contract-v1/phase4-utility-preparation.json`
  with SHA-256
  `620f2ce32bb19aed8ce64ed0c12cddd4e0684db9f5b78add11e5b8ce6445456b` and
  state `ready_for_admitted_input`; Phase-4 admission remains closed.
- Locked verification passed full pytest `622 passed` with 28 warnings;
  focused utility tests passed `5`, and the model/Phase-4 suite passed `24`.
  The selected-model stability PID `70598` and public-data PID `87421` with
  sidecar PID `88019` remain untouched. Archive/rclone remains deferred.

## Current continuation update — Phase-3 failure classification

- The durable public-data runner now carries sanitized collector exception
  classes and failure-layer labels into each source/symbol sample, its summary
  aggregation, and the read-only dashboard/API health projection. Response bodies,
  headers, messages, credentials, and provider state are not copied into these
  fields.
- Focused Phase-3 coverage is `14 passed`. The active r4 root at
  `artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
  remains untouched under PID `87421`; it predates this implementation and is
  not retroactively upgraded. Future qualification roots will contain the
  complete sanitized failure classification. Phase-3 admission remains closed.
- Locked verification for this continuation passed full pytest `624 passed` with
  28 warnings and acceptance suites
  `129/152/126/69/24/34/10/11/27/18/5`; Ruff/format, lock, compilation,
  dashboard build, diff hygiene, tracked-secret, and tracked-weight checks all
  passed. These checks do not open Phase-0, Phase-3, or later external gates.
  The machine-readable model roster points to the active r3 stability root;
  PID `70598` and its append-only evidence remain untouched.

## Current continuation update — complete Phase-3 acceptance coverage

- The offline Phase-3 validator checks optional `failure_classes` and
  `failure_layers` for safe bounded labels and duplicate entries, while old
  immutable roots may omit those new fields.
- The acceptance runner now executes the complete `tests/phase3` directory;
  the former explicit list omitted WSS diagnostics, admission, validator, and
  resource-monitor tests. The complete Phase-3 suite passes `87` tests.
- Post-#119 locked verification passed full pytest `626 passed` with 28
  warnings and acceptance suites
  `129/152/126/87/24/34/10/11/27/18/5`. Phase-3 admission remains closed.

## Current continuation update — terminal Phase-3 sample boundary

- PR #110 changes future durable Phase-3 windows to collect one explicit sample
  whose cycle starts at or after the configured target. The sample is marked
  `terminal_sample=true` and summaries expose `terminal_sample_count`; the
  process no longer finalizes immediately after a pre-target cycle. The
  boundary regression is covered by
  `tests/phase3/test_phase3_public_data_qualification_runner.py` and included
  in the Phase-3 acceptance suite.
- Verification after PR #110: full pytest `616 passed`; acceptance suites
  `128/152/126/68/19/34/10/11/27/18/5`; Ruff/format, lock, compilation,
  dashboard build, diff hygiene, tracked-secret, and tracked-weight checks all
  passed.
- The active r4 public-data process PID `87421` was launched before PR #110
  with code SHA-256
  `c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`; it is
  not restarted or retroactively treated as if it had the new terminal marker.
  Its independent resource sidecar remains PID `88019`. Evaluate that root
  using the evidence actually recorded, then use a fresh root if the terminal
  boundary remains unresolved.

## Current continuation update — Phase-3 admission evaluator and active window

- Added the offline, non-admitting
  [`scripts/evaluate_phase3_admission.py`](../../scripts/evaluate_phase3_admission.py)
  boundary with focused tests in
  [`tests/phase3/test_phase3_admission.py`](../../tests/phase3/test_phase3_admission.py).
  It validates timestamp-derived qualification duration and terminal-sample
  presence, append-only public/read-only invariants, reviewed endpoint identity,
  all-cycle primary continuity, fail-closed disagreement and source selection,
  and a completed error-free OS resource sidecar. It performs no network I/O,
  does not mutate qualification roots, and cannot represent formal admission.
- Evaluation of the completed r3 root is preserved at
  `artifacts/phase3/public-market-data-admission/20260811T043711Z-two-hour-r3-v2/phase3-admission-evaluation.json`
  with SHA-256
  `cbb8ec53d793887f17ebeccab8db33a52051082cdd989ff780b7a5f854cf0c1b`.
  Recommendation is `PENDING_EXTERNAL_EVIDENCE`; blockers are
  `qualification_window_incomplete`, `no_healthy_primary_source_for_btc_eth`,
  and `primary_snapshot_sequence_or_replay_failure`. The final r3 sample
  preceded its target despite process finalization after the target; no policy
  was relaxed and no admission was opened.
- A fresh four-hour public-data qualification is active under PID `87421` at
  `artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
  until `2026-08-11T08:24:40.271709Z`; its separate read-only resource sidecar
  is PID `88019` at
  `artifacts/phase3/public-market-data-resource-monitor/20260811T042355Z-four-hour-r4-fixed-v2`.
  Preserve both roots, do not restart or concatenate them, and evaluate only
  after the runner reaches a terminal state.
- Selected-model stability PID `70598` remains untouched. Archive/rclone is
  externally deferred and was not touched. Phase 0 stability, Phase 3
  admission, Phases 4–7, Phase 8 formal admission, Alpha E0–E7, and Phase 10
  human approval remain open.

## Current continuation update — durable Phase-7 runner boundary

- Added the narrow `DurablePaperSoakRunner` boundary under
  `src/advisorai/soak/durable.py`. It persists immutable run configuration,
  fsync'd hash-chained interval records, atomic PID/heartbeat status, one-owner
  locking, and restart hydration without resetting the start time.
- A pre-terminal bounded run writes progress only; the immutable `summary.json`
  is created only by a real sample at or beyond 60 calendar days. The summary
  is permanently `evidence_for_review_only` and cannot open Phase 7.
- Focused durable-soak, existing soak, lint, and format checks pass. No real
  Phase-7 process was launched because Phase 0–6 admission prerequisites are
  not satisfied. Archive/rclone remains externally deferred and untouched.
- PID `70598` and the completed Phase-3 root remain preserved; the next legal
  work is the genuine Phase-0 terminal sample and subsequent external gate
  review.

## Current continuation update — completed Phase-3 qualification

- The corrected durable root
  `artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`
  completed at `2026-08-11T03:14:39.940009Z` after 63 cycles and 378 samples.
  Its config/status/summary/heartbeat SHA-256 values are respectively
  `eb09ac0aa008c5a42c7e318178c79421bdf4d471b5649ddf65baa50a59f12398`,
  `df8a7aa57aa95205636ce0e800882f6ccca0647b386a29488c83b7bba97ed5da`,
  `eb33cb5939feb5126bef3eff210c3710a95d6fbf3d85b3433bc2ad024a191ed7`, and
  `5d44ef77d3bf459f75c8141c53dbb45e6275489399d42616a1ad20ddd1fcb66`.
- Offline validation at
  `artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json`
  returned `PASS_FOR_REVIEW`, `qualification_state=evidence_for_review_only`,
  `phase3_admission=false`, and no issues. Its SHA-256 is
  `efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca`.
  Append-only validation covered 378 samples, 126 source selections, 126
  disagreement records, and 78 health transitions. All selections failed
  closed with zero silent substitutions; three replay failures, one
  out-of-order event, and 22 severe disagreement observations remain preserved.
- The corrected resource sidecar at
  `artifacts/phase3/public-market-data-resource-monitor/20260811T025102Z-pid13339-v2`
  reached `deadline_reached` with 32 observations and no resource errors.
  Its summary SHA-256 is
  `42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd`.
  The qualification process and sidecar are no longer running; the first v1
  sidecar remains preserved as failed evidence.
- PID `70598` remains untouched and active under
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3`.
  A read-only inspection observed sequence 107 at
  `2026-08-11T03:47:10.345140Z`, SHA-256
  `c3e9e65afe59a78c80687ca19243e28cbf70f227131e4f207c1a05c8bd34b02f`; the
  configured terminal sample has not occurred.
  The 24-hour gate remains pending; no root was stopped, restarted, modified,
  concatenated, or backdated.
- Archive/rclone remains externally deferred and was not touched. Phase 0
  stability, Phase-3 admission, Phases 4–7, Phase-8 formal admission, Alpha
  E0–E7, and Phase-10 human approval remain open. The next legal work item is
  read-only inspection of PID `70598` and the genuine terminal-sample gate.
- Independent recheck root
  `artifacts/phase3/public-market-data-durable/20260811T034114Z-one-cycle-recheck`
  corroborated the source blocker: six public connections, 503 valid events,
  Binance BTC/ETH stale at `5.096588s`/`5.011760s`, Coinbase quarantined,
  Deribit degraded, no credentials, and no writes. Summary SHA-256:
  `698ad40af908757a398d19c6df83e4bfc50209bca541fe8b3acd6c314d6eff1e`.

## Prior continuation update — durable Phase-3 source qualification

### Historical details

- PID `70598` remains untouched under
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3`.
  Its latest read-only sample is sequence 81 at `2026-08-11T01:26:03.661578Z`,
  record SHA-256
  `cd5525a4c0fe993999708fa10d0736623045604765e025a43e169340610c89fe`; no
  root was restarted, concatenated, or modified.
- The prior PID `62977` Phase-3 root completed its fixed two-hour target at
  `2026-08-11T01:13:21.437160Z` with summary SHA-256
  `96aac309e23df24e090b97a99127b33d4dbb90e9b593cf76d909ef43e65f0283`.
  Independent validation reloaded its append-only ledgers and verified 336
  samples across 56 cycles; its result remains evidence-for-review-only.
- PID `13339` is the fresh durable, read-only Phase-3 qualification process at
  `artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`.
  It started at `2026-08-11T01:14:37.205719Z`, targets
  `2026-08-11T03:14:37.205719Z`, and uses code SHA-256
  `c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`. Its
  immutable config records snapshot limit 100,
  `credentials_loaded=false`, and `order_writes_attempted=false`.
  Heartbeat, status, raw spool, observation, selection, disagreement, and
  health-transition logs are append-only; the result is not an admission
  record.
- The first v1 resource sidecar root
  `artifacts/phase3/public-market-data-resource-monitor/20260811T023624Z-pid13339-systemd`
  is preserved as failed hash evidence. The corrected v2 systemd sidecar
  `advisorai-phase3-resource-monitor-20260811T025103Z-v2.service` observes PID
  `13339` without writing to the qualification root. Its separate evidence
  root is
  `artifacts/phase3/public-market-data-resource-monitor/20260811T025102Z-pid13339-v2`
  with config SHA-256
  `3202ad6c45f750a9b1c250336a0d7819cdcfa78486a6ee5bd78d645c544d3e08`.
  It records sanitized OS resource and target-root growth metrics only.
- The implementation supplies deterministic HEALTHY/DEGRADED/STALE/
  DISCONNECTED/RECOVERING/QUARANTINED transitions, provider-truth snapshot and
  sequence recovery, severe-disagreement abstention, and explicit failover or
  fail-closed selection. The dashboard/API projection is read-only and names
  the actual current source.
- Archive/rclone remains externally deferred and was not touched. Coinbase and
  Binance execution evidence remain preserved. Phase 0 stability, Phase 3
  continuous admission, Phases 4–6 real paper evidence, Phase 7, Phase 8 formal
  admission, Phase 9, Alpha E0–E7, and Phase 10 human approval remain open.

### Historical Phase-3 source-spine details

- PID `70598` remains untouched and active under
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3`.
  Its latest read-only sample at `2026-08-11T01:15:16.975316Z` recorded 79
  passing cycles with last record SHA-256
  `1130100d0cff6fd829d7546d147d1b7220f4d4a0e70c56672443e5c5e355c7d2`.
  The 24-hour gate remains pending; no earlier root was concatenated.
- The layered Binance Testnet WSS diagnostic at
  `artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json`
  has SHA-256
  `8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b`.
  DNS, TCP, and TLS passed. The locked runtime reached valid BTC/ETH public
  WSS messages on successful attempts, but an ETH connection timeout remained;
  classification is intermittent `websocket_connection_timeout`, not
  provider-unavailable. The earlier missing-library root remains independent.
- The corrected depth run at
  `artifacts/phase3/binance-spot-testnet-depth/20260810T211531.293435Z/phase3-binance-spot-testnet-depth.json`
  has SHA-256
  `f75f4e25ba48d923df4cba4e29d7ccf4b45e7382a05b5f63bb3a500b8b59fcde`.
  An ETH stream was replay-equivalent; BTC and other streams include WSS/runtime
  or adjusted-future fail-closed results. The pre-fix local snapshot
  selector incident is preserved and was regression-tested.
- The credential-free public market-data bake-off selected Binance public
  BTCUSDT/ETHUSDT as the current primary candidate at
  `artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`,
  SHA-256
  `14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`.
  It completed four full BTC/ETH public windows, two reconnects per symbol,
  adjusted freshness, and real cross-source observations. It is explicitly
  read-only and separate from Binance testnet execution; longer unattended
  operation, sequence/snapshot recovery, and failover evidence remain open.
- Archive/rclone remains externally deferred and was not touched. Coinbase and
  the previously qualified Binance no-fill/cancel evidence remain preserved.

The next legal work item is longer Phase-3 public-source freshness/recovery and
independent-source disagreement evidence while the Phase-0 timer continues.

## Current continuation update — Binance Spot Testnet qualification

The following supersedes the earlier pending-operator Binance bullets below.

- Fresh authenticated read-only evidence passed all required operations at
  `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`,
  SHA-256
  `c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72`, with
  exact reviewed host `testnet.binance.vision` and configuration hash
  `b41638ffc13149796f29676826b54097d2e7c417d9e4b1ff4d72be6d12f87286`.
- After the final adapter source was settled, a read-only-only smoke passed
  again at
  `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T201450.306674Z/binance-spot-testnet-read-only-smoke.json`,
  SHA-256
  `b3a8b54f446599b50547bab98240db0fe8e1380fd969a6a220fccac1c83fe8e7`,
  matching adapter source SHA-256
  `ec3077cc726a045420c714f99c5c2e026351190348fdc9779f96e21cff034e0d` and
  making no writes.
- One supervised fake-funds BTCUSDT `LIMIT_MAKER` lifecycle then passed through
  deterministic RiskKernel, authoritative OMS, Binance transport,
  reconciliation, cancellation, restart hydration, TCA, attribution, and
  deterministic failure drills. Evidence:
  `artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`,
  SHA-256
  `db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70`.
- The real order remained unfilled and was cancelled. Real fill ingestion is
  therefore not claimed; the typed OMS fill path and non-inducible failure
  cases are fixture-tested. No production, transfer, withdrawal, or fallback
  venue call occurred.
- The selected-model stability root
  `phase0-selected-24h-terminal-sample-20260810-r3` remains active under PID
  `70598`; it was not restarted, stopped, concatenated, or modified.
- Archive/rclone work is externally deferred and was not touched. The next
  legal independent work is Phase-3 source/reconnect evidence and truthful
  status updates while Phase-0 stability continues.
- The latest locked verification passed 607 tests and all eleven acceptance
  suites with results `128/152/126/66/19/34/10/7/27/18/5`.
- A fresh bounded Phase-3 REST/raw-first retry made seven public calls at
  `artifacts/phase3/source-qualification/20260810T201653.611706Z/phase3-v3-core-source-qualification.json`,
  SHA-256
  `60cac1ba77fa31735c87b02e29125985e9d4e69b2e592886e317b0ed61ecca01`.
  BTC-USD native ticker, Deribit index, and SEC RSS passed; Coinbase ETH-USD
  remained HTTP 404 and GDELT remained HTTP 429. The Phase-3 gate stays open
  only for further external evidence; no source substitution was made.

## Completed in this continuation

- Regenerated the requirement-to-evidence matrix in
  [`gate-matrix.md`](gate-matrix.md).
- Evaluated DuckLake in an isolated environment, verified snapshots,
  time-travel, reopen, and portable-copy recovery, and rejected it on measured
  laptop resource/catalog cost. Evidence SHA-256:
  `77b88992a8dfd64d47ad4da0ee73d197644bb8a21a54d3199b254f4742026154`.
- Pinned and reviewed upstream Hermes Agent `v2026.8.3` at commit
  `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`; completed one synthetic
  coordinator/subagent task in a WSL2 namespace. Evidence SHA-256:
  `2fcfe86c151bffe2f4c59af0f7e0e029005a4ad94675c47fc3c18348a151b51c`.
- Added the resumable exact-route remote stability runner and hash-chain
  contracts; targeted tests pass.
- Preserved and classified the Novita shared-pool HTTP 429 as an open,
  quarantined incident. Incident SHA-256:
  `825e78c3cf416df52ddd1e7b51b4df7801c6bde3adee08149158602ff183a9d6`.
- Re-ran the repository verification pass in the complete locked optional-extra
  environment: full pytest `575 passed`, acceptance suites
  `128/152/123/54/19/34/10/7/27/18/5`, Ruff, format, lock, compilation,
  dashboard build, diff hygiene, ignored-secret, and tracked-model-weight checks.
  The isolated verification environment left the durable worker environment
  unchanged.
- Hardened the generic Phase-0 command-version availability probe with a
  bounded five-second timeout after the local `rclone --version` probe exceeded
  its previous two-second WSL startup budget. This only stabilizes dependency
  inventory classification; it does not run or close the deferred archive gate.
- Preserved and classified the DigitalOcean exact-route stability failure: the
  fresh root recorded 62 cycles with three upstream shared-pool HTTP 429
  gateway abstentions. The failed runner was stopped without altering its
  append-only cycles; the root is quarantined and its incident evidence is at
  `artifacts/phase0/remote-route-stability/20260809T173237.710604Z/incident.json`
  with SHA-256
  `f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`.
- Started replacement root
  `artifacts/phase0/remote-route-stability/20260810T034500Z` after a bounded
  exact-route smoke passed. It recorded 11 passing cycles before an immutable
  upstream HTTP 429 gateway abstention and was quarantined with incident SHA
  `805d763d69841515f7beb676ec2a0dea2e2043106dbb4dbc43b292bff4350e9f`.
- Fixed the exact-route stability runner to stop immediately after a failed
  sample and added regression coverage. A systemd-backed corrected root at
  `artifacts/phase0/remote-route-stability/20260810T053600Z` stopped after its
  first sanitized `deadline_exhausted` gateway abstention and was quarantined
  with incident SHA
  `5b6d5ffe9133811a664f24151b95fcd850f130cff718bc6ed1eae9289178cff1`.
  No failed route samples are concatenated and no route window is currently
  active; another exact-route attempt is provider-availability/time-dependent.
- Updated `configs/models/phase0_remote_roster.json` so the DigitalOcean
  candidate remains eligible for a future reviewed retry while its current
  stability evidence is explicitly `failed_quarantined`, linked to the latest
  hash chain and sanitized incident record.
- Implemented and locally tested the Coinbase Exchange Sandbox-specific
  `CB-ACCESS-*` signer, exact sandbox REST/WS host guard, provider account/
  product/order/fill schema mapping, scoped `PAPER_VENUE` factory, and
  redacted provider-specific read-only smoke runner.
- Ran the real Coinbase read-only smoke against the reviewed sandbox. `/time`,
  `/products`, authenticated `/accounts` projections, `/orders`, and a
  product-filtered `/fills` read were attempted. The returned catalogue
  contained `BTC-USD` but omitted required `ETH-USD`; account, balance,
  position, and open-order reads passed, while the fills read returned
  sanitized HTTP 401. The smoke failed closed and no
  order/cancel/transfer/withdrawal was attempted. Evidence:
  `artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`,
  SHA-256 `79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`.
- Selected Binance Spot Testnet as the single BTC/ETH replacement candidate
  after reviewing the official Spot Testnet API contract and rejecting OKX
  Demo for its production-host simulated-trading boundary. Implemented and
  fixture-tested the provider-specific Binance HMAC signer, exact testnet
  host/path guard, product/filter mapper, account/balance/position/order/fill
  projections, restart-safe symbol query, scoped private smoke, and public
  product qualifier. The public qualifier measured both `BTCUSDT` and
  `ETHUSDT` at
  `artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/binance-spot-testnet-public-truth.json`
  with SHA-256
  `34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6`.
  Authenticated Binance reads and the paper lifecycle were subsequently
  qualified in the current continuation; see the immutable reports in the
  current update above. The real path observed no fill.
- Added the Binance venue-selection decision and runbook. The private smoke
  accepts an explicit `--secrets /mnt/c/projects/advisorai-v3/secrets.env`
  path, resolves only `PAPER_VENUE`, persists reference names and sanitized
  schema/count results, and cannot fall back to production. The Coinbase
  adapter/evidence remains preserved and is not weakened to BTC-only.
- Implemented the credential-free ordered Binance/Bybit paper-venue bake-off in
  `scripts/qualify_paper_venue_candidates.py`. The final real comparison
  queried both non-production APIs for server time, BTC/ETH product truth and
  filters, order books, and public trades. Both candidates passed; Binance was
  selected by the preferred-first rule. No credentials were resolved and no
  write endpoint was called. Evidence:
  `artifacts/phase2/paper-venue-bakeoff/20260810T190539.057729Z/paper-venue-candidate-bakeoff.json`,
  SHA-256
  `78d8034c56a1b651da968129e463d73d23745a95565e1d9e80092a0bbd569b3a`.
  Added five offline regression tests, including exact production-host
  rejection metadata and provider-status collision protection. Bybit remains
  measured but unselected; no second adapter or ledger was added.
- Implemented the scoped two-provider rclone-crypt boundary with backward-
  compatible singular settings, explicit provider A/B raw and crypt aliases,
  sanitized command failures, repo-local explicit secrets plumbing, and the
  controlled qualification runner. The initial real run found no populated
  scoped values and made zero network calls. After the operator populated the
  canonical `/mnt/c/projects/advisorai-v3/secrets.env`, fresh roots
  `20260810T151929.702375Z` and `20260810T152427.385117Z` were preserved. The
  latest root
  `artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z/`
  measured independent Provider A/B crypt uploads/restores, three-way SHA-256
  equality, and all recovery drills. Provider A raw-layer enumeration passed;
  Provider B raw recursive enumeration returned a sanitized provider command
  failure, so the gate remains partial and not qualified. Report SHA-256:
  `be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf`;
  manifest SHA-256:
  `202e1564c1b56fcde7a50e2a0307cbd36a2e05771e6f308c1de51584d3ed9093`.
  The runner now applies the bounded timeout to raw listings and has regression
  coverage in `tests/phase0/test_rclone_qualification.py`.
- Added native provider event-time normalization and the bounded real-source
  qualification runner in `scripts/qualify_phase3_sources.py`. The latest
  real run used seven public calls and recorded successful raw-spool replay for
  Coinbase BTC-USD ticker, Deribit BTC index, and SEC official RSS. Coinbase
  ETH-USD returned HTTP 404 and GDELT returned HTTP 429, so the evidence at
  `artifacts/phase3/source-qualification/20260810T044558.818461Z/` remains
  `EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE`; its evidence SHA-256 is
  `d435e99b59d815700ccfc5d75e309632ecc91fa1aea3cd3b6c7157a02df272bf`.
- Added the bounded public Coinbase Sandbox WSS qualifier in
  `scripts/qualify_phase3_coinbase_wss.py` with host pinning, raw-first
  per-connection spools, typed ticker replay, reconnect measurement, and
  provider sequence-gap and freshness detection. Its two real 12-second
  connections completed and replayed 29 ticker events/23 heartbeats; freshness
  passed with maximum event age 2.078 seconds and maximum heartbeat interval
  1.015 seconds, but both observed provider sequence gaps. Evidence is at
  `artifacts/phase3/coinbase-wss-qualification/20260810T044142.351959Z/` with
  SHA-256 `a41fa2367a7f940e8197d5f8e0188765f9c522086091f93df988e0b2abbde702`;
  the Phase-3 gate remains pending.
- Added the Phase-3 REST/WSS qualification tests to the eleven-phase
  acceptance runner. The Phase-3 acceptance suite now executes 51 tests,
  including freshness, future-timestamp, Binance depth replay, and
  fail-closed coverage.
- Added the isolated Coinbase Sandbox level-2/level2-batch qualifier and
  reducer at `scripts/qualify_phase3_coinbase_level2.py` with six focused
  tests. The direct `level2` channel delivered heartbeats but no snapshot in
  its bounded run. The public `level2_batch` run delivered one BTC-USD
  snapshot, 79 updates, and 12 heartbeats; validation had zero failures,
  live/replay book-state hashes matched, maximum event age was 0.576 seconds,
  and maximum heartbeat interval was 1.081 seconds. Evidence is at
  `artifacts/phase3/coinbase-level2-qualification/20260810T052805.696329Z/phase3-coinbase-level2-qualification.json`
  with SHA-256
  `dc620a8fa41458fa4f89396e33687b13750461a3cd643be1b18d0588092e23de`.
  This is bounded source evidence only; continuous recovery and Phase-3
  admission remain pending.
- Added the credential-free Binance Spot Testnet depth qualifier and Phase-3
  acceptance coverage. It pins the reviewed REST host
  `testnet.binance.vision` and stream host `stream.testnet.binance.vision`,
  persists raw depth/snapshot inputs, validates `U/u` continuity and uncrossed
  books, and compares live processing with raw replay. The real run at
  `artifacts/phase3/binance-spot-testnet-depth/20260810T173135.489992Z/`
  captured four BTC/ETH snapshots and 289 updates with matching final-book
  hashes; all four fresh connections completed, but provider event timestamps
  were ahead of local receipt. Its report SHA-256 is
  `b794c7fd2c014c89928c7bf2ad4b73fde253a615818dddd27a4da53a025c76c0`.
  Injected REST-outage, sequence-gap, stale-data, and snapshot-disagreement
  drills passed. This remains partial external source evidence; synchronized
  freshness, recovery, longer operation, and independent source disagreement
  are not admitted.
- Added bounded provider/local clock-offset measurement to the Binance depth
  qualifier. It spools `/api/v3/time`, keeps raw future-event counts, applies
  only the measured midpoint offset to freshness, and fails closed for invalid
  or excessive offsets. Focused source tests pass; the two real reports above
  predate this implementation and do not claim offset-aware evidence.
- Attempted the missing 120-second Binance BTC/ETH depth window in a fresh
  immutable root. All four public WSS connections failed closed before their
  first message with the sanitized `WebSocketTransportError` class, so no REST
  snapshot was attempted and no write occurred. The preserved report is
  `artifacts/phase3/binance-spot-testnet-depth/20260810T182011.404029Z/phase3-binance-spot-testnet-depth.json`
  with SHA-256
  `7b249a125c78e346c7b9d028850e2b7cbf004c890e005bad6f6f8d70b92ddd08`;
  deterministic fault drills still passed. This is a provider/runtime
  availability failure, not a successful longer-operation qualification.
- Re-ran the Binance depth qualifier after the clock-offset implementation in
  a fresh 20-second BTCUSDT/ETHUSDT root. Both WSS connections failed closed
  before their first message with `WebSocketTransportError`, made zero REST
  calls, and passed deterministic fault drills. Evidence:
  `artifacts/phase3/binance-spot-testnet-depth/20260810T185425.534127Z/phase3-binance-spot-testnet-depth.json`,
  SHA-256
  `daee289fd1373477c5c22f4b792ff4e07b452c93e4544e21f757dde7080e9831`.
  This is preserved as a post-change provider/runtime availability failure,
  not clock-synchronized freshness evidence.
- A further bounded 20-second retry at
  `artifacts/phase3/binance-spot-testnet-depth/20260810T201946.533716Z/phase3-binance-spot-testnet-depth.json`
  failed closed before the first WSS message on all four connections, made
  zero REST calls, and passed deterministic drills. Its SHA-256 is
  `ce402b7bdd67513c90b1cc5bf744d0a8d455a6f1b7f927610a84f997699b8415`.
  This remains provider/runtime availability evidence, not a Phase-3 pass.
- Fixed the Phase-3 raw-spool replay fixture to use its explicit historical
  quality cutoff rather than wall-clock time; this prevents the test from
  becoming stale as the calendar advances. The pre-qualification focused suite
  and full locked verification passed with 570 tests; the post-qualification
  rerun passed 578 tests and is recorded in the current update above.
- Discovered and fixed the selected-model stability runner's missing terminal
  sample at the 24-hour boundary. The fresh post-format root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`
  preserved 273 passing cycles but ended `short_smoke_complete` at
  `23.968570833055555` hours; summary SHA-256:
  `ec8208a4419aef1f1a85dc0d43e984feb6bb6f45b92a65fd67b1be956bad1661`.
  A fresh immutable root is required after the runner fix; no cycles are
  concatenated.
- Measured and hardened the disposable local Docker OS boundary for Phase 8.
  The probe used the explicit local Docker socket, no repository, credential,
  broker, order, or production mounts, and recorded zero external network calls,
  a root-identity read-only-root denial, constrained tmpfs write, zero effective
  capabilities, denied unshare/mount probes, and bounded process controls.
  Evidence is at
  `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
  with SHA-256
  `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`.
  Universal native syscall/C-extension containment, credential/production-tree
  isolation, and a real Hermes capability task remain unattested; formal
  Phase-8 admission stays closed.

## Durable processes

| Process | PID | Evidence root | State |
|---|---:|---|---|
| Selected local model stability | 70598 | `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3` | `PENDING_STABILITY`; r3 started `2026-08-10T18:07:25.593600Z`, 79 cycles had passed at `2026-08-11T01:15:16.975316Z`, last record SHA-256 `1130100d0cff6fd829d7546d147d1b7220f4d4a0e70c56672443e5c5e355c7d2`; inspect heartbeat and preserve the process |
| DigitalOcean exact remote route stability | — | `artifacts/phase0/remote-route-stability/20260810T053600Z` (quarantined) | `QUARANTINED`; first corrected probe ended in deadline exhaustion; retry is provider-availability/time-dependent |

The current selected-model process is detached with its exact command recorded
by the runbook. The selected-model predecessor root
`phase0-selected-24h-terminal-sample-20260810` is preserved as interrupted
after seven passing cycles; r2 is separately preserved as interrupted after
eight passing cycles, both from the sanitized unavailable-cwd failure. Their
interruption records and stderr hashes remain immutable. The fresh r3 run uses
a new admission root, absolute startup/evidence paths, and an explicit
repository root. The failed remote route process was stopped after its immutable
failure evidence was preserved; the replacement uses only `DIRECT_LLM`, a
synthetic structured probe, no fallbacks, and no tool execution. Failed samples
must not be concatenated with the replacement root.

## Remaining gates and blockers

- Phase-0 local model stability remains in its 24-hour duration gate. The
  fresh `phase0-selected-24h-terminal-sample-20260810` root is interrupted
  after seven passing cycles and r2 after eight passing cycles by the
  unavailable-cwd `FileNotFoundError`; both are preserved and cannot be resumed
  or concatenated. The cwd-fix smoke passed, and replacement root
  `phase0-selected-24h-terminal-sample-20260810-r3` is active under PID
  `70598` with a new immutable runtime-admission root; 79 cycles had passed at
  `2026-08-11T01:15:16.975316Z`, and the latest record hash was
  `1130100d0cff6fd829d7546d147d1b7220f4d4a0e70c56672443e5c5e355c7d2`, and the
  24-hour result does not yet exist. All
  current DigitalOcean duration roots are quarantined: the 20260809T173237.710604Z
  root has three upstream shared-pool HTTP 429 abstentions; the corrected
  20260810T053600Z root stopped after a deadline-exhausted first probe. No
  failed samples may be concatenated. A new exact-route attempt is blocked by
  provider availability/time, not by an unreviewed fallback.
- Their incident records are immutable at
  `artifacts/phase0/remote-route-stability/20260809T171000Z/incident.json`
  (SHA-256
  `302220c0b2be692de953848d7cf2b8058baceb271581a776f52f82c3d13f8677`) and
  `artifacts/phase0/remote-route-stability/20260809T173059.039176Z/incident.json`
  (SHA-256
  `a3f8a51aeb5a437b1dd5c570cf86ce2cc4eb47b86e108055fcbf0b0ae34a9f8e`).
- Phase-0 real `rclone crypt` evidence is `EXTERNALLY MEASURED / PARTIAL`, not
  admitted: both independent crypt restores matched the fresh source SHA and
  all recovery drills passed in the latest root, but Provider B raw recursive
  enumeration returned a sanitized provider command failure. The six scoped
  values were consumed only from `/mnt/c/projects/advisorai-v3/secrets.env`;
  no values were printed or persisted. Resolve the reviewed Provider B raw
  listing, then run a fresh explicit qualification. The manual provider copy
  statement is not admission evidence.
- Coinbase Sandbox identity, reviewed REST host, and scoped credentials are
  configured. The current zero-network resolver check passes against the
  canonical repository-local secrets inventory with configuration hash
  `138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`.
  The remaining provider blockers are that the actual sandbox product
  catalogue did not expose `ETH-USD` and the product-filtered fills read
  returned HTTP 401. The required read-only gate and paper lifecycle remain
  closed. Secret values must not be sent in chat.
- Binance Spot Testnet public truth and the authenticated read-only gate pass
  for the required BTC/ETH symbols. One supervised no-fill/cancel lifecycle is
  externally measured and qualified; real fill ingestion is not observed and
  remains fixture-tested. No credential value may be printed, copied into a
  second file, or persisted in evidence.
- Phase 3–7 real source/paper operation and the 60-day soak cannot start until
  the selected venue, source, model, risk, OMS, reconciliation, and recovery
  prerequisites are genuinely admitted.
- Phase 3 now has partial real source evidence, but its complete gate remains
  closed: Coinbase ETH-USD is absent, GDELT is rate-limited, and the bounded
  WSS probe observed provider sequence gaps; the fresh 120-second Binance
  attempt failed closed before its first message with a sanitized transport
  failure; the post-offset 20-second attempt also failed before its first
  message and made zero REST calls. Continuous freshness/recovery and
  cross-source disagreement soak remain pending.
- Phase 8 now has real local Docker boundary evidence for network denial,
  read-only-root denial, capability dropping, and bounded process controls, but
  native-syscall/C-extension, credential, production-tree, and real-capability
  attestation remain incomplete. Formal admission is still closed.
- Alpha E0–E7 remains plan-only and blocked. Phase 10 remains human-controlled.

Next legal work is to preserve the healthy model stability worker, continue
credential-free Phase-3 source/reconnect evidence only when the reviewed
providers are available, and use the already-qualified Binance adapter only
through the deterministic paper chain. The archive
gate is externally deferred and must not be touched in this continuation. No
model, Hermes runtime, or remote route has trading authority.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
