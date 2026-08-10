# V3 implementation audit

This audit maps the authoritative architecture and its phase sub-plans to the
current executable base. “Local” means the boundary, contract, or deterministic
fixture exists in this repository. It does not convert an external, timed, or
human gate into a unit-test claim.

| Phase | Local implementation boundary | Automated evidence | Remaining admission evidence |
|---|---|---|---|
| 0 | Typed gateway/archive/event ports; trading-authority denylist; policy-enforced three-tier gateway; exact model acquisition; isolated runtime attestation; real frozen-data local bake-off; strict role roster; append-only stability records; durable Phase-0 gate and redacted gateway-call records; local component evidence drill; exact-route stability runner; scoped two-provider rclone-crypt qualification boundary | [`tests/phase0`](../../tests/phase0), [`tests/contracts`](../../tests/contracts), [`scripts/run_phase0_component_bakeoff.py`](../../scripts/run_phase0_component_bakeoff.py), [`tests/phase0/test_remote_stability.py`](../../tests/phase0/test_remote_stability.py), [`tests/expansion/test_rclone.py`](../../tests/expansion/test_rclone.py), [`tests/phase0/test_rclone_qualification.py`](../../tests/phase0/test_rclone_qualification.py), [`scripts/qualify_rclone_archive.py`](../../scripts/qualify_rclone_archive.py) | 24-hour selected-model stability, a future exact-route stability root, and complete two-provider raw-layer archive evidence remain; the latest archive root measured independent A/B crypt restores and equal SHA-256 values, but Provider B raw recursive listing returned a sanitized provider command failure; the prior DigitalOcean root recorded three upstream shared-pool HTTP 429 gateway abstentions and is quarantined at `artifacts/phase0/remote-route-stability/20260809T173237.710604Z/incident.json` (SHA-256 `f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`); root `20260810T034500Z` was quarantined after another HTTP 429 with incident SHA `805d763d69841515f7beb676ec2a0dea2e2043106dbb4dbc43b292bff4350e9f`; corrected root `20260810T053600Z` stopped after a deadline-exhausted first probe with incident SHA `5b6d5ffe9133811a664f24151b95fcd850f130cff718bc6ed1eae9289178cff1`; Novita exact-route trial was preserved as a failed/quarantined 429 incident; DuckLake was measured and rejected at `artifacts/phase0/ducklake-comparison/20260809T162300Z/ducklake-comparison.json`; pinned upstream Hermes runtime review is at `artifacts/phase0/external-hermes-review/20260809T162031Z/external-hermes-review.json`; latest local report `artifacts/phase0/component-bakeoff/20260810T000406.852454Z/phase0-component-bakeoff.json` has SHA-256 `6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7` and passed without opening admission |
| 1 | Immutable contracts; PIT snapshot/lake/query boundaries; Parquet manifests; SQLite WAL ledgers/outbox; identity registry; config bundles/rollback; measured resource leases; traces, FTS5-first memory, optional deterministic hashing recall, durable flows, and service ownership | [`tests/contracts`](../../tests/contracts), [`tests/data`](../../tests/data), [`tests/point_in_time`](../../tests/point_in_time), [`tests/resources`](../../tests/resources), [`tests/recovery/test_config_bundles.py`](../../tests/recovery/test_config_bundles.py), [`tests/recovery/test_phase1_local_rebuild.py`](../../tests/recovery/test_phase1_local_rebuild.py), [`tests/memory`](../../tests/memory), [`tests/services`](../../tests/services) | Local rollback/Bronze rebuild report passed at `artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json`; provider-specific paper deployment rollback and long-lived restore evidence remain external |
| 2 | Native paper/testnet adapter with optional strict venue identity; Coinbase Exchange Sandbox-specific CB-ACCESS signer and exact host guard; Binance Spot Testnet HMAC signer and exact host/path guard; provider schema mapping for products/accounts/orders/fills; raw event spool/replay; typed native trade/book/bar/funding/open-interest normalization; account/cash/margin/funding/borrow/FX/corporate-action ledger; cost-aware target builder; authoritative RiskKernel/kill switch; policy-bound order-level risk evidence; durable OMS; ambiguous/reconnect/partial-fill handling; changed-payload idempotency rejection; venue/account/open-order reconciliation; TCA; cadence-gated runtime admission | [`tests/execution`](../../tests/execution), [`tests/integrations`](../../tests/integrations), [`tests/integrations/test_coinbase_exchange.py`](../../tests/integrations/test_coinbase_exchange.py), [`tests/integrations/test_binance_spot.py`](../../tests/integrations/test_binance_spot.py), [`tests/integrations/test_binance_spot_lifecycle.py`](../../tests/integrations/test_binance_spot_lifecycle.py), [`tests/integrations/test_paper_venue_bakeoff.py`](../../tests/integrations/test_paper_venue_bakeoff.py), [`scripts/qualify_paper_venue_candidates.py`](../../scripts/qualify_paper_venue_candidates.py), [`scripts/qualify_binance_spot_testnet_lifecycle.py`](../../scripts/qualify_binance_spot_testnet_lifecycle.py), [`tests/runtime`](../../tests/runtime) | Coinbase real read-only smoke remains partial: the sandbox product catalogue returned `BTC-USD` but not required `ETH-USD`; authenticated account/balance/position/open-order reads passed, but the product-filtered fills read returned HTTP 401. Binance authenticated read-only evidence passed all required operations; one supervised fake-funds `LIMIT_MAKER` lifecycle measured one signed submission, authoritative reconciliation, cancellation, restart hydration, TCA, zero unexplained attribution residuals, and deterministic failure drills. The real no-fill path is qualified; fill ingestion remains fixture-tested, and Phase-0 admission of the real Nautilus runtime plus later paper gates remain pending |
| 3 | Native/CCXT/Deribit/LSE-context parsers; raw-first REST/WSS replay; typed native market events with provider timestamp normalization; RSS/GDELT and official source parsers; untrusted-content stripping; PIT availability/revision/origin metadata; quality findings and cutoff dashboard; bounded real-source qualification runners with WSS freshness measurement and level-2 book recovery boundary | [`tests/data/test_collectors.py`](../../tests/data/test_collectors.py), [`tests/data/test_market_events.py`](../../tests/data/test_market_events.py), [`tests/data/test_official.py`](../../tests/data/test_official.py), [`tests/data/test_acquisition.py`](../../tests/data/test_acquisition.py), [`tests/phase3/test_source_qualification.py`](../../tests/phase3/test_source_qualification.py), [`tests/phase3/test_coinbase_wss_qualification.py`](../../tests/phase3/test_coinbase_wss_qualification.py), [`tests/phase3/test_coinbase_level2_qualification.py`](../../tests/phase3/test_coinbase_level2_qualification.py), [`scripts/qualify_phase3_sources.py`](../../scripts/qualify_phase3_sources.py), [`scripts/qualify_phase3_coinbase_wss.py`](../../scripts/qualify_phase3_coinbase_wss.py), [`scripts/qualify_phase3_coinbase_level2.py`](../../scripts/qualify_phase3_coinbase_level2.py) | REST evidence remains partial at `artifacts/phase3/source-qualification/20260810T044558.818461Z/phase3-v3-core-source-qualification.json` (SHA-256 `d435e99b59d815700ccfc5d75e309632ecc91fa1aea3cd3b6c7157a02df272bf`): the bounded retry still records BTC-USD native ticker, Deribit index, and SEC RSS raw replay passes, Coinbase ETH-USD HTTP 404, and GDELT HTTP 429. Ticker WSS evidence at `artifacts/phase3/coinbase-wss-qualification/20260810T044142.351959Z/phase3-coinbase-wss-qualification.json` (SHA-256 `a41fa2367a7f940e8197d5f8e0188765f9c522086091f93df988e0b2abbde702`) completed two real connections with deterministic replay and freshness passing, but observed provider sequence gaps. Level-2 batch evidence at `artifacts/phase3/coinbase-level2-qualification/20260810T052805.696329Z/phase3-coinbase-level2-qualification.json` has SHA-256 `dc620a8fa41458fa4f89396e33687b13750461a3cd643be1b18d0588092e23de` and passed bounded snapshot/update validation and replay. Continuous freshness soak, recovery, and source-disagreement evidence remain external; no Phase-3 admission is claimed |
| 3 current evidence addendum | Binance Spot Testnet depth snapshot/update reducer and raw replay boundary | [`tests/phase3/test_binance_depth_qualification.py`](../../tests/phase3/test_binance_depth_qualification.py), [`scripts/qualify_phase3_binance_spot_testnet_depth.py`](../../scripts/qualify_phase3_binance_spot_testnet_depth.py) | The qualifier now includes fixture-tested bounded provider/local clock-offset measurement and raw future-event retention. Bounded root `artifacts/phase3/binance-spot-testnet-depth/20260810T173135.489992Z/phase3-binance-spot-testnet-depth.json` has SHA-256 `b794c7fd2c014c89928c7bf2ad4b73fde253a615818dddd27a4da53a025c76c0`; four BTC/ETH snapshots and 289 updates replay-equivalent, all four fresh connections completed, and provider event timestamps were ahead of local receipt. A fresh requested 120-second root `artifacts/phase3/binance-spot-testnet-depth/20260810T182011.404029Z/phase3-binance-spot-testnet-depth.json` has SHA-256 `7b249a125c78e346c7b9d028850e2b7cbf004c890e005bad6f6f8d70b92ddd08` and failed closed before the first message on all four WSS attempts with sanitized `WebSocketTransportError`; no REST snapshot or write was attempted. Both real roots predate the offset implementation. Injected fault drills passed; clock-synchronized freshness, recovery, longer operation, source disagreement, and Phase-3 admission remain pending |
| 4 | Mandatory naive/drift/seasonal/linear/LightGBM boundaries; real isolated ModernFinBERT/MiniLM/DeBERTa, TTM-R2/R3, TSPulse, Chronos and Kronos workers; frozen public walk-forward and sentiment evaluation; one-family GPU lease; evidence-bound role roster | [`tests/models`](../../tests/models), [`tests/phase0`](../../tests/phase0) | Selected-role 24-hour stability and later paper net-utility evidence; TabPFN-TS waits on gated terms |
| 5 | Policy Mission Router; bounded adaptive council waves; typed role results; snapshot/mission-bound runs; ancestry-aware evidence graph; dissent/expiry/cutoff handling; target-only DecisionBundle and RiskKernel hand-off | [`tests/agents`](../../tests/agents), [`tests/api`](../../tests/api) | Real provider route selection and scored multi-factor evidence from live V3-Core data |
| 6 | Benchmark portfolio comparisons; robust covariance/factors/capacity/margin/stress; purged walk-forward, multiple-testing, sensitivity/regime checks; TCA/P&L attribution; incident/postmortem reconciliation; model challenge evidence | [`tests/institutional`](../../tests/institutional), [`tests/data/test_observability.py`](../../tests/data/test_observability.py) | Production paper-order sample proving exact attribution and unresolved-incident handling |
| 7 | Durable soak samples/gate; data/model/agent/risk/execution scorecard fields; measured headroom and no-trade/benchmark net-utility checks; all required adverse scenarios; ledger-backed sample rebuild; recovery report and archive-restore boundary | [`tests/recovery/test_soak.py`](../../tests/recovery/test_soak.py) | At least 60 calendar days, meaningful adverse sample, stable resources, clean reconciliation, and positive net utility |
| 8 | Hermes isolation policy and concrete bounded process runner with enforced child socket/DNS, read-only filesystem, conventional sensitive-path and process-environment metadata policies, and common process-spawn denial; sensitive-environment scrubbing; typed research/strategy/collector/model/runbook/capability artifacts; permission-filtered capability registry/broker; lifecycle through active-read; disposable Docker OS-boundary probe; explicit human approval for active-write-limited | [`tests/capabilities`](../../tests/capabilities), [`scripts/run_phase8_capability_evidence.py`](../../scripts/run_phase8_capability_evidence.py), [`scripts/probe_phase8_os_sandbox.py`](../../scripts/probe_phase8_os_sandbox.py), `artifacts/phase8/capability-evidence/20260808T050150.878842Z/phase8-capability-evidence.json`, `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`, external review runbook | Local Hermes-to-active-read evidence passed with SHA-256 `d6e44c90574c5209bd658319637605a00269fe49fe9cad7120766ecdc2cd79e5`; pinned external Hermes package completed a synthetic loopback task inside WSL2 namespaces with report SHA-256 `2fcfe86c151bffe2f4c59af0f7e0e029005a4ad94675c47fc3c18348a151b51c`; the Docker boundary measured root-identity read-only-root/network denial, constrained tmpfs, zero capabilities, and denied unshare/mount probes with report SHA-256 `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`; formal Phase-8 admission remains pending because universal native syscall/C-extension containment, credential/production-tree isolation, and a real provider route are not attested |
| 9 | Vintaged SEC/ALFRED boundary; equity corporate-action/daily-council boundary; compliant browser ladder; one-at-a-time challenger registry; duplicate-provider rejection, safe archive keys, and two-provider archive verification/rclone boundary | [`tests/expansion`](../../tests/expansion), [`tests/data/test_official.py`](../../tests/data/test_official.py) | Marginal-value and headroom evidence for each real source/model/framework addition |
| 10 | Explicit human authorization artifact; fixed loss/notional budget; policy/state-hash-bound final order guard; AI-offline safety check; automatic paper-rollback readiness | [`tests/live`](../../tests/live) | Phase 7 completion, explicit human approval, and supervised bounded live validation |
| Alpha Team extension | Integrated plan and conformance boundary only; no Research Brain, DSL, candidate, experiment, validation, or promotion implementation is claimed by this row | None; future evidence must be tied to the E0-E7 gate in [`alpha-team-extension.md`](alpha-team-extension.md) | E0 is not yet satisfied; no Alpha Team runtime, paper candidate, or admission evidence is claimed |

## Latest Phase-3 Binance availability evidence

A further bounded 20-second retry at
`artifacts/phase3/binance-spot-testnet-depth/20260810T201946.533716Z/phase3-binance-spot-testnet-depth.json`
failed closed before the first WSS message on all four connections, made zero
REST calls, and passed deterministic drills. Its SHA-256 is
`ce402b7bdd67513c90b1cc5bf744d0a8d455a6f1b7f927610a84f997699b8415`.
This remains provider/runtime availability evidence, not a Phase-3 pass.

## Global invariant checks

- No gateway, agent, Hermes task, browser job, capability, or model adapter has
  an order-submission or risk-limit-relaxation action.
- Risk decisions bind the immutable policy ID plus account and market state
  hashes; ordinary callers reject by default, and target reduction is explicit.
- Orders, fills, account events, reconciliations, capability/model/challenger
  transitions, incidents, and Phase-0 gate records are idempotent and replayable
  from local ledgers.
- PIT snapshots reject future availability/ingestion/event data; quality
  dashboards retain lineage, revision, origin, disagreement, and cutoff state.
- The acceptance runner executes phases in order and stops at the first failed
  phase. It reports only local executable evidence; it never opens a live gate.

## Verification record

The pre-qualification local run on 2026-08-10 passed all 570 tests in one process with every
declared optional extra active in an isolated locked verification environment, and
all eleven isolated phase suites. The post-qualification rerun passed 578 tests
and phase suites `128/152/126/54/19/34/10/7/27/18/5`. The exact phase distribution and static checks are kept in
[`status.md`](status.md). Phase 0’s 24-hour evidence, Phase 7’s 60-day soak, and
Phase 10’s human/live approval remain intentionally pending. Repository-wide Ruff
format checking passes with all 266 Python files formatted. The Phase-1 local
rollback/Bronze rebuild report is immutable evidence with SHA-256
`6e8cd86017dacea7b4a0fff8e9ea41901ec4bb7ee02961f5811dcbb7266342b2` and does
not open any external or human gate. The Phase-0 component evidence report is
immutable evidence with SHA-256
`6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7`; it also
does not open any external or human gate. The Phase-8 capability report is
immutable evidence with SHA-256
`d6e44c90574c5209bd658319637605a00269fe49fe9cad7120766ecdc2cd79e5`; it also
records enforced child socket/DNS, read-only filesystem, conventional
sensitive-path and process-environment metadata policies, common process-spawn
denial, and does not create a formal gate record or global capability admission.
Direct native syscalls/C-extension escapes are outside this in-process evidence
and require separate OS-level sandbox attestation. A real local Docker
boundary probe subsequently measured network denial, read-only-root denial,
zero effective capabilities, and bounded process controls at
`artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
with SHA-256
`1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`; it did
not attest universal native syscall/C-extension containment or open formal
admission.

The current requirement-to-evidence matrix is
[`gate-matrix.md`](gate-matrix.md). It records the fresh detached Phase-0
stability run, the DuckLake challenger rejection, the pinned upstream Hermes
review, and the remaining operator/time-dependent gates without promoting any
of them to formal admission.

The Coinbase Exchange Sandbox adapter is implemented in
[`src/advisorai/integrations/coinbase_exchange.py`](../../src/advisorai/integrations/coinbase_exchange.py)
and bound through the `PAPER_VENUE` resolver scope only. Its sanitized real
smoke evidence is
`artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`
with SHA-256
`79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`. The
adapter and local schema/signing tests are implemented/tested; the external
read-only gate is `PENDING_OPERATOR_ACTION` because `ETH-USD` was absent from
the actual sandbox catalogue and the fills read returned HTTP 401. No Coinbase
order was submitted.

The selected Binance Spot Testnet candidate is implemented in
[`src/advisorai/integrations/binance_spot.py`](../../src/advisorai/integrations/binance_spot.py)
with positive/negative coverage in
[`tests/integrations/test_binance_spot.py`](../../tests/integrations/test_binance_spot.py).
Its public qualifier measured `BTCUSDT` and `ETHUSDT` from the live testnet
catalogue at
`artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/binance-spot-testnet-public-truth.json`
with SHA-256
`34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6`.
The authenticated smoke is
[`scripts/smoke_binance_spot_testnet.py`](../../scripts/smoke_binance_spot_testnet.py).
The fresh read-only report at
`artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`
has SHA-256
`c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72` and passed
server time, products, BTC/ETH mapping, account, balances, positions, open
orders, and fills without a write. The subsequent supervised lifecycle report
at
`artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`
has SHA-256
`db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70` and
qualified the single signed post-only submission/cancel and no-fill path.
Fill ingestion is not claimed as real-provider evidence because no fill
occurred; it remains covered by deterministic fixtures.
After the final adapter source was settled, a read-only-only rerun passed the
same gate at
`artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T201450.306674Z/binance-spot-testnet-read-only-smoke.json`
with SHA-256
`b3a8b54f446599b50547bab98240db0fe8e1380fd969a6a220fccac1c83fe8e7` and
adapter source SHA-256
`ec3077cc726a045420c714f99c5c2e026351190348fdc9779f96e21cff034e0d`; it made
nine read-only calls and no write.

The latest independent Phase-3 REST/raw-first retry is
`artifacts/phase3/source-qualification/20260810T201653.611706Z/phase3-v3-core-source-qualification.json`
with SHA-256
`60cac1ba77fa31735c87b02e29125985e9d4e69b2e592886e317b0ed61ecca01`.
It preserved the prior partial result: Coinbase BTC-USD, Deribit BTC index,
and SEC RSS passed; Coinbase ETH-USD returned HTTP 404 and GDELT HTTP 429.
Phase-3 remains pending continuous freshness/recovery and independent-source
evidence.
