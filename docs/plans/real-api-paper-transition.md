# Real-API and paper-execution transition plan

## Status and authority

This is an operational sub-plan for moving from deterministic fixtures to real
external read APIs, real API-LLM calls, and one real paper/testnet venue. It is
subordinate to the authoritative
[`AdvisorAI V3 architecture`](../../advisorai-federated-multi-agent-quant-architecture-v3.md)
and the current phase plans. It creates no new admission path, does not relax
authority gates, and does not enable live capital. The 2026-09-30 planning
rebaseline makes the owner-operated session workstation authoritative. Old
24-hour/60-day requirements in prior evidence remain historical; they are not
the primary workstation gate. See
[`session-oriented-runtime.md`](session-oriented-runtime.md).

```text
real public/licensed data + real LLM reasoning + local deterministic services
                                      ↓
                  one approved paper/testnet execution venue only
                                      ↓
       local reconciliation, attribution, incidents, replay, and scorecards

The target decision chain inserts a typed Decision Model proposal before the
deterministic Portfolio Constructor. The displayed high-level bridge above
does not authorize an LLM or model to propose orders directly.
```

LLMs, Hermes, browser tasks, collectors, capabilities, and dashboards never
receive order-submission or risk-limit authority. Live capital remains prohibited
until the Phase 10 human-approval gate.

## Objective and fixed V3-Core scope

Operate the compact V3-Core loop across owner-started sessions with actual
external inputs while execution stays paper/testnet-only. Recover and reconcile
state at startup, detect permitted data gaps, preserve point-in-time truth,
typed evidence, deterministic RiskKernel/OMS authority, local state,
reproducible replay, and controlled learning from paper-trading problems.

| Area | Fixed transition scope |
|---|---|
| Assets | BTC and ETH only |
| Venue | One approved paper/testnet venue |
| Cadence | 5-minute observations, 1-hour decisions, 4-hour context |
| Market truth | Native venue REST/WebSocket |
| Context | Deribit public data, official RSS, GDELT |
| LLM | One selected direct route and explicit recovery route; LiteLLM/OmniRoute only after Phase-0 evidence |
| Storage | Local Parquet, DuckDB, SQLite WAL |
| Forecast baseline | Naive/statistical/LightGBM before any promoted challenger |
| Execution path | Target → RiskKernel → OMS → one testnet adapter |

Do not add equities, a second venue, browser collection, multiple optimizers,
Hermes execution, or live capital during this transition.

## Required operator decisions

Record these decisions before enabling a connector:

1. The one named paper/testnet venue and its reviewed REST/WebSocket endpoints.
2. One LLM provider/model route and its direct recovery route.
3. Whether LiteLLM is selected or remains a quarantined candidate.
4. Dedicated read-only/testnet-only API credentials, with provider-side IP,
   permission, quota, and withdrawal restrictions where available.
5. Local data/state paths and tested rclone-crypt archive remotes with
   independent two-provider restore evidence.

Every connector must have an owner, purpose, endpoint identity, data grade,
quota/cost, version, rollback procedure, contract test, integration smoke test,
and recorded configuration hash. A credential alone never enables a connector.

## Credential and network policy

`secrets.env` is a local Git-ignored operator input. The runtime must add a typed
secret loader that loads only adapter-specific allowlisted names and never writes
values to artifacts, traces, logs, test fixtures, prompts, browser tasks, Hermes
tasks, or capability bundles.

- Reject blank, malformed, and live-environment credentials for V3-Core.
- Redact headers and values from every error and trace.
- Pass a credential only to its owning transport.
- Reject broker/venue/order/risk-limit/live-deployment credentials for browser,
  Hermes, generated-code, model-gateway, and capability requests.
- Rotate secrets only through a versioned configuration activation; never change
  a decision that is already in flight.

Use a Linux filesystem for populated secrets where possible. Production exchange
keys, production hostnames, transfer endpoints, and withdrawal endpoints are
explicitly out of scope.

## Workstream A — integration configuration and safety

1. Add typed adapter settings: endpoint URL, allowed hosts, environment, timeout,
   retry budget, rate limit, circuit-breaker threshold, credential reference,
   provider/venue identity, and adapter version.
2. Implement the secret loader and redaction utility.
3. Use lifecycles: `disabled → configured → smoke-tested → shadow → active-read`
   for data/LLM connectors; `disabled → smoke-tested → paper-only` for the venue.
4. Mark network tests as explicit opt-in. Offline tests keep injected/recorded
   transports and must remain network-free.
5. Persist configuration-bundle hash, credential reference name (not value),
   endpoint, adapter version, and activation/revocation reason.

**Acceptance:** missing secrets, invalid endpoints, TLS failures, timeouts, rate
limits, schema violations, and circuit opens fail closed without leaking a secret
or changing trading state.

## Workstream B — real V3-Core data ingestion

Implement in this order:

1. Native venue REST bootstrap plus WebSocket collection for BTC/ETH trades,
   books, bars, funding, and open interest.
2. Append raw messages to the local spool before parsing or acknowledgement.
3. Normalize to Bronze/Silver records with event/effective/publication,
   first-available and ingestion times; revision, origin, parser, and raw hash.
4. Add Deribit public derivatives context, then official RSS and GDELT, retaining
   source origin/syndication metadata and untrusted-content sanitization.
5. Detect duplicates, sequence gaps, stale data, clock drift, missing fields,
   schema changes, and cross-source disagreement.
6. Build immutable snapshots only at explicit closed one-hour cutoffs.

**Acceptance:** raw spool replay reproduces records and snapshots; stale, gapped,
future, unavailable, or malformed source data blocks a decision rather than being
silently substituted.

## Workstream C — one real LLM gateway, reasoning only

1. Implement one concrete direct-provider transport behind `ModelGatewayPort`.
2. Record provider/model/gateway identity, route/fallback path, prompt/tool
   version, request hash, latency, tokens, cost, and failure class.
3. Use typed JSON outputs only. Reject all order/trade/limit/credential/deployment
   tool calls at the gateway boundary.
4. Send artifact IDs and minimized/redacted data; never copied ledgers or secrets.
5. Apply Standard/Deep API budgets and Resource Governor admission.
6. On failure, return deterministic recovery/abstention behavior. Recovery is not
   independent evidence.
7. Use exact gateway identity, typed output, privacy, resource, failure, and
   authority evidence for the selected integration scope. Long route endurance
   may be retained as optional diagnostic or server-profile evidence, not a
   workstation infrastructure prerequisite.

**Acceptance:** each output is attributable to a pinned route; malformed or late
output, forbidden tool calls, or budget/circuit failure causes abstention and
cannot affect RiskKernel or OMS.

## Workstream D — one real paper/testnet venue transport

1. Implement a venue-specific transport behind `NativeVenueAdapter`; accept only
   `paper`, `testnet`, and `paper_testnet` environments.
2. Restrict hosts to reviewed testnet/paper endpoints; reject production hosts,
   production accounts, withdrawals, and transfers.
3. Persist order intent before submission. Bind client order IDs to the existing
   idempotency key and reconcile an ambiguous acknowledgement before retrying.
4. Ingest testnet order, fill, position, cash, fee, funding, and open-order state
   through the authoritative account ledger.
5. Reconcile independently on schedule and after reconnect, timeout, or ambiguous
   acknowledgement.
6. Preserve stale-data rejection, price collars, hard limits, rate limits,
   reduce-only handling, and the independent kill switch.

**Acceptance:** duplicate, partial-fill, reject, timeout, reconnect, outage,
stale-data, hash mismatch, and reconciliation mismatch fixtures fail safely and
produce replayable ledger/incident evidence.

## Workstream E — session-operated decision loop

1. At session start, acquire the owner lock; validate code/config/ledger
   identities; recover and reconcile paper/testnet state; find exact collector
   watermarks and permitted data gaps; backfill and rebuild current PIT state;
   then start only required collectors/workers.
2. Schedule a decision only after closed 5-minute data is persisted and a valid
   one-hour snapshot exists.
3. Enforce the chain: EvidenceGraph → `DecisionModelPort`/typed proposal →
   deterministic Portfolio Constructor → RiskKernel → OMS → Nautilus
   paper/testnet transport.
4. At stop, stop new proposals/jobs, finish or checkpoint bounded work,
   reconcile orders/account state, persist ledgers/watermarks, unload workers,
   release locks/leases, and write an immutable session-end record.
5. Build a read-only local operator interface only after service ownership is
   stable. It may display health, freshness, resources, snapshots, gateway route
   and cost, evidence/dissent, targets, risk, orders/fills, reconciliation, TCA,
   attribution, and incidents.
6. The operator interface has no direct order button, limit editor, secret viewer,
   arbitrary SQL, or raw capability execution.

**Acceptance:** UI/API failure cannot stop raw persistence, risk, OMS,
reconciliation, or the kill switch; visible state is derived from local ledgers
and immutable artifacts.

## Workstream F — paper-learning and problem remediation

Persist the complete chain for each decision:

```text
snapshot → quality report → evidence/forecasts → Decision Model proposal
→ Portfolio Constructor → risk → execution
plan/order/fill → reconciliation → TCA/attribution → realized outcome → scorecard
```

1. Classify problems as data availability/quality, lineage, calibration, evidence
   independence, target construction, risk rejection, execution cost, venue/API,
   reconciliation, resources, or operator/process failure.
2. Create an incident for hard-control failure, unexplained reconciliation/
   attribution residual, invalidated decision, or material operational failure.
3. Preserve the original cutoff/artifacts, reproduce each incident offline, find
   root cause, add a regression test, and replay before re-admission.
4. Update source/model/agent/route scorecards only after the forecast horizon has
   closed. Track calibration, abstention, net utility after realistic costs,
   latency, API cost, data reliability, fill quality, and incident rate by regime.
5. Treat every logic/model/prompt/source/strategy change as a challenger. It must
   pass frozen-data replay and shadow evidence; it cannot self-promote or loosen
   a risk limit.

**Acceptance:** every material paper problem produces evidence, a reproducible
offline replay, an incident or scorecard record, and a corrective regression
test—never autonomous production modification.

## Workstream G — integrated session and recovery evidence

1. Repeat clean start → operate → stop → restart; compare authoritative account,
   order, position, P&L, ledger, and mission state.
2. Exercise source/network outage, stale-data abstention, exact gap detection,
   permitted backfill, and PIT snapshot rebuild after hours/days offline.
3. Crash with an open/ambiguous paper order; reconcile with venue state before
   retry and verify there is no duplicate action.
4. Exercise unclean shutdown, ledger rebuild, interrupted-agent checkpoint,
   model/GPU-worker failure, lock/lease cleanup, and repeated lazy model
   load/inference/unload with bounded residual resources.
5. Compare outcomes with no-trade, equal-allocation, inverse-volatility, and
   simple risk-budget baselines after fees, spread, impact, delay, funding, and
   borrowing where applicable.
6. Accumulate evidence across independent sessions and varied market/source
   states. A provisional 20–30 meaningful sessions and roughly 100–150
   operating hours may inform review, subject to evidence quality; neither is a
   magic threshold or calendar-time substitute.

**Acceptance:** complete-chain traceability, state equivalence, no duplicate
orders, deterministic reconciliation, PIT-safe gap recovery, truthful job
status, bounded resources, and no unresolved safety/reconciliation/data
integrity incident. Long continuous runtime is optional diagnostic or
deployment-profile evidence.

## Test and evidence matrix

| Test tier | Network access | Required evidence |
|---|---|---|
| Unit/contract | None | parsing, PIT validation, authority denial, redaction, idempotency, hashes |
| Recorded replay | None | raw fixtures, schema drift, gaps, snapshots, decisions, fills |
| Adapter integration | Explicit opt-in | auth, rate limit, timeout, reconnect, testnet state query |
| Paper shadow | Real read APIs/testnet | full decision chain, reconciliation, TCA, scorecards |
| Session/recovery validation | Real paper environment | repeated sessions, crash/restart, gaps/backfill, reconciliation, job/worker failure, resources and state equivalence |

Integration tests must be separately invoked, redact outputs, use a dedicated
testnet account, and never run from an untrusted capability or pull request.

## Required records

- Connector card: owner, scope, endpoint, allowed host, environment, credential
  reference, source grade, limits/cost, adapter version, and rollback.
- Configuration bundle, code/lock hash, raw manifest, and quality report.
- Gateway route/prompt/tool metadata with no credential material.
- Order/fill/account/reconciliation/TCA/attribution artifacts linked by IDs.
- Incident, postmortem, replay result, regression-test reference, scorecards,
  resource samples, archive verification, and phase-gate evidence.

## Explicit non-goals and exit condition

This transition excludes live credentials/capital, production exchange hosts,
withdrawals/transfers, a second venue, equities, browser collectors, Hermes
execution, permanent agent fleets, automatic self-modification, and any cloud
service as authoritative state.

It is complete for the workstation milestone when the real-data/testnet loop
can start, recover, operate, and stop across sessions with traceable fail-closed
hand-offs and reproducible paper-problem remediation. Completion is
implementation/integration evidence only; it does not admit a model, strategy,
live capital, or any capability beyond its recorded scope.
