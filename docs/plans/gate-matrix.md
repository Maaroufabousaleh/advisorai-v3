# AdvisorAI V3 gate matrix

Checkpoint regenerated 2026-08-10 from `main` at
`d419f6b91814017d1e877bed818a0b0c3c88b2db` after PR #75 merged.
This matrix separates implementation, tests, local measurements, external
measurements, qualification, and admission. A passing test suite does not open
an external, timed, or human gate.

Current Phase-0 stability addendum (superseding the earlier baseline row): the
r2 root recorded eight passing cycles and then failed closed on the same
`FileNotFoundError` working-directory loss as its predecessor; its immutable
interruption record SHA-256 is
`4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`. The
absolute-path runner fix is implemented and regression-tested; a one-cycle
cwd-fix smoke passed with all three candidates, while fresh r3 is active under
PID `70598` from `2026-08-10T18:07:25.593600Z`. State remains
`PENDING_STABILITY`; no predecessor cycles are concatenated and no roster role
is promoted.

| Stage / requirement | Authoritative source | Implementation present? | Automated tests? | Local deterministic evidence? | Real external evidence? | Timed evidence? | Human action? | Current gate state | Blocker | Next admissible action |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| Phase 0 contracts, ports, policy gateway, model/runtime harness | architecture §11; phase-00 plan | yes | yes | yes | no | no | no | TESTED / LOCALLY MEASURED | none for local boundary | Preserve accepted local records; do not treat them as admission |
| Phase 0 selected-model stability: TTM-R2, Finance DeBERTa-v3, FinBERT-MiniLM | phase-00 plan; model-runtime runbook | yes, including terminal-sample boundary fix, absolute startup/evidence paths, and explicit repository-root launch | yes | partial | no | pending | no | PENDING_STABILITY / INTERRUPTED-THEN-RESTARTED | predecessor root `phase0-selected-24h-post-format-final-20260809` ended `short_smoke_complete` at `23.968570833055555` hours after 273 passing cycles; r1 recorded 7 passing cycles and r2 recorded 8 before the same sanitized unavailable-cwd `FileNotFoundError`; r2 interruption SHA-256 is `4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`; fresh r3 is active under PID `70598` after a passing one-cycle smoke | Preserve all interrupted roots and the new admission root separately; inspect r3 heartbeat and wait for a real terminal sample; never concatenate cycles |
| Phase 0 remote route bake-off | phase-00 plan; remote-model runbook | yes, including resumable stability runner with stop-on-failure | yes | short live route evidence plus preserved hash-chained failures | yes, exact provider/model/endpoint identity on earlier successful samples; current retries failed closed | pending 24-hour window | provider availability/time-dependent | EXTERNALLY MEASURED / QUARANTINED | Novita and DigitalOcean roots are quarantined after shared-pool HTTP 429, deadline exhaustion, and earlier runner-integrity incidents; corrected root `artifacts/phase0/remote-route-stability/20260810T053600Z` stopped after its first failed probe and has no eligible duration evidence | Preserve incident roots; retry the reviewed exact route only after provider availability returns, using a fresh systemd-backed root; never concatenate failed samples |
| Phase 0 Nautilus / Prefect / Hamilton seams | phase-00 plan | yes | yes | yes, credential-free component drill | no provider-specific evidence | no | no | TESTED / QUARANTINED | external Nautilus qualification and operational use remain governed by Phase 0 | Keep local seam evidence; qualify only through the selected gate |
| Phase 0 Parquet-manifest vs DuckLake comparison | architecture §4.2; phase-00 plan | manifest/DuckDB baseline yes | baseline yes | yes | yes, isolated challenger review | no | no | QUALIFIED / REJECTED | DuckLake snapshot/reopen worked, but the second catalog added measurable footprint and relocation override complexity without enough incremental value | Keep manifest-managed Parquet + DuckDB + SQLite WAL; preserve the immutable comparison report |
| Phase 0 external Hermes coordinator/subagent review | architecture §8; phase-00 plan | repository harness and pinned external runtime reviewed | local security tests yes | yes | yes, synthetic loopback route only | no | no | EXTERNALLY MEASURED / QUARANTINED | real provider/model route and complete native/filesystem OS attestation remain absent | Preserve the pinned review; formal admission remains closed and no runtime enters AdvisorAI core |
| Phase 0 rclone-crypt upload/verify/restore | architecture §4.2; phase-00 plan; rclone archive qualification runbook | typed adapter, scoped process environment, backward-compatible singular config, explicit A/B provider pairs, and bounded raw-list timeout yes | `tests/expansion/test_rclone.py`, `tests/config/test_secrets.py`, `tests/phase0/test_rclone_qualification.py`, and qualification runner pass | in-memory restore yes; fresh real sanitized roots | yes, independent A/B crypt upload/restore and three-way SHA equality; Provider A raw-layer check passed, Provider B recursive raw enumeration failed | no | provider-B raw-listing recovery/configuration review | EXTERNALLY MEASURED / PARTIAL / NOT QUALIFIED | Latest root `artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z` report SHA-256 `be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf` has Provider B raw-layer command failure; no plaintext exposure is claimed for the incomplete listing | Diagnose or remediate the reviewed Provider B raw listing, then run a fresh explicit A/B qualification; never promote the three-way restore alone to archive admission |
| Phase 0 resource/privacy/failure behavior | phase-00 plan; resource and gateway runbooks | yes | yes | yes | partial route observations | stability pending | no | TESTED / PENDING_STABILITY | selected runtime duration and real route repetition remain incomplete | Continue durable stability and bounded route evidence |
| Phase 1 deterministic foundation and local rollback/Bronze rebuild | phase-01 plan | yes | yes | immutable local report | no provider deployment | no | no | QUALIFIED LOCALLY | real paper deployment rollback and archive restore remain external | Preserve local report; run provider-specific drill only after venue setup |
| Phase 2 deterministic paper core and Coinbase Exchange Sandbox transport | phase-02 plan; real-api-paper-transition.md | yes; Coinbase-specific `CB-ACCESS-*` signer, schema mapper, exact sandbox host guard, and read-only smoke runner | yes, including `tests/integrations/test_coinbase_exchange.py` | replay/failure fixtures plus signer/product/OMS boundary tests | partial: real Coinbase `/time`, `/products`, `/accounts`, `/orders`, and `/fills` requests reached the reviewed sandbox; account/balance/position/open-order reads passed, but product mapping and fills did not | no | provider catalogue/profile and fills-permission action | EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | the returned 13-product sandbox catalogue contained `BTC-USD` but not the required `ETH-USD`; the product-filtered fills read returned HTTP 401; no order writes were attempted | Preserve the Coinbase evidence and rerun only if a reviewed sandbox profile genuinely exposes both required products and grants the documented fills read permission; never fall back to the generic smoke or production |
| Phase 2 selected BTC/ETH paper venue candidate — Binance Spot Testnet | phase-02 plan; real-api-paper-transition.md; paper-venue-selection.md | yes; provider-specific HMAC signer, exact testnet host/path guard, product/filter mapper, account/balance/position/order/fill schema mapper, scoped read-only smoke, and existing `NativeTransport` boundary | yes, `tests/integrations/test_binance_spot.py` plus offline config/path tests | signer, provider schema, idempotent write, restart-query, and production/transfer rejection fixtures; public server/product qualifier | public Spot Testnet server time and catalogue measured; required `BTCUSDT` and `ETHUSDT` were present in `artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/` with evidence SHA-256 `34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6`; authenticated reads not yet run | no | operator must review the single `PAPER_VENUE` profile for the exact Binance testnet host and create/restrict testnet API credentials | IMPLEMENTED / TESTED / EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | public product truth passes, but the current scoped profile is still Coinbase and no authenticated Binance read-only smoke or paper order has been admitted | Run `scripts/check_transition_config.py` with `testnet.binance.vision`, then the explicit opt-in `scripts/smoke_binance_spot_testnet.py`; do not send an order until all read-only operations pass |
| Phase 3 V3-Core source spine | phase-03 plan; real-api-paper-transition.md | yes, including bounded raw-first REST/WSS qualification runners, native event-time normalization, WSS freshness measurement, and a separate Coinbase level-2 book reducer | yes, `tests/data`, `tests/phase3/test_source_qualification.py`, `tests/phase3/test_coinbase_wss_qualification.py`, `tests/phase3/test_coinbase_level2_qualification.py` | parser/replay fixtures | partial: the latest bounded REST retry still records BTC-USD native ticker, Deribit index, and SEC RSS replay passes, Coinbase ETH-USD 404, and GDELT 429; ticker WSS freshness passed but provider sequence gaps were observed; public `level2_batch` measured one BTC-USD snapshot, 79 updates, and 12 heartbeats with zero book-validation failures and matching replay state | no continuous source operation | no, reviewed public endpoints only | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | REST root `artifacts/phase3/source-qualification/20260810T044558.818461Z` has 3 passes/2 failures; ticker WSS remains gap-flagged; direct `level2` had no snapshot, while `level2_batch` bounded evidence passed | Preserve immutable REST/WSS/level2 roots; collect longer freshness, reconnect/recovery, and source-disagreement evidence without substituting sources |
| Phase 3 current evidence addendum | phase-03 plan; real-api-paper-transition.md | Binance public depth qualifier and raw snapshot/update replay are implemented | `tests/phase3/test_binance_depth_qualification.py`; Phase-3 acceptance includes it | reducer, replay, and deterministic fault-drill fixtures | real root `artifacts/phase3/binance-spot-testnet-depth/20260810T173135.489992Z/phase3-binance-spot-testnet-depth.json`, SHA-256 `b794c7fd2c014c89928c7bf2ad4b73fde253a615818dddd27a4da53a025c76c0`: four BTC/ETH snapshots and 289 updates replay-equivalent; all four connections completed; provider event timestamps were ahead of local receipt | no continuous window | no | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | clock-drift/freshness failure; independent source disagreement remains unmeasured | preserve the immutable root; perform one clock-synchronized recovery run and collect independent-source disagreement |
| Phase 4 quantitative baseline council | phase-04 plan | yes | yes | public-data bake-off and roster | no paper utility | stability pending | no | LOCALLY MEASURED / PENDING_STABILITY | role winners require stability; paper net utility is later | Finish Phase 0 stability, then collect real paper outcomes |
| Phase 5 typed evidence council | phase-05 plan | yes | yes | independence/authority fixtures | no real V3-Core scored council | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real source/model/provider route and data are absent | Exercise with admitted real V3-Core data after earlier gates |
| Phase 6 institutional controls and attribution | phase-06 plan | yes | yes | deterministic risk/attribution fixtures | no real paper order sample | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real paper fills and residual incidents are absent | Run the complete paper chain and reconcile exact attribution |
| Phase 7 unattended paper soak and recovery | phase-07 plan | yes | yes | local soak/recovery fixtures | no | 60 calendar days required | operator supervision | PENDING_TIME_GATE | Phase 0–6 real prerequisites and venue operation are not ready | Prepare durable runner; launch only after prerequisites are real |
| Phase 8 Hermes capability lifecycle | phase-08 plan | yes, including disposable Docker boundary probe | yes, including `tests/capabilities/test_os_sandbox_probe.py` | immutable fixture active-read report plus real local Docker boundary measurement | partial: pinned external runtime/synthetic task; no real model route or real Hermes capability task | no | review required for active-write only | EXTERNALLY MEASURED / QUARANTINED | Docker measured network denial, read-only root denial, zero effective capabilities, and bounded process controls, but native syscall/C-extension containment and credential/production-tree isolation are not attested; earlier gates remain closed | Preserve the OS-boundary report; evaluate a real isolated Hermes capability only after earlier phase gates and stronger containment evidence permit it |
| Phase 9 controlled expansion | phase-09 plan | yes | yes | challenger/source boundaries | no marginal-value challenger evidence | no | no | QUARANTINED | Phase 0–7 and E0 are not satisfied | Keep additions quarantined; reject challengers with evidence when evaluated |
| Phase 10 bounded-live readiness guards | phase-10 plan | yes | yes | readiness/AI-offline fixtures | no live validation | no | explicit human approval required | PENDING_OPERATOR_ACTION | Phase 7 and all prerequisites incomplete; no human authorization | Keep live closed; do not create approval or enable production |
| Real API/paper transition bridge | real-api-paper-transition.md; Coinbase and Binance connector runbooks | yes | yes | offline config/adapter evidence, Coinbase and Binance contract tests | partial: Coinbase private reads reached the reviewed sandbox but failed required product/fills checks; Binance public server/product truth measured both required symbols | no | operator venue selection/profile review and Binance testnet credentials | EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | Coinbase cannot satisfy the required ETH-USD/fills gate from the observed sandbox; Binance authenticated read-only evidence and paper lifecycle are still absent | Keep Coinbase quarantined, review the Binance Spot Testnet profile in the canonical `PAPER_VENUE` inventory, then run the Binance-specific read-only smoke; no generic smoke or production fallback |
| Alpha E0 — V3-Core prerequisite | alpha-team-extension.md | plan-only | none | none beyond base evidence | no | inherits Phase 0–7 | no | BLOCKED | Phase 0–7 paper/recovery/data/risk/resource gates are not complete | Do not implement Alpha runtime; continue base gates |
| Alpha E1 — Research Brain | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0 and Phase 7 prerequisite | Wait; only maintain plan/traceability |
| Alpha E2 — Controlled Alpha Lab | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0/E1 gates | Do not build DSL or candidate runtime early |
| Alpha E3 — first V3 strategy challenger | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E2 plus real V3-Core paper evidence | Wait for admission |
| Alpha E4 — optional capability adapters | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E3 and Phase 8/9 authority | Keep external challengers quarantined |
| Alpha E5 — equities / long horizon | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E4 and point-in-time equity gate | No equity expansion |
| Alpha E6 — controlled candidate expansion | alpha-team-extension.md | no, plan-only | no | no | 60+ healthy paper days required | no | BLOCKED | E5 and per-scope soak | No candidate activation |
| Alpha E7 — bounded-live scope | alpha-team-extension.md | no, plan-only | no | no | no | human approval required | BLOCKED | Phase 10 explicit go-live review | No live scope or approval |

## Current external blockers

- Coinbase Exchange Sandbox configuration is present in the local ignored
  `secrets.env` and passed the zero-network reviewed-host check with configuration
  hash `138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`.
  The adapter uses only the `PAPER_VENUE` credential scope; secret values were
  not printed or persisted.
- The latest real Coinbase read-only attempt reached `/time`, `/products`,
  authenticated `/accounts` projections, `/orders`, and a product-filtered
  `/fills` read, then failed closed because the returned catalogue had `BTC-USD`
  but no required `ETH-USD`; account/balance/position/open-order reads passed,
  while fills returned sanitized HTTP 401. Immutable sanitized evidence is at
  `artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`
  with SHA-256 `79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`.
  No order, cancel, transfer, or withdrawal was attempted.
- Binance Spot Testnet is the selected BTC/ETH replacement candidate. Its
  public, credential-free qualifier measured server time and provider product
  truth for both `BTCUSDT` and `ETHUSDT`. Evidence is at
  `artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/binance-spot-testnet-public-truth.json`
  with SHA-256
  `34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6`.
  The provider-specific adapter and private smoke are implemented and tested;
  authenticated Binance evidence remains pending because the canonical scoped
  `PAPER_VENUE` profile has not yet been reviewed/switched to Binance. No
  Binance credential value was printed or persisted, and no order was sent.
- The Binance private smoke requires the operator to review the existing single
  `PAPER_VENUE` inventory for the exact `testnet.binance.vision` host and
  restricted fake-funds credentials, then run the explicit `--secrets`
  command in `docs/runbooks/binance-spot-testnet.md`. A second secrets file is
  not allowed.
- The current zero-network resolver check passes against the canonical
  repository-local secrets inventory with configuration hash
  `138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`; no
  second secrets inventory is maintained.
- The typed two-provider rclone qualification boundary is implemented and
  fixture-tested. The initial controlled real-run attempt found no populated
  `ARCHIVE_RCLONE` values and made zero network calls. The latest fresh root at
  `artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z/`
  measured independent A/B crypt uploads/restores, three-way SHA equality, and
  all recovery drills. Provider A raw-layer enumeration passed; Provider B raw
  recursive enumeration returned a sanitized provider command failure. The
  latest report SHA-256 is
  `be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf`; the
  manual A/B copy statement is deliberately not counted as qualification.
- The previous Phase-0 24-hour worker was interrupted by the laptop shutdown;
  its evidence is preserved. The post-format replacement root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`
  ended `short_smoke_complete` at 23.96857 hours after 273 passing cycles; its
  summary SHA-256 is
  `ec8208a4419aef1f1a85dc0d43e984feb6bb6f45b92a65fd67b1be956bad1661`. The
  runner now requires a real terminal sample at/after the duration boundary.
  Fresh root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810`
  recorded 7 passing cycles and then exited at cycle execution with a
  sanitized `FileNotFoundError` because its worker cwd was unavailable. The
  preserved interruption evidence is
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810/interruption.json`;
  the stderr-log SHA-256 is
  `482f878994c8dbf8b339cb48460ae576b37400f1209f7ce76f7d988a181f68e6`.
  Replacement root
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r2`
  is preserved as interrupted after eight passing cycles; its interruption
  record SHA-256 is `4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`.
  Fresh r3 is active under PID `70598` with cycle 1 passing; do not concatenate
  roots.
- DuckLake comparison is complete and rejected with measured evidence at
  `artifacts/phase0/ducklake-comparison/20260809T162300Z/ducklake-comparison.json`.
- The pinned upstream Hermes review is complete as partial external-runtime
  evidence at
  `artifacts/phase0/external-hermes-review/20260809T162031Z/external-hermes-review.json`;
  it used a synthetic loopback provider and does not open Phase 8.
- A disposable Docker boundary probe was measured on 2026-08-10 at
  `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
  with SHA-256
  `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`.
  It recorded zero external network calls, a root-identity read-only root
  filesystem check, a constrained writable tmpfs, dropped capabilities, denied
  unshare/mount escape probes, and bounded process controls using the local
  Docker runtime. It did not mount the repository, credentials, broker, order,
  or production paths. Universal native syscall and C-extension containment
  remain `not_attested`, so this evidence is not formal Hermes or Phase-8
  admission.
- The exact Novita route stability trial is preserved as a failed/quarantined
  run after an upstream shared-pool HTTP 429. Earlier DigitalOcean roots were
  quarantined for runner-integrity defects, and the later root at
  `artifacts/phase0/remote-route-stability/20260809T173237.710604Z` recorded 62
  cycles with three immutable upstream shared-pool HTTP 429 gateway abstentions;
  its incident SHA-256 is
  `f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`.
  Root `artifacts/phase0/remote-route-stability/20260810T034500Z` then recorded
  11 passing cycles followed by an HTTP 429 and is quarantined by incident SHA
  `805d763d69841515f7beb676ec2a0dea2e2043106dbb4dbc43b292bff4350e9f`.
  Corrected root `artifacts/phase0/remote-route-stability/20260810T053600Z`
  stopped after its first deadline-exhausted probe and is quarantined by
  incident SHA `5b6d5ffe9133811a664f24151b95fcd850f130cff718bc6ed1eae9289178cff1`.
  No route window is active; failed samples are not concatenated.
- Phase 7 requires real paper/testnet operation plus an actual 60-day duration.
- Phase 10 requires explicit human approval and remains closed.

## Safety truth

No model, LLM route, Hermes task, browser task, dashboard, or Alpha Team plan
has trading authority. `RiskKernel` remains the deterministic veto and `OMS`
remains authoritative. Live-capital deployment is not approved.
