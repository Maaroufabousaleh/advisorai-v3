# AdvisorAI V3 continuation checkpoint

Checkpoint captured 2026-08-10 on `main`
`c55afcd4ed168fe798f54841ac090c234b99783a` after PR #50 merged.

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
  environment: full pytest `521 passed`, acceptance suites
  `124/152/107/22/19/34/10/7/25/18/5`, Ruff, format, lock, compilation,
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
  sanitized command failures, and the controlled qualification runner. The
  first explicit real-run attempt generated a fresh source artifact but found
  no populated `ARCHIVE_RCLONE` values and made zero network calls. Its
  immutable evidence is at
  `artifacts/phase0/rclone-crypt-qualification/20260810T003430.872217Z/rclone-crypt-qualification.json`
  with manifest SHA-256
  `fde44ab7ed3e0572c999b6a749f6eeeb718e39251e070939e71ad045ccfe7aed` and
  canonical evidence SHA-256
  `fb044389dbcb9bbe52a469c9993bf8cc45d1c11c83dcdcf259e2d6d4bc5bd67b`.

## Durable processes

| Process | PID | Evidence root | State |
|---|---:|---|---|
| Selected local model stability | 9456 | `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809` | `PENDING_STABILITY`; inspect status/heartbeat before action |
| DigitalOcean exact remote route stability | stopped after PID 33057 | `artifacts/phase0/remote-route-stability/20260809T173237.710604Z` | `QUARANTINED`; three upstream shared-pool HTTP 429 gateway abstentions; fresh 24-hour root required |

The local process remains detached with its exact command recorded by the
runbook. The failed remote route process was stopped after its immutable failure
evidence was preserved; its run used only `DIRECT_LLM`, a synthetic structured
probe, no fallbacks, and no tool execution. Failed samples must not be
concatenated with a future root.

## Remaining gates and blockers

- Phase-0 local model stability remains in its 24-hour duration gate. The
  DigitalOcean route root failed closed on three upstream shared-pool HTTP 429
  gateway abstentions and must be replaced by a fresh immutable 24-hour root;
  no failed samples may be concatenated.
- The earlier DigitalOcean roots were preserved and quarantined for runner
  integrity defects. The latest root is also quarantined by the incident above;
  only a future fresh root with immutable config/code attestation is eligible
  for continued evidence.
- Their incident records are immutable at
  `artifacts/phase0/remote-route-stability/20260809T171000Z/incident.json`
  (SHA-256
  `302220c0b2be692de953848d7cf2b8058baceb271581a776f52f82c3d13f8677`) and
  `artifacts/phase0/remote-route-stability/20260809T173059.039176Z/incident.json`
  (SHA-256
  `a3f8a51aeb5a437b1dd5c570cf86ce2cc4eb47b86e108055fcbf0b0ae34a9f8e`).
- Phase-0 real `rclone crypt` upload/verify/restore remains
  `PENDING_OPERATOR_ACTION`: configure `RCLONE_CONFIG`,
  `RCLONE_CONFIG_PASS`, `RCLONE_REMOTE_A`, `RCLONE_CRYPT_REMOTE_A`,
  `RCLONE_REMOTE_B`, and `RCLONE_CRYPT_REMOTE_B` through the reviewed scoped
  secrets boundary, then rerun the command in the rclone archive runbook. Do
  not paste any values into chat. The manual provider copy statement is not
  admission evidence.
- Coinbase Sandbox identity, reviewed REST host, and scoped credentials are
  configured. The remaining provider blockers are that the actual sandbox
  product catalogue did not expose `ETH-USD` and the product-filtered fills
  read returned HTTP 401. A later strict local resolver check also rejected a
  non-allowlisted inventory variable before constructing the scoped resolver;
  no value was logged or persisted. The required read-only gate and paper
  lifecycle remain closed. Secret values must not be sent in chat.
- Phase 3–7 real source/paper operation and the 60-day soak cannot start until
  earlier gates and venue prerequisites are real.
- Phase 8 OS filesystem/native-syscall/C-extension attestation is incomplete;
  namespace network denial alone is not formal admission evidence.
- Alpha E0–E7 remains plan-only and blocked. Phase 10 remains human-controlled.

Next legal work is to preserve the healthy local stability worker, complete the
remaining unblocked Phase-0 checks, and start a fresh exact-route window only
when the reviewed DigitalOcean route is expected to be available. Rerun the
Coinbase-specific read-only smoke only after the reviewed sandbox catalogue
genuinely exposes both required products. No model, Hermes runtime, or remote
route has trading authority.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
