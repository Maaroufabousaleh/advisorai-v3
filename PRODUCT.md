# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React/TypeScript dashboard with a typed Python API; local/LAN deployment for the first release.

## Users

The primary user is a single owner-operator running and reviewing AdvisorAI V3 from a private workstation or trusted LAN. They need fast situational awareness and safe, explicit control over research missions, paper/testnet execution, deterministic risk controls, data quality, resource envelopes, incidents, recovery, and audit evidence.

## Product Purpose

AdvisorAI V3 is a local-first, session-oriented quantitative and agentic workstation with one deterministic safety and execution spine. The owner starts it when needed, typically operates it for approximately 5–8 hours, stops cleanly, and restarts later after state recovery, paper/testnet reconciliation, and permitted data catch-up. It combines point-in-time data and specialized model evidence through an Evidence Council and typed Decision Model proposal, then constructs auditable target portfolios, risk decisions, paper execution plans, reconciliation, attribution, and controlled learning. The dashboard makes the complete operating state understandable without bypassing canonical services.

## Positioning

Many analytical agents may contribute evidence, but no forecast becomes a trade until it survives independent evidence checks, portfolio construction, deterministic pre-trade risk, realistic execution controls, reconciliation, and attribution. The system’s distinctive mechanism is federated intelligence with a single authoritative safety/execution boundary.

## Operating Context

The operator works on a resource-bounded Windows/WSL laptop and uses explicit modes: Trade/Fast, Standard, Deep, Builder, and Recovery. Paper/testnet is the initial operating scope. Session lifecycle and integrated paper evidence govern workstation readiness; phase gates govern operational admission and promotion. Live capital remains separate, explicitly human-approved, and tightly gated. Immutable point-in-time data, SQLite WAL ledgers, manifest-managed Parquet, DuckDB/Polars analysis, service ownership, incidents, and recovery records are operational truth. A server/always-on deployment is optional and has a separate endurance profile.

## Capabilities and Constraints

- Existing Python services own missions, evidence councils, data, resource governance, account state, RiskKernel, OMS, reconciliation, incidents, and live readiness. Complete session start/stop/recovery and the generic Decision Model boundary are target implementation work, not current capability claims.
- The dashboard may read projections and issue narrowly scoped commands through authenticated API boundaries; it must not write ledgers directly, loosen limits, submit live orders, expose credentials, or let AI services control orders.
- V1 controls paper/testnet workflows and visibly reports live readiness while keeping Phase 10 activation locked.
- The first deployment is private local/LAN with password, MFA, step-up re-authentication, TLS when exposed beyond localhost, strict session controls, and auditability.

## Brand Commitments

The owner requested a cool, futuristic quantitative-finance control room that remains professional, clear, and easy to operate. The visual language must support high-speed scanning and confidence rather than decorative spectacle.

## Evidence on Hand

The architecture dossier, phase plans, executable Python contracts, tests, YAML configuration bundles, service registry, ledgers, risk controls, and live-readiness guard are in the repository. No existing web UI, HTTP listener, logo, customer evidence, or production dashboard data exists. Illustrative dashboard values must be labelled synthetic or simulated paper state.

## Product Principles

1. Deterministic controls are authoritative.
2. Every decision is explainable, time-bounded, and auditable.
3. Evidence independence matters more than agent count.
4. Resource, data, and execution failures fail closed.
5. The operator sees the whole system before taking a high-impact action.

## Accessibility & Inclusion

The dashboard must support keyboard navigation, visible focus, non-color status communication, high contrast, reduced motion, accessible chart summaries, and responsive monitoring at smaller widths.
