# ADR 0001: Canonical trading authority

Status: accepted (architecture authority)

AdvisorAI has one authoritative sequence:

`validated snapshot → independent evidence → calibrated forecast → target portfolio → deterministic RiskKernel → deterministic OMS/Nautilus execution → reconciliation → attribution`.

The Advisor API/Mission Router owns mission policy; PydanticAI/Pydantic Graph own
typed analysis and fusion; NautilusTrader owns live-event replay and execution;
the deterministic Portfolio Constructor and RiskKernel own portfolio safety; the
OMS ledger owns order/fill truth. Agents, model gateways, Hermes, browser tasks,
and imported capabilities have no order submission or risk-limit relaxation
authority.

Consequences:

- `TargetPortfolio`, `RiskDecision`, and `ExecutionPlan` are typed artifacts,
  not authorizations to trade.
- All parent intents and child orders require idempotency keys and later
  reconciliation.
- The Phase 1 package intentionally contains no exchange, order-submission, or
  agent-runtime implementation.
