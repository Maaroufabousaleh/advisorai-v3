# AdvisorAI V3 continuation checkpoint

Checkpoint captured 2026-08-09 on branch
`continuation/phase0-evidence-and-stability`, based on main
`b16964544ddb9bfe12ef842f524273167df6d315`.

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
- Completed the repository verification pass: full pytest `501 passed`,
  acceptance suites `123/151/96/22/19/34/10/7/25/11/5`, Ruff, format, lock,
  compilation, dashboard build, diff hygiene, and secret/model-weight checks.

## Durable processes

| Process | PID | Evidence root | State |
|---|---:|---|---|
| Selected local model stability | 9456 | `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809` | `PENDING_STABILITY`; inspect status/heartbeat before action |
| DigitalOcean exact remote route stability | 33057 | `artifacts/phase0/remote-route-stability/20260809T173237.710604Z` | `PENDING_STABILITY`; passing samples at checkpoint |

Both processes are detached with exact commands recorded by their runbooks and
must not be restarted or concatenated with earlier evidence. The remote route
run uses only `DIRECT_LLM`, a synthetic structured probe, no fallbacks, and no
tool execution.

## Remaining gates and blockers

- Phase-0 local model stability and DigitalOcean route stability require their
  full 24-hour durations.
- The earlier DigitalOcean roots were preserved and quarantined for runner
  integrity defects; only the fresh root with immutable config/code
  attestation is eligible for continued evidence.
- Their incident records are immutable at
  `artifacts/phase0/remote-route-stability/20260809T171000Z/incident.json`
  (SHA-256
  `302220c0b2be692de953848d7cf2b8058baceb271581a776f52f82c3d13f8677`) and
  `artifacts/phase0/remote-route-stability/20260809T173059.039176Z/incident.json`
  (SHA-256
  `a3f8a51aeb5a437b1dd5c570cf86ce2cc4eb47b86e108055fcbf0b0ae34a9f8e`).
- Phase-0 real `rclone crypt` upload/verify/restore requires operator-configured
  archive remotes; the two-provider restore is unavailable.
- Paper venue identity, reviewed allowlist, sandbox/testnet credentials, and
  read-only smoke require operator configuration. Secret values must not be
  sent in chat.
- Phase 3–7 real source/paper operation and the 60-day soak cannot start until
  earlier gates and venue prerequisites are real.
- Phase 8 OS filesystem/native-syscall/C-extension attestation is incomplete;
  namespace network denial alone is not formal admission evidence.
- Alpha E0–E7 remains plan-only and blocked. Phase 10 remains human-controlled.

Next legal work is to inspect both durable processes, preserve any failure
evidence, complete the remaining unblocked Phase-0 checks, and only then
advance the earliest satisfied gate. No model, Hermes runtime, or remote route
has trading authority.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
