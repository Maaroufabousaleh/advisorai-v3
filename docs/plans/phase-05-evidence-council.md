# Phase 5 — Evidence Council and Decision Model

## Objective

Turn point-in-time, provenance-bearing independent evidence into a typed
Decision Model proposal and target portfolio while preserving a deterministic
authority boundary.

## Work packages

1. Implement Mission Router, Snapshot Builder, and policy-budgeted Fast,
   Standard, Deep, Builder, and Recovery workflows.
2. Add on-demand typed roles: Data Verifier, Technical/Flow,
   Derivatives/Regime, News/Event, Skeptic/Base-Rate, Risk/Opportunity, and
   Synthesizer.
3. Implement EvidenceGraph ancestry, source/factor/model correlation,
   dissent/missing evidence, expiry, adaptive waves, early stop, and cutoff
   expiry.
4. Define `DecisionModelPort`, `DecisionModelArtifact`, and `DecisionProposal`
   with typed LONG/SHORT/FLAT/ABSTAIN (or equivalent), probability
   distribution, calibrated confidence, score, horizon, optional
   regime/strategy class, uncertainty, evidence references, missing-evidence
   flags, disagreement, and expiry. Prose is optional.
5. Provide reference `LayaAdapter` (or equivalent small local typed-decision
   adapter) and common interfaces for optional `KevAdapter`, `JevAdapter`, and
   future `AdvisorCDMAdapter`. Jev is never mandatory.
6. Pass the proposal through the deterministic Portfolio Constructor. Record
   model/provider/prompt/tool versions, evidence ancestry, scorecard inputs,
   and the final proposal artifact.

Adapter qualification is provider-neutral. Pin the exact model, code,
checkpoint, configuration, and license; validate typed input/output, timeout,
retry and fallback policy, abstention and malformed-output handling,
calibration, disagreement, privacy redaction, offline behavior, and remote
cost. Measure local RAM/VRAM and latency on the target workstation. Laya and
Kev remain local/shadow prototypes until their exact artifacts pass; Jev
requires authoritative provider terms before an adapter is planned. See the
[technology decision register](../reference/technology-decision-register.md)
and [ADR 0009](../decisions/0009-typed-decision-model-boundary.md).

## Prohibited authority

The Decision Model cannot alter `RiskPolicy`, submit or cancel orders, access
broker secrets, override EvidenceGraph or stale/missing-data failure, bypass
Portfolio Constructor or RiskKernel, or self-promote. Invalid, late, or
unavailable output becomes missing evidence and results in a permitted
fallback or abstention.

## Exit gate

Duplicated or syndicated evidence cannot create quorum; material conflict,
missing evidence, stale state, invalid Decision Model output, and expired
proposals fail closed; no agent or model output can bypass deterministic
portfolio/risk/execution contracts.

## Explicitly out of scope

No standing agent swarm, direct order tool, mandatory Jev route, or claim that
repeated LLM outputs are independent evidence.
