# Research replay v1 — multi-asset time-series momentum

One command replays a sourced, versioned DSL factor against a buy-and-hold
baseline on frozen point-in-time ETF inputs, separately per exposure.

## Reproduce

```bash
# from the omp/research-to-paper-integration worktree
uv sync --group dev
uv run python scripts/run_research_replay.py --label <your-label>
cat var/research-replay/evidence/<your-label>/manifest.json
```

Expected (frozen snapshot `data/research-replay/snapshots/etf-daily-2015-2025-v1`):

| Exposure | Obs | Trades | Net (bps) | Baseline (bps) | Accuracy |
|---|---|---|---|---|---|
| SPY | 120 | 9 | 6650.84 | 14335.70 | 0.6250 |
| TLT | 120 | 18 | -889.77 | -1912.53 | 0.4417 |

The factor loses to buy-and-hold on SPY and loses less than buy-and-hold on
TLT. Both are valid measurements; nothing was tuned on the holdout.

## What is proven

- Hypothesis sourced to Moskowitz–Ooi–Pedersen (JFE 2012,
  `doi:10.1016/j.jfineco.2011.11.003`), citation-only intake, lineage
  `paper → hypothesis → factor → experiment → validation` in the registry.
- DSL factor `c2faeb70…` parsed, hashed, and evaluated past-only (252d
  formation, 21d holding, month-end cutoffs, 21d embargo, purged walk-forward).
- 120 observations per exposure from 2766 aligned SPY/TLT bars (2015–2025),
  vendor-adjusted closes, zero gaps, per-exposure conservative costs.
- Deterministic `ReplayEngine` ordering with conflict detection; numerics
  reproduce exactly across state dirs (see `test_frozen_snapshot_reproduces…`).
- Per-exposure independence: one bad exposure is recorded as failure evidence,
  never masks its sibling.

## What is not proven

- No paper-order authority for any market; this is research measurement only.
- Yahoo vendor data is research-grade, not execution-grade; redistribution
  terms apply to the raw receipts.
- Regime labels are realized-volatility terciles, not the (unimplemented)
  canonical MarketStateArtifact.
- No model, Hermes, or OpenBB involvement yet — later slices.
