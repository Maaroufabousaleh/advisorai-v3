# Phase 8 — Hermes and Skill Foundry

## Objective

Implement Hermes as an isolated research/build runtime that exports
reproducible, quarantined capabilities. Implementation may begin before Phase
7 session admission as long as every output remains behind typed interfaces
and the capability lifecycle.

## Work packages

1. Add per-mission Hermes profiles and isolated task environments for Deep,
   Builder, or offline Recovery work.
2. Implement typed ResearchBundle, CandidateStrategy, CollectorCandidate,
   ModelAdapterCandidate, CapabilityBundle, EnvironmentManifest, and runbook
   exports with immutable provenance.
3. Add capability registry/broker plus scout, pin, inspect, sandbox,
   wrap/build, contract/security/performance tests, review, shadow, and
   active-read lifecycle handling.
4. Deliver one missing deterministic collector or adapter through the
   complete quarantined lifecycle.
5. Permit Research Brain/Alpha Team integration behind the same capability
   and permission boundary, one admitted adapter at a time.

## Exit and admission gate

Hermes creates a reproducible quarantined capability that may reach
active-read after its own exact evidence passes. Active-read is not broker
access, write authority, self-promotion, or a live-trading permission.

## Explicitly out of scope

No Hermes order tool, limit change, broker credentials, live-repository
mutation, automatic live-capital write authority, Hermes memory as authority,
or automatic capability admission.
