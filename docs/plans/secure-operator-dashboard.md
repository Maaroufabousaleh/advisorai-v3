# AdvisorAI V3 Secure Operator Dashboard

## Summary

Build a local/LAN, single-owner operator console by extending the React/TypeScript app and typed Python API. It will make portfolio risk, system health, data freshness, paper execution, research evidence, and audit state instantly visible—while preserving AdvisorAI’s existing rule that deterministic services, not the UI or AI, own risk, orders, ledgers, and live activation.

The visual world is a restrained “quant command deck”: dark, dense, precise, and futuristic without decorative neon. Real-time state uses purposeful cyan, warning amber, loss/critical red, and approval green; typography and charting prioritize high-speed professional scanning over spectacle.

## Workstation views

Extend the existing [React console](../../dashboard) and
[dashboard API](../reference/api.md). Use a shared instrument search, watchlists,
asset and venue filters, and synchronized time ranges. Keep a persistent state
strip for paper mode, account, data freshness, reconciliation and halt status.
Selecting an instrument, news item, order or position opens contextual detail
without losing the current workspace.

| Workspace | What the operator can see and do |
| --- | --- |
| Overview | See reconciled net liquidation, daily P&L and drawdown, exposure and risk headroom, market/session state, key headlines, recent fills and system exceptions. Drill into the owner of each number. |
| Portfolio | Switch between an individual paper account and a labeled aggregate in its reporting currency. Inspect cash, positions, cost basis, realized and unrealized P&L, fees, allocation, gross/net exposure and time-series performance. Show valuation and FX timestamps if applicable. Compare a selected benchmark, distinguish funding flows from returns and expose options Greeks where the underlying data supports them. |
| Markets & OpenBB | Search securities and venue instruments, build watchlists, screen an entitled universe, inspect linked price/volume charts and filings, and filter options chains by expiry, strike and right. Show bid/ask, spread, contract size, open interest, implied volatility and Greeks where available, including the timestamp, delay and provenance for each feed. |
| News & Events | Filter by portfolio, watchlist, ticker, underlying, topic or source. Combine entitled headlines, SEC/issuer releases, earnings and macro calendars, and approved crypto venue notices. Show publication and ingestion times, original links, and affected positions. Group duplicate coverage and flag corrections. Render full text or transcripts only where display rights allow. |
| Paper Execution | Inspect active and historical orders, fills, rejections, cancellations, fees, slippage and reconciliation. Trace a fill back to the proposal, strategy/version, risk result, paper account and venue acknowledgement. |
| Research & Models | Read surfaced papers, filings and linked evidence. Compare experiments and forecasts by checkpoint, horizon, as-of time, uncertainty, calibration, baseline error and source snapshot. Show agent proposals, dissent, abstentions and fallback reasons with their underlying artifacts. |
| Risk, Operations & Audit | Inspect hard limits, exposure, drawdown, stale sources, disconnects, recovery and audit history. Follow an incident to the affected account and owning service. Use the existing guarded halt/resume controls. |

### Integration choices

| Component | Planned use |
| --- | --- |
| [OpenBB Open Data Platform](https://docs.openbb.co/odp) | Power one **Markets & OpenBB** page in the existing AdvisorAI React app via the I5 read-only integration. Start with one real qualified endpoint and label upstream provider, delay, entitlement and partial coverage. Expand by dataset after qualification. |
| [OpenBB Workspace custom backends](https://docs.openbb.co/workspace/developers/data-integration) | Optional external research workspace using its documented widget API. Evaluate a separate read-only launch/deep link and narrow market widget projection only after confirming compatibility, account terms and access. Keep paper account, order and control data inside AdvisorAI. |
| [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) | First direct chart integration candidate for candlesticks, volume, comparative equity curves and synchronized crosshairs. Carry upstream [attribution](https://github.com/tradingview/lightweight-charts/blob/master/website/docs/intro.mdx). |
| [AG Grid Community](https://www.ag-grid.com/eula/community/) | First direct grid integration candidate for dense positions, fills, news and option chains. Use Community features for sorting/filtering and measured rendering; use typed server pagination for views beyond the client budget. |
| [FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) and [Ghostfolio](https://github.com/ghostfolio/ghostfolio) | Evaluate one concrete Fincept market or research workflow and use Ghostfolio as an allocation/returns reference. Review the exact AGPL-licensed component, deployment terms, data rights and incremental value before deciding on code reuse. Keep AdvisorAI's own visual design and existing account projections. |

### Delivery and acceptance

1. **Portfolio and execution first.** On a permitted paper session, compare
   displayed cash, positions, marks, P&L, fees and fill IDs against the same
   ledger snapshot and venue reconciliation. Verify transfer-adjusted returns
   and reporting-currency conversion on known examples. Test one stale mark,
   one partial fill and one mismatch. Each must show its actual state and
   provenance without presenting fabricated values or combining stale totals
   across accounts.
2. **Markets, OpenBB and news.** Display a qualified OpenBB result and verify
   the visible value against its direct upstream at the same cutoff. Test an
   unavailable scope and stale quote. Show a sourced issuer release, one
   duplicate headline group, and a later correction in the news view. Test a
   listed option across two expiries and verify every field against the
   admitted contract data. Cross-link the instrument's chart, option chain,
   portfolio position and news.
3. **Research and model drill-down.** Trace one readable primary document to
   its originating source and one real Chronos and TimesFM run to its exact
   checkpoint, input snapshot and horizon. Show score and uncertainty only
   where those results exist. Open and inspect one agent proposal and one
   abstention before following an eligible proposal to its paper fill.
4. **Operator acceptance.** Demonstrate hotkey and keyboard-only flow,
   visible reconnect/stale states, persistent responsive halt control,
   trace links and zero browser exposure of provider secrets. Measure
   grid/chart interaction and initial load on the target desktop with a
   realistic option chain, recorded news stream and paper order history.
   Report the dataset size, device and latency budget before recording a pass.

Keep the initial portfolio workspace useful as soon as qualified ledger
projections are available. Add the Markets & OpenBB and News & Events views
as I5 providers qualify, and verify the entire dashboard during the
[I9 workstation session](native-first-integration-roadmap.md#i9--joined-autonomous-paper-workstation).

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
- The repository already supplies the React application and a typed FastAPI dashboard API. Extend their existing read models, guarded commands and authentication.
- The dashboard introduces projections and adapters around existing canonical services; it does not duplicate account, risk, OMS, ledger, or service ownership.
