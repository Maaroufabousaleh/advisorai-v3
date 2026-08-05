# Phase 3 — V3-Core data spine

## Objective

Supply BTC and ETH paper decisions with replayable, independent, point-in-time
V3-Core evidence.

## Work packages

1. Collect native venue trades, books, bars, funding and open interest with
   append-only raw persistence.
2. Add Deribit derivatives context, official/company RSS and GDELT events/news;
   LSE may be an audited corroborator but never sole authority.
3. Expose freshness, quality, gap, origin, revision, first-available-time, and
   cross-source-disagreement checks and dashboards.
4. Freeze snapshots by explicit cutoffs for every replay, model, and mission.

## Exit gate

Immutable replay, complete source lineage, and cross-source disagreement handling
pass.

## Explicitly out of scope

No equities, SEC/ALFRED, browser collection, or a source class promoted merely
because it is free or aggregated.
