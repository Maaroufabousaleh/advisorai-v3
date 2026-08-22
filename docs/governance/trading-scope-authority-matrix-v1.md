# Trading Scope & Authority Matrix V1

This is a scope contract layered on the Human Governance & Risk Policy V1. It
does not activate Phase 10, load credentials, create an order capability, or
change RiskKernel/OMS behavior.

## Matrix

| Scope | V1 rule | Result |
| --- | --- | --- |
| `LIVE_ELIGIBLE` | BTCUSDT and ETHUSDT spot, long or flat only | May be considered for autonomous scope approval only when live activation, qualification, governance, RiskKernel, OMS, and venue gates all pass |
| `DISABLED` | Shorts, margin, futures, perpetuals, options, leveraged tokens | Scope evaluator refuses the action |
| `SYSTEM_FORBIDDEN` | Withdrawals, external transfers, API-key/security administration | Refused regardless of model, human, venue, or urgency |
| `HUMAN_TECHNICAL_GATE` | New asset, broker/venue, leverage, model/strategy promotion, risk-limit relaxation, resume after hard kill | Requires both a valid human authorization and a separate technical gate |
| `AUTONOMOUS_PROTECTIVE` | Deterministic risk reduction and emergency protection | May be permitted without alpha confidence, but still requires deterministic trigger, RiskKernel, and unambiguous OMS state |
| `RESEARCH_ONLY` | Exploration of assets outside live scope | No trading authority or live-scope implication |

The evaluator's `execution_authority` field is permanently `false`. An
`ALLOW_AUTONOMOUS` scope result is a typed governance result, not an order,
venue permission, credential, or live-capital activation.

## Live-eligible conditions

For an autonomous spot action, all of the following are required:

- instrument is BTCUSDT or ETHUSDT;
- market type is spot;
- direction is long or flat;
- live activation has independently been permitted;
- model/runtime qualification is valid;
- the upstream governance decision is `ALLOW_AUTONOMOUS`;
- venue approval is explicit;
- RiskKernel approves;
- OMS state is unambiguous.

The scope policy does not infer a venue from a symbol. An empty configured venue
list is intentional; the technical venue gate must identify the exact approved
venue for a future run.

## Human and technical gates

Agents and models may propose a new asset, broker, venue, leverage change,
promotion, risk-limit relaxation, or recovery action. They may not approve it.
The authorization artifact must be a valid `HUMAN` authorization bound to the
Human Governance policy version, and the independent technical gate must also
be true. A normal UI cannot bypass malformed OMS state, broken provenance,
missing reconciliation, unsupported venues, or a RiskKernel refusal.

## Research separation

Research may inspect assets and market structures outside this matrix. Such
records must remain explicitly `RESEARCH_ONLY`; they cannot be converted into
live eligibility by a model, notebook, dashboard, or agent. Promotion into
live scope is a separate human/technical-gated action.

## Operator decisions still required

The policy intentionally does not invent an account amount. The operator must
choose `planned_advisorai_capital`; Stage 0 remains 5% of that separately
specified amount. The recommended notification design is mobile push for urgent
incidents and approval requests, with the dashboard for detail. Telegram is a
possible development channel, but it must carry approval messages only—not
broker credentials or direct order authority. No channel choice activates live
capital.
