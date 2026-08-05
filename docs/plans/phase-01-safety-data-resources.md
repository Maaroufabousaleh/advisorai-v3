# Phase 1 — Safety, data truth, and resource skeleton

## Objective

Build the deterministic foundation that makes later research and paper trading
auditable, point-in-time safe, resource-bounded, and recoverable.

## Work packages

1. Implement immutable Pydantic contracts from Phase 0, including the full
   point-in-time observation and evidence-lineage metadata.
2. Implement local Bronze/Silver/Gold storage using manifest-managed immutable
   Parquet, snapshot cutoffs, source origin/revision rules, and canonical
   instrument identity. Use local DuckDB/Polars only as query/compute clients;
   SQLite WAL owns ledgers.
3. Create separate account, order, mission, model, capability, and incident
   ledger namespaces with idempotency keys and append-only event records.
4. Implement measured Resource Governor leases, the authority-preserving
   load-shedding order, structured local traces, and immutable config/version
   bundles with auditable activation and rollback.
5. Seed only the resource/mode configuration required by V3-Core. Preserve the
   specified mode ceilings and concurrency caps; no agent may self-report
   capacity.

## Exit gate

Bronze rebuild is deterministic; point-in-time leakage fixtures fail;
idempotency and config rollback pass.

## Explicitly out of scope

No venue connection, risk approval implementation, target portfolio optimizer,
Nautilus runtime, gateway/agent invocation, browser, Hermes, training, or order
submission. Phase 1 only establishes their typed boundaries.
