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

## Exit gate

No model is admitted unless it adds past-only calibrated net utility or useful
risk information over mandatory baselines within resource limits.

## Explicitly out of scope

No automatic model promotion, multi-model GPU residency, direct model-to-order
path, or TabPFN-TS baseline dependency.
