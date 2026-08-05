# Architecture traceability

This matrix is the conformance control for the phase sub-plans. Requirements in
the authority are preserved verbatim in meaning; a later phase cannot weaken an
earlier boundary or acceptance condition.

| Authority section | Binding design rule | Owning phases | Verification evidence |
|---|---|---|---|
| 1, 2 | Typed/versioned hand-offs; deterministic risk and OMS are the sole execution path | 0–10 | Contract tests, dependency checks, replay/reconciliation tests |
| 3 | Five mission modes, measured resource admission, stated laptop ceilings and load shedding | 1, 5, 7–10 | Resource tests, metrics, soak record |
| 4 | Point-in-time metadata, Parquet/DuckDB/SQLite tiers, source grades and compliant acquisition ladder | 1, 3, 9 | Leakage fixtures, manifests, lineage and source audits |
| 5 | API reasoning via recorded gateway; local forecasts are evidence; baselines and calibrated utility decide promotion | 0, 4, 5, 7, 9 | Bake-offs, ModelCards, calibration and net-utility reports |
| 6 | Logical desks are elastic; shared ancestry is discounted; evidence gates retain dissent | 5, 7, 9 | Evidence-graph and quorum fixtures, scorecards |
| 7 | Forecasts produce constrained target portfolios; RiskKernel has sole veto; OMS is idempotent and reconciled | 2, 4–7, 10 | Risk, replay, execution, reconciliation and attribution tests |
| 8 | Hermes is on-demand, sandboxed and may emit quarantined artifacts only | 8, 9 | Isolation audit, capability contracts and review trail |
| 9 | Ledgers/evidence are authoritative; structured local observability; no self-modifying production authority | 1–10 | SQLite/Parquet records, lifecycle and incident tests |
| 10 | Always-on/off-demand process boundaries and repository dependency isolation | 0, 1, 2, 5, 8 | Package-group and import-boundary checks |
| 11 | Expansion follows the exact phase gates | 0–10 | Signed gate record per phase |
| 12 | V3-Core is BTC/ETH, one venue, 1-hour/5-minute/4-hour, paper/testnet only | 2–7 | Scope/config review and replay fixtures |
| 13 | Acceptance matrix is non-negotiable | 1–10 | Per-domain gate evidence |
| 14 | Explicit exclusions: no AI/browser/Hermes orders; no false independence/leakage/self-modification/HFT claims | 0–10 | Security, dependency, contract and review tests |
| 15 | Success is safety, reproducibility and rejection on insufficiency—not profitability | 1–10 | Soak, recovery and decision records |

## Global invariants

1. No agent, model gateway, Hermes task, browser task, imported capability, or
   source adapter can submit orders or relax risk limits.
2. `RiskPolicy` is versioned and immutable for a decision. `RiskKernel` can only
   approve, reduce, or reject against authoritative market/account/order state.
3. Large data moves by immutable artifact ID; never by copied chat transcript.
4. Backtests and decisions use explicit `as_of` cutoffs and first-available times.
5. A component becomes active only through its phase gate and recorded evidence;
   it remains a challenger otherwise.
6. Live capital is prohibited until Phase 10's explicit human approval gate.
