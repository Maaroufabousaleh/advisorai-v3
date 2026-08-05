# Phase 2 — Deterministic paper-trading core

## Objective

Make one approved crypto venue's paper/testnet lifecycle safe with all AI and
research services offline.

## Work packages

1. Add one native venue adapter, raw event spool/replay, and the Nautilus event
   pipeline; NautilusTrader is the canonical replay/execution engine.
2. Build authoritative account, position, cash, fee, funding, and margin state.
3. Implement the minimal cost-aware TargetPortfolio constructor, deterministic
   RiskKernel, immutable RiskPolicy binding, and kill switch.
4. Implement the canonical idempotent OMS state machine, simple immediate and
   passive-limit execution policies, paper/testnet lifecycle, reconciliation,
   and initial TCA.

## Exit gate

Duplicate, ambiguous acknowledgement, partial-fill, reconnect, stale-data,
venue-outage, price-collar, and kill-switch fixtures fail safely and every ledger
reconciles.

## Explicitly out of scope

No live capital, no LLM decision path, no competing execution engine, and no
TWAP/VWAP/POV until measured need exists.
