# ADR 0002: Local point-in-time truth and measured resource authority

Status: accepted (architecture authority)

Local manifest-managed Parquet holds immutable Bronze/Silver/Gold data; SQLite
WAL holds mission, model, capability, incident, account, order, and fill ledger
state; DuckDB/Polars are query/compute clients. Cloud drives are not compute
truth. Every observation has first-available time, ingestion time, source origin,
revision, parser, raw artifact hash, quality grade, and intended use.

Backtests and missions must read explicit `as_of` snapshots. Any artifact or
observation that became available later is rejected rather than silently merged.

The Resource Governor samples operating-system RAM and GPU state. It owns leases,
mode ceilings, concurrency caps, the single GPU lease, and the prescribed
optional-work load-shedding order. Agents cannot self-report capacity. Critical
account/order/fill state, reconciliation, risk, kill switch, and raw-market
persistence are never shed.
