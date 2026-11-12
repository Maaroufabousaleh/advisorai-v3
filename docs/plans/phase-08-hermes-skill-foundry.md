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
   and permission boundary, one admitted adapter at a time. Qanat may be
   evaluated as an optional research-only MCP adapter; independent AdvisorAI
   checks remain authoritative.

## Exit and admission gate

Hermes creates a reproducible quarantined capability that may reach
active-read after its own exact evidence passes. Active-read is not broker
access, write authority, self-promotion, or a live-trading permission.

## Explicitly out of scope

No Hermes order tool, limit change, broker credentials, live-repository
mutation, automatic live-capital write authority, Hermes memory as authority,
or automatic capability admission.

## Permission classes and backend qualification

Assign every job an explicit trust class and capability profile before launch.
Filesystem access, network destinations, secret scopes, process and resource
limits, GPU visibility, timeout, artifact export, cleanup, and audit identity
default to deny. The class definitions and backend-independent policy are in
[ADR 0012](../decisions/0012-agent-permission-and-sandbox-boundary.md).

Hardened Docker is a convenience baseline and is not a VM-equivalent claim.
gVisor and Kata are host-qualified prototypes; Firecracker is deferred until a
concrete workload justifies it. Missing or failed isolation must fail closed,
without silent fallback to a weaker backend. A backend being installed or
starting a container is not an admission record.

As of the 2026-11-11 PR review, [PR #207](https://github.com/Maaroufabousaleh/advisorai-v3/pull/207)
is an open draft. Its description says Kata is not installed, guest/adversarial
runtime tests were not performed, and Phase 8 is not admitted. This workstream
does not treat it as current-main Kata evidence.
