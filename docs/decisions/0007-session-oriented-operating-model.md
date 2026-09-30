# ADR 0007: Session-oriented workstation operating model

Status: accepted planning authority; lifecycle implementation and admission
remain pending.

AdvisorAI's normal product profile is a single-owner local workstation. The
owner typically starts a 5–8 hour working session, uses research and
paper/testnet workflows, then stops cleanly. On each start, the system validates
identity and ledgers, recovers authoritative state, reconciles venue state,
detects and repairs permitted data gaps, restores eligible work, and initializes
only needed workers. Always-on/server deployment is optional and has a separate
admission profile.

Implementation readiness and operational admission are separate. Software may
be implemented before an earlier model, source, or capability passes admission
if it remains quarantined, typed, research/shadow/paper constrained, and unable
to grant itself authority. Sequential gates govern operating scope and
promotion, not whether unrelated safe infrastructure can be built.

Historical 24-hour and 60-day records remain factual historical evidence. Their
old continuous-duration requirement is superseded as the primary workstation
readiness criterion. Phase 7 evaluates repeated complete-system sessions,
recovery, reconciliation, PIT integrity, and bounded resources. An optional
continuous deployment profile may retain duration-specific tests.

The deterministic Portfolio Constructor, RiskKernel, OMS, NautilusTrader,
reconciliation, and credential boundaries are unchanged. No model, LLM,
Decision Model, Hermes job, or research agent receives order or risk-limit
authority.
