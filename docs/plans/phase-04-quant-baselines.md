# Phase 4 — Quantitative baseline council

## Objective

Admit only compact forecast models that improve past-only, calibrated, net
decision utility for V3-Core.

## Work packages

1. Build naive/statistical and LightGBM baselines plus their data/feature/label
provenance.
2. Run TTM-R3 with TTM-R2 as its control, then TSPulse in a CPU wave. TSPulse contributes integrity/regime
   features rather than being assumed to forecast price.
3. Select exactly one initial GPU family—Chronos-2-small or Kronos-mini—through
   Phase 0/4 evaluation. Load one family at a time and micro-batch assets.
4. Implement common ForecastArtifact, rolling calibration, abstention, utility,
   correlation, regime-failure, latency/RAM/VRAM evaluation, rapid screening,
   and realistic Nautilus replay.

## TimesFM 3 research challenger (post-design discovery)

TimesFM 3 (`google/timesfm-3.0-pytorch`) is recorded as a
`PRIORITY_CHALLENGER` with `RESEARCH_ONLY`, `NON_PRODUCTION`, and
`NON_COMMERCIAL_WEIGHTS` restrictions. It is a newly discovered, non-blocking
Phase-4B-style qualification track; it does not replace naive, drift,
seasonal, linear, LightGBM, TTM-R2, TTM-R3, Chronos-2-small, or any already
qualified candidate, and it is not added to the authoritative V3-Core roster.

The sequence is:

```text
current Phase-4 experiment
        -> seal current evidence
        -> TimesFM-3 research qualification
        -> admit / narrow-role / reject
```

Qualification cannot block the existing core roadmap unless it exposes a
correctness defect in a shared contract. The TimesFM 3 adapter consumes frozen
point-in-time inputs and emits the existing `Forecast` artifact inside a
research evidence wrapper. The path ends at calibration, utility,
disagreement, and challenger evidence; it has no path to orders, OMS,
RiskKernel, credentials, or live capital.

The first controlled matrix is deliberately incremental: A is BTC-only
univariate; B adds ETH for a pure multivariate test; C adds realized
volatility and volume; D adds funding, open interest, and basis; E adds only
strictly known-future calendar covariates. Each step is compared against
naive, drift, seasonal, linear, LightGBM, TTM-R2, TTM-R3, Chronos-2-small,
and the TimesFM 3 univariate/multivariate variants. Targets include return,
direction/calibration, realized volatility, future range, volume/liquidity
state, and probabilistic/tail forecasts. Generic benchmark performance is not
financial-alpha evidence.

The machine-readable matrix is
[`configs/research/timesfm3-qualification.yaml`](../../configs/research/timesfm3-qualification.yaml);
the checkpoint governance record is
[`configs/research/timesfm3.yaml`](../../configs/research/timesfm3.yaml).

## Exit gate

No model is admitted unless it adds past-only calibrated net utility or useful
risk information over mandatory baselines within resource limits.

## Explicitly out of scope

No automatic model promotion, multi-model GPU residency, direct model-to-order
path, or TabPFN-TS baseline dependency.
