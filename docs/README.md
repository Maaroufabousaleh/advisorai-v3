# AdvisorAI V3 documentation

The repository is intentionally documented in layers. The root [README](../README.md) explains the project in a few minutes; this index routes readers to verified setup, architecture, operation, and extension details. The long phase plans and runbooks remain the operational record for gated evidence work.

> [!NOTE]
> Documentation describes the current implementation unless a page is explicitly marked **target**, **gated**, **experimental**, or **planned**. A passing unit test is local evidence, not automatic phase admission.

## Start here

| If you want to… | Read |
| --- | --- |
| Install dependencies and run the repository | [Installation](getting-started/installation.md) |
| Get a first working result | [Quickstart](getting-started/quickstart.md) |
| Understand the YAML and environment layers | [Configuration](getting-started/configuration.md) and [Configuration reference](reference/configuration.md) |
| See how data becomes a paper decision | [Data flow](concepts/data-flow.md) and [Execution model](concepts/execution-model.md) |
| Understand ownership and dependency direction | [Architecture](concepts/architecture.md) and [Components](reference/components.md) |
| Run or protect the dashboard | [Operator console](guides/operator-console.md) |
| Find an exact command or route | [CLI reference](reference/cli.md) and [API reference](reference/api.md) |
| Add a collector, gateway, role, or capability | [Extending the system](development/extending.md) |
| Contribute safely | [Development setup](development/setup.md), [Testing](development/testing.md), and [CONTRIBUTING](../CONTRIBUTING.md) |

## Concepts

- [Architecture](concepts/architecture.md) — current module boundaries, ownership, storage, optional runtimes, and target service topology.
- [Data flow](concepts/data-flow.md) — source metadata, raw spooling, point-in-time snapshots, quality gates, and read-only analysis.
- [Execution model](concepts/execution-model.md) — the proposal-to-paper-order lifecycle, risk vetoes, order state machine, reconciliation, and live guard.
- [Project status](concepts/status.md) — implemented, gated/experimental, planned, and deliberately absent capabilities.

## Guides

- [Operator console](guides/operator-console.md) — screens, synthetic versus ledger-backed data, protected mode, and guarded commands.
- [Paper/API transition](plans/real-api-paper-transition.md) — the existing hand-off plan for opt-in real data/LLM APIs and one paper/testnet venue.
- [Local recovery and rollback](runbooks/local-recovery-and-rollback.md) — deterministic recovery-first operations.

## Reference

- [Configuration reference](reference/configuration.md) — YAML bundles, modes, risk controls, environment variables, and credential scopes.
- [CLI reference](reference/cli.md) — verified module entry points, scripts, flags, and opt-in network commands.
- [API reference](reference/api.md) — the optional dashboard API routes and command contract.
- [Components](reference/components.md) — source package map and service registry ownership.

## Development

- [Development setup](development/setup.md) — Python, dashboard, optional extras, and local state.
- [Testing](development/testing.md) — test groups, acceptance phases, static checks, and network boundaries.
- [Extending the system](development/extending.md) — ports, adapters, evidence roles, collectors, models, and capabilities.
- [Debugging](development/debugging.md) — common local failures and how to distinguish availability, admission, and runtime state.
- [Contributing](../CONTRIBUTING.md) — change boundaries and review checklist.
- [Security](../SECURITY.md) — the implemented safety/security model and reporting guidance.

## Existing plans, runbooks, and decisions

These documents are valuable evidence and operational detail, but they are not a substitute for the current-implementation concepts above:

- [Phase plan index](plans/README.md)
- [Current status and evidence record](plans/status.md)
- [Implementation audit](plans/implementation-audit.md)
- [Traceability matrix](plans/traceability.md)
- [Architecture decisions](decisions)
- [Operational runbooks](runbooks)

The architecture dossier at [`advisorai-federated-multi-agent-quant-architecture-v3.md`](../advisorai-federated-multi-agent-quant-architecture-v3.md) is the design and phase authority. Where it describes target services or future integrations, this documentation labels those boundaries rather than presenting them as deployed processes.
