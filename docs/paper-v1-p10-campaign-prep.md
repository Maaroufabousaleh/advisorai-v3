# PAPER-V1 P10 — qualification campaign prep (BLOCKED on P08/P09)

Status: **preparation only — do not run.** Entry requires the reviewed
integrated P08 revision (merged base + approved strategy + qualified inputs)
with P09 fault evidence accepted. Running sessions on any other revision
produces rehearsal notes, never qualification evidence.

## Entry criteria (all must hold)

- P08 integrated revision pinned (commit + config hash) and reviewed; the
  approved strategy, venue profile, and input snapshot recorded in the tracker.
- P09 fault demonstrations accepted on that exact revision (stale data,
  interruption, ambiguous ack, mismatch, restart, emergency stop).
- Validators green on the revision: P02 canary + P03 fill validators where
  the session writes to Testnet; offline fault suite (`tests/session/
  test_integrated_fault_recovery.py`) green.
- Owner session window scheduled: supervised 5–8h sessions per the Phase 7
  plan; single operator for all venue writes (tracker rule 4).

## Session matrix (provisional targets, not pass criteria)

Phase 7 suggests 20–30 meaningful sessions / 100–150h as planning targets.
Every session must vary at least one dimension; repeating an identical clean
session adds no evidence:

- Market: trend / range / high-volatility (record regime + venue).
- Source health: clean / stale-source abstention / gap + backfill + PIT rebuild.
- Faults (each at least once, on the integrated revision): network/source
  interruption + recovery; open/ambiguous order + crash + reconcile (no
  duplicate); unclean shutdown + ledger rebuild + account reconstruction;
  interrupted research/Hermes job (checkpoint truth, never falsely complete);
  model worker death → missing evidence + allowed fallback/abstention;
  emergency stop → kill switch + clean end.
- Decisions/trades: abstain, reject, route + fill, route + cancel — each
  exercised; unapproved research outputs must never reach the OMS (test
  explicitly per P08).

## Per-session evidence checklist

Record for every session (paths stay private when they hold restricted data):

- Revision (commit + config hash), venue profile, session + run ids, wall
  clock start/end, operator.
- Decision cycles: counts by stage (routed/abstained/rejected/failed),
  order/fill/cancel ids, reconciliation verdict per cycle.
- Faults injected or observed: trigger, detection, alert text, recovery
  action, post-recovery reconciliation.
- End state: stop reason, outcome (clean/unclean/emergency), open orders
  (must be none or explicitly reconciled), leases released, resource
  bounds (RSS/VRAM/fds/locks) where the matrix requires it.
- Evidence locations + SHA-256 of manifests/ledgers; validator outputs
  verbatim (exit codes included).

## Abort rules (session invalid, kill switch stays engaged)

Stale/gapped/future data, malformed or late model output, repeated transport
failures, ambiguous acknowledgements, reconciliation mismatch, unexpected
host/endpoint, resource exhaustion, any credential exposure. Resume requires
reconciliation, an incident record, a reproducible replay, and operator review
(per `docs/runbooks/real-api-paper-operations.md`).

## What never counts

- Elapsed time, session count, or clean cycles alone (no compression of the
  observation window).
- Fixture/simulator-only runs where the plan requires venue-integrated runs.
- Sessions on an unreviewed revision, or with unresolved reconciliation /
  data-integrity incidents (zero unresolved incidents required).
- Forward-window tuning or re-labeling viewed data as unseen (P06 rules
  stay in force throughout the campaign).

## Tracking

Log each session in the work log (`docs/paper-v1-work-log.md`) under a P10
entry: matrix cell covered, evidence hashes, validator outcomes, incidents,
and remaining cells. Update the tracker P10 row only when the row's full
acceptance criterion is met.

## Resume trigger

Unblock when: P04 merged → P05/P06/P07 close on the merged base → P08
integrated revision reviewed → P09 fault evidence accepted on it. Then pin
the campaign revision here and schedule session 1.
