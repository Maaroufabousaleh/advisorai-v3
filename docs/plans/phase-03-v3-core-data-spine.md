# Phase 3 — V3-Core data spine

## Objective

Supply BTC and ETH paper decisions with replayable, independent, point-in-time
V3-Core evidence.

## Work packages

1. Collect native venue trades, books, bars, funding and open interest with
   append-only raw persistence.
2. Add Deribit derivatives context and reviewed company/official RSS sources.
   Classify each source using [ADR 0011](../decisions/0011-provider-source-authority-and-pit.md):
   official-primary, qualified-secondary, discovery/context-only, or
   unqualified. GDELT is discovery/context-only, not issuer truth. LSE may be
   an audited corroborator but never sole authority. Keep each feed's rights,
   delay, authentication, timestamp, revision, quality, and fallback record.
3. Expose freshness, quality, gap, origin, revision, first-available-time, and
   cross-source-disagreement checks and dashboards.
4. Freeze snapshots by explicit cutoffs for every replay, model, and mission.

## Exit gate

Immutable replay, complete source lineage, and cross-source disagreement handling
pass.

## Explicitly out of scope

No equities, SEC/ALFRED, browser collection, or a source class promoted merely
because it is free or aggregated.

SEC EDGAR and FRED/ALFRED remain candidates for later issuer and macro work;
this Phase 3 BTC/ETH scope does not admit them. Exchange feeds are not assumed
free or redistributable as a category. See the [source and technology
register](../reference/technology-decision-register.md) for their current
planning status and per-source acceptance criteria.
