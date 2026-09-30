# Session-oriented runtime and recovery plan

**Status:** target architecture and validation plan; not evidence that the
lifecycle is implemented or admitted.
**Normal operating profile:** one owner starts AdvisorAI for a working session,
typically about 5–8 hours, then stops it cleanly. Optional always-on/server
deployment is a separate profile and is not required for the workstation.

## Product contract

AdvisorAI is a local-first, session-oriented quantitative and agentic
workstation. It must be safe and useful after a daily restart, multiple unused
days, network loss, process failure, a dead model worker, interrupted research,
or an unclean machine shutdown. Shutdown is a normal operation, not an incident.
No agent, model, collector, or scheduler must remain active while the owner has
closed AdvisorAI.

This document defines implementation readiness and session validation. Passing
it does not admit a model, strategy, venue, or capability for a broader
operating scope. Operational promotion remains controlled by the phase and
capability gates.

## Start lifecycle

```text
START_REQUESTED
  -> acquire single-owner application/session lock
  -> verify config, code, schema, and environment identity
  -> verify ledger chains, artifact manifests, and local state roots
  -> inspect the last SessionEndRecord and detect clean stop vs. crash
  -> recover authoritative account, position, P&L, order, and mission state
  -> reconcile paper/testnet venue account, positions, and open orders
  -> inspect source/collector watermarks and calculate exact offline gaps
  -> backfill only permitted sources; preserve original availability/PIT times
  -> rebuild and validate the current point-in-time snapshot and source health
  -> initialize only workers needed for this session and operating mode
  -> restore or resume eligible checkpointed jobs under current policy
  -> verify resource headroom, GPU lease availability, and lock ownership
  -> SESSION_READY
```

Any integrity mismatch, ambiguous venue state, missing authority record, stale
required source, failed reconciliation, or unsafe resource state enters a
typed recovery/abstain state. Restart must never turn an uncertain state into
permission to act.

## Clean stop lifecycle

```text
STOP_REQUESTED
  -> stop accepting new missions and new trading proposals
  -> stop scheduling new research and optional model work
  -> finish, cancel, or checkpoint bounded jobs by their declared policy
  -> freeze new target proposals
  -> reconcile outstanding paper/testnet orders and account state
  -> persist authoritative positions, cash, fees, P&L, and order state
  -> flush outbox, ledgers, artifact manifests, and collector watermarks
  -> checkpoint experiment and Research Brain job state
  -> unload model workers and release GPU/runtime/resource leases
  -> release worker and application locks
  -> append immutable SessionEndRecord
  -> STOPPED_CLEANLY
```

The stop path records unfinished work as interrupted or checkpointed. It must
not claim a mission, experiment, order, or reconciliation completed merely
because the process ended.

## Emergency shutdown and crash recovery

Emergency stop prioritizes deterministic safety and state integrity: stop new
proposals, assert the kill switch when required, preserve write-ahead and raw
spools, record the last durable offsets, release a GPU lease only after its
worker is confirmed stopped, and retain unresolved jobs/orders for recovery.
On restart, validate/rebuild ledgers, query the paper/testnet venue, reconcile
before any new order, detect data gaps, and enter recovery mode until all
required invariants pass. Ambiguous acknowledgements are reconciled, never
blindly retried.

## Planned lifecycle records

Use existing typed contracts where possible; the following names describe
logical records, not claims that runtime types already exist:

| Record | Minimum content |
|---|---|
| `SessionStartRecord` | session ID, owner/process lock identity, code/config/schema/environment hashes, prior end-record ID, start reason, recovery classification |
| `SessionCheckpoint` | session/job IDs, authoritative ledger offsets, artifact IDs, watermarks, pending work, checkpoint hash, resumability policy |
| `SessionEndRecord` | session ID, clean/emergency/unclean outcome, end offsets, reconciliation state, open orders, unfinished jobs, resource/lease cleanup, hash links |
| `RecoveryRecord` | prior session, detected fault/gap, rebuild/reconciliation actions, decisions and outcomes, current recovery mode |
| `DataGapRecord` | source/instrument, exact interval, last valid watermark, permitted backfill/source policy, availability and PIT lineage |
| `RuntimeLeaseRecord` | worker/model identity, lease owner, resource envelope, acquire/release timestamps and cleanup outcome |

Records are immutable and linked by stable IDs. A projection may summarize
them; the projection is not authority.

## Session validation policy

Phase 7 validates the complete paper/testnet system across independent usage
sessions and market/source states. It prioritizes:

1. repeated start → operate → stop → restart with equivalent authoritative
   account/order/ledger state;
2. crash recovery and no duplicate order after restart;
3. deterministic venue reconciliation, including ambiguous acknowledgements;
4. exact offline-gap detection, permitted backfill, and PIT correctness;
5. interrupted Hermes/research-job checkpoint and resume semantics;
6. model/GPU worker death leading to missing evidence and safe abstention;
7. ledger rebuild, paper-account reconstruction, and lock/lease cleanup;
8. repeated model load/inference/unload with bounded residual RAM, VRAM, file
   descriptors, processes, and locks;
9. correct state and authority after network loss and multiple unused days;
10. traceability from data through Decision Model proposal, RiskKernel, OMS,
    venue reconciliation, attribution, outcome memory, and next-session state.

Calendar uptime is not the primary workstation criterion. An initial planning
target for review may be 20–30 meaningful sessions and roughly 100–150
accumulated operating hours across varied market/source conditions and a
meaningful decision/trade sample. These are provisional evidence targets, not
magic admission thresholds; reviewers may revise them from observed sample
quality. Readiness requires zero unresolved safety, reconciliation, or data
integrity incidents in the reviewed scope. Evidence must include volatility or
regime variation and relevant source, network, process, and resource failures.

24-hour model/runtime stability and multi-week continuous paper runs remain
historical evidence or optional diagnostics. A separately approved server or
always-on deployment profile may define its own endurance gate without making
that gate a prerequisite for the workstation.

## End-to-end failure scenarios

| Scenario | Required behavior |
|---|---|
| Normal restart | Recover state; authoritative state matches the prior clean stop; no duplicate actions |
| Source unavailable | Mark data stale, abstain when required, detect exact gap; resume after permitted backfill and PIT rebuild |
| Open paper order plus crash | Query/reconcile venue and local ledger before any retry; never duplicate an ambiguous order |
| Agent/job interruption | Preserve interrupted/checkpointed status and provenance; never mark complete on process exit |
| GPU/model worker death | Record missing evidence; use an independently permitted fallback or abstain; no authority escalation |
| Unclean machine shutdown | Validate or rebuild ledgers, reconcile account/orders, and enter recovery mode |
| Offline for hours/days | Record exact gap, backfill only reviewed sources, preserve first-available times, rebuild current PIT snapshot |
| Resource/lease leak | Repeated sessions stay within reviewed residual RAM/VRAM/process/file-descriptor/lock bounds |

## Optional continuous deployment profile

A future server profile may add supervision, remote observability, 24-hour
endurance, maintenance windows, and unattended incident response. It needs a
separate ADR, threat/resource review, and admission evidence. It must preserve
the same data, risk, OMS, reconciliation, credential, and capability
boundaries, and remains optional to the owner-operated workstation.
