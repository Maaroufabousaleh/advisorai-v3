# Trading Scope & Authority Matrix V1

This policy is stacked on the Human Governance & Risk Policy V1. It is a
non-executing, typed scope boundary: it does not activate Phase 10, load
credentials, create an order capability, or change RiskKernel/OMS behavior.

## Authority principle

```text
AI estimates opportunity
  -> Portfolio Constructor proposes exposure
  -> GovernanceDecision evaluates opportunity/risk policy
  -> TradingScopeDecision evaluates legal/system scope
  -> deterministic RiskKernel is the final veto
  -> authoritative OMS owns order state
```

An `ALLOW_WITHIN_GOVERNANCE` result is not an order, venue permission,
credential, capital authorization, or live activation. `execution_authority` is
permanently false. No LLM, agent, dashboard, browser, Hermes task, Research
Brain, or Alpha Team component can approve its own authorization or execute a
scope-changing action.

## V1 eligibility tiers

| Tier | Meaning | Execution implication |
| --- | --- | --- |
| `RESEARCH_ELIGIBLE` | Assets, venues, models, and strategies may be studied | No paper or live authority |
| `PAPER_ELIGIBLE` | A separately qualified paper strategy may be exercised | Paper-only; no live capital or production venue |
| `LIVE_ELIGIBLE` | Only BTCUSDT/ETHUSDT in crypto spot | Still requires live activation, GovernanceDecision, RiskKernel, OMS, venue, capital, credentials, and qualification gates |

Recognizing a ticker never makes it live-eligible. Research and paper scope are
separate from capital authority.

## Initial live scope

The only live-eligible asset class is `CRYPTO_SPOT`:

- `BTCUSDT` spot, long or flat;
- `ETHUSDT` spot, long or flat.

Short selling is disabled. Futures, perpetual futures, options, CFDs, margin
borrowing, leveraged tokens, and synthetic leveraged exposure are each
explicitly disabled. Venue availability does not imply authorization; the
current empty venue list intentionally requires an explicit reviewed
`venue_approved=true` input.

The shipped policy has `live_activation_permitted=false`. Even a simulated
future activation requires the existing GovernanceDecision to allow the action,
valid qualification, explicit capital scope, minimum credential capabilities,
healthy OMS state, and RiskKernel approval.

## Authority matrix

| Authority class | Examples | LLM/agent behavior |
| --- | --- | --- |
| `SYSTEM_FIXED` | withdrawals, transfers, security administration, short/derivative trade paths | No proposal can turn the action into runtime authority; evaluator hard-blocks it |
| `HUMAN_ONLY` | capital allocation/stage and loss/drawdown threshold changes | May propose; only a valid human authorization may approve |
| `HUMAN_AND_TECHNICAL_GATE` | new live instrument, venue/broker, leverage, model/strategy promotion, risk-limit relaxation, hard-kill recovery | Requires both exact technical qualification and a valid human authorization |
| `AUTONOMOUS_WITHIN_LIMITS` | qualified BTC/ETH spot risk-increasing action | Only after upstream GovernanceDecision and deterministic RiskKernel/OMS gates pass |
| `AUTONOMOUS_RISK_REDUCTION` | reduce an existing authorized long, cancel unsafe orders, emergency protection, tighten limits, reduce leverage | Can act without alpha confidence, but cannot create negative or unsupported exposure |
| `PAPER_ONLY` | paper strategy execution | Remains paper evidence and cannot imply live authority |
| `RESEARCH_ONLY` | research, backtest, proposing an instrument | May cover assets outside live scope without granting trading authority |

The explicit machine-readable rows cover opening/increasing BTC and ETH spot
positions, reductions/closures, unsupported instruments, derivatives/margin,
leverage changes, order cancellation, venues, endpoints, asset addition,
model/strategy lifecycle, thresholds, position limits, capital allocation,
transfers, withdrawals, research, paper strategies, emergency stop, and hard
kill recovery.

## Asymmetric risk authority

Risk reduction is not a loophole. A reduction must remain in the authorized
spot scope, refer to an existing long where applicable, preserve non-negative
exposure, and pass deterministic RiskKernel/OMS state checks. A request that
would create a short, use a derivative, use an unsupported venue, or transfer
capital is blocked even during an emergency.

Position-limit tightening is an autonomous risk-reduction direction. The hard
single-asset ceiling remains 15%; exceeding it is rejected. Loosening a limit
requires a human authorization and the relevant technical gate. Capital,
leverage, model, strategy, venue, asset, and recovery changes never advance
automatically.

## Lifecycle and promotion

Models use `RESEARCH -> CHALLENGER -> SHADOW -> PAPER -> ADMITTED -> RETIRED`.
Strategies use `PROPOSED -> SCREENED -> VALIDATED -> SHADOW -> PAPER ->
PROMOTED -> RETIRED`. Promotion requires the formal evidence/qualification gate
and the human gate. Performance alone cannot self-promote a model or strategy.

## Credential capability contract

The minimum future live execution capability is:

- market-data read;
- account read;
- order read;
- order create;
- order cancel.

The scope contract forbids withdrawal crypto/fiat, external or internal
transfers, withdrawal-address changes/whitelisting, API-key administration, and
account-security administration. If a declared live credential set includes a
forbidden capability or omits a required one, live scope fails closed. This
contract contains no secret material and does not inspect real credentials.

## Human authorization and notifications

Human-gated actions require an immutable `HUMAN` authorization bound to the
Human Governance policy version and an independent technical gate. Agent/LLM
authorizations, expired authorizations, and silence are not approval. System-
forbidden actions cannot be enabled by an authorization artifact.

`planned_advisorai_capital` remains an explicit operator input; the system must
never infer it from account balance. Stage 0 remains 5% of that separately
specified amount. The recommended notification design is mobile push for
urgent incidents and approval requests, with dashboard detail. Telegram may be
used in development for approval messages only; it must never carry broker
credentials or direct order authority.

## Safety boundary

This PR does not touch Phase-4 evidence, PID 80779, model inference, broker
calls, credentials, orders, RiskKernel authority, OMS authority, or Phase-10
activation. Live capital remains unauthorized.
