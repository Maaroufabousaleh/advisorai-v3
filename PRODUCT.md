# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React/TypeScript dashboard with a typed Python API; local/LAN deployment for the first release.

## Users

The primary user is a single owner-operator running and reviewing AdvisorAI V3 from a private workstation or trusted LAN. They need fast situational awareness and safe, explicit control over research missions, paper/testnet execution, deterministic risk controls, data quality, resource envelopes, incidents, recovery, and audit evidence.

## Product Purpose

AdvisorAI V3 is a federated research and paper-trading system with one deterministic safety and execution spine. It turns point-in-time data and independent evidence into auditable target portfolios, risk decisions, paper execution plans, reconciliation, attribution, and controlled learning. The dashboard makes the complete operating state understandable and controllable without bypassing those canonical services.

## Positioning

Many analytical agents may contribute evidence, but no forecast becomes a trade until it survives independent evidence checks, portfolio construction, deterministic pre-trade risk, realistic execution controls, reconciliation, and attribution. The system’s distinctive mechanism is federated intelligence with a single authoritative safety/execution boundary.

## Operating Context

The operator works on a resource-bounded Windows/WSL laptop and uses explicit modes: Trade/Fast, Standard, Deep, Builder, and Recovery. The system is paper/testnet only until external phase gates, soak evidence, reconciliation, and explicit human approval pass. Immutable point-in-time data, SQLite WAL ledgers, manifest-managed Parquet, DuckDB/Polars analysis, service ownership, incidents, and recovery records are operational truth.

## Capabilities and Constraints

- Existing Python services own missions, evidence councils, data, resource governance, account state, RiskKernel, OMS, reconciliation, incidents, and live readiness.
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
