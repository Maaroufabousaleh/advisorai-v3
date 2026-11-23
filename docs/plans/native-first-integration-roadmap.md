# AdvisorAI V3 — Native-first integration and capability delivery roadmap

**Proposal date:** 2026-11-23  
**Status:** PROPOSED for owner review. No phase below is certified by this document.  
**Scope:** Deliver the real open-source, federated trading and research system, not a new infrastructure demo or a replacement trading engine.  
**Primary references:** [Authoritative architecture](../../advisorai-federated-multi-agent-quant-architecture-v3.md), [Alpha Team](../../advisorai-v3-alpha-team-plan.md), [technology register](../reference/technology-decision-register.md), [PAPER-V1 tracker](../../AdvisorAI_Paper_Readiness_Tracker.md).

## Product success criterion

An owner starts an AdvisorAI session and the actual selected open-source engines ingest legally accessible point-in-time market/research evidence, produce versioned model and agent outputs, identify and independently test strategies, approve only explicitly reviewed paper strategies, route eligible paper orders through deterministic safety and the real execution engine, reconcile venue state, measure outcomes, remember failures and successes, and recover or stop without unsafe orders. Crypto and U.S. equity/bond research are first-class; equity execution needs a separate qualified paper venue. Real-money trading and autonomous strategy promotion are **not** authorized by this plan.

This is a **delivery sequence**, not a reset of the historical Phase 0–10 gates, the PAPER-V1 P01–P10 records, or existing evidence. Those remain historical/operational qualification records. The integration milestones I1–I9 below define **what must actually work** and take priority for new feature implementation. An existing component can be reused without rewriting it.

## Non-negotiable development rules

1. **Real upstream first.** Call the installed, version-pinned upstream library/engine for its designated job. A wrapper, available import, injected test runner, mock, synthetic UI or home-built equivalent cannot close that integration milestone. Keep mocks for fault unit tests only; the gate additionally requires a real native execution trace.
2. **No intermediary web stack or toy engine.** Do not build a temporary REST backend, distributed orchestrator, substitute backtester, generic agent framework, or unrelated dashboard to demonstrate a library. Invoke the upstream engine in-process or through its documented native worker/CLI. An API/UI is delivered only when it serves the actual running system.
3. **Deliver an observable result each phase.** Each milestone has an executable demonstration, native call evidence, deterministic acceptance checks where applicable, negative tests, measured resources/costs and a copyable operator command. Propose small scripts/tests that invoke the upstream library; never replace its logic.
4. **One owner per authoritative concern.** NautilusTrader is the selected event/replay/execution engine; AdvisorAI's deterministic RiskKernel vetoes risk and its ledger remains canonical for approved intent/order/reconciliation truth. Map event/order identities explicitly; no parallel autonomous broker engines or double bookings. PydanticAI owns typed agent execution, Prefect durable research workflows, Hamilton feature dependencies, and DuckDB/Parquet/SQLite data truth.
5. **Integration is not trading authorization.** A selected library is core even before it passes tests, but paper-market authority is profile-specific and strategy-specific. An unqualified adapter cannot submit orders. Historical evaluations cannot be relabeled as forward results. Live capital remains prohibited without separate approval.
6. **Core versus optional is explicit.** Core tools must be implemented in the roadmap. Supplementary external tools receive a named capability/acceptance test; those without incremental value may be deferred, but a narrow BTC/ETH V3-Core profile must never be mistaken for the entire product.
7. **Keep workstation feasibility honest.** Record peak RSS/VRAM, runtime, model load/unload, API usage, and disk use on the target 16 GB RAM / RTX 4060 8 GB WSL2 host. No assumption that all GPU models run together. Scheduled jobs run inside owner sessions unless a separate operating profile is approved.
8. **No fictitious passing status.** The status table is updated only with exact PR, commit, environment, input hashes, native upstream class/function, logs, artifacts, and reviewer verdict. Missing evidence = OPEN or BLOCKED, not DONE.

## How to read status and verify a phase

- **OPEN** = the required native demonstration/acceptance evidence is not yet recorded.
- **IN PROGRESS** = implementation exists or runs, but at least one mandatory test/evidence is missing.
- **PASS (INTEGRATION)** = every listed technical acceptance criterion has exact native upstream execution evidence on a pinned revision.
- **PAPER ADMITTED** = separate strategy, broker/account, risk, data and integrated-paper admission completed under the existing PAPER-V1 or later policies. An integration PASS never implies this.
- **BLOCKED** = a named prerequisite prevents the specific native demonstration; record the blocker and next action.

For each phase, a reviewed PR must include: upstream repository and exact installed version/commit; pinned configuration; dependency/license/weight/feed checks; relevant code paths; actual command and exit status; manifest and raw/sanitized logs; source/snapshot/commit/config hashes; expected versus actual assertions; positive and negative test reports; performance measurements; explicit remaining work and reviewer decision. Store evidence outside tracked source under an immutable local artifacts root, for example artifacts/native-delivery/I1/<run-id>/; only its manifest/hash and allowed summary are committed. This is a **proposed naming convention**, not a claim that the path or CLI exists now.

Suggested direct acceptance test namespace to create as the implementation progresses: tests/native_delivery/test_i1_nautilus.py through test_i9_integrated_paper.py. These tests are **specifications to implement**, not existing runnable tests. Each test must use the real upstream code for its named functionality. Do not mark a stage PASS merely because tests are named correctly.

## One-screen delivery board

| Phase | Owner/capability | Demo you must be able to run | Status on 2026-11-23 |
| --- | --- | --- | --- |
| **I1 — NautilusTrader** | Actual native replay and paper execution, joined to RiskKernel/OMS | One real upstream engine backtest on a frozen BTC/ETH snapshot; one bounded, authorized paper-venue lifecycle through the selected native route | **OPEN** — native wrapper/test double exists; real integrated engine not demonstrated |
| **I2 — PydanticAI** | Real PydanticAI/Graph evidence council | Real model-backed specialist runs and a typed, auditable proposal with disagreement and abstention | **OPEN** — callable council exists; upstream agent execution not demonstrated |
| **I3 — Prefect + Hamilton** | Native durable research flows and feature DAG | Native Prefect run recovers a failed research task; native Hamilton graph computes PIT features | **OPEN** — wrappers/local flow and graph exist |
| **I4 — Model fabric + gateway** | Actual inference from frozen reference models | Real inference from selected Chronos, TTM-R2, Kronos, LightGBM, FinBERT-family models; measured routing and fallbacks into evidence | **OPEN** — isolated/runtime evidence exists; joined model fabric not demonstrated |
| **I5 — Data and event intelligence** | Real qualified market, filings and macro data | BTC/ETH source lineage plus equity/ETF/filing/action/macro PIT snapshots with exact-source provenance | **OPEN** — data lake, collector contracts and partial real feeds exist |
| **I6 — Hermes + Research Brain** | Real sandboxed builder/research worker and durable experiment memory | Paper → sourced hypothesis → code candidate → independent test → stored accepted/rejected result | **OPEN** — registry and bounded rehearsals exist; native Hermes pipeline not admitted |
| **I7 — External quant/agent capability suite** | Qlib, TradingAgents, Optuna and scoped external tools | Actual Qlib equity benchmark and actual TradingAgents dissent trial on the same frozen, dated evidence | **OPEN** — evaluations/design only |
| **I8 — Multi-asset paper research/venue** | Stocks, ETFs and bonds without pretending Binance trades them | Auditable SPY/TLT/AAPL research and at least one verified eligible equity-paper order/fill/reconciliation using separately qualified venue | **OPEN** — equity research/fixtures exist; equity paper venue not admitted |
| **I9 — Autonomous integrated paper workstation** | Whole native pipeline, supervision, outcome feedback | Operator starts session; approved strategy drives paper decision/orders/reconciliation; recovery and research outcomes survive restart | **OPEN** — PAPER-V1 is 5/10 per tracker; strategy and integrated evidence outstanding |

No percentage of project completeness should be inferred from the number of passed milestones. The current evidence snapshots are not newly executed audit results.

## I1 — NautilusTrader: direct engine first

**Goal:** NautilusTrader is truly doing replay and paper-event/execution work; AdvisorAI is no longer using a custom ReplayEngine disguised as NautilusTrader.

**Use existing code:** native paper venue/OMS, account ledger, RiskKernel and PIT data lake. Replace only the missing native-engine execution path; do not rewrite these components. Freeze one compatible NautilusTrader release, its Python runtime and native API; inspect its actual installed API before coding. Upstream documents both BacktestEngine and BacktestNode; choose one matching the pinned release, not nightly examples.

**Required executable demonstration:**
1. Run a native NautilusTrader backtest using a pinned, nonempty historical BTCUSDT (and then ETHUSDT) dataset loaded from AdvisorAI's PIT-safe lake/catalog. Show the actual upstream engine object, event counts, generated orders, genuine engine fills, position changes, fees and P&L. At least one trade must occur in the controlled example.
2. Run the identical snapshot, strategy version, start equity, instrument definition and cost model twice. Economic outputs, instrument/order count, signed fills/quantities, final cash, positions and P&L must match within documented numerical precision. Random run IDs need not match.
3. Verify independent cash/position/fee/mark accounting against AdvisorAI's ledger and detect intentional disagreement. Prevent duplicate submissions or double-application of a fill when the same event is presented twice.
4. Prove one risk-rejected trade leads to **zero** venue submissions. Kill-switch and stale-input scenarios also produce zero submissions; a deliberate late bar must never influence an earlier decision.
5. With explicitly authorized fake-funds credentials, prove a bounded native-engine-to-approved paper-venue lifecycle (submit/ack or reject, cancel/fill as obtainable, reconcile and restart without duplication). If the pinned upstream version does not support the required Binance Spot Testnet mode, document the exact missing capability, use a narrow reviewed adapter to connect the native engine, or qualify another compatible paper venue. Do not silently substitute the legacy native-only route and declare NautilusTrader complete.
6. Supply one copyable direct invocation command plus a manifest proving the invoked upstream engine and venue pathway.

**PASS:** 1–6 are evidenced on the same reviewed code/config identity; no unresolved accounting mismatch, duplicate-order incident or unauthorized write. **FAIL:** only imports/tests wrappers or an injected replay_runner, or the venue lifecycle bypasses NautilusTrader. **Separate:** this is not strategy profitability, PAPER-V1 completion or live-capital approval.

Primary code requiring repair: [nautilus wrapper](../../src/advisorai/execution/nautilus.py), [native event replay](../../src/advisorai/execution/events.py), [paper runtime](../../src/advisorai/runtime/paper.py), [replay test](../../tests/execution/test_cross_engine_replay.py).

## I2 — PydanticAI / Pydantic Graph: real specialist agents

**Goal:** Real model-backed specialist agents exchange source-linked, structured evidence under the existing EvidenceGraph and portfolio-risk boundary.

**Demo:** on one frozen market/news/financial evidence packet, run at least three distinct real PydanticAI agents: Data Verifier, Technical/Quant Analyst, and Skeptic. Use PydanticAI Agent, registered tool calls and typed output validation. Add the other planned council roles through the same path. Implement Pydantic Graph transitions where stateful analysis/dissent demands them; do not write a second graph framework.

**Tests:**
1. Capture native agent invocation and model identity, prompt/tool versions, retrieval sources, timestamps, token/cost budget, typed Evidence/AgentRun and dissent artifacts. At least one actual upstream model call is required; no canned evidence passes the gate.
2. Prove that syndicated/repeated evidence does not fake source independence; two derived summaries of one publisher count as one source family.
3. Present contradictory sources and a skeptic objection; disagreement must be retained, not silently averaged away.
4. Revoke one tool, inject malicious text requesting an order or credential, and force a timeout/malformed typed reply. Verify denied tools and zero OMS/RiskKernel mutation, with explicit missing evidence or abstention.
5. Produce a valid typed DecisionProposal when evidence is sufficient, or an auditable ABSTAIN when not. A decision proposal is not an order.

**PASS:** real agents (not role-function stubs) run on sourced evidence, preserve disagreement and never obtain trading authority.

## I3 — Prefect + Hamilton: use the actual workflow and feature libraries

**Goal:** Native Prefect flows manage bounded research jobs; native Hamilton driver computes versioned, cutoff-safe features.

**Demo:** run a real Prefect flow: collect/read existing PIT snapshot → compute Hamilton features → register a baseline experiment → write immutable result. Fail one task deliberately, retry according to bounded policy, stop/restart, and recover without duplicate records.

**Tests:**
1. Prefect flow/task identities, states, retries and failure/restart history are observable from the actual Prefect runtime, not only custom FlowRun dataclasses.
2. The native Hamilton driver resolves at least five related features, including a rolling and lagged feature; same input hash produces equivalent feature values and versions.
3. Late/missing/future observations fail at the availability cutoff. Cycles/missing dependencies are rejected.
4. A retried task never duplicates research records, order events or factor promotion. No workflow writes to the trading ledger except through reviewed owner APIs.
5. Measure RSS, time, persisted state and cleanup in an owner-session stop.

**PASS:** live native calls and their task/graph traces are captured; generic function wrappers cannot pass.

## I4 — Selected local models and inference gateway

**Goal:** The frozen reference models actually infer, not merely appear as strings or runner callbacks.

**Core roles:** Chronos-2-small (probabilistic forecasting), TTM-R2 (independent temporal control), Kronos-small (OHLCV/K-line model), LightGBM (structured baseline), compact FinBERT-family sentiment, and a small typed decision-model route. One strong on-demand reasoning model is behind the reviewed gateway. Existing naive/drift/seasonal/linear baselines stay as controls.

**Tests:**
1. For each selected model, record exact upstream checkpoint, weights/license, loader, native model class, input context, output shape, device, model/code hashes and one actual forward pass on a valid frozen input. Report models unavailable or incompatible explicitly; in particular do not disguise the existing 48-bar versus 512-value TTM-R2 mismatch.
2. Evaluate required probabilistic, price, OHLCV, tabular or sentiment outputs against a reproducible simple baseline; report error, calibration, latency and incremental net decision utility after costs. **Integration can PASS even if a model has no alpha; economic promotion cannot.**
3. Prove one-model-family GPU lease, lazy load/inference/unload, bounded residual memory across three cycles and deterministic fallback/ABSTAIN when a model fails.
4. Verify a real, identified provider/model request and response through the selected gateway, including restricted tool permissions, token/cost budget and failure fallback. A LiteLLM-labelled injected transport is not a successful LiteLLM integration.
5. Publish source/evidence lineage from at least two genuinely distinct model families into an actual council session. GPU workers cannot access brokerage credentials.

**PASS:** selected real inference works and contributes valid typed evidence under tested resource/failure limits. Models that fail resource or model-specific tests remain explicitly BLOCKED without blocking every other model.

## I5 — Qualified financial data, equities and event intelligence

**Goal:** Give agents and models authoritative, time-aware evidence for crypto **and** U.S. equities, ETFs and bonds.

**Demo:** real BTC/ETH source capture plus qualified dated AAPL, SPY and TLT bars; one SEC 10-K/10-Q amendment chain; a split/dividend/inactive-symbol fixture; and one vintage-aware macro observation. Read each as it would have been known at a historical decision cutoff.

**Tests:** store publisher/feed identity, exact endpoint and entitlement, instrument identifier, exchange/calendar, raw hash, event/effective/acceptance/first-available/ingestion timestamps, adjustments and revisions. Reject future filings/revised macro data at earlier cutoffs. Compare a secondary feed where required and do not count two wrappers of the same publisher as independent. Exercise source loss, gap detection, duplicate/revised bar handling and bounded rate-limit fallback. No unreviewed provider rights or secret in logs.

**Tool choices:** direct SEC EDGAR with a qualified EdgarTools parser if it offers measurable value; official FRED/ALFRED; Alpaca SIP historical qualification and one secondary EOD feed. OpenBB is a *selected possible read-only aggregation adapter* for specific uncovered endpoints, not a second source of truth. Do not defer essential stock-data support merely because Binance is the first order venue.

## I6 — Hermes and Research Brain: actual research factory

**Goal:** Real isolated Hermes agents create useful, testable research capabilities, while the Research Brain durably remembers sources, experiments, failures and outcomes.

**Demo:** fetch a permitted paper through real sources (OpenAlex/Crossref/arXiv/author code), extract a source-linked claim, create HypothesisCard, have the actual pinned Hermes runtime implement a narrow candidate/adapter in a sandbox, run independent tests/replay, submit ExperimentArtifact and ValidationReport, and retain both a deliberately rejected candidate and a passing technical candidate.

**Tests:** genuine Hermes task transcript with fixed turn/tool/resource budget and artifact hashes; network/filesystem/credential denials demonstrated by adversarial probes; no write to trusted repository or trading services; repeat task without duplicate ledger artifacts; terminate mid-task and record interrupted truth; trace PaperCard → HypothesisCard → Candidate → Experiment → Validation → Decision. Paper2Code may supply a specific paper-without-code capability only if its code materially improves reproduction, with the exact upstream license and independent replay. Cognee/LLM Wiki/Qanat are retrieval or experiment adapters only if they add measured value without replacing canonical research truth.

**PASS:** actual upstream Hermes work safely exports one reproducible candidate; durable source-linked good and bad outcomes survive restart. A stand-in research script or a sandbox-only demo is insufficient.

## I7 — External quantitative and finance-agent capabilities

**Goal:** Integrate specialized tools as **real, bounded contributors**, not as competing trading authorities or never-ending lists of deferred ideas.

**Mandatory initial demonstrations:**
1. **Qlib:** run real Qlib Alpha158 + LightGBM on a frozen equity snapshot with specified universe, train/validation/test windows, feature/label cutoffs and pinned dependencies. Compare out-of-sample rank IC, turnover and returns after costs against simple alternatives. Independently replay selected candidates in NautilusTrader.
2. **TradingAgents:** run the actual upstream bull/bear analysis on the identical dated evidence packet as the existing Equity Council. Count independently verifiable, source-backed corrections, latency and API costs; do not give it order authority.
3. **Optuna:** run a bounded real Optuna study on only the preregistered training/validation split. Retain all trials, seeds, budget and chosen configuration; untouched holdout not used for tuning.
4. **Fast screening:** use Polars/NumPy or VectorBT only for fast screening, followed by the same native Nautilus independent event replay for finalists.

**Scoped candidates:** Trading-R1 for a distinct difficult financial reasoning role; ai-hedge-fund for a reproducible valuation method; EdgarTools for parser robustness; OpenBB for a named feed; skfolio/CVXPY for one allocation need; LEAN for occasional cross-engine checks; Freqtrade for selected lookahead-test methods; QuantaAlpha/AlphaAgent/WorldQuant tools only after exact code/license/rights/role reviews. FinceptTerminal/OpenTerminal are UI/connector references unless one **specific** separable component beats the existing system. Reject duplicating OMS, risk, data truth or execution just to increase library count.

**PASS:** each selected initial tool executes its native implementation, emits a typed, reproducible result, and has a named value/cost comparison. A challenger that loses a fair benchmark is recorded as rejected; no pretend improvement.

## I8 — Multi-asset paper capability (not crypto-only)

**Goal:** Real U.S. stock/ETF/bond research and a separately admitted **fake-money** venue route, while preserving the qualified Binance crypto path.

**Demo:** paper research for AAPL, SPY and TLT with corporate-action-aware accounting and an explicit venue-eligible strategy. Connect NautilusTrader to a qualified IBKR paper or Alpaca Paper profile, as technically supported. Show one authorized nonzero submission, an actual venue acknowledgement and fill where available, exact cash/position/fee reconciliation, cancel or reject behavior, and no resubmission after restart. If a fill cannot be obtained in the controlled run, mark that criterion OPEN and preserve truthful evidence.

**Tests:** market calendar/halts/ticker changes, split/dividend and delisting handling, order lot/precision constraints, position/account mapping, fees, stale inputs, unauthenticated start refusal, venue outage and recovery. Binance Spot Testnet must never receive SPY/TLT orders; separate symbols/accounts/permissions per venue.

**PASS:** a genuine permitted equity-paper lifecycle and independent reconciliation under the selected venue profile. Crypto paper readiness does not imply equity readiness.

## I9 — Joined autonomous paper workstation

**Goal:** All upstream engines work together for a complete session; the only routine human action is to start/stop or approve bounded scope changes. No autonomous live-capital authority.

**Demo:** the actual data/model/agent/research stack produces auditable evidence and an approved strategy proposal, deterministic Portfolio Constructor and RiskKernel assess it, Nautilus/OMS route eligible fake-money orders, broker reconciliation and TCA/outcomes persist, Research Brain links later evidence, and the next owner session recovers state before any new action.

**Tests:**
1. Pin one reviewed release/config for a full owner-started 5–8-hour session (shorter smoke tests are preparation only). Prove real agent/model/engine invocation, source-to-outcome trace and clean stop.
2. Prove no-trade/ABSTAIN when quorum, model or data is missing; exactly zero unauthorized paper submissions.
3. Inject stale source, provider loss, venue outage, ambiguous ACK, duplicate fill, worker death and unclean shutdown. Reconcile account and orders before resuming; zero duplicate submissions and zero unresolved safety/data-integrity incidents.
4. Check model loading and GPU/resource cleanup across repeated sessions; no leaked locks, leases or credentials.
5. Record independent strategy admission evidence and meaningful forward paper outcomes after measured fees/slippage. A model or strategy that does not beat its agreed benchmark is rejected, not promoted.
6. Complete and cross-reference the existing PAPER-V1 P06–P10 acceptance work and reviewed operational sign-off. The suggested 20–30 meaningful sessions / 100–150 hours are planning targets, not automatic success.

**PASS (INTEGRATION):** native components and end-to-end fault/recovery proofs all pass. **PAPER ADMITTED:** additionally the exact approved strategy/venue/data/resource profile passes PAPER-V1 with human review. **LIVE:** never implied or authorized by either result.

## Tool policy: core is not the same as admitted

| Role | Selected integration/core priority | Alternative or conditional, not blanket rejection |
| --- | --- | --- |
| Event backtest and execution | NautilusTrader (I1) | LEAN only independent spot-check; Freqtrade selected research test |
| Typed multi-agent execution | PydanticAI/Pydantic Graph (I2) | TradingAgents added as source-backed specialist challenger (I7) |
| Workflows + feature graph | Prefect and Hamilton (I3) | No parallel scheduler/feature DAG without demonstrated gap |
| Model inference + routing | Chronos, TTM-R2, Kronos, LightGBM, FinBERT, local decision adapter, approved model gateway (I4) | TimesFM/TTM-R3/Kronos-base/TabPFN-TS models are challenger research after baseline fit |
| Sources and scientific truth | Qualified direct data, SEC, macro, PIT lake (I5) | OpenBB, EdgarTools, alternative feeds chosen for specific gaps |
| Autonomous builder/memory | Hermes, Research Brain registry (I6) | Paper2Code, Cognee, LLM Wiki, Qanat scoped capability additions |
| Quant research | Qlib, Optuna, fast screening and independent native replay (I7) | QuantaAlpha, AlphaAgent, alternative optimizers after safety, license and marginal-value review |
| Multi-asset venue | Qualified Nautilus paper integration for crypto and an equity venue (I1/I8) | IBKR versus Alpaca based on real account/API and recovery evidence |

**Revisit rule:** For any tool presently marked DEFERRED in the historical technology register, do not silently change its historical decision. Create an explicit dated disposition with chosen role, exact upstream/license, proposed native trial, scope, benchmark, resource/cost estimate, owner and PASS/REJECT trigger. If it provides a distinct needed function, move it into the delivery queue without waiting for unrelated PAPER-V1 profitability. The core library integrations I1–I4 are **not** optional challengers just because their operational admission is pending.

## Required documentation presentation for every implementation PR

Every phase PR updates **this board** plus:
- **Now works:** a runnable, copyable CLI/session command, with exact output excerpt and upstream evidence.
- **Acceptance:** concise test IDs, expected and observed values, and PASS/FAIL/BLOCKED per test.
- **Still missing:** missing real upstream calls, fixtures, credentials, resources, risks, evidence and owner.
- **Next deliverable:** one specific direct-native end-to-end demonstration, not a proposed architecture-only or UI-only milestone.
- **Links:** native code, relevant tests, immutable evidence manifest/hashes, exact reviewed commit and upstream docs.

Do not label a roadmap tick, number of passing tests or green CI as evidence of trading performance. Do not use a synthetic dashboard to imply venue readiness.

## Dependencies and starting move

Start at **I1 now**: establish the pinned NautilusTrader runtime, replace the injected-replay-only demonstration with an actual engine run, and join it to the existing RiskKernel/OMS account semantics. I2 can begin using the existing Evidence Council interfaces immediately afterward; native provider/model execution for I2 may be developed in parallel, but the I2 gate requires a real upstream result. I3–I8 progressively fill in capabilities on the same system without additional web services or substitute engines. I9 completes the supervised integrated demonstration and operational PAPER-V1 evidence.

**No phase is completed merely by writing this roadmap.**
