# Architecture traceability

This matrix maps the authoritative architecture to implementation readiness
and operational admission separately. Phase sequencing governs promotion and
authority; safe, typed, quarantined implementation may proceed across phases
before earlier admission gates pass.

| Authority area | Binding design rule | Implementation ownership | Admission/verification evidence |
|---|---|---|---|
| Product/session model | Owner-operated 5–8 hour sessions; clean stop, recover, reconcile, catch up, resume; optional server profile | Phase 1 and session lifecycle plan; Phase 7 integrated validation | SessionStart/Checkpoint/End/Recovery records; state equivalence, crashes, data gaps, locks/leases |
| Trading authority | Typed/versioned handoffs; deterministic Portfolio Constructor, RiskKernel, OMS, Nautilus, and reconciliation retain sole authority | Phases 1, 2, 5, 6 | Contract, permission, replay, ambiguous acknowledgement, duplicate prevention, and reconciliation evidence |
| Reference model fabric | Chronos-2-small, TTM-R2, Kronos-small, LightGBM, compact FinBERT implementation, local typed Decision Model, on-demand reasoning LLM | Phase 0/4 adapters; integration can proceed without broad model bake-offs | Level A identity/PIT/typing/resource/load/failure/no-authority evidence; Level B paper evidence; Level C challengers later |
| Decision Model | EvidenceGraph → generic DecisionModelPort → typed DecisionProposal → deterministic Portfolio Constructor | Phase 5; adapter implementation is quarantined until exact qualification | Schema, evidence references, calibration/uncertainty, expiry, missing-data behavior, abstention, authority denial |
| Data and PIT truth | Immutable provenance, cutoff and availability time, gap/revision/origin, no future leakage | Phases 1/3, session lifecycle, Research Brain registries | PIT fixtures, raw replay, exact gap/backfill records, snapshot hashes |
| Evidence Council | Shared ancestry is discounted; dissent/missing evidence retained; agents are elastic jobs | Phase 5; bounded coordinator/workers in Alpha Team | Evidence-graph, quorum, cutoff, mission checkpoint, and failure fixtures |
| Research Brain / Alpha Team | Literature retrieval is separate from empirical canonical truth; all claims trace to sources; failed work retained | Phase 6/9 foundations may begin earlier in quarantine | DuckDB/Parquet/SQLite IDs and hashes, experiment replay, rejected/failed artifacts, provenance review |
| Decision/outcome training data | Immutable decision-time inputs; outcomes attached later by ID; no future leakage | Phase 6/9 record and registry implementation | DecisionRecord → OutcomeResolution → TrainingExample lineage and cutoff validation |
| Hermes/Skill Foundry | On-demand isolated jobs; no credentials, production mutation, orders, or self-activation | Phase 8 implementation may start before Phase 7 | Sandbox, capability, reproducibility, security, and active-read scope evidence |
| Resource model | Target i7/16 GB/WSL2 ~11 GB/RTX 4060 8 GB; lazy models, sequential waves, one GPU lease, no resident swarm | Resource/session services and work schedulers | Repeated session/model lifecycle residual RSS/VRAM/process/fd/lock and failover evidence |
| Model evidence policy | Level A integration, Level B integrated paper evaluation, Level C deferred challengers | Phase 0/4 and Phase 7 | Exact, versioned reports; challenger work cannot block core infrastructure |
| Integrated paper milestone | Complete data→evidence→proposal→portfolio→RiskKernel→OMS/Nautilus→reconciliation→TCA→outcomes→next session | Phases 2–7, dashboard traceability | Repeated normal/failure sessions; paper/testnet scope only; no profitability assertion |
| Limited live capital | Separate, explicit, human-approved scope; no AI/research authority | Phase 10 | Separate approval, risk caps, parity/recovery, and rollback evidence |

## Global invariants

1. No model, LLM, Decision Model, Hermes job, browser task, research agent,
   source adapter, or imported capability can submit orders, cancel an
   ambiguous order blindly, or relax risk limits.
2. `RiskPolicy` is versioned and immutable for a decision. RiskKernel may
   approve, reduce, or reject against authoritative market/account/order
   state. Reconciliation is authoritative.
3. Large data moves by immutable artifact ID, not copied chat transcript.
4. All decisions and research use explicit point-in-time cutoffs and
   first-available times. Future outcome labels remain separate records.
5. Implementation readiness does not imply operational admission. Quarantined
   work may be built and tested but cannot self-promote or acquire authority.
6. Historical gate evidence, run roots, hashes, timestamps, and outcomes remain
   factual. Superseded planning requirements are annotated, not rewritten into
   different historical results.
7. Live capital is prohibited until Phase 10's explicit, human-approved gate.
8. Broker credentials remain unavailable to research, agent, browser,
   generated-code, and model-evaluation sandboxes.
