# Service boundaries

The target process layout is intentionally represented as deployment boundaries,
not as a second source of trading truth.

Always-on Trade/Fast services are `advisor-api`, `market-node`, `collector-node`,
`data-writer`, `account-ledger`, and `resource-governor`. On-demand services are
the agent fabric, model gateway, quant/NLP/risk/TCA workers, Prefect, Hermes,
browser, and archive workers.

Phase 2's deterministic paper core is implemented under `src/advisorai/execution`;
the service packaging and Nautilus runtime admission remain governed by the Phase
0 bake-off records.

The canonical names, ownership, dependencies, and mode admission rules are
executable in `advisorai.services.ServiceRegistry`.  Deployments should use that
registry as the process-boundary manifest; it does not create a second account,
risk, OMS, or ledger authority.

See the repository [components reference](../docs/reference/components.md) for
the current ownership table and [architecture](../docs/concepts/architecture.md)
for the distinction between this target topology and the local launcher.
