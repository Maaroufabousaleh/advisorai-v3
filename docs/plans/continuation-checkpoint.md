# AdvisorAI V3 continuation checkpoint

Checkpoint captured 2026-08-10 on `main`
`0c499af95ef6072b71577f350d15315ee6a48c09` after PR #66 merged.

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
  environment: full pytest `547 passed`, acceptance suites
  `126/152/107/44/19/34/10/7/27/18/5`, Ruff, format, lock, compilation,
  dashboard build, diff hygiene, ignored-secret, and tracked-model-weight checks.
  The isolated verification environment left the durable worker environment
  unchanged.
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
  acceptance runner. The Phase-3 acceptance suite now executes 44 tests,
  including freshness and future-timestamp fail-closed coverage.
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
- Fixed the Phase-3 raw-spool replay fixture to use its explicit historical
  quality cutoff rather than wall-clock time; this prevents the test from
  becoming stale as the calendar advances. The focused suite and full locked
  verification now pass with 547 tests.
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
| Selected local model stability | 9456 | `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809` | `PENDING_STABILITY`; inspect status/heartbeat before action |
| DigitalOcean exact remote route stability | — | `artifacts/phase0/remote-route-stability/20260810T053600Z` (quarantined) | `QUARANTINED`; first corrected probe ended in deadline exhaustion; retry is provider-availability/time-dependent |

Both current processes are detached with exact commands recorded by their
runbooks. The failed remote route process was stopped after its immutable
failure evidence was preserved; the replacement uses only `DIRECT_LLM`, a
synthetic structured probe, no fallbacks, and no tool execution. Failed samples
must not be concatenated with the replacement root.

## Remaining gates and blockers

- Phase-0 local model stability remains in its 24-hour duration gate. All
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
  configured. The remaining provider blockers are that the actual sandbox
  product catalogue did not expose `ETH-USD` and the product-filtered fills
  read returned HTTP 401. A later strict local resolver check also rejected a
  non-allowlisted inventory variable before constructing the scoped resolver;
  no value was logged or persisted. The required read-only gate and paper
  lifecycle remain closed. Secret values must not be sent in chat.
- Phase 3–7 real source/paper operation and the 60-day soak cannot start until
- Phase 3 now has partial real source evidence, but its complete gate remains
  closed: Coinbase ETH-USD is absent, GDELT is rate-limited, and the bounded
  WSS probe observed provider sequence gaps. Continuous freshness/recovery and
  cross-source disagreement soak remain pending.
- Phase 8 now has real local Docker boundary evidence for network denial,
  read-only-root denial, capability dropping, and bounded process controls, but
  native-syscall/C-extension, credential, production-tree, and real-capability
  attestation remain incomplete. Formal admission is still closed.
- Alpha E0–E7 remains plan-only and blocked. Phase 10 remains human-controlled.

Next legal work is to preserve the healthy model stability worker, resolve or
reproduce the Provider B raw-layer listing failure, complete the remaining
unblocked Phase-0 checks, and inspect durable evidence without restarting or
concatenating runs. Rerun the Coinbase-specific read-only smoke only after the
reviewed sandbox catalogue genuinely exposes both required products. No model,
Hermes runtime, or remote route has trading authority.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
