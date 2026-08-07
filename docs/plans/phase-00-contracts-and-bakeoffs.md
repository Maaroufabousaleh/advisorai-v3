# Phase 0 — Freeze contracts and run bake-offs

## Objective

Make all subsequent choices reproducible before production dependencies are
adopted. This phase has no trading authority and no resident agent fleet.

## Work packages

1. Record architecture decisions for canonical ownership, Pydantic contracts,
   `ModelGatewayPort`, `ArchiveBackend`, `EventBusPort`, and the rejection of
   competing risk/execution authority.
2. Define and contract-test the core typed artifacts: Snapshot, Evidence,
   Forecast, TargetPortfolio, RiskPolicy/Decision, ExecutionPlan, Order/Fill,
   Reconciliation, Attribution, ModelCard, AgentRun, and CapabilityCard.
3. Run identical typed/tool-call tests for direct API, LiteLLM, and OmniRoute.
   Measure route identity, privacy, idle/active RSS, failure handling, and
   24-hour stability. LiteLLM is only a provisional baseline; OmniRoute remains
   quarantined until it passes all tests.
4. Compare TTM-R3 against the TTM-R2 control, Chronos-2-small,
   Kronos-mini/small, and later TabPFN-TS. Qualify TSPulse separately for
   anomaly/integrity/regime features; it is never a price forecaster.
   against naive/statistical/LightGBM baselines for utility, calibration,
   latency, RAM, and VRAM. Do not treat co-trained variants as independent.
5. Measure Nautilus adapters/replay, Prefect, Hamilton, Parquet manifests versus
   DuckLake, one isolated Hermes coordinator/subagent, and rclone-crypt upload,
   verification, and two-provider restore.

## Required records

Pinned versions, code/data/model hashes, environment lock, benchmark inputs,
resource samples, privacy/identity result, failure trace, and admission decision
belong in the model/capability/architecture ledgers.

## Exit gate

Selected components fit the resource envelopes, exact model/source versions are
reproducible, and there is no unexplained 24-hour memory growth.

## Explicitly out of scope

No live or paper orders, no automatic promotion, no browser workflow, no Hermes
write authority, and no dependency on a cloud drive or remote event bus.
