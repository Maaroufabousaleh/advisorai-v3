# Phase 6 — Institutional controls and research validity

## Objective

Complete portfolio, risk, accounting, attribution, and immutable outcome
controls needed for defensible integrated paper/testnet operation.

## Work packages

1. Compare constrained targets with no-trade, equal-weight, inverse-volatility,
   simple risk-budget, and prior champion portfolios.
2. Add robust covariance/factors, liquidity/capacity, margin,
   scenario/stress, drawdown, and expected-shortfall diagnostics. RiskKernel
   remains the sole deterministic approval/reduction/rejection authority.
3. Complete OMS, venue reconciliation, TCA, and P&L/risk/execution
   attribution.
4. Add purged walk-forward, multiple-testing, sensitivity, regime,
   independent-challenge, incident, and postmortem evidence.
5. Define immutable `DecisionTrainingRecord` inputs from decision-time-only
   state and a separate `OutcomeResolution` added only after defined horizons
   close. Preserve DecisionRecord → OutcomeResolution → TrainingExample
   identity and hashes.
6. Record realized returns, MAE/MFE, volatility, drawdown, transaction costs,
   slippage, paper order/fill outcomes, portfolio incremental utility,
   correctness/abstention quality, and regime only as later labels. Keep all
   future information out of pre-decision records.

## Exit and admission gate

Every paper action passes PIT, portfolio, cost, capacity, stress, and hard
limits; order/account state reconciles; unexplained attribution residuals
create an incident. A complete record pipeline is an implementation
deliverable, not authority to train or promote a model.

## Explicitly out of scope

No live capital, risk-limit relaxation by any agent, VaR-only safety claim,
future-label leakage, or training of the future AdvisorAI-CDM as part of this
plan.
