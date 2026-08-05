"""Process ownership and dependency boundaries from the architecture plan."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.config import MissionMode


class ServiceKind(StrEnum):
    ALWAYS_ON = "always_on"
    ON_DEMAND = "on_demand"


class ServiceDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    kind: ServiceKind
    owns: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    allowed_modes: tuple[MissionMode, ...] = tuple(MissionMode)
    critical: bool = False
    resource_bounded: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("service name cannot be blank")
        return value.strip()

    @field_validator("owns", "depends_on")
    @classmethod
    def normalize_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("service ownership and dependencies must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def validate_descriptor(self) -> ServiceDescriptor:
        if self.critical and not self.resource_bounded:
            raise ValueError("critical services must remain resource-bounded")
        if self.kind is ServiceKind.ALWAYS_ON and not self.allowed_modes:
            raise ValueError("always-on services require an explicit mode boundary")
        return self


DEFAULT_SERVICES: tuple[ServiceDescriptor, ...] = (
    ServiceDescriptor(
        name="advisor-api",
        kind=ServiceKind.ALWAYS_ON,
        owns=("mission_routing", "typed_api", "approval_boundary"),
        depends_on=("market-node", "account-ledger"),
        critical=True,
    ),
    ServiceDescriptor(
        name="market-node",
        kind=ServiceKind.ALWAYS_ON,
        owns=("nautilus_events", "risk_kernel", "oms", "venue_adapter"),
        depends_on=("collector-node", "account-ledger", "resource-governor"),
        critical=True,
    ),
    ServiceDescriptor(
        name="collector-node",
        kind=ServiceKind.ALWAYS_ON,
        owns=("raw_market_persistence", "source_health"),
        depends_on=("data-writer", "resource-governor"),
        critical=True,
    ),
    ServiceDescriptor(
        name="data-writer",
        kind=ServiceKind.ALWAYS_ON,
        owns=("bronze_silver_gold", "lake_manifests"),
        depends_on=("resource-governor",),
        critical=True,
    ),
    ServiceDescriptor(
        name="account-ledger",
        kind=ServiceKind.ALWAYS_ON,
        owns=("account_state", "cash_positions_margin", "reconciliation_projection"),
        depends_on=("resource-governor",),
        critical=True,
    ),
    ServiceDescriptor(
        name="resource-governor",
        kind=ServiceKind.ALWAYS_ON,
        owns=("measured_resource_admission", "load_shedding"),
        critical=True,
    ),
    ServiceDescriptor(
        name="agent-fabric",
        kind=ServiceKind.ON_DEMAND,
        owns=("pydantic_typed_evidence",),
        depends_on=("advisor-api", "resource-governor"),
    ),
    ServiceDescriptor(
        name="model-gateway",
        kind=ServiceKind.ON_DEMAND,
        owns=("recorded_model_routes",),
        depends_on=("resource-governor",),
    ),
    ServiceDescriptor(
        name="quant-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("forecast_candidates",),
        depends_on=("data-writer", "resource-governor"),
    ),
    ServiceDescriptor(
        name="nlp-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("finance_nlp_evidence",),
        depends_on=("data-writer", "resource-governor"),
    ),
    ServiceDescriptor(
        name="risk-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("risk_analytics",),
        depends_on=("market-node", "account-ledger", "resource-governor"),
    ),
    ServiceDescriptor(
        name="tca-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("tca_attribution",),
        depends_on=("market-node", "account-ledger"),
    ),
    ServiceDescriptor(
        name="prefect",
        kind=ServiceKind.ON_DEMAND,
        owns=("durable_schedules_retries",),
        depends_on=("resource-governor",),
    ),
    ServiceDescriptor(
        name="hermes-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("quarantined_research_build",),
        depends_on=("resource-governor",),
        allowed_modes=(MissionMode.DEEP, MissionMode.BUILDER, MissionMode.RECOVERY),
    ),
    ServiceDescriptor(
        name="browser-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("compliant_discovery_only",),
        depends_on=("resource-governor",),
        allowed_modes=(MissionMode.DEEP, MissionMode.BUILDER, MissionMode.RECOVERY),
    ),
    ServiceDescriptor(
        name="archive-worker",
        kind=ServiceKind.ON_DEMAND,
        owns=("encrypted_archive_transfer",),
        depends_on=("resource-governor",),
        allowed_modes=(MissionMode.DEEP, MissionMode.BUILDER, MissionMode.RECOVERY),
    ),
)


class ServiceRegistry:
    """Immutable service catalog with ownership collision checks."""

    def __init__(self, descriptors: tuple[ServiceDescriptor, ...] = DEFAULT_SERVICES) -> None:
        if not descriptors:
            raise ValueError("service registry cannot be empty")
        names = [descriptor.name for descriptor in descriptors]
        if len(names) != len(set(names)):
            raise ValueError("service names must be unique")
        by_name = set(names)
        missing_dependencies = {
            dependency
            for descriptor in descriptors
            for dependency in descriptor.depends_on
            if dependency not in by_name
        }
        if missing_dependencies:
            raise ValueError(
                f"service dependencies are not registered: {sorted(missing_dependencies)}"
            )
        owners: dict[str, str] = {}
        for descriptor in descriptors:
            for capability in descriptor.owns:
                prior = owners.get(capability)
                if prior is not None and prior != descriptor.name:
                    raise ValueError(f"capability {capability!r} has multiple owners")
                owners[capability] = descriptor.name
        if owners.get("risk_kernel") != "market-node" or owners.get("oms") != "market-node":
            raise ValueError("market-node must own both RiskKernel and OMS")
        if owners.get("account_state") != "account-ledger":
            raise ValueError("account-ledger must own account state")
        self._descriptors = {descriptor.name: descriptor for descriptor in descriptors}

    def get(self, name: str) -> ServiceDescriptor:
        try:
            return self._descriptors[name]
        except KeyError as exc:
            raise KeyError(f"unknown service {name!r}") from exc

    def all(self) -> tuple[ServiceDescriptor, ...]:
        return tuple(self._descriptors.values())

    def startup_order(self) -> tuple[ServiceDescriptor, ...]:
        """Return a deterministic dependency-first startup order."""

        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[ServiceDescriptor] = []

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ValueError(f"service dependency cycle includes {name!r}")
            visiting.add(name)
            descriptor = self._descriptors[name]
            for dependency in descriptor.depends_on:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(descriptor)

        for name in sorted(self._descriptors):
            visit(name)
        return tuple(ordered)

    def admit_mode(self, name: str, mode: MissionMode) -> ServiceDescriptor:
        descriptor = self.get(name)
        if mode not in descriptor.allowed_modes:
            raise PermissionError(f"service {name!r} is not admitted in {mode.value} mode")
        return descriptor
