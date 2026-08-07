"""Typed V3-Core and risk-policy configuration loaders.

The YAML files are reviewed configuration inputs, not authority by themselves.
This module validates the deliberately small V3-Core scope and converts the
reviewed risk limits into the immutable :class:`RiskPolicy` artifact consumed by
the deterministic RiskKernel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import AssetClass, RiskLimit, RiskPolicy, SourceGrade


class GpuModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    choose_one: tuple[str, ...]

    @field_validator("choose_one")
    @classmethod
    def require_gpu_candidates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("GPU model selection requires named candidates")
        if len(normalized) < 2:
            raise ValueError("V3-Core GPU selection must retain at least two challengers")
        if len(normalized) != len(set(normalized)):
            raise ValueError("GPU model candidates must be unique")
        return normalized


class V3CoreStorage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active: str
    archive: str

    @model_validator(mode="after")
    def require_local_authority(self) -> V3CoreStorage:
        if self.active != "local_parquet_duckdb_sqlite":
            raise ValueError("V3-Core active storage must remain local Parquet/DuckDB/SQLite")
        if self.archive != "manual_tested_rclone_crypt":
            raise ValueError("V3-Core archive must remain tested rclone-crypt")
        return self


class V3CoreConfig(BaseModel):
    """The constrained initial universe and runtime boundary from the plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_class: AssetClass
    universe: tuple[str, ...]
    venue: str
    primary_horizon: str
    observation_interval: str
    context_horizon: str
    execution: str
    primary_data: str
    context_sources: tuple[str, ...]
    optional_corroboration: tuple[str, ...] = ()
    deterministic_models: tuple[str, ...]
    cpu_models: tuple[str, ...]
    gpu_model_selection: GpuModelSelection
    agent_roles: tuple[str, ...]
    excluded: tuple[str, ...]
    storage: V3CoreStorage

    @field_validator(
        "venue",
        "primary_horizon",
        "observation_interval",
        "context_horizon",
        "execution",
        "primary_data",
    )
    @classmethod
    def normalize_scope_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("V3-Core scope fields cannot be blank")
        return value.strip()

    @field_validator(
        "universe",
        "context_sources",
        "optional_corroboration",
        "deterministic_models",
        "cpu_models",
        "agent_roles",
        "excluded",
    )
    @classmethod
    def normalize_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("V3-Core configuration lists cannot contain blank entries")
        if len(normalized) != len(set(normalized)):
            raise ValueError("V3-Core configuration lists must be unique")
        return normalized

    @field_validator("universe")
    @classmethod
    def normalize_universe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item.upper() for item in value)

    @model_validator(mode="after")
    def enforce_core_scope(self) -> V3CoreConfig:
        if self.asset_class is not AssetClass.CRYPTO:
            raise ValueError("V3-Core is crypto-only")
        if tuple(item.upper() for item in self.universe) != ("BTC", "ETH"):
            raise ValueError("V3-Core universe must remain exactly BTC and ETH")
        if (
            self.primary_horizon != "1h"
            or self.observation_interval != "5m"
            or self.context_horizon != "4h"
        ):
            raise ValueError(
                "V3-Core horizons must remain 1h decisions, 5m observations, and 4h context"
            )
        if self.execution != "paper_testnet_only":
            raise ValueError("V3-Core cannot enable live execution")
        if self.primary_data != "native_venue_rest_websocket":
            raise ValueError("V3-Core primary data must remain the native venue REST/WebSocket")
        if not {"deribit", "gdelt", "official_rss"}.issubset(self.context_sources):
            raise ValueError("V3-Core context source roster is incomplete")
        if "audited_lse_cross_check" not in self.optional_corroboration:
            raise ValueError("V3-Core must retain optional audited LSE corroboration")
        if not {"naive", "statistical", "lightgbm"}.issubset(self.deterministic_models):
            raise ValueError("V3-Core deterministic model roster is incomplete")
        if not {"ttm-r2", "tspulse"}.issubset(self.cpu_models):
            raise ValueError("V3-Core CPU model roster is incomplete")
        if len(self.agent_roles) < 3:
            raise ValueError("V3-Core requires a multi-factor agent council")
        if "hermes" not in {item.lower() for item in self.excluded}:
            raise ValueError("Hermes must remain excluded from V3-Core")
        return self


class AgentConfig(BaseModel):
    """Typed admission for the V3-Core logical role and call budgets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    roles: tuple[str, ...]
    minimum_paper_factor_families: int = Field(ge=1)
    minimum_paper_source_families: int = Field(ge=1)
    remote_llm_calls_standard: int = Field(ge=0, le=2)
    remote_llm_calls_deep: int = Field(ge=0, le=4)

    @field_validator("roles")
    @classmethod
    def require_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("agent roles must be unique and non-blank")
        required = {
            "data_verifier",
            "technical_flow",
            "derivatives_regime",
            "news_event",
            "skeptic_base_rate",
            "risk_opportunity",
            "synthesizer",
        }
        if not required.issubset(normalized):
            raise ValueError("V3-Core agent role roster is incomplete")
        return normalized


class ModelConfig(BaseModel):
    """Pinned baseline/candidate roster; no implicit model substitution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    baselines: tuple[str, ...]
    cpu_candidates: tuple[str, ...]
    gpu_candidates: tuple[str, ...]
    gpu_residency: str
    promotion_metric: str

    @field_validator("baselines", "cpu_candidates", "gpu_candidates")
    @classmethod
    def normalize_model_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("model roster entries cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_roster(self) -> ModelConfig:
        values = (*self.baselines, *self.cpu_candidates, *self.gpu_candidates)
        if any(not item.strip() for item in values) or len(values) != len(set(values)):
            raise ValueError("model roster entries must be unique and non-blank")
        if not {"naive", "drift", "seasonal", "linear", "lightgbm"}.issubset(self.baselines):
            raise ValueError("mandatory deterministic baselines are missing")
        if not {"ttm-r3", "ttm-r2", "tspulse"}.issubset(self.cpu_candidates):
            raise ValueError("V3-Core CPU candidates are incomplete")
        if not {
            "chronos-2-small",
            "kronos-mini",
            "kronos-small",
            "tabpfn-ts",
        }.issubset(self.gpu_candidates):
            raise ValueError("V3-Core GPU candidates are incomplete")
        if self.gpu_residency != "one_family_at_a_time":
            raise ValueError("GPU residency must remain one family at a time")
        if not self.promotion_metric.strip():
            raise ValueError("model promotion metric is required")
        return self


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str
    venue_count: int = Field(ge=1, le=1)
    order_policies: tuple[str, ...]
    time_in_force: tuple[str, ...]
    ambiguous_ack: str
    idempotency: str
    canonical_engine: str

    @field_validator("environment", "ambiguous_ack", "idempotency", "canonical_engine")
    @classmethod
    def normalize_execution_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("execution configuration tokens cannot be blank")
        return value.strip()

    @field_validator("order_policies")
    @classmethod
    def normalize_order_policies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("execution order policies must be unique and non-blank")
        return normalized

    @field_validator("time_in_force")
    @classmethod
    def normalize_time_in_force(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("execution time-in-force values must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def validate_paper_boundary(self) -> ExecutionConfig:
        if self.environment not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError("V3-Core execution must remain paper/testnet only")
        if set(self.order_policies) != {"immediate", "passive_limit"}:
            raise ValueError("V3-Core execution policies must remain immediate/passive_limit")
        if not {"GTC", "IOC"}.issubset({value.upper() for value in self.time_in_force}):
            raise ValueError("V3-Core execution requires GTC and IOC time-in-force")
        if self.ambiguous_ack != "reconcile_before_retry":
            raise ValueError("ambiguous acknowledgements must reconcile before retry")
        if self.idempotency != "required_for_parent_and_child":
            raise ValueError("parent and child order idempotency is mandatory")
        if self.canonical_engine != "nautilus_trader":
            raise ValueError("NautilusTrader remains the canonical execution engine")
        return self


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    family: str
    origin: str
    grade: SourceGrade
    intended_use: str
    parser_version: str

    @field_validator("name", "family", "origin", "intended_use", "parser_version")
    @classmethod
    def require_source_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source configuration fields cannot be blank")
        return value.strip()


class SourceRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[SourceConfig, ...]

    @model_validator(mode="after")
    def validate_sources(self) -> SourceRegistryConfig:
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)) or not self.sources:
            raise ValueError("source configuration names must be non-empty and unique")
        native = next((source for source in self.sources if source.name == "native_venue"), None)
        if native is None or native.grade is not SourceGrade.EXECUTION:
            raise ValueError("V3-Core requires an execution-grade native venue source")
        required = {"native_venue", "deribit_context", "official_rss", "gdelt"}
        if not required.issubset(names):
            raise ValueError("V3-Core source registry is incomplete")
        return self


class ResourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str
    wsl_memory_gib_approx: float = Field(gt=0)
    minimum_headroom_gib: float = Field(ge=1.5)
    gpu_global_leases: int = Field(ge=1, le=1)
    browser_global_jobs: int = Field(ge=1, le=1)
    heavy_duckdb_global_jobs: int = Field(ge=1, le=2)
    cpu_bound_global_jobs: int = Field(ge=1, le=2)
    backtest_train_global_jobs: int = Field(ge=1, le=1)
    hermes_coordinators: int = Field(ge=1, le=1)
    hermes_subagents_initial: int = Field(ge=1, le=1)

    @field_validator("target")
    @classmethod
    def require_target(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource target is required")
        return value.strip()


class RiskConfig(BaseModel):
    """Reviewed YAML risk limits converted to an immutable policy artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    hard_limits: dict[str, Decimal]
    stale_data_rejects: bool
    kill_switch: str
    ai_can_loosen_limits: bool

    @field_validator("policy_version", "kill_switch")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("risk configuration tokens cannot be blank")
        return value.strip()

    @field_validator("hard_limits")
    @classmethod
    def validate_limits(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        normalized_names = tuple(name.strip() for name in value)
        if not value or any(not name for name in normalized_names):
            raise ValueError("risk configuration requires named hard limits")
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("risk hard-limit names must be unique")
        if any(not limit.is_finite() or limit < 0 for limit in value.values()):
            raise ValueError("risk hard limits must be finite and non-negative")
        return {name.strip(): limit for name, limit in value.items()}

    @model_validator(mode="after")
    def enforce_fail_closed_policy(self) -> RiskConfig:
        if not self.stale_data_rejects:
            raise ValueError("risk configuration must reject stale data")
        if self.kill_switch != "independent":
            raise ValueError("risk configuration must retain an independent kill switch")
        if self.ai_can_loosen_limits:
            raise ValueError("AI cannot loosen deterministic risk limits")
        return self

    def to_policy(self, *, effective_at: datetime, approved_by: str) -> RiskPolicy:
        if effective_at.tzinfo is None or effective_at.utcoffset() is None:
            raise ValueError("risk policy effective_at must include a timezone")
        if not approved_by.strip():
            raise ValueError("risk policy conversion requires an approver")
        from advisorai.execution.risk import RiskKernel

        unsupported = set(self.hard_limits).difference(RiskKernel.SUPPORTED_HARD_LIMITS)
        if unsupported:
            raise ValueError(
                f"risk configuration contains unsupported hard limits: {sorted(unsupported)}"
            )
        return RiskPolicy(
            policy_version=self.policy_version,
            effective_at=effective_at.astimezone(UTC),
            hard_limits=tuple(
                RiskLimit(name=name, limit=limit, unit=_unit_for_limit(name))
                for name, limit in sorted(self.hard_limits.items())
            ),
            approved_by=approved_by.strip(),
        )


def _unit_for_limit(name: str) -> str:
    """Keep units explicit while allowing the YAML to stay compact."""

    if name.endswith("_bps") or name == "price_collar_bps":
        return "bps"
    if name in {"max_leverage", "max_concentration", "max_liquidity_participation"}:
        return "ratio"
    if name in {
        "max_data_disagreement",
        "max_model_drift",
        "max_unsupported_regime",
        "max_expired_forecast",
        "max_reconciliation_discrepancies",
        "max_venue_health",
    }:
        return "flag"
    if name in {"max_stale_seconds", "max_clock_drift_seconds"}:
        return "seconds"
    if name == "max_order_rate":
        return "count"
    return "USD"


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"configuration file {path} must contain an object")
    return payload


def load_v3_core_config(path: Path) -> V3CoreConfig:
    return V3CoreConfig.model_validate(_load_yaml(path))


def load_risk_config(path: Path) -> RiskConfig:
    return RiskConfig.model_validate(_load_yaml(path))


def load_agent_config(path: Path) -> AgentConfig:
    return AgentConfig.model_validate(_load_yaml(path))


def load_model_config(path: Path) -> ModelConfig:
    return ModelConfig.model_validate(_load_yaml(path))


def load_execution_config(path: Path) -> ExecutionConfig:
    return ExecutionConfig.model_validate(_load_yaml(path))


def load_source_registry_config(path: Path) -> SourceRegistryConfig:
    return SourceRegistryConfig.model_validate(_load_yaml(path))


def load_resource_config(path: Path) -> ResourceConfig:
    return ResourceConfig.model_validate(_load_yaml(path))
