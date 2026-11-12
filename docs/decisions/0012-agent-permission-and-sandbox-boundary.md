# ADR 0012: Agent permission classes independent of sandbox backend

**Status:** accepted planning authority. Host-qualified backend selection and
capability admission remain pending.

## Context

Hermes, generated code, browser tasks, research agents, and imported
capabilities have different trust and authority needs. Docker, gVisor, Kata,
and microVMs are implementation backends; none defines AdvisorAI's business
permissions by itself. A backend being installed or a container starting does
not prove that a workload cannot read host secrets or affect trading state.

## Decision

Every job is assigned a trust class and an explicit capability profile before
launch. Permissions default to deny and include filesystem mounts and access
modes, network destinations, secret scopes, process and resource limits, GPU
visibility, timeout, artifact export, cleanup, and audit identity.

| Trust class | Intended workload | Required boundary |
|---|---|---|
| TRUSTED_CORE | Reviewed deterministic application services | Writes only to the stores and paths owned by that service; secret scope is explicit and least-privilege. |
| TRUSTED_OPERATOR_TOOL | Owner-facing local administration and recovery tools | Typed, confirmed, audited operations; no implicit trading or live-mode authority. |
| RESTRICTED_RESEARCH | Reviewed read-only analysis over immutable snapshots | Read-only snapshot mounts; no broker, order, or RiskKernel access; network and GPU use are explicit. |
| UNTRUSTED_RESEARCH | Generated code, imported research tools, and agent-authored experiments | No credentials; no host write mounts; default-deny network; bounded CPU/RAM/time; controlled artifact export only. |
| UNTRUSTED_NATIVE | Unreviewed native binaries or workloads requiring a stronger kernel boundary | Qualified VM-backed isolation is required; fail closed if unavailable; never fall back silently. |

The backend-selection policy is separate from this matrix. Hardened Docker is
a convenience baseline, not a VM-equivalent boundary. gVisor and Kata may be
prototyped after host qualification. Firecracker is deferred until a concrete
workload justifies its operational cost. No backend receives a broker
credential, OMS handle, RiskKernel mutation permission, or live order tool.

## Required qualification evidence

Before any untrusted capability is admitted, record the host/backend identity
and exact image or binary digest; verify default-deny networking, scoped mounts,
absence of implicit host secrets, bounded process/CPU/RAM/time, GPU policy,
explicit artifact-export path, adversarial filesystem/network/process fixtures,
resource-exhaustion behavior, crash cleanup, and sandbox identity in the audit
ledger. Repeat on each materially different host or backend configuration.

If the selected isolation mechanism is absent or fails a required check, the
job does not run under a weaker boundary. Exported artifacts are scanned,
hashed, schema-validated, and independently reviewed before local registration.

## Consequences

Phase 8 owns Hermes capability sandbox evidence. A draft backend PR or local
Docker measurement does not establish Kata runtime qualification or Phase 8
admission. The permission contract survives backend replacement.

## References

- [Technology and integration decision register](../reference/technology-decision-register.md)
- [Phase 8 Hermes and Skill Foundry](../plans/phase-08-hermes-skill-foundry.md)
- [Security model](../../SECURITY.md)
- [Runtime dependency boundary](0006-runtime-dependency-boundary.md)
