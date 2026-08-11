# AdvisorAI V3 gate matrix

Checkpoint refreshed 2026-08-11 from the clean `main` anchor
`647cb3a65b19ed088fda9ff1e86d6a25ae6139aa` (PRs #86–#112 merged; PRs #95–#96
are documentation-only follow-ups to the #94 implementation/evidence anchor;
PR #103 adds the offline Phase-3 qualification validator, PR #105 records the
independent Phase-3 availability recheck, and PR #108 adds the durable Phase-7
runner boundary; PR #109 adds the offline Phase-3 admission evaluator, PR #110
adds the terminal-sample runner fix, and PR #112 requires the explicit terminal
marker during review).
The
Phase-3 durable source-health implementation, bounded snapshot resource fix,
concurrent symbol collection, accurate connection accounting, resource sidecar,
offline validation, and separate offline admission evaluator are merged. The
completed corrected real qualification root is recorded below and is not an
admission record.
Future durable windows now include an explicit post-boundary terminal sample;
the active r4 root predates that fix and remains separately identified by its
recorded code hash.
This matrix separates implementation, tests, local measurements, external
measurements, qualification, and admission. A passing test suite does not open
an external, timed, or human gate.

## Latest Phase-4 utility preparation

PR #114 adds the offline, fail-closed Phase-4 paper-utility boundary under
`src/advisorai/phase4/paper_utility.py` and its preparation command under
`scripts/prepare_phase4_utility_evaluation.py`. The contract is ready to
consume admitted BTC/ETH paper observations, but rejects unadmitted Phase-3
input and cannot open Phase-4 admission. Its preparation manifest is
`artifacts/phase4/utility-evaluation-preparation/20260811T051344.190783212Z-offline-contract-v1/phase4-utility-preparation.json`
with SHA-256
`620f2ce32bb19aed8ce64ed0c12cddd4e0684db9f5b78add11e5b8ce6445456b`.
This is implementation/local evidence only; no real paper utility or model
promotion is claimed.

Current Phase-0 stability addendum (superseding the earlier baseline row): the
r2 root recorded eight passing cycles and then failed closed on the same
`FileNotFoundError` working-directory loss as its predecessor; its immutable
interruption record SHA-256 is
`4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`. The
absolute-path runner fix is implemented and regression-tested; a one-cycle
cwd-fix smoke passed with all three candidates, while fresh r3 is active under
PID `70598` from `2026-08-10T18:07:25.593600Z`; a read-only observation recorded
sequence 107 at `2026-08-11T03:47:10.345140Z` with record SHA-256
`c3e9e65afe59a78c80687ca19243e28cbf70f227131e4f207c1a05c8bd34b02f`. State remains
`PENDING_STABILITY`; no predecessor cycles are concatenated and no roster role
is promoted. The prompt-named non-r3 root remains append-only and is not
modified; its status file still names PID `12973`, while that PID is no longer
present in the host process table. The separately active r3 root is preserved
under PID `70598` with the same read-only observation; this is not a terminal
sample and does not open the timed gate.

| Stage / requirement | Authoritative source | Implementation present? | Automated tests? | Local deterministic evidence? | Real external evidence? | Timed evidence? | Human action? | Current gate state | Blocker | Next admissible action |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| Phase 0 contracts, ports, policy gateway, model/runtime harness | architecture §11; phase-00 plan | yes | yes | yes | no | no | no | TESTED / LOCALLY MEASURED | none for local boundary | Preserve accepted local records; do not treat them as admission |
| Phase 0 selected-model stability: TTM-R2, Finance DeBERTa-v3, FinBERT-MiniLM | phase-00 plan; model-runtime runbook | yes, including terminal-sample boundary fix, absolute startup/evidence paths, and explicit repository-root launch | yes | partial | no | pending | no | PENDING_STABILITY / INTERRUPTED-THEN-RESTARTED | predecessor root `phase0-selected-24h-post-format-final-20260809` ended `short_smoke_complete` at `23.968570833055555` hours after 273 passing cycles; r1 recorded 7 passing cycles and r2 recorded 8 before the same sanitized unavailable-cwd `FileNotFoundError`; r2 interruption SHA-256 is `4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`; fresh r3 is active under PID `70598`, with read-only sequence 107 at `2026-08-11T03:47:10.345140Z` and record SHA-256 `c3e9e65afe59a78c80687ca19243e28cbf70f227131e4f207c1a05c8bd34b02f`; the target terminal sample has not occurred | Preserve all interrupted roots and the new admission root separately; inspect r3 heartbeat and wait for a real terminal sample; never concatenate cycles |
| Phase 0 remote route bake-off | phase-00 plan; remote-model runbook | yes, including resumable stability runner with stop-on-failure | yes | short live route evidence plus preserved hash-chained failures | yes, exact provider/model/endpoint identity on earlier successful samples; current retries failed closed | pending 24-hour window | provider availability/time-dependent | EXTERNALLY MEASURED / QUARANTINED | Novita and DigitalOcean roots are quarantined after shared-pool HTTP 429, deadline exhaustion, and earlier runner-integrity incidents; corrected root `artifacts/phase0/remote-route-stability/20260810T053600Z` stopped after its first failed probe and has no eligible duration evidence | Preserve incident roots; retry the reviewed exact route only after provider availability returns, using a fresh systemd-backed root; never concatenate failed samples |
| Phase 0 Nautilus / Prefect / Hamilton seams | phase-00 plan | yes | yes | yes, credential-free component drill | no provider-specific evidence | no | no | TESTED / QUARANTINED | external Nautilus qualification and operational use remain governed by Phase 0 | Keep local seam evidence; qualify only through the selected gate |
| Phase 0 Parquet-manifest vs DuckLake comparison | architecture §4.2; phase-00 plan | manifest/DuckDB baseline yes | baseline yes | yes | yes, isolated challenger review | no | no | QUALIFIED / REJECTED | DuckLake snapshot/reopen worked, but the second catalog added measurable footprint and relocation override complexity without enough incremental value | Keep manifest-managed Parquet + DuckDB + SQLite WAL; preserve the immutable comparison report |
| Phase 0 external Hermes coordinator/subagent review | architecture §8; phase-00 plan | repository harness and pinned external runtime reviewed | local security tests yes | yes | yes, synthetic loopback route only | no | no | EXTERNALLY MEASURED / QUARANTINED | real provider/model route and complete native/filesystem OS attestation remain absent | Preserve the pinned review; formal admission remains closed and no runtime enters AdvisorAI core |
| Phase 0 rclone-crypt upload/verify/restore | architecture §4.2; phase-00 plan; rclone archive qualification runbook | typed adapter, scoped process environment, backward-compatible singular config, explicit A/B provider pairs, and bounded raw-list timeout yes | `tests/expansion/test_rclone.py`, `tests/config/test_secrets.py`, `tests/phase0/test_rclone_qualification.py`, and qualification runner pass | in-memory restore yes; fresh real sanitized roots | yes, independent A/B crypt upload/restore and three-way SHA equality; Provider A raw-layer check passed, Provider B recursive raw enumeration failed | no | provider-B raw-listing recovery/configuration review | EXTERNALLY MEASURED / PARTIAL / NOT QUALIFIED | Latest root `artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z` report SHA-256 `be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf` has Provider B raw-layer command failure; no plaintext exposure is claimed for the incomplete listing | Diagnose or remediate the reviewed Provider B raw listing, then run a fresh explicit A/B qualification; never promote the three-way restore alone to archive admission |
| Phase 0 resource/privacy/failure behavior | phase-00 plan; resource and gateway runbooks | yes | yes | yes | partial route observations | stability pending | no | TESTED / PENDING_STABILITY | selected runtime duration and real route repetition remain incomplete | Continue durable stability and bounded route evidence |
| Phase 1 deterministic foundation and local rollback/Bronze rebuild | phase-01 plan | yes | yes | immutable local report | no provider deployment | no | no | QUALIFIED LOCALLY | real paper deployment rollback and archive restore remain external | Preserve local report; run provider-specific drill only after venue setup |
| Phase 2 deterministic paper core and Coinbase Exchange Sandbox transport | phase-02 plan; real-api-paper-transition.md | yes; Coinbase-specific `CB-ACCESS-*` signer, schema mapper, exact sandbox host guard, and read-only smoke runner | yes, including `tests/integrations/test_coinbase_exchange.py` | replay/failure fixtures plus signer/product/OMS boundary tests | partial: real Coinbase `/time`, `/products`, `/accounts`, `/orders`, and `/fills` requests reached the reviewed sandbox; account/balance/position/open-order reads passed, but product mapping and fills did not | no | provider catalogue/profile and fills-permission action | EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | the returned 13-product sandbox catalogue contained `BTC-USD` but not the required `ETH-USD`; the product-filtered fills read returned HTTP 401; no order writes were attempted | Preserve the Coinbase evidence and rerun only if a reviewed sandbox profile genuinely exposes both required products and grants the documented fills read permission; never fall back to the generic smoke or production |
| Phase 2 selected BTC/ETH paper venue candidate — Binance Spot Testnet | phase-02 plan; real-api-paper-transition.md; paper-venue-selection.md | yes; provider-specific HMAC signer, exact testnet host/path guard, product/filter mapper, account/balance/position/order/fill schema mapper, top-of-book read, scoped read-only smoke, supervised lifecycle runner, and existing `NativeTransport` boundary | yes, `tests/integrations/test_binance_spot.py`, `tests/integrations/test_binance_spot_lifecycle.py`, `tests/integrations/test_paper_venue_bakeoff.py`, plus offline config/path tests | signer, provider schema, idempotent write, restart-query, production/transfer rejection fixtures, deterministic lifecycle/failure-drill tests, and credential-free ordered Binance/Bybit comparison runner | Fresh authenticated read-only evidence passed all eight required operations at `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`, SHA-256 `c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72`, configuration hash `b41638ffc13149796f29676826b54097d2e7c417d9e4b1ff4d72be6d12f87286`; one supervised fake-funds `LIMIT_MAKER` BTCUSDT lifecycle then measured one signed POST, one signed DELETE, authoritative query/reconciliation, restart hydration, TCA/zero-residual attribution, and no real fill at `artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`, SHA-256 `db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70`; no transfer/withdrawal/production call | no | no new operator action for this venue-specific paper qualification; real fill and longer source/soak evidence remain separate gates | EXTERNALLY MEASURED / QUALIFIED / PENDING_BASE_GATES | the Binance venue/read-only/cancel lifecycle is measured and qualified for the observed no-fill path; real fill ingestion remains unobserved, and Phase 0 stability plus Phase 3–7 admission gates remain open | Preserve the immutable evidence and use Binance only through the existing RiskKernel → OMS chain; do not repeat a signed order merely to obtain a fill |
| Phase 3 V3-Core source spine | phase-03 plan; real-api-paper-transition.md | yes, including bounded raw-first REST/WSS qualification runners, native event-time normalization, WSS freshness measurement, and a separate Coinbase level-2 book reducer | yes, `tests/data`, `tests/phase3/test_source_qualification.py`, `tests/phase3/test_coinbase_wss_qualification.py`, `tests/phase3/test_coinbase_level2_qualification.py` | parser/replay fixtures | partial: the latest bounded REST retry still records BTC-USD native ticker, Deribit index, and SEC RSS replay passes, Coinbase ETH-USD 404, and GDELT 429; ticker WSS freshness passed but provider sequence gaps were observed; public `level2_batch` measured one BTC-USD snapshot, 79 updates, and 12 heartbeats with zero book-validation failures and matching replay state | no continuous source operation | no, reviewed public endpoints only | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | REST root `artifacts/phase3/source-qualification/20260810T044558.818461Z` has 3 passes/2 failures; ticker WSS remains gap-flagged; direct `level2` had no snapshot, while `level2_batch` bounded evidence passed | Preserve immutable REST/WSS/level2 roots; collect longer freshness, reconnect/recovery, and source-disagreement evidence without substituting sources |
| Phase 3 latest REST retry | phase-03 plan; real-api-paper-transition.md | raw-first public REST qualification runner | `tests/phase3/test_source_qualification.py` | replay/duplicate-append/freshness fixtures | Fresh root `artifacts/phase3/source-qualification/20260810T201653.611706Z/phase3-v3-core-source-qualification.json`, SHA-256 `60cac1ba77fa31735c87b02e29125985e9d4e69b2e592886e317b0ed61ecca01`, made seven public calls: Coinbase BTC-USD ticker, Deribit index, and SEC RSS passed; Coinbase ETH-USD returned HTTP 404 and GDELT HTTP 429 | no | no | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | the provider failures remain real external availability/product truth; continuous freshness, reconnect/recovery, and independent-source disagreement remain unmeasured | Preserve the root; do not substitute ETH or GDELT data, and collect the next independent Phase-3 evidence only when available |
| Phase 3 current evidence addendum | phase-03 plan; real-api-paper-transition.md | Binance public depth qualifier, raw snapshot/update replay, and bounded provider/local clock-offset measurement are implemented | `tests/phase3/test_binance_depth_qualification.py`; Phase-3 acceptance includes it | reducer, replay, clock-offset, and deterministic fault-drill fixtures | bounded root `artifacts/phase3/binance-spot-testnet-depth/20260810T173135.489992Z/phase3-binance-spot-testnet-depth.json`, SHA-256 `b794c7fd2c014c89928c7bf2ad4b73fde253a615818dddd27a4da53a025c76c0`: four BTC/ETH snapshots and 289 updates replay-equivalent; all four connections completed; provider event timestamps were ahead of local receipt. A fresh requested 120-second root `artifacts/phase3/binance-spot-testnet-depth/20260810T182011.404029Z/phase3-binance-spot-testnet-depth.json`, SHA-256 `7b249a125c78e346c7b9d028850e2b7cbf004c890e005bad6f6f8d70b92ddd08`, failed closed before the first message on all four WSS attempts with `WebSocketTransportError`; both reports predate the offset implementation, and no REST snapshot or write was attempted in the fresh root | no continuous window | no | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | prior real freshness failure plus a subsequent provider/runtime WSS availability failure; independent source disagreement remains unmeasured | preserve both immutable roots; retry only after availability is reviewed, then run the offset-aware qualifier and collect recovery and independent-source disagreement |
| Phase 3 public market-data plane and execution separation | phase-03 plan; real-api-paper-transition.md | yes; credential-free reviewed public REST/WSS source cards, raw-first bake-off, provider-time metadata, product/filter truth, and explicit no-write separation from Binance Spot Testnet execution | yes, `tests/data/test_public_market_data.py` and WSS/depth regression suites | deterministic source-card/selection tests and replay boundaries | v2 root `artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`, SHA-256 `14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`: Binance public BTCUSDT/ETHUSDT completed four full read-only windows, two reconnects per symbol, adjusted freshness passed after a measured 0.794-second provider/local offset, and real Coinbase-vs-Binance BTC/ETH top-of-book observations were recorded; Coinbase had one adjusted-future session and Deribit remained context-only | no continuous unattended window | no | EXTERNALLY MEASURED / PARTIAL / PENDING_EXTERNAL_EVIDENCE | longer unattended operation, sequence/snapshot recovery, source disagreement policy, and provider failover are not yet admitted; Binance Testnet WSS remains intermittent and is not silently substituted | Run longer independent source windows, recovery, and explicit failover drills; keep execution writes confined to the admitted Binance transport |
| Phase 4 quantitative baseline council | phase-04 plan | yes; offline paper-utility boundary now requires admitted Phase-3 input, explicit provenance, and closed admission | yes, `tests/models/test_paper_utility.py` plus existing model tests | public-data bake-off, roster, and preparation manifest | no real paper utility | stability pending | no | IMPLEMENTED / TESTED / PENDING_STABILITY / PENDING_EXTERNAL_EVIDENCE | role winners require stability; admitted BTC/ETH paper observations and real net-utility evidence do not exist | Finish Phase 0 and Phase 3 gates, then evaluate current candidates against mandatory baselines; do not promote automatically |
| Phase 5 typed evidence council | phase-05 plan | yes | yes | independence/authority fixtures | no real V3-Core scored council | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real source/model/provider route and data are absent | Exercise with admitted real V3-Core data after earlier gates |
| Phase 6 institutional controls and attribution | phase-06 plan | yes | yes | deterministic risk/attribution fixtures | no real paper order sample | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real paper fills and residual incidents are absent | Run the complete paper chain and reconcile exact attribution |
| Phase 7 unattended paper soak and recovery | phase-07 plan | yes; `DurablePaperSoakRunner` now provides immutable run identity, fsync'd hash-chained samples, PID/heartbeat status, lock ownership, restart hydration, and terminal-sample enforcement | yes, `tests/recovery/test_durable_soak.py` plus existing soak/recovery tests | bounded resume/tamper/failure evidence only | no | 60 calendar days required | operator supervision | PENDING_TIME_GATE | Phase 0–6 real prerequisites and venue operation are not ready; no real root launched | Wire the admitted paper runtime and launch one supervised durable root only after earlier gates pass |
| Phase 8 Hermes capability lifecycle | phase-08 plan | yes, including disposable Docker boundary probe | yes, including `tests/capabilities/test_os_sandbox_probe.py` | immutable fixture active-read report plus real local Docker boundary measurement | partial: pinned external runtime/synthetic task; no real model route or real Hermes capability task | no | review required for active-write only | EXTERNALLY MEASURED / QUARANTINED | Docker measured network denial, read-only root denial, zero effective capabilities, and bounded process controls, but native syscall/C-extension containment and credential/production-tree isolation are not attested; earlier gates remain closed | Preserve the OS-boundary report; evaluate a real isolated Hermes capability only after earlier phase gates and stronger containment evidence permit it |
| Phase 9 controlled expansion | phase-09 plan | yes | yes | challenger/source boundaries | no marginal-value challenger evidence | no | no | QUARANTINED | Phase 0–7 and E0 are not satisfied | Keep additions quarantined; reject challengers with evidence when evaluated |
| Phase 10 bounded-live readiness guards | phase-10 plan | yes | yes | readiness/AI-offline fixtures | no live validation | no | explicit human approval required | PENDING_OPERATOR_ACTION | Phase 7 and all prerequisites incomplete; no human authorization | Keep live closed; do not create approval or enable production |
| Real API/paper transition bridge | real-api-paper-transition.md; Coinbase and Binance connector runbooks | yes | yes | offline config/adapter evidence, Coinbase and Binance contract tests, supervised Binance lifecycle tests | Coinbase private reads remain partial; Binance authenticated read-only, provider-filtered BTC/ETH mapping, and one supervised fake-funds cancellation lifecycle are externally measured and preserved | no | no new venue operator action; later fill/soak gates remain supervised | EXTERNALLY MEASURED / QUALIFIED / PENDING_BASE_GATES | Coinbase cannot satisfy the required ETH-USD/fills gate; Binance is usable for the observed no-fill paper path, while real fill, Phase 0 stability, Phase 3 operational source, and later Phase 4–7 evidence remain open | Keep Coinbase quarantined; continue Phase 3 independently and use the selected Binance adapter only behind deterministic RiskKernel and authoritative OMS |
| Alpha E0 — V3-Core prerequisite | alpha-team-extension.md | plan-only | none | none beyond base evidence | no | inherits Phase 0–7 | no | BLOCKED | Phase 0–7 paper/recovery/data/risk/resource gates are not complete | Do not implement Alpha runtime; continue base gates |
| Alpha E1 — Research Brain | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0 and Phase 7 prerequisite | Wait; only maintain plan/traceability |
| Alpha E2 — Controlled Alpha Lab | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0/E1 gates | Do not build DSL or candidate runtime early |
| Alpha E3 — first V3 strategy challenger | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E2 plus real V3-Core paper evidence | Wait for admission |
| Alpha E4 — optional capability adapters | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E3 and Phase 8/9 authority | Keep external challengers quarantined |
| Alpha E5 — equities / long horizon | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E4 and point-in-time equity gate | No equity expansion |
| Alpha E6 — controlled candidate expansion | alpha-team-extension.md | no, plan-only | no | no | 60+ healthy paper days required | no | BLOCKED | E5 and per-scope soak | No candidate activation |
| Alpha E7 — bounded-live scope | alpha-team-extension.md | no, plan-only | no | no | no | human approval required | BLOCKED | Phase 10 explicit go-live review | No live scope or approval |

## Latest Phase-3 Binance WSS availability retry

A further bounded 20-second retry at
`artifacts/phase3/binance-spot-testnet-depth/20260810T201946.533716Z/phase3-binance-spot-testnet-depth.json`
has SHA-256
`ce402b7bdd67513c90b1cc5bf744d0a8d455a6f1b7f927610a84f997699b8415`.
All four public WSS connections failed closed before their first message with
`WebSocketTransportError`, made zero REST calls, and passed deterministic
fault drills. This is provider/runtime availability evidence, not a
freshness, reconnect, or Phase-3 admission pass.

## Latest Phase-3 WSS layer diagnosis

The credential-free diagnostic at
`artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json`
has SHA-256
`8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b`.
DNS resolved, TCP connectivity succeeded, and TLS negotiated TLS 1.3. The
isolated locked transition runtime reported `websockets` 16.1.1; direct BTC
and ETH attempts reached first public market messages on successful attempts,
valid subscriptions received acknowledgements, and BTC reconnect passed. ETH
had one connection timeout before a later successful attempt. The final
classification is `websocket_connection_timeout`, not provider-unavailable.
The earlier `.venv` probe is preserved separately as a local missing-library
classification with SHA-256
`bc08d878e70193368bea67981a24ba3033704314e61626f7c796951caa13da9f`.
Malformed subscriptions were not sent.

The post-diagnostic depth run at
`artifacts/phase3/binance-spot-testnet-depth/20260810T211531.293435Z/phase3-binance-spot-testnet-depth.json`
has SHA-256
`f75f4e25ba48d923df4cba4e29d7ccf4b45e7382a05b5f63bb3a500b8b59fcde`.
It captured an ETH stream with live/replay equivalence and preserved a BTC
connection failure plus adjusted-future fail-closed results on other streams.
The preserved report is partial
operational evidence, not a Phase-3 admission.

## Public market-data plane selection

The credential-free public bake-off selected Binance public market data as the
current primary candidate at
`artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`
with SHA-256
`14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`.
It verified public product truth, filters, books, trades, server time, four
full BTC/ETH WSS windows, two reconnects per symbol, adjusted freshness, and
cross-source top-of-book observations without credentials or write methods.
The source card is separate from the Binance Spot Testnet execution
adapter; it does not load broker credentials and cannot submit orders. Coinbase
public data remains an unselected candidate because its current product records
did not provide complete minimum-quantity fields; Deribit remains context-only.
Longer freshness, reconnect/resubscription, gap/snapshot recovery,
outage/backoff, source disagreement, and no-silent-substitution failover are
still pending.

## Current Phase-3 durable source-health gate

| Requirement | Implementation and tests | Real evidence | Current state / next action |
|---|---|---|---|
| Restartable unattended qualification | `scripts/run_phase3_public_data_qualification.py`; `src/advisorai/collectors/source_health.py`, `market_recovery.py`, `source_disagreement.py`, and `source_failover.py`; `scripts/validate_phase3_public_data_qualification.py`; `scripts/monitor_phase3_process_resources.py`; `tests/phase3/test_source_health_controls.py`, `test_phase3_qualification_validation.py`, and `test_phase3_resource_monitor.py` | Completed root `artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3` reached its target at `2026-08-11T03:14:39.940009Z` with 63 cycles/378 samples, summary SHA-256 `eb33cb5939feb5126bef3eff210c3710a95d6fbf3d85b3433bc2ad024a191ed7`, config SHA-256 `eb09ac0aa008c5a42c7e318178c79421bdf4d471b5649ddf65baa50a59f12398`, status SHA-256 `df8a7aa57aa95205636ce0e800882f6ccca0647b386a29488c83b7bba97ed5da`, and heartbeat SHA-256 `5d44ef77d3bf459f75c8141c53dbb45e6275489399d42616a1ad20ddd1fcb66`. The offline validation report at `artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json` has SHA-256 `efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca`, state `PASS_FOR_REVIEW`, `phase3_admission=false`, and no validation issues. The corrected v2 resource sidecar reached `deadline_reached` with 32 observations and no resource errors; summary SHA-256 `42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd` | IMPLEMENTED / TESTED / EXTERNALLY MEASURED / QUALIFIED FOR REVIEW / NOT ADMITTED. The root is no longer running. Final Binance sources were stale, Coinbase sources quarantined, and Deribit sources degraded; all 126 selections failed closed, silent substitution was zero, three replay failures and 22 severe disagreements were preserved. Keep admission closed and proceed only after Phase-0 stability and remaining Phase-3 criteria are satisfied. |
| Deterministic health and failover | Typed HEALTHY, DEGRADED, STALE, DISCONNECTED, RECOVERING, and QUARANTINED transitions; hash-chained transition ledger; explicit severe-disagreement abstention and fail-closed selection; sanitized read-only dashboard/API | Completed r3 validation reloaded 78 health transitions and 126 source selections. Final Binance states were `STALE`, Coinbase states `QUARANTINED`, and Deribit states `DEGRADED`; 126/126 selections failed closed, silent substitution was zero, disagreement was severe 22 times, and the root preserved three replay failures | No Phase-3 admission yet. The measured state machine is externally qualified for review; preserve source identity and fail closed until the remaining admission criteria and Phase-0 stability gate pass. |

The offline admission evaluator at
`scripts/evaluate_phase3_admission.py` is a separate, read-only review boundary;
it validates the requested duration from immutable timestamps, requires a real
terminal sample, checks public/write separation, source-card endpoint identity,
all-cycle primary-source continuity, fail-closed disagreement and selection
behavior, and a completed error-free resource sidecar. It cannot represent a
formal Phase-3 admission or write a `PhaseGateRecord`. Its focused tests are in
`tests/phase3/test_phase3_admission.py`.

Evaluation of the completed r3 root produced
`artifacts/phase3/public-market-data-admission/20260811T043711Z-two-hour-r3-v2/phase3-admission-evaluation.json`
with SHA-256
`cbb8ec53d793887f17ebeccab8db33a52051082cdd989ff780b7a5f854cf0c1b` and
recommendation `PENDING_EXTERNAL_EVIDENCE`. The exact blockers are
`qualification_window_incomplete` (the last sample preceded the target even
though the process finalized after it),
`no_healthy_primary_source_for_btc_eth`, and
`primary_snapshot_sequence_or_replay_failure`. This is a stricter review of
the existing evidence, not a policy relaxation.

A fresh four-hour root is currently running independently at
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
under PID `87421`, with resource sidecar PID `88019` at
`artifacts/phase3/public-market-data-resource-monitor/20260811T042355Z-four-hour-r4-fixed-v2`.
Its target is `2026-08-11T08:24:40.271709Z`, code SHA-256 is
`c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`, and the
public connectors are credential-free and write-free. Both processes are
durable evidence only; neither is an admission record and neither may be
restarted or concatenated.

An independent one-cycle recheck at
`artifacts/phase3/public-market-data-durable/20260811T034114Z-one-cycle-recheck`
made six public connections and received 503 valid events. Its summary SHA-256
is `698ad40af908757a398d19c6df83e4bfc50209bca541fe8b3acd6c314d6eff1e`.
Binance BTC/ETH again ended stale at `5.096588s`/`5.011760s` against the
5-second policy; Coinbase remained quarantined and Deribit degraded. This
corroborates the durable-window blocker and does not justify relaxing the
policy or silently substituting a source.

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
  truth for both `BTCUSDT` and `ETHUSDT`. Fresh authenticated read-only
  evidence then passed server time, products, BTC/ETH mapping, account,
  balances, positions, open orders, and fills at
  `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`
  with SHA-256
  `c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72`.
  One supervised fake-funds `LIMIT_MAKER` lifecycle then passed the
  deterministic RiskKernel → OMS → Binance transport → reconciliation chain
  with one signed submission, one cancellation, restart recovery, TCA, zero
  unexplained attribution residuals, and deterministic failure drills. Its
  report is at
  `artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`
  with SHA-256
  `db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70`.
  The real path observed no fill; fill ingestion remains fixture-tested. No
  Binance credential value was printed or persisted, and no production,
  transfer, or withdrawal endpoint was called.
- The final-source read-only rerun is immutable evidence at
  `artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T201450.306674Z/binance-spot-testnet-read-only-smoke.json`
  with SHA-256
  `b3a8b54f446599b50547bab98240db0fe8e1380fd969a6a220fccac1c83fe8e7` and
  adapter source SHA-256
  `ec3077cc726a045420c714f99c5c2e026351190348fdc9779f96e21cff034e0d`.
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
  Fresh r3 is active under PID `70598` with latest observed sequence 103 at
  `2026-08-11T03:25:18.150192Z` and last record SHA-256
  `49bb4f3ea73fce5661ec64bb546cdba08cc21ac07ba74b97834b6a656b494fb0`;
  do not concatenate roots.
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
