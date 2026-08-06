# AdvisorAI V3 phase plans

These plans decompose the authoritative architecture in
[`advisorai-federated-multi-agent-quant-architecture-v3.md`](../../advisorai-federated-multi-agent-quant-architecture-v3.md).
They do not add alternate ownership, bypasses, or live-trading scope. A phase may
start only after all earlier gates pass; expansion remains gate-driven rather than
calendar-driven.

| Phase | Sub-plan | Delivery boundary | Exit gate |
|---|---|---|---|
| 0 | [contracts and bake-offs](phase-00-contracts-and-bakeoffs.md) | Select reproducible, resource-safe candidates | Fits envelopes, reproducible versions, 24-hour stability |
| 1 | [safety, data truth, resources](phase-01-safety-data-resources.md) | Typed immutable foundation | Deterministic rebuild, leakage, idempotency and rollback pass |
| 2 | [deterministic paper core](phase-02-paper-core.md) | One-venue paper execution safety path | Failures reconcile and fail safely |
| 3 | [V3-Core data spine](phase-03-v3-core-data-spine.md) | Crypto data, context and lineage | Replay, lineage and disagreements pass |
| 4 | [quantitative baseline council](phase-04-quant-baselines.md) | Calibrated forecast candidates | Net utility/risk value within resources |
| 5 | [typed evidence council](phase-05-evidence-council.md) | Independent evidence to target portfolio | No false quorum or contract bypass |
| 6 | [institutional controls](phase-06-institutional-controls.md) | Portfolio/risk validity and attribution | Every paper order passes all checks |
| 7 | [unattended paper soak](phase-07-paper-soak.md) | Continuous V3-Core proof | 60 days plus stable, safe evidence |
| 8 | [Hermes and Skill Foundry](phase-08-hermes-skill-foundry.md) | Quarantined capability creation | Active-read only, no trading authority |
| 9 | [controlled expansion](phase-09-controlled-expansion.md) | One challenger/source at a time | Positive marginal value without regression |
| 10 | [limited live capital](phase-10-limited-live-capital.md) | Explicitly approved bounded live operation | Correctness survives all AI/research outages |

The repository contains executable boundaries and deterministic fixtures across
all phases, while external admission gates remain closed. It deliberately does
not enable live credentials, live order submission, unmeasured named-model
promotion, or automatic capability authority.

See [traceability.md](traceability.md) for the architecture-to-sub-plan mapping.
See [implementation-audit.md](implementation-audit.md) for the package-to-code,
test, and external-gate evidence matrix.

The user-facing operator console and its security boundary are specified in
[secure-operator-dashboard.md](secure-operator-dashboard.md); it is an interface
over these phase-owned services, not a new trading authority.
