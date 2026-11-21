# Constrained portfolio optimization — optional OSS evaluation plan

**Status:** PROTOTYPE/EXPERIMENT; PLAN ONLY; no solver/framework selected, installed, benchmark-qualified, or admitted by this document.  
**Review date:** 2026-11-21.  
**Owner:** AdvisorAI deterministic Portfolio Constructor; independent RiskKernel remains the final veto.  
**Phase binding:** Phase 6 institutional controls (portfolio baselines and constraint validation); Phase 9 / Alpha Team (bounded optimizer challengers). Research-only prototyping may be implemented early behind a disabled adapter, but no activation or promotion bypasses existing gates.

## Why this is an extension, not a replacement

The implemented \`src/advisorai/execution/portfolio.py\` contains \`TargetPortfolioBuilder\` and \`PortfolioConstraints\`. It constructs cost-aware targets, preserves cash accounting, applies no-trade/minimum-notional bands, and **validates** gross/net exposure, turnover, position size, leverage, concentration, minimum cash, and liquidity participation. It does **not** presently solve a mathematical optimization problem to select the best feasible weights.

The target architecture already requires: point-in-time forecast/evidence → typed proposal → deterministic Portfolio Constructor → \`TargetPortfolio\` → independent \`RiskKernel\` → OMS/NautilusTrader → reconciliation/attribution. Keep those owners. An optimizer proposes a candidate allocation *inside* portfolio construction; it never grants order, risk-limit, broker, strategy-promotion, or live-capital authority. Current V3-Core and the real-API paper transition explicitly exclude multiple optimizers. This is an optional research extension, **not a PAPER-V1 release requirement**.

## Decision: select a narrow stack by measured value

| Candidate / official upstream | Candidate role | Planning decision / overlap guard |
|---|---|---|
| [skfolio](https://github.com/skfolio/skfolio) ([optimization docs](https://skfolio.org/user_guide/optimization.html)) | **First research challenger** for mean-risk, risk-budget, tail-risk, hierarchical methods, costs/turnover, and financial cross-validation | PROTOTYPE/EXPERIMENT only. Test marginal net utility and leakage resistance on AdvisorAI snapshots. Do not let it own canonical data, a second portfolio/ledger, or execution. |
| [CVXPY](https://github.com/cvxpy/cvxpy) ([solver guide](https://www.cvxpy.org/tutorial/solvers/index.html)) | **First native modeling candidate** for explicit deterministic convex constraints/objectives | PROTOTYPE/EXPERIMENT, not an independent optimizer authority. Review DCP validity, versions and results; choose **one qualified** implementation rather than keeping every framework active. |
| [Clarabel](https://github.com/oxfordcontrol/Clarabel.rs) / [OSQP](https://github.com/osqp/OSQP) | Candidate numerical backends for convex conic / quadratic allocation problems | PROTOTYPE/EXPERIMENT; benchmark accuracy, residuals, memory, termination and warm/cold latency. Use only when formulation is actually supported. |
| [HiGHS](https://github.com/ERGO-Code/HiGHS) ([solver guide](https://ergo-code.github.io/HiGHS/dev/solvers/)) | LP/MILP for linear constraints, cardinality and lot/integer decisions; also native convex QP where the selected interface supports it | PROTOTYPE/EXPERIMENT after a defined problem warrants it. Do not assume every modeling adapter exposes every native HiGHS capability. |
| [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | Broad tail-/drawdown-risk and risk-parity alternative | DEFERRED research challenger unless a named measure or robust formulation unavailable/unsatisfactory in skfolio demonstrates additional value. Do not keep two equivalent portfolio framework owners. |
| [cvxportfolio](https://github.com/cvxgrp/cvxportfolio) | Offline multi-period planning, financing/turnover-policy research | DEFERRED / isolated comparison only. GPLv3 and dependencies require specific legal review before incorporation/distribution. Its simulator does not replace NautilusTrader replay, OMS, or broker state. |
| [OR-Tools CP-SAT](https://github.com/google/or-tools) | Integer/logical scheduling and discrete asset-allocation constraints where LP/MILP is insufficient | DEFERRED until a concrete hard combinatorial need and measured benefit. CP-SAT uses integer variables/scaling, not a drop-in continuous mean-variance/QP engine. It does not replace Prefect or the Resource Governor. |
| [OR-Tools MathOpt](https://developers.google.com/optimization/math_opt) / [SciPy optimize](https://docs.scipy.org/doc/scipy/reference/optimize.html) | Optional alternative modeling bridge / simpler HiGHS-backed LP/MILP access | DEFERRED unless it reduces adapter complexity on a measured workload. No second generic abstraction layer by default. |
| [SCIP / PySCIPOpt](https://github.com/scipopt/PySCIPOpt) | Difficult mixed-integer nonlinear/constrained formulations | DEFERRED; use only after proving the selected convex/MILP formulation cannot capture a necessary requirement; review license and solver complexity. |
| [PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) / [pymoo](https://github.com/anyoptimization/pymoo) | Simple classical allocation / multi-objective experimental benchmarks | Reference-only unless they add a non-overlapping, preregistered comparison; not permanent parallel optimizers. |
| [Optuna](https://github.com/optuna/optuna) | Bounded Alpha Team hyperparameter/strategy research | **Retain the existing ADOPTED-IN-PLAN decision**; not a portfolio solver. Never tune on test/forward outcomes, and do not add DEAP/pymoo for the same role without a measured deficiency. |

**Candidate default for the first bake-off:** compare (A) simple existing AdvisorAI baselines, (B) skfolio on frozen research copies, and (C) a small AdvisorAI-native convex model expressed through CVXPY with Clarabel/OSQP. Include HiGHS only if the specific LP/MILP task demands it. **This is a test ordering, not an irrevocable choice of production packages.**

Official documentation confirms that CVXPY is a modeling layer rather than the numerical solver; skfolio expects optimization quantities and costs at the return-series periodicity; HiGHS provides LP, MIP, and convex QP algorithms in its native interface. Exact APIs, packaging, backend support, licenses, and dependency versions must be checked and pinned for the trial. Some solvers are not valid for all model classes, and a continuous optimum cannot be treated as an executable fractional/lot-sized order without independent conversion and verification.

## Architecture and typed boundary

\`\`\`text
validated PIT snapshot + approved forecasts + account state + risk-policy version
                                    |
                             DecisionProposal
                                    |
                      deterministic Portfolio Constructor
                         | optional disabled-by-default port
                         v
                      PortfolioOptimizerPort
                (isolated, bounded, side-effect-free)
                         |
                 OptimizationCandidate
         weights/target quantities, costs, constraint residuals,
         solver status/gap, reason, code/config/seed/data hashes
                         |
                   TargetPortfolioBuilder
              cash/cost/lot rounding + hard feasibility recheck
                         |
                     typed TargetPortfolio
                         |
              RiskKernel (independent final veto)
                         |
                  OMS -> NautilusTrader
                         |
                   ledger + attribution
\`\`\`

The optimizer must not read order credentials, submit orders, edit \`RiskPolicy\`, bypass \`TargetPortfolioBuilder\`, mutate account state, or write canonical records. No LLM/agent can relax constraints or select a model for execution by prose. An infeasible, time-limited, non-convergent, numerically unstable, invalidly rounded, unsupported, or missing-data result is **rejected**, and the deterministic safe path is abstention/no-trade (or an already-approved fallback explicitly validated by RiskKernel); never silently route an unchecked solution.

### Candidate interface (design sketch, not implemented)

- Input: \`snapshot_id\`, \`as_of\`/availability cutoff, instrument identities/universe, frozen expected-return/scenario/covariance artifacts, approved account positions/cash/marks, costs/impact/lot rules, \`risk_policy_version\`, objective/constraint schema, deadline and resource envelope.
- Output: \`OptimizationCandidate\` with signed target quantities or weights, expected net utility, predicted costs/turnover/risk, solver/backend/method/code/version/config identity, deterministic seed if relevant, termination status/gap, primal feasibility/constraint residuals, time/memory, input hashes and explicit rejection reasons.
- Validation: a separately coded deterministic validator converts quantities to executable venue increments, recomputes cash including fees/spread/impact, tests concentration/exposure/liquidity/turnover against current authoritative account state, and rejects impossible quantities. RiskKernel independently rechecks again at order time.
- Reproducibility: immutable problem manifest and snapshots; solver options and tolerances; deterministic tie-breaking if possible; every attempted and rejected optimization is recorded. No forward label enters a decision-time fit or optimizer input.

## Problems worth testing (in order)

1. **Continuous constrained allocation (first):** BTC/ETH-only research profiles; cost-aware mean-variance or suitable convex risk objective subject to full-cash accounting, position/concentration/leverage/liquidity/turnover bounds and minimum reserve. Compare OSQP vs Clarabel as supported. A small universe may reveal no incremental value over a simple baseline.
2. **Tail-risk/robust allocation:** only if the first profile exposes regime fragility or heavy-tail failure. Test CVaR, drawdown-aware or robust formulations in skfolio; challenge with Riskfolio-Lib only for a concrete gap.
3. **Multi-asset research:** equity/bond/crypto cross-asset target construction on independently qualified, point-in-time research bars/calendars, FX and corporate actions. No SPY/TLT order routing on Binance Testnet; equity/bond paper execution needs a separately qualified venue.
4. **Discrete trade constraints:** indivisible shares, min lots, cardinality, fixed trade cost, venue tick/step sizing; formulate an appropriate MILP with HiGHS first. Try CP-SAT only if logical/complex combinatorial benefits justify scaled-integer modeling.
5. **Research job scheduling (separate concern):** optional CP-SAT experiment for bounded CPU/GPU/RAM/deadline dependencies, measured against existing Prefect + Resource Governor behavior. Prefect remains workflow owner; Resource Governor remains the physical admission authority. Do not put a solver into the live event/order hot path.

An illustrative continuous objective is \`maximize(mu.T @ w - lambda * quad_form(w, covariance) - transaction_cost(w, current_w))\`, subject to explicitly versioned feasible bounds. It is not an alpha generator; \`mu\` and covariance must be point-in-time, estimated from training data only, and robust to estimation error. Convexity, unit/periodicity consistency and solver support are prerequisites.

## Offline bake-off and promotion protocol

1. **Pre-register:** exact decision deficiency, use cases, objectives, admissible risk limits, datasets/time windows, costs, rebalance frequency, baselines, failure and latency budgets, memory ceiling, trial budget and statistical acceptance criteria.
2. **Pin and isolate:** review upstream repository/revision, code and dependency licenses (including model/data terms), supply-chain health and sandbox permissions; install into a removable research environment on the actual laptop. No venue credentials, canonical writes, orders, or network dependency in deterministic replay.
3. **Freeze PIT data:** point-in-time closed bars, as-of macro/filing data when applicable, corporate actions and instrument identity. Estimate means/covariance/scenarios on *past-only* windows. Preserve missing/stale-data and intentionally leaky fixtures.
4. **Baseline and compare:** no-trade, equal-weight, inverse-volatility, simple risk-budget and prior champion; use identical cost/spread/impact, instrument universe, risk bounds, calendar, snapshot, seed and constraints for every candidate. Compare net utility, max drawdown, tail loss, concentration, turnover, failed/abstained allocations, constraint violations and stability across markets/regimes.
5. **Solver validation:** infeasible and unbounded instances; conflicting bounds; near-singular covariance; numerical overflow; zero liquidity; disconnected market; lot rounding; stale/negative cash; fixed cost and integer precision; deadline expiration; resource exhaustion. Require exact typed failure and zero downstream order effect. Verify optimality/status and feasibility independently of the solver's own report.
6. **Independent replay:** screen on frozen copies, use holdout/purged walk-forward and multiple-testing accounting, and independently replay finalists through the canonical AdvisorAI/NautilusTrader market/cost/latency model; forward shadow is separate from historical selection.
7. **Go/no-go and rollback:** retain at most one primary optimizer method per qualified profile behind \`PortfolioOptimizerPort\`; admit only for the specifically tested assets/venue/mode with human-reviewed change, pinned manifest and measured incremental net utility without safety/resource regression. On invalid result or regression, disable the port and use the existing approved deterministic no-trade/baseline path. Never self-promote.

**Exit evidence:** a benchmark report with immutable manifests, exact versions/hashes, trial ledger (including failures), constraint/residual checks, CPU/RAM/latency profiles on the target workstation, OOS net-cost comparison, independent replay, forward shadow evidence if seeking paper admission, and a rollback rehearsal. A better in-sample Sharpe ratio or solver objective is *not* sufficient.

## Ownership, scope and dependencies

- **Portfolio Constructor:** sole owner of the typed optimizer port and cost/quantity/feasibility conversion. Solver and high-level framework are replaceable implementations, not separate owners.
- **RiskKernel:** unchanged independent hard-limit approval/reduction/rejection, kill switch, authoritative account state and final order-time veto.
- **OMS/NautilusTrader:** unchanged event replay, orders, fills, reconciliation, execution costs/latency and paper-testnet venue adapter.
- **Alpha Team / Hermes:** may research or implement candidates in sandbox and emit versioned \`ExperimentArtifact\`/\`ValidationReport\`. They cannot activate optimizer backends or write production trading state.
- **Prefect / Resource Governor:** unchanged scheduler and hard physical resource authority; no permanent CP-SAT scheduling service.
- **Current release:** \`configs/v3_core.yaml\` excludes \`multiple_optimizers\`; \`docs/plans/real-api-paper-transition.md\` and PAPER-V1 readiness gates remain authoritative. This plan does not expand BTC/ETH paper execution, add equity/bond routing, replace the existing Phase 6 work, or authorize live capital.

## Related plans and references

- [Authoritative architecture §7.2](../../advisorai-federated-multi-agent-quant-architecture-v3.md#72-portfolio-construction)
- [Phase 6 institutional controls](phase-06-institutional-controls.md)
- [Phase 9 controlled expansion](phase-09-controlled-expansion.md)
- [Alpha Team extensibility](alpha-team-extension.md)
- [Technology decision register](../reference/technology-decision-register.md)
- [Real-API paper transition](real-api-paper-transition.md)
- [Current Portfolio Builder](../../src/advisorai/execution/portfolio.py)
