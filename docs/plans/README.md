# AdvisorAI V3 phase plans

These plans decompose the authoritative architecture in
[`advisorai-federated-multi-agent-quant-architecture-v3.md`](../../advisorai-federated-multi-agent-quant-architecture-v3.md).
They preserve one roadmap and the existing deterministic owners. The 2026-09-30
planning rebaseline makes implementation readiness and operational admission
separate tracks.

## Two-track phase model

**Implementation readiness** covers architecture, interfaces, services,
workflows, lifecycle, sandboxing, agents, adapters, Research Brain, and
dashboard work. Safe components may be implemented in parallel across phase
boundaries when they remain typed, quarantined, research/shadow/paper-only, do
not receive broker/order/risk-limit authority, and cannot grant themselves
admission.

**Operational admission** covers whether an exact model/source/capability/
strategy/configuration may operate in a particular scope. Its gates remain
sequential where safety requires. A pending earlier model or timed gate blocks
promotion or authority, not safe software implementation downstream.

| Phase | Sub-plan | Implementation focus | Operational admission focus |
|---|---|---|---|
| 0 | [contracts and bounded integration qualification](phase-00-contracts-and-bakeoffs.md) | Stable ports, reference adapters, PIT/identity/resource/failure contracts | Level A participation only for exact reviewed identity; broad bake-offs deferred |
| 1 | [safety, data truth, resources](phase-01-safety-data-resources.md) | Immutable data, ledgers, resource and session lifecycle | Deterministic rebuild, leakage, idempotency, recovery and rollback evidence |
| 2 | [deterministic paper core](phase-02-paper-core.md) | Paper/testnet execution and reconciliation spine | Reconcile failures safely before any paper scope is admitted |
| 3 | [V3-Core data spine](phase-03-v3-core-data-spine.md) | PIT collection, quality, watermarks and backfill | Source-specific provenance, freshness, recovery and disagreement evidence |
| 4 | [quantitative reference fabric](phase-04-quant-baselines.md) | Chronos-2-small, TTM-R2, Kronos-small, LightGBM, compact FinBERT adapter contracts | Level A integration; comparative model admission remains separate |
| 5 | [Evidence Council and Decision Model](phase-05-evidence-council.md) | EvidenceGraph, typed DecisionModelPort, DecisionProposal, portfolio handoff | Evidence sufficiency, calibration, missing data and dissent remain fail-closed |
| 6 | [institutional controls](phase-06-institutional-controls.md) | Portfolio/risk/OMS controls, attribution, outcome and experiment records | Every paper action stays under deterministic limits and reconciliation |
| 7 | [Integrated Paper Session Validation](phase-07-integrated-paper-session-validation.md) | Complete end-to-end session, stop, restart, gap and failure validation | Repeated-session system evidence; no mandatory calendar uptime gate |
| 8 | [Hermes and Skill Foundry](phase-08-hermes-skill-foundry.md) | Isolated research/build runtime; implementation may begin earlier | Per-capability sandbox/reproducibility/read-only admission |
| 9 | [controlled expansion](phase-09-controlled-expansion.md) | Research Brain, Alpha Team, experiment/outcome memory; implementation may begin earlier | One versioned challenger/source/capability scope at a time |
| 10 | [limited live capital](phase-10-limited-live-capital.md) | Separate live-control design and review | Explicit human approval and strictly separate bounded-live gate |

The [session-oriented runtime plan](session-oriented-runtime.md) defines start,
stop, crash recovery, session records, and validation scenarios. The primary
near-term milestone is **AdvisorAI Integrated Alpha**: start, recover, run the
paper/testnet chain through reconciliation and outcome memory, then stop and
resume safely. It is not a profitability or live-capital claim.

## Reference model policy

The frozen integration fabric is Chronos-2-small (probabilistic forecasting),
TTM-R2 (lightweight temporal control), Kronos-small (finance/OHLCV evidence),
LightGBM (structured baseline), FinBERT or the selected compact financial
sentiment implementation, a local typed Decision Model reference (Laya or
equivalent), and one on-demand strong reasoning LLM behind `ModelGatewayPort`.
Chronos and Kronos have complementary roles and share a single lazy global GPU
lease. Jev is optional and never required. This roster is for integration,
not permanent approval. Broad model challenger work follows complete-system
evidence.

## Historical evidence and current policy

Historical 24-hour and 60-day evidence records, interrupted roots, old
preregistrations, and hashes remain unchanged. Their old continuous-duration
requirements are **superseded planning requirements** for the owner-operated
workstation, not falsified historical facts. Long endurance tests remain
optional diagnostics or profile-specific gates for a future always-on server
deployment.

## Status and evidence references

The repository contains implementation and historical evidence at different
maturity levels. A passing test, local probe, or short model integration check
does not itself grant operational admission. See:

- [traceability](traceability.md) for the implementation/admission ownership map;
- [implementation audit](implementation-audit.md) for current and historical code/evidence summaries;
- [gate matrix](gate-matrix.md) for the current policy interpretation and preserved evidence state;
- [status dossier](status.md) for dated evidence records;
- [continuation checkpoint](continuation-checkpoint.md) for append-only handoff history.

The secure operator console is specified in
[`secure-operator-dashboard.md`](secure-operator-dashboard.md); it is a
read/projection and guarded workflow surface, not another trading authority.

Operational procedures remain in
[`docs/runbooks/real-api-paper-operations.md`](../runbooks/real-api-paper-operations.md),
[`docs/runbooks/phase0-component-bakeoff.md`](../runbooks/phase0-component-bakeoff.md),
[`docs/runbooks/model-runtime-qualification.md`](../runbooks/model-runtime-qualification.md),
and the provider-specific runbooks. Their historical measurements remain
intact; current execution requirements follow the policies above.
