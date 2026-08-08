# Phase 8 — Hermes and Skill Foundry

## Objective

Use Hermes only as an isolated research/build runtime that exports reproducible,
quarantined capabilities.

## Work packages

1. Add isolated Hermes profiles and per-mission task environments for Deep,
   Builder, or offline Recovery mode only.
2. Implement typed ResearchBundle, CandidateStrategy, CollectorCandidate,
   ModelAdapterCandidate, CapabilityBundle, EnvironmentManifest and runbook
   exports, each with validation and immutable provenance.
3. Add capability registry/broker plus scout, pin, inspect, sandbox, wrap/build,
   contract/security/performance tests, review, shadow, active-read and
   active-write-limited lifecycle handling.
4. Deliver one missing deterministic collector or adapter through that complete
   lifecycle.
5. If the optional [Alpha Team extension](alpha-team-extension.md) is admitted
   after E0, accept only one E4 research capability adapter at a time through
   the same `CapabilityBundle` lifecycle and permission boundary.

## Exit gate

Hermes creates a reproducible quarantined capability that reaches active-read
without broker credentials, live deployment, or order authority.

## Explicitly out of scope

No Hermes order tool, limit change, live-repository mutation, automatic
live-capital write authority, reliance on Hermes memory as authority, or
automatic Alpha Team capability admission.
