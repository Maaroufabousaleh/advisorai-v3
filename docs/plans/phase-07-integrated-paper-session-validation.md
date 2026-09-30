# Phase 7 — Integrated Paper Session Validation

## Objective

Prove that the complete V3 system works safely end to end across ordinary
owner-operated paper/testnet sessions, stops, restarts, missing-data intervals,
process failures, and changing market conditions. Normal use is an active
session of approximately 5–8 hours; AdvisorAI need not remain alive between
sessions.

```text
data → features → frozen forecast fabric → Evidence Council → Decision Model
→ TargetPortfolio → RiskKernel → OMS → Nautilus paper/testnet venue
→ reconciliation → TCA/attribution → outcome memory → next-session recovery
```

## Validation work packages

1. Repeat start → operate → clean stop → restart and compare authoritative
   account, position, P&L, order, ledger, and mission state.
2. Inject or replay source loss, stale data, network outage, exact gap
   detection/backfill, and PIT snapshot rebuild.
3. Restart with open paper orders and ambiguous acknowledgements; reconcile
   venue/account/order state before any further action and prove no duplicate
   order.
4. Exercise unclean shutdown, ledger validation/rebuild, paper account
   reconstruction, and recovery mode.
5. Interrupt Hermes/research jobs and prove their immutable mission status is
   checkpointed/interrupted, never falsely complete; resume only if policy
   permits.
6. Kill a model/GPU worker and prove the result is missing evidence followed
   by an allowed fallback or abstention.
7. Repeat lazy model load/inference/unload and full application cycles; measure
   bounded residual RSS/VRAM, processes, file descriptors, locks, and leases.
8. Verify traceability from source/PIT snapshot through proposal, portfolio,
   RiskKernel, OMS, fills/reconciliation, TCA/attribution, outcome labels, and
   the following session.

## Session evidence

Use independent sessions and meaningful variation in volatility/regime,
source health, network/process failure, and decision/trade outcomes. A
provisional review target may be 20–30 meaningful sessions and approximately
100–150 accumulated operating hours, subject to evidence-quality review. These
are planning targets, not scientifically established magic thresholds;
calendar duration or raw hours alone never prove readiness or profitability.
The reviewed scope must have zero unresolved safety, reconciliation, or data
integrity incidents.

## Exit gate

The complete system recovers authoritative state; reconciles venue/account and
orders before resuming; detects and handles data gaps without PIT leakage;
does not duplicate actions after restart; treats failed workers/jobs truthfully;
cleans up locks and leases; stays within reviewed resource bounds across
repeated sessions; and never escalates authority after restart. Model/strategy
admission and live-capital approval remain separate decisions.

## Historical and optional endurance evidence

Old 24-hour stability and 60-calendar-day paper requirements are superseded
planning requirements for this single-owner workstation. Existing runs,
interruption records, hashes, and gate results remain historical facts and are
not rewritten. Long continuous uptime may be used as an optional diagnostic or
as a profile-specific gate for a future always-on server deployment; it is not
a prerequisite for workstation readiness.
