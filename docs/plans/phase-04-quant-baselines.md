# Phase 4 — Quantitative reference fabric

## Objective

Integrate a modest, role-separated forecast and feature fabric so the complete
system can be implemented and exercised. The roster is a reference for
integration, not a claim of model superiority or permanent admission.

## Reference roles

| Adapter | Role |
|---|---|
| Chronos-2-small | General probabilistic/time-series forecast and uncertainty intervals |
| TTM-R2 | Lightweight independent temporal forecast/control; retain the already integrated/measured R2 reference |
| Kronos-small | Finance/OHLCV/K-line-specific modeling and complementary market-price-action evidence |
| LightGBM | Deterministic structured/tabular baseline and factor interactions |
| FinBERT or selected compact financial sentiment implementation | News/event/language sentiment evidence |

## Work packages

1. Keep naive/statistical baselines and LightGBM as deterministic comparisons.
2. Put each model behind exact, versioned typed adapters and record provenance,
   cutoff, calibration state, uncertainty, output validity, latency, and
   resource usage.
3. Keep Chronos and Kronos as distinct roles. Use lazy model loading and the
   single global GPU lease; run forecast waves sequentially and unload workers
   when done. TTM-R2 may run when useful. The scheduler can skip optional
   inference when evidence is sufficient, data is unavailable, a worker is
   quarantined, resources are exhausted, or latency would be exceeded.
4. Connect calibrated forecast evidence to the EvidenceGraph without granting
   a forecast or model any portfolio, risk, broker, or order authority.
5. Make model failure explicit as missing evidence; use only policy-approved
   fallback behavior or abstain.

## Integration qualification and admission

**Level A:** exact identity/version; input/output typing; point-in-time safety;
bounded resource use; load/inference/unload reliability; failure behavior; no
authority leakage. This is the minimum integration check.

**Level B:** assess the reference roster in integrated paper operation for
calibration, disagreement, decision utility, latency, costs, resources,
regimes, and incremental contribution.

**Level C:** after the integrated application exists, run challenger research
such as TimesFM 3, TTM-R3, Kronos-base, TabPFN-TS, newer TSFMs, and new
decision models. These comparisons do not block core infrastructure.

## Exit gate

The reference adapter interfaces, failure fallbacks, resource waves, and
traceable evidence are integrated and testable. Operational model promotion
requires its own evidence and recorded decision; a pending or rejected model
does not block implementation of Phase 5–9 components when quarantined.

## Explicitly out of scope

No mandatory Chronos-vs-Kronos winner selection, TTM-R3 re-experiment,
multi-model GPU residency, broad bake-off, custom model training, or direct
model-to-order path.
