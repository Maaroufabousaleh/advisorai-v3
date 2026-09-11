# Human Governance & Risk Policy V1

This document describes typed policy infrastructure. It is not a live-trading
authorization, a performance claim, or a replacement for the existing
RiskKernel, OMS, phase gates, or human Phase-10 review.

## Authority boundary

The AdvisorAI authority chain is:

```text
AI estimates opportunity
  -> Portfolio Constructor proposes exposure
  -> deterministic RiskKernel determines permission
  -> authoritative OMS determines order state
  -> Human Governance controls policy and promotion changes
```

The governance package only evaluates typed inputs and emits an immutable
decision artifact. It does not submit, cancel, amend, or route orders. An LLM,
agent, Hermes, Alpha Team component, browser, or dashboard cannot change its
own limits, create a human authorization, or override RiskKernel/OMS.

## Default activation state

`live_capital_authorized` is `false` in the reviewed V1 configuration. Planned
AdvisorAI capital is a separately supplied value; it is never inferred from an
account balance. Missing planned capital, a missing or malformed authorization,
an agent/LLM actor, an expired authorization, or a missing Phase-10 gate keeps
activation blocked.

The staged allocation ladder is 5%, 10%, 20%, and 35% of the separately
approved planned allocation. There is no automatic progression. Every stage
increase is a human-only authorization event with a bounded scope and expiry.

Phase 10 remains `HUMAN APPROVAL REQUIRED / NOT APPROVED`. Adding this package
does not enable a broker, load a credential, transfer capital, or change the
runtime execution path.

## Risk states

The policy uses total AdvisorAI-managed equity, including realized and
unrealized P&L, for daily loss and high-water-mark drawdown inputs.

| Trigger | State | Governance effect |
| --- | --- | --- |
| daily loss <= -0.50% | `DAILY_DERISK` | risk-increasing multiplier <= 0.50; marginal entries are restricted |
| daily loss <= -1.00% | `DAILY_HALT` | new risk, leverage increases, and position expansion are blocked |
| drawdown <= -4.00% | `DRAWDOWN_DERISK` | risk-increasing multiplier <= 0.50; marginal entries are restricted |
| drawdown <= -6.00% | `HARD_DRAWDOWN_KILL` | new risk, leverage, expansion, and promotion are blocked |

Risk-reducing and emergency-protective decisions remain available to the
deterministic RiskKernel/OMS boundary. The policy does not panic-liquidate a
portfolio solely because a percentage threshold was crossed.

## Position and leverage boundary

The policy applies a quarter-Kelly boundary to an already reviewed
`raw_kelly_fraction`, then applies volatility, liquidity, correlated-exposure,
single-asset, and RiskKernel caps in that order. It does not estimate Kelly.
Any missing input fails closed. The single-asset hard ceiling is 15%.

BTCUSDT and ETHUSDT are represented as one `crypto_directional` correlated-risk
group. V1 deliberately does not invent an aggregate production threshold for
that group; a valid independent group-exposure assessment is required before
risk can increase. A future numerical cap must be a separately reviewed policy
change.

Normal gross leverage is 1.00x. The absolute V1 ceiling is 1.25x, but future
leverage is disabled by default. A model may propose a leverage change; only a
human authorization can enable a separately reviewed policy state.

## Asymmetric autonomy

For risk-increasing actions, calibrated quantitative confidence of at least
0.90 is necessary but not sufficient. The confidence must come from a typed
calibration contract; LLM prose, agent self-confidence, and a natural-language
percentage are not accepted. A qualifying action also needs urgent or
independently measured timing evidence, an expected net edge at least 2x
conservative all-in cost, fresh PIT evidence, healthy sources, an admitted
model role, valid regime/liquidity/spread/portfolio/correlation state, healthy
reconciliation, unambiguous OMS state, and an approving RiskKernel result.

Medium confidence (0.70–<0.90) is human-review territory when time permits and
does not become permission to add uncertain risk when urgent. Low confidence
(<0.70) abstains from risk increase. Deterministic risk reduction does not
require alpha confidence, but still remains subject to the authoritative
RiskKernel/OMS safety boundary.

## Human-only changes

Human authorization is required for enabling or increasing live capital,
allocation stages, loss/drawdown thresholds, single-asset caps, leverage,
asset classes, venues, short/options/futures/margin, model or strategy
promotion, portfolio objectives, RiskKernel rules, OMS authority, and
kill-switch behavior. Authorization records are frozen, hashed, scoped to an
exact policy/repository identity, and may expire. Silence is never approval.

A normal approval UI cannot bypass malformed OMS state, missing reconciliation,
broken provenance, ambiguous execution, missing secrets boundaries, unsupported
venues, or corrupted RiskKernel state. Those are engineering/recovery matters,
not a dashboard permission.

## Auditability and limitations

Every decision binds the policy hash, input snapshot hash, timestamp, reason
codes, risk state, and decision hash. Policy changes require an explicit new
version. No secret is stored in governance artifacts.

This is a conservative growth-oriented initial live-risk policy subject to
empirical validation and explicit human revision. It makes no claim of
profitability, safety, Sharpe, or future return. Professional legal,
regulatory, and operational review remains required before any live-capital
activity.
