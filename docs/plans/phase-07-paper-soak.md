# Phase 7 — Unattended paper soak

## Objective

Operate V3-Core continuously and prove that its safety and measured value survive
real operational and adverse conditions.

## Work packages

1. Run the always-on V3-Core services and failure injection/restore drills.
2. Maintain data/model/agent/risk/execution scorecards and resource/headroom
   measurements.
3. Compare outcomes against no-trade and simple benchmark portfolios net of
   realistic costs.
4. Triage and resolve all reconciliation, safety, data integrity, and recovery
   incidents before proceeding.

## Durable launch-readiness boundary

`advisorai.soak.DurablePaperSoakRunner` provides the process/evidence boundary
for the eventual supervised paper runtime. It binds one immutable run identity
to code, configuration, policy, model-roster, source-roster, venue, and
paper/testnet environment hashes; writes fsync'd hash-chained interval records;
maintains an atomic PID/heartbeat status projection; serializes one owner with
a lock; and resumes an existing root without resetting its start time. A real
terminal record at or after the configured 60-calendar-day boundary is required
before `summary.json` is written. Bounded `max_samples` runs remain progress
evidence only and cannot open Phase-7 admission. The immutable config also
records a sanitized exact command and stop/restart procedures.

The runner accepts an already-wired paper-cycle sample factory. It has no venue,
credential, order, cancel, RiskKernel, or OMS authority of its own. A real root
must not be launched until the Phase 0–6 prerequisites are admitted and the
operator has reviewed the exact process configuration.

## Exit gate

At least 60 calendar days and a meaningful decision/trade sample, including
adverse conditions; stable resources; no unresolved reconciliation or safety
incident; and positive evidence net of realistic costs. Time alone does not prove
profitability.

## Explicitly out of scope

No live capital, Hermes integration, or simultaneous expansion of framework,
model, and source scope.
