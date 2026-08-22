# Human Notification & Approval Boundary V1

This is transport-neutral policy infrastructure. It does not configure a
provider, load credentials, activate Phase 10, submit orders, or change the
RiskKernel/OMS authority boundary.

## Authority boundary

```text
governance/scope decision
  -> NotificationRequest or ApprovalRequest
  -> future authenticated human transport
  -> HumanResponse validation
  -> HumanAuthorization bridge
  -> governance/scope re-evaluation
  -> RiskKernel
  -> OMS
```

A notification transport can deliver information, deliver an approval request,
and return a response. It cannot submit or cancel an order, change policy or
risk limits, forge `HumanAuthorization`, access broker secrets, or mutate
RiskKernel/OMS state. A transport interface has no order-facing methods.

The notification boundary is not an execution path. For an already-authorized
urgent autonomous action, governance, scope, RiskKernel, and OMS remain the
critical path; the action is recorded/notified afterward. Deterministic
protective risk reduction likewise occurs before its emergency notification.

## Events and routing

The typed classes are `INFO`, `OPPORTUNITY`, `APPROVAL_REQUIRED`,
`RISK_WARNING`, `CRITICAL_INCIDENT`, `EMERGENCY_ACTION_TAKEN`, and
`SYSTEM_HEALTH`. Priorities are `LOW`, `NORMAL`, `HIGH`, and `CRITICAL`.

The reviewed default routes are:

- critical incidents and emergency actions: mobile-push-capable route plus
  dashboard;
- approval requests and meaningful risk warnings: an interactive-capable route
  plus dashboard;
- opportunities, system health, and low-information events: dashboard/log;
- low-confidence abstention: log only unless portfolio risk makes it important.

The route names are capability declarations, not provider integrations. No
Telegram, push, email, webhook, or broker credential is included in V1.

## Approval and expiry

`ApprovalRequest` and `HumanResponse` are immutable, hashed records. A bridge
may create an existing `HumanAuthorization` only when the request exists, its
hash and policy identity match, it has not expired or already reached a
terminal response, the action/value match, the actor is `HUMAN`, and a future
transport has explicitly established authentication. Missing authenticity,
stale policy identity, an agent/LLM response, or an expired request fails
closed. Silence is never approval.

The in-memory `ApprovalLedger` is append-only and records approval, rejection,
acknowledgement, deferral, and expiry observations. Decision windows are
explicit durations; V1 does not invent provider-specific production TTLs.

## Deduplication and security

Repeated unresolved events use a stable key derived from notification class,
subject, reason, governed action, and policy identity. Equal/lower-severity
repeats are suppressed; a severity increase is delivered. Notification and
approval text rejects credential/signing markers, including API keys, API
secrets, broker credentials, authentication tokens, withdrawal addresses, and
private signing material. Evidence references are immutable and unique.

`NullTransport` and `InMemoryTransport` are development-only safe transports.
They do not make live capital authorized. The current default remains
`live_capital_authorized=false`, and any eventual live action still requires
the existing governance, scope, RiskKernel, and OMS gates.

## Future provider requirements

A provider may only be used for approval if its declared capabilities include
authenticated approval receipt, interactive actions, and encryption. This
module does not define the cryptographic identity protocol; a future provider
adapter must establish that contract before setting the response's
`authentication_established` field. Provider selection and credential handling
require a separate reviewed change.
