# Alpha Team extension

## Status and authority

This is the integrated, optional extension plan for the governed Alpha Team,
continuous research intake, alpha discovery, and local Research Brain described
in [`advisorai-v3-alpha-team-plan.md`](../../advisorai-v3-alpha-team-plan.md).
It supplements the authoritative
[`advisorai-federated-multi-agent-quant-architecture-v3.md`](../../advisorai-federated-multi-agent-quant-architecture-v3.md)
and the existing Phase 0-10 plans; it does not create a second roadmap, replace
their owners, or weaken any gate.

The extension is plan-only. It cannot start until the E0 prerequisite below is
met, and no implementation, admission, paper result, or live-readiness claim is
implied by this document.

## Boundary and invariants

The Alpha Team is a bounded scientific-research factory. It may collect and
structure permitted research, propose hypotheses, create replayable candidates,
run experiments, and produce validation evidence. It may not create an order,
change a risk limit, access broker credentials, promote itself, or become a
second data, portfolio, risk, OMS, backtest, or execution authority.

The base architecture remains authoritative:

| V3 owner | Alpha Team integration boundary |
|---|---|
| Bronze/Silver/Gold data plane | Read-only, frozen, point-in-time snapshots through `ResearchDataPort`; no untracked store or availability bypass |
| Hamilton feature graph | Versioned `FeatureCandidatePort` proposals only; active features remain under the base owner |
| Market Structure and Regime Engine | Consume `MarketStateArtifact` and report regime slices; never replace deterministic regime calculation |
| ModelGateway and typed councils | Policy-routed, typed research work only; models are never risk, promotion, or order authority |
| Prefect and resource governance | Bounded, cancellable research workflows; no unbounded agent loop in Trade/Fast mode |
| Hermes Skill Foundry | Quarantined adapter/test/research capabilities only; no credentials, production mutation, or self-activation |
| Portfolio Constructor, RiskKernel, OMS, NautilusTrader | Receive only independently approved candidates for replay and bounded paper routing; their authority remains exclusive |
| Dashboard | Read-only inspection and guarded research workflow views; never another live-trading control surface |

The following rules apply to every stage:

1. Existing V3 paper/testnet and live-capital gates remain unchanged.
2. Research is a lead, not an approved alpha: an LLM idea, chart, in-sample
   statistic, or third-party result is insufficient evidence.
3. Data, features, candidates, environments, seeds, results, approvals, and
   rejections must be versioned, immutable, and replayable.
4. Generation, evaluation, and promotion use separate processes and
   permissions. Early promotion also requires recorded human approval.
5. Mining, training, embedding, Hermes, and large backtests are queued jobs;
   they yield their GPU lease and resource budget to trading health.
6. An Alpha Team recommendation may tighten or recommend no-trade. It may
   never relax a RiskKernel limit.

## Typed research boundary

E1 introduces typed artifacts and registries without changing canonical trading
artifacts. The minimum contracts are:

| Artifact | Required evidence |
|---|---|
| `PaperCard` | source URL/hash, authors, publication/version date, license, claimed result, limitations, relevance |
| `HypothesisCard` | economic rationale, universe/horizon, point-in-time inputs and lags, baseline, preregistered plan, failure modes, owner, budget, expiry |
| `CandidateFactorArtifact` | restricted DSL AST/hash, lineage, feature versions, lookbacks, universe, neutralization intent, duplicate/complexity score |
| `ExperimentArtifact` | candidate/code/environment/data-snapshot hashes, seeds, windows, all metrics, costs/capacity, exposures, regime slices, complete trial count |
| `ValidationReport` | leakage, temporal validation, multiple-testing, independent replay, stability, portfolio incrementality, approval/rejection rationale |
| `PromotionDecision` | versioned policy, human approver where required, scope, expiry, rollback condition |

Use Parquet/DuckDB for analytical history and SQLite WAL for transactional
registries. A vector index is retrieval only, never source of truth. The
evidence graph preserves `PaperCard -> ClaimCard -> HypothesisCard -> Candidate
-> Experiment -> Decision`, including failed and rejected work.

Research roles are elastic jobs, not permanently chatting agents: Scout,
Librarian, Quant Research Lead, Factor Miner, Strategy Engineer, Experiment
Conductor, Statistical Red Team, Portfolio/Risk Reviewer, and Hermes Capability
Engineer. On the target laptop, begin with one coordinator and at most two
research workers; the coordinator serializes GPU work and caps API fan-out.

## Research and experiment policy

The Scout uses only allow-listed sources such as arXiv/OpenAlex/Crossref,
official market and issuer sources, existing V3 adapters, and reviewed OSS
release feeds. It stores permitted source material, hashes, license and version
metadata. It does not indiscriminately scrape paywalled sites, social networks,
or arbitrary repositories. Preprints remain leads, with peer-review status
recorded separately.

The Miner searches a restricted, typed factor DSL: approved arithmetic, ranks,
rolling statistics, robust transforms, lags, cross-sectional operators, and
safe domain functions. Operators declare input types, warm-up, units,
look-ahead rules, and cost. Arbitrary Python, `eval`, imports, network and
filesystem access, future data, hidden model weights, and unconstrained code
generation are prohibited.

Every candidate follows this recorded pipeline:

```text
proposed -> screened -> validated -> shadow -> paper -> promoted -> retired
                 \-> rejected      \-> rejected              \-> retired
```

The gate requires point-in-time truth; preregistered simple baselines; purged
and embargoed walk-forward or equivalent temporal validation; untouched final
holdouts; all-trial multiple-testing accounting; realistic cost, capacity and
failure assumptions; neighbourhood, period, regime and exposure robustness;
independent NautilusTrader replay; portfolio incrementality; and preregistered
shadow/paper evidence. A failed or unreplayable result blocks promotion.

Fast screening may use Polars/NumPy or VectorBT. Finalists require an
independent NautilusTrader replay using the canonical event, cost, and latency
model. Qlib, QuantaAlpha, external benchmarks, and other frameworks are
challengers only; they cannot become a V3 truth source or execution authority.

## Extension sequence and gates

| Stage | Integration point and deliverable | Gate to proceed |
|---|---|---|
| E0 - V3-Core prerequisite | No extension component; confirm Phase 0-7 paper, recovery, data, risk, and resource gates with AI services stopped | All prerequisite gates pass |
| E1 - Research Brain add-on | After Phase 7: typed artifacts, DuckDB/SQLite registries, evidence graph, source policy, Scout, provenance and license checks | One paper and one failed strategy trace and replay end-to-end |
| E2 - Controlled Alpha Lab | Inside Phase 8/9 research scope: DSL, feature catalog, baseline suite, fast screen, full manifests, validation/red team, dashboard views | Deliberately leaky/overfit candidates are rejected and a known baseline reproduces |
| E3 - First V3 strategy challenger | Small BTC/ETH research-only candidate set using `MarketStateArtifact` and independent Nautilus replay | Incremental net value over the V3 baseline across preregistered regimes; eligible only for paper shadow |
| E4 - Optional capability adapters | One Hermes, QuantaAlpha, AlphaAgent, Qlib, DEAP, or Optuna adapter at a time, with CapabilityCards and sandbox checks | Removable adapter cannot escape its sandbox and emits a replayable artifact |
| E5 - Equity and long-horizon extension | SEC/IR/corporate-action snapshots, equity factor families, research cards, and paper ledger | Clean point-in-time/corporate-action history; crypto assumptions are not reused blindly |
| E6 - Controlled candidate expansion | Limited approved-paper candidates, decay monitoring, and allocation research | At least 60 healthy unattended paper days per scope with no unresolved reconciliation or data-quality incident |
| E7 - Optional bounded-live scope | No new architecture: use the existing Phase 10 credential enclave, caps, approvals, and emergency controls | Separate explicit go-live review, operational parity, and tested immediate paper rollback |

Stages are evidence-driven, not calendar-driven. E1 is optional and must not
delay or alter Phase 8. E2-E6 remain challenger work under Phase 8/9 ownership;
E7 does not reduce the Phase 10 gate.

## Capability and dependency admission

No external repository enters the trusted codebase due to an autonomous or
agentic label. Each candidate follows: pin commit, SCA/license review, isolated
install, frozen-snapshot reproducibility test, security and tool-permission
review, benchmark, `CapabilityCard`, then quarantine or read-only activation.

QuantaAlpha is a Phase 9 research challenger; use only its trajectory/factor
lineage concepts in an isolated Qlib environment. Inalpha is an architecture
reference only until separate licensing review, because direct AGPL reuse would
carry copyleft obligations. WorldQuant Miner is a reference or isolated
external-platform experiment only: automatic submission, credential patterns,
and platform scores as validation are prohibited. `awesome-quant` is discovery
input, not an approved dependency list.

## Operations and first delivery slice

Research dashboard views expose an inbox, hypothesis/factor lineage, experiment
inspector, promotion board, market-state/portfolio overlay, and resource/security
operations. They trace a paper outcome back through candidate version, decision
snapshot, market state, factor/forecast evidence, RiskKernel result, order/fill,
TCA, and attribution. The existing `advisor-api` and lightweight local views are
the default; no permanent Grafana/Prometheus stack is introduced without need.

The first implementation slice after E0 is deliberately narrow:

1. Add the six artifacts and SQLite/DuckDB registries.
2. Implement an allow-listed arXiv/OpenAlex/GitHub Scout with provenance.
3. Define the factor DSL and feature-availability contract; register ten simple
   BTC/ETH factors with unit tests.
4. Add a baseline suite and deliberately leaky factor to prove Red Team
   rejection.
5. Export one `ExperimentArtifact` from a screen and reproduce it in Nautilus.
6. Add the Research Inbox, Factor Registry, and Experiment Inspector.
7. Run at most one fixed-scope mining mission: 50 candidates, fixed windows and
   budget, with all trials recorded.

No stage authorizes a live order, live credential, withdrawal permission, or
automatic live-capital increase.
