# Phase 0 — Contracts and bounded integration qualification

## Objective

Freeze the typed boundaries needed to finish the complete AdvisorAI system.
Use a small reference model fabric for integration. Broad model selection is
deferred and is not a prerequisite for infrastructure or agentic workflows.

## Work packages

1. Maintain canonical ownership decisions for `ModelGatewayPort`,
   `DecisionModelPort`, `ArchiveBackend`, `EventBusPort`, session lifecycle,
   evidence, portfolio, and execution.
2. Define and contract-test Snapshot, Evidence, Forecast, `DecisionProposal`,
   `DecisionTrainingRecord`, TargetPortfolio, RiskPolicy/Decision,
   ExecutionPlan, Order/Fill, Reconciliation, Attribution, ModelCard,
   AgentRun, CapabilityCard, and session start/checkpoint/end/recovery records.
3. Pin exact code/data/model/config/environment identity for every reference
   adapter. A bounded Level A check covers typed inputs/outputs, PIT safety,
   resource bounds, load/inference/unload, failures, and authority separation.
4. Integrate the frozen references: Chronos-2-small, TTM-R2, Kronos-small,
   LightGBM, the selected compact FinBERT-family implementation, the Laya or
   equivalent local Decision Model adapter, and an on-demand remote reasoning
   gateway route. Integration status is not comparative admission.
5. Preserve existing gateway, Nautilus, Prefect, Hamilton, Parquet/DuckDB,
   archive, and Hermes evidence; implement their required stable interfaces
   and quarantine incomplete candidates.

## Required records

Versioned contracts, adapter identity manifests, exact inputs/outputs,
resource and failure behavior, PIT/authority checks, and admission state belong
in the appropriate model, source, runtime, capability, or lifecycle ledger.
No prose, local test, or integration check may be projected as a promotion
record.

## Exit and admission semantics

**Implementation readiness:** the typed ports and reference adapter seams are
available for integrated development, with missing/unavailable outputs
represented explicitly.

**Level A participation:** an exact component may participate only within its
reviewed operating scope after identity, contract, PIT, resource, load/unload,
failure, and authority checks. Comparative utility and wider promotion are
separate admission decisions.

Historical 24-hour evidence remains factual. The old mandatory 24-hour
stability rule is a superseded planning requirement for the session
workstation. Repeated model lifecycle and resource cleanup are assessed in
integrated session validation. No model experiment or acquisition is initiated
by this plan.

## Explicitly out of scope

No live orders, automatic promotion, Jev dependency, direct model-to-order
path, broad model bake-off prerequisite, resident agent fleet, or model
training. No update to historical artifacts or gate results.
