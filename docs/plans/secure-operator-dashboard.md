# AdvisorAI V3 Secure Operator Dashboard

## Summary

Build a local/LAN, single-owner operator console using React/TypeScript and a typed Python API. It will make portfolio risk, system health, data freshness, paper execution, research evidence, and audit state instantly visible—while preserving AdvisorAI’s existing rule that deterministic services, not the UI or AI, own risk, orders, ledgers, and live activation.

The visual world is a restrained “quant command deck”: dark, dense, precise, and futuristic without decorative neon. Real-time state uses purposeful cyan, warning amber, loss/critical red, and approval green; typography and charting prioritize high-speed professional scanning over spectacle.

## Key Changes

- Establish two clearly separated planes:
  - **Data plane:** immutable read models for account state, positions, P&L/attribution, market/data quality, forecasts, evidence, runs, resource envelopes, incidents, and ledger-backed audit history.
  - **Control plane:** narrowly scoped commands for launching paper missions, selecting approved modes, proposing validated configuration revisions, starting/halting paper workflows, and emergency halt. The UI never talks directly to SQLite, brokers, secrets, RiskKernel, OMS, or venue adapters.

- Add an authenticated Python API with versioned typed response/command schemas and a React/TypeScript client.
  - Read APIs expose summaries and drill-down artifacts with `as_of`, freshness, source lineage, policy/config version, and canonical artifact IDs.
  - Command APIs accept idempotency keys, enforce server-side authorization, append auditable events, and return a command receipt plus resulting authoritative state.
  - Paper-order or target actions must traverse the existing evidence gate → target portfolio → RiskKernel → OMS path; no endpoint may create a bypass.
  - Live activation remains unavailable in V1. The UI surfaces the Phase 10 checklist, missing evidence, and immutable “paper-only” status rather than an activation control.

- Build the primary overview around the first-ten-seconds operating question:
  - A persistent top bar shows environment (`PAPER/TESTNET`), global kill status, active mode, data freshness, reconciliation state, resource headroom, and last ledger update.
  - The main grid prioritizes net liquidation/P&L, exposure and limit utilization, risk decision state, data-quality failures, critical incidents, active mission progress, and recent reconciliation/TCA outcomes.
  - Every figure links to its evidence: snapshot, timestamp, policy/config hash, source family, and owning service.

- Create focused workspaces: **Overview**, **Missions & Evidence**, **Portfolio & Paper Execution**, **Risk & Limits**, **Data/Models**, **System Health**, **Incidents & Recovery**, **Audit Trail**, and **Settings**.
  - Mission views expose consensus, strongest dissent, evidence independence, confidence, expiry, and abstentions—not just a recommendation.
  - Risk views make hard limits, current utilization, rejected decisions, stale-data blocks, and kill-switch status legible; limits are displayed as policy-owned and cannot be loosened by automated workflows.
  - System views mirror the executable service registry, dependency state, mode admission, resource governor/load shedding, source health, and recovery records.
  - Audit views provide immutable timelines and filters for mission, decision, risk, configuration, incident, approval, and paper-execution artifacts.

- Make controls deliberate and reversible:
  - Configuration changes use a reviewed draft → typed validation → human confirmation → durable audit event → atomic apply/rollback flow, with a visible diff and affected services.
  - Emergency halt is one prominent, always-available action that immediately enters a safe state; resuming requires MFA step-up, explicit confirmation, clean reconciliation, and a recorded reason.
  - Destructive actions, risk-policy changes, paper-order controls, recovery operations, and configuration applies require a short-lived re-authentication window plus typed confirmation of the action target.

- Secure the local/LAN deployment by default:
  - Use Argon2id password storage, TOTP MFA with recovery codes, short-lived server-side sessions in `Secure`, `HttpOnly`, `SameSite=Strict` cookies, CSRF protection, rate limiting, session/device revocation, and idle timeout.
  - Bind the API to localhost by default; LAN use requires explicit TLS configuration and an allowlisted network/host setting. Never place secrets, provider credentials, raw tokens, or sensitive prompt content in the client, logs, or audit UI.
  - Apply strict security headers/CSP, origin checks, input validation, least-privilege API scopes, structured security audit events, and dependency/SBOM scanning.
  - Treat the dashboard as an untrusted client: all authority, phase gates, policy hashes, freshness checks, and approval invariants are revalidated by the owning backend service.

## Experience and Responsive Behavior

- Desktop is the primary operating surface: a compact left rail, fixed state strip, resizable data panels, synchronized time/range controls, and a contextual inspector for any artifact or alert.
- Mobile is a secure monitoring companion: health, alerts, kill control, approvals, and read-only drill-downs remain available; dense execution/configuration workflows stay desktop-first.
- Use progressive disclosure: the overview shows exceptions and decision-ready summaries, while detail panels reveal lineage, source artifacts, model/version hashes, and event timelines.
- Support keyboard navigation, high-contrast semantic states, non-color status labels, reduced motion, accessible chart summaries, and responsive empty/loading/error/offline states.

## Test Plan

- Contract tests for every dashboard read model and command schema; verify artifact IDs, `as_of` times, versions, lineage, and no future/unavailable data leaks.
- Authorization tests for session expiry, MFA step-up, CSRF, origin protection, rate limits, command idempotency, audit logging, and denial of direct order/risk/live-deploy authority.
- End-to-end tests for paper mission launch, evidence-gated rejection, risk rejection, configuration review/rollback, emergency halt/resume, incident triage, and live-readiness display.
- UI tests for critical-state hierarchy, keyboard operation, screen-reader labels, reduced motion, responsive monitoring layout, stale/loading/error states, and no reliance on color alone.
- Security and regression checks: dependency scanning, CSP/header verification, secret-redaction tests, API fuzz/validation coverage, and confirmation that the existing Phase 0/7/10 gates remain unbypassable.

## Assumptions

- The first release is for one trusted owner-operator on a private local/LAN deployment.
- V1 controls paper/testnet workflows only; live trading stays locked until existing external evidence and explicit human approval gates pass.
- FastAPI (or an equivalent typed Python HTTP layer) and the React application are new additions; no current web framework or HTTP listener exists.
- The dashboard introduces projections and adapters around existing canonical services; it does not duplicate account, risk, OMS, ledger, or service ownership.
