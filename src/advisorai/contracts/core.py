"""Immutable, versioned artifacts for the AdvisorAI decision chain.

These contracts are intentionally broad enough to freeze the inter-service
boundary before Phase 2 implementations exist. They confer no execution
authority: a target portfolio or execution plan is not an order submission.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "v3.0"
FORBIDDEN_CAPABILITY_ACTIONS = frozenset({"submit_order", "change_risk_limit", "live_deploy"})


def normalize_authority_action(value: str) -> str:
    """Normalize an action name before applying the trading-boundary denylist."""

    if not isinstance(value, str):
        return ""
    # Normalize common API spellings (camelCase, dotted names, slashes and
    # plural resource names) before applying the denylist.  Authority checks
    # must be semantic, not dependent on one provider's naming convention.
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return re.sub(r"_+", "_", normalized)


def is_forbidden_authority_action(value: str) -> bool:
    """Reject order/risk/deployment write authority, including common aliases."""

    normalized = normalize_authority_action(value)
    if not normalized:
        return False
    if normalized in FORBIDDEN_CAPABILITY_ACTIONS:
        return True
    tokens = set(normalized.split("_"))
    singular_tokens = {token[:-1] if token.endswith("s") else token for token in tokens}
    if tokens.intersection({"submitorder", "change_risk_limit", "livedeploy"}):
        return True
    if {"live", "deploy"}.issubset(tokens) or {"production", "deploy"}.issubset(tokens):
        return True
    if (
        "broker" in singular_tokens or "exchange" in singular_tokens or "venue" in singular_tokens
    ) and tokens.intersection({"credential", "secret", "key", "token"}):
        return True
    if singular_tokens.intersection({"order"}) and not tokens.intersection(
        {"read", "list", "query", "history", "status"}
    ):
        return True
    order_write_verbs = {
        "amend",
        "cancel",
        "create",
        "delete",
        "execute",
        "place",
        "send",
        "submit",
        "write",
    }
    if singular_tokens.intersection({"order", "trade"}) and tokens.intersection(order_write_verbs):
        return True
    # A direct trade verb is itself order authority unless it is clearly a
    # read-only noun such as ``read_trades`` or ``trade_history``.
    if tokens.intersection(
        {"execute", "place", "submit", "send", "trade"}
    ) and not tokens.intersection({"read", "list", "query", "history", "status"}):
        return True
    risk_write_verbs = {
        "change",
        "disable",
        "increase",
        "modify",
        "override",
        "relax",
        "set",
        "write",
    }
    risk_nouns = {"risk", "limit", "exposure", "leverage", "position", "margin"}
    return bool(tokens.intersection(risk_nouns) and tokens.intersection(risk_write_verbs))


def _require_digest(value: str, info: object) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        field = getattr(info, "field_name", "digest")
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _optional_digest(value: str | None, info: object) -> str | None:
    if value is None:
        return None
    return _require_digest(value, info)


def _require_finite_decimal(value: Decimal | None, info: object) -> Decimal | None:
    if value is None:
        return value
    if not value.is_finite():
        field = getattr(info, "field_name", "decimal")
        raise ValueError(f"{field} must be finite")
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


class ArtifactModel(BaseModel):
    """Strict immutable base for every durable hand-off."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    artifact_id: UUID = Field(default_factory=uuid4)
    contract_version: str = CONTRACT_VERSION
    created_at: datetime = Field(default_factory=utc_now)

    _created_at_aware = field_validator("created_at")(require_aware)

    @field_validator("contract_version")
    @classmethod
    def require_current_contract(cls, value: str) -> str:
        if value != CONTRACT_VERSION:
            raise ValueError(f"artifacts must use contract version {CONTRACT_VERSION}")
        return value

    def canonical_hash(self) -> str:
        payload = self.model_dump_json(exclude={"artifact_id", "created_at"})
        return sha256(payload.encode("utf-8")).hexdigest()


class ArtifactTier(StrEnum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


class SourceGrade(StrEnum):
    EXECUTION = "execution_grade"
    RESEARCH = "research_grade"
    CONTEXT = "context_only"


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    FX = "fx"
    FUTURE = "future"
    OPTION = "option"
    FUND = "fund"
    OTHER = "other"


class InstrumentIdentity(ArtifactModel):
    """Canonical, venue-aware instrument identity; symbols alone are insufficient."""

    canonical_id: str = Field(min_length=1)
    asset_class: AssetClass
    venue: str | None = None
    venue_symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _valid_from_aware = field_validator("valid_from")(require_aware)
    _valid_to_aware = field_validator("valid_to")(require_aware)

    @model_validator(mode="after")
    def validate_validity_window(self) -> InstrumentIdentity:
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class ArtifactReference(ArtifactModel):
    tier: ArtifactTier
    uri: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    dataset: str = Field(min_length=1)
    first_available_at: datetime
    parser_version: str | None = None

    _first_available_aware = field_validator("first_available_at")(require_aware)

    _content_hash = field_validator("content_hash")(_require_digest)


class PointInTimeObservation(ArtifactModel):
    """Normalized observation with the complete availability and lineage contract."""

    instrument: InstrumentIdentity
    entity_id: str | None = None
    event_time: datetime | None = None
    effective_time: datetime | None = None
    source_published_at: datetime | None = None
    first_available_at: datetime
    ingested_at: datetime
    source_revision: str | None = None
    supersedes_observation_id: UUID | None = None
    raw_artifact_hash: str = Field(min_length=64, max_length=64)
    parser_version: str = Field(min_length=1)
    source_family: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    syndication_chain: tuple[str, ...] = ()
    quality_grade: SourceGrade
    delay_seconds: int | None = Field(default=None, ge=0)
    intended_use: str = Field(min_length=1)
    value: str = Field(min_length=1)

    _event_time_aware = field_validator("event_time")(require_aware)
    _effective_time_aware = field_validator("effective_time")(require_aware)
    _source_published_at_aware = field_validator("source_published_at")(require_aware)
    _first_available_at_aware = field_validator("first_available_at")(require_aware)
    _ingested_at_aware = field_validator("ingested_at")(require_aware)

    _raw_artifact_hash = field_validator("raw_artifact_hash")(_require_digest)

    @model_validator(mode="after")
    def validate_lineage_time_order(self) -> PointInTimeObservation:
        if self.source_published_at and self.first_available_at < self.source_published_at:
            raise ValueError("first_available_at cannot precede source_published_at")
        if self.ingested_at < self.first_available_at:
            raise ValueError("ingested_at cannot precede first_available_at")
        if self.event_time and self.event_time > self.ingested_at:
            raise ValueError("event_time cannot be after ingestion")
        if self.effective_time and self.effective_time > self.ingested_at:
            raise ValueError("effective_time cannot be after ingestion")
        if self.supersedes_observation_id == self.artifact_id:
            raise ValueError("an observation cannot supersede itself")
        if len(self.syndication_chain) != len(set(self.syndication_chain)):
            raise ValueError("syndication chain entries must be unique")
        return self


class Snapshot(ArtifactModel):
    as_of: datetime
    purpose: str = Field(min_length=1)
    observation_ids: tuple[UUID, ...] = ()
    artifact_references: tuple[ArtifactReference, ...] = ()
    data_quality_state: str = "validated"

    _as_of_aware = field_validator("as_of")(require_aware)

    @model_validator(mode="after")
    def forbid_future_references(self) -> Snapshot:
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("snapshot observation IDs must be unique")
        if not self.purpose.strip() or not self.data_quality_state.strip():
            raise ValueError("snapshot purpose and data quality state are required")
        future = [
            reference.uri
            for reference in self.artifact_references
            if reference.first_available_at > self.as_of
        ]
        if future:
            raise ValueError(f"snapshot contains artifacts unavailable as_of: {future}")
        return self


class Evidence(ArtifactModel):
    claim: str = Field(min_length=1)
    supports: bool | None = None
    source_artifact_ids: tuple[UUID, ...] = ()
    source_family: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    syndication_chain: tuple[str, ...] = ()
    observed_at: datetime
    first_available_at: datetime
    transformation_lineage: tuple[str, ...] = ()
    model_version: str | None = None
    provider_route: str | None = None
    prompt_version: str | None = None
    capability_version: str | None = None
    assumptions: tuple[str, ...] = ()
    uncertainty: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    expires_at: datetime
    invalidation_conditions: tuple[str, ...] = ()

    _observed_at_aware = field_validator("observed_at")(require_aware)
    _first_available_at_aware = field_validator("first_available_at")(require_aware)
    _expires_at_aware = field_validator("expires_at")(require_aware)

    @model_validator(mode="after")
    def validate_evidence_time_order(self) -> Evidence:
        if self.first_available_at < self.observed_at:
            raise ValueError("first_available_at cannot precede observed_at")
        if self.expires_at <= self.first_available_at:
            raise ValueError("expires_at must be after first_available_at")
        if len(self.source_artifact_ids) != len(set(self.source_artifact_ids)):
            raise ValueError("evidence source artifact IDs must be unique")
        if len(self.syndication_chain) != len(set(self.syndication_chain)):
            raise ValueError("evidence syndication chain entries must be unique")
        return self


class Forecast(ArtifactModel):
    instrument: InstrumentIdentity
    snapshot_id: UUID
    cutoff: datetime
    horizon_seconds: int = Field(gt=0)
    target: str = Field(min_length=1)
    point_forecast: Decimal | None = None
    quantiles: tuple[tuple[Decimal, Decimal], ...] = ()
    distribution: str | None = None
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    abstained: bool = False
    abstention_reason: str | None = None
    model_version: str = Field(min_length=1)
    data_hash: str = Field(min_length=32)
    feature_hash: str = Field(min_length=32)
    code_hash: str = Field(min_length=32)
    calibration_version: str = Field(min_length=1)
    training_cutoff: datetime
    known_support_limits: tuple[str, ...] = ()
    failure_regimes: tuple[str, ...] = ()
    latency_ms: int = Field(ge=0)
    peak_ram_mib: int = Field(ge=0)
    peak_vram_mib: int = Field(ge=0)

    _cutoff_aware = field_validator("cutoff")(require_aware)
    _training_cutoff_aware = field_validator("training_cutoff")(require_aware)

    _data_hash = field_validator("data_hash")(_require_digest)
    _feature_hash = field_validator("feature_hash")(_require_digest)
    _code_hash = field_validator("code_hash")(_require_digest)
    _point_forecast_finite = field_validator("point_forecast")(_require_finite_decimal)

    @field_validator("distribution")
    @classmethod
    def normalize_distribution(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("forecast distribution cannot be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_forecast(self) -> Forecast:
        if self.training_cutoff > self.cutoff:
            raise ValueError("training_cutoff cannot be after forecast cutoff")
        if self.abstained and not self.abstention_reason:
            raise ValueError("abstained forecasts require abstention_reason")
        if self.abstained and (
            self.point_forecast is not None or self.quantiles or self.distribution
        ):
            raise ValueError("abstained forecasts cannot carry a forecast payload")
        if (
            not self.abstained
            and self.point_forecast is None
            and not self.quantiles
            and self.distribution is None
        ):
            raise ValueError(
                "forecast needs a point, quantile, or distribution payload unless abstained"
            )
        if self.quantiles:
            if any(
                not probability.is_finite() or not value.is_finite()
                for probability, value in self.quantiles
            ):
                raise ValueError("forecast quantiles must be finite")
            probabilities = [probability for probability, _value in self.quantiles]
            if any(
                not Decimal("0") <= probability <= Decimal("1") for probability in probabilities
            ):
                raise ValueError("forecast quantile probabilities must be between zero and one")
            if probabilities != sorted(probabilities) or len(set(probabilities)) != len(
                probabilities
            ):
                raise ValueError("forecast quantile probabilities must be strictly ordered")
        return self


class TargetPosition(ArtifactModel):
    instrument: InstrumentIdentity
    target_quantity: Decimal
    expected_return_after_costs: Decimal | None = None
    rationale_artifact_ids: tuple[UUID, ...] = ()

    _target_quantity_finite = field_validator("target_quantity")(_require_finite_decimal)
    _expected_return_finite = field_validator("expected_return_after_costs")(
        _require_finite_decimal
    )


class TargetPortfolio(ArtifactModel):
    snapshot_id: UUID
    positions: tuple[TargetPosition, ...]
    cash_target: Decimal
    construction_method: str
    expected_cost: Decimal = Field(ge=Decimal("0"))
    risk_constraints_version: str
    no_trade_comparison: str

    _cash_target_finite = field_validator("cash_target")(_require_finite_decimal)
    _expected_cost_finite = field_validator("expected_cost")(_require_finite_decimal)

    @model_validator(mode="after")
    def validate_target_contract(self) -> TargetPortfolio:
        identifiers = [position.instrument.canonical_id for position in self.positions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("target portfolio cannot contain duplicate instruments")
        if not self.construction_method.strip():
            raise ValueError("target portfolio construction_method is required")
        if not self.risk_constraints_version.strip():
            raise ValueError("target portfolio risk constraints version is required")
        if not self.no_trade_comparison.strip():
            raise ValueError("target portfolio requires a no-trade comparison")
        return self


class RiskLimit(ArtifactModel):
    name: str
    limit: Decimal = Field(ge=Decimal("0"))
    unit: str = Field(min_length=1)

    _limit_finite = field_validator("limit")(_require_finite_decimal)

    @field_validator("name", "unit")
    @classmethod
    def normalize_limit_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("risk limits require a name and unit")
        return value.strip()

    @model_validator(mode="after")
    def require_named_limit(self) -> RiskLimit:
        if not self.name.strip() or not self.unit.strip():
            raise ValueError("risk limits require a name and unit")
        return self


class RiskPolicy(ArtifactModel):
    policy_version: str
    effective_at: datetime
    hard_limits: tuple[RiskLimit, ...]
    approved_by: str

    _effective_at_aware = field_validator("effective_at")(require_aware)

    @model_validator(mode="after")
    def unique_limit_names(self) -> RiskPolicy:
        if not self.policy_version.strip() or not self.approved_by.strip():
            raise ValueError("risk policy version and approver are required")
        names = [limit.name for limit in self.hard_limits]
        if len(names) != len(set(names)):
            raise ValueError("risk limit names must be unique")
        return self


class RiskOutcome(StrEnum):
    APPROVED = "approved"
    REDUCED = "reduced"
    REJECTED = "rejected"


class RiskDecision(ArtifactModel):
    target_portfolio_id: UUID
    risk_policy_id: UUID
    outcome: RiskOutcome
    authoritative_state_hash: str = Field(min_length=64, max_length=64)
    reasons: tuple[str, ...] = ()
    reduced_positions: tuple[TargetPosition, ...] = ()

    _authoritative_state_hash = field_validator("authoritative_state_hash")(_require_digest)

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> RiskDecision:
        if self.outcome is RiskOutcome.REJECTED and not self.reasons:
            raise ValueError("rejected risk decisions require reasons")
        if self.outcome is RiskOutcome.REDUCED and not self.reduced_positions:
            raise ValueError("reduced risk decisions require reduced positions")
        if self.outcome is RiskOutcome.APPROVED and self.reduced_positions:
            raise ValueError("approved risk decisions cannot contain reduced positions")
        reduced_instruments = [
            position.instrument.canonical_id for position in self.reduced_positions
        ]
        if len(reduced_instruments) != len(set(reduced_instruments)):
            raise ValueError("reduced risk decisions cannot contain duplicate instruments")
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("risk decision reasons cannot be blank")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("risk decision reasons must be unique")
        return self


class ExecutionPlan(ArtifactModel):
    risk_decision_id: UUID
    target_portfolio_id: UUID
    policy_version: str
    instructions: tuple[str, ...]
    expires_at: datetime

    _expires_at_aware = field_validator("expires_at")(require_aware)

    @model_validator(mode="after")
    def validate_execution_plan(self) -> ExecutionPlan:
        if not self.policy_version.strip():
            raise ValueError("execution plan policy version is required")
        if not self.instructions or any(not item.strip() for item in self.instructions):
            raise ValueError("execution plan requires non-empty instructions")
        if self.expires_at <= self.created_at:
            raise ValueError("execution plan must expire after it is created")
        return self


class OrderState(StrEnum):
    CREATED = "created"
    RISK_APPROVED = "risk_approved"
    ROUTED = "routed"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    RECONCILED = "reconciled"


class Order(ArtifactModel):
    parent_intent_id: UUID
    execution_plan_id: UUID
    instrument: InstrumentIdentity
    side: str
    quantity: Decimal = Field(gt=Decimal("0"))
    order_type: str
    price: Decimal | None = Field(default=None, gt=Decimal("0"))
    time_in_force: str
    idempotency_key: str = Field(min_length=1)
    state: OrderState = OrderState.CREATED

    @field_validator("side", "order_type", "time_in_force", mode="before")
    @classmethod
    def normalize_execution_tokens(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("execution tokens must be non-empty strings")
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_price(self) -> Order:
        if self.side.lower() not in {"buy", "sell"}:
            raise ValueError("order side must be buy or sell")
        if not self.order_type.strip():
            raise ValueError("order type is required")
        if not self.time_in_force.strip():
            raise ValueError("time in force is required")
        if self.order_type.lower() in {"limit", "passive_limit"} and self.price is None:
            raise ValueError("limit orders require a positive price")
        return self

    @field_validator("idempotency_key")
    @classmethod
    def require_idempotency_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotency key cannot be blank")
        return value.strip()


class Fill(ArtifactModel):
    order_id: UUID
    venue_fill_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=Decimal("0"))
    price: Decimal = Field(gt=Decimal("0"))
    fee: Decimal = Field(ge=Decimal("0"))
    occurred_at: datetime

    _occurred_at_aware = field_validator("occurred_at")(require_aware)

    @field_validator("venue_fill_id")
    @classmethod
    def require_fill_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("venue_fill_id cannot be blank")
        return value.strip()


class Reconciliation(ArtifactModel):
    as_of: datetime
    account_state_hash: str = Field(min_length=64, max_length=64)
    order_ids: tuple[UUID, ...] = ()
    fill_ids: tuple[UUID, ...] = ()
    reconciled: bool
    discrepancies: tuple[str, ...] = ()

    _as_of_aware = field_validator("as_of")(require_aware)
    _account_state_hash = field_validator("account_state_hash")(_require_digest)

    @model_validator(mode="after")
    def validate_reconciliation_state(self) -> Reconciliation:
        if len(self.order_ids) != len(set(self.order_ids)):
            raise ValueError("reconciliation order IDs must be unique")
        if len(self.fill_ids) != len(set(self.fill_ids)):
            raise ValueError("reconciliation fill IDs must be unique")
        if self.reconciled and self.discrepancies:
            raise ValueError("a reconciled record cannot contain discrepancies")
        if not self.reconciled and not self.discrepancies:
            raise ValueError("an unreconciled record must identify discrepancies")
        return self


class Attribution(ArtifactModel):
    reconciliation_id: UUID
    data_forecast: Decimal
    allocation_selection: Decimal
    risk_overlay: Decimal
    execution_financing: Decimal
    regime_capacity: Decimal
    unexplained_residual: Decimal
    currency: str
    # Kept optional for backwards-compatible deserialization of older ledger
    # artifacts; new reconciliations always populate the total explicitly.
    total_pnl: Decimal | None = None

    _components_finite = field_validator(
        "data_forecast",
        "allocation_selection",
        "risk_overlay",
        "execution_financing",
        "regime_capacity",
        "unexplained_residual",
    )(_require_finite_decimal)
    _total_pnl_finite = field_validator("total_pnl")(_require_finite_decimal)

    @field_validator("currency")
    @classmethod
    def require_currency(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("attribution currency is required")
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_total(self) -> Attribution:
        if self.total_pnl is not None:
            components = (
                self.data_forecast,
                self.allocation_selection,
                self.risk_overlay,
                self.execution_financing,
                self.regime_capacity,
                self.unexplained_residual,
            )
            if self.total_pnl != sum(components, Decimal("0")):
                raise ValueError("attribution total P&L must equal all components")
        return self


class ModelCard(ArtifactModel):
    model_name: str
    model_version: str
    role: str
    data_hash: str = Field(min_length=32)
    code_hash: str = Field(min_length=32)
    training_cutoff: datetime
    calibration_version: str | None = None
    support_limits: tuple[str, ...] = ()
    failure_regimes: tuple[str, ...] = ()
    lifecycle_state: str = "challenger"
    model_family: str | None = None
    independent_of: tuple[str, ...] = ()
    evaluation_hash: str | None = None
    net_utility_after_costs: Decimal | None = None
    calibration_score: Decimal | None = None
    resource_envelope: str | None = None

    _training_cutoff_aware = field_validator("training_cutoff")(require_aware)

    _data_hash = field_validator("data_hash")(_require_digest)
    _code_hash = field_validator("code_hash")(_require_digest)
    _evaluation_hash = field_validator("evaluation_hash")(_optional_digest)
    _net_utility_finite = field_validator("net_utility_after_costs")(_require_finite_decimal)
    _calibration_finite = field_validator("calibration_score")(_require_finite_decimal)

    @field_validator("independent_of")
    @classmethod
    def normalize_model_ancestry(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("model ancestry entries must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def validate_model_card(self) -> ModelCard:
        if not self.model_name.strip() or not self.model_version.strip() or not self.role.strip():
            raise ValueError("model card name, version, and role are required")
        if self.model_family is not None and not self.model_family.strip():
            raise ValueError("model family cannot be blank")
        if self.resource_envelope is not None and not self.resource_envelope.strip():
            raise ValueError("model resource envelope cannot be blank")
        if self.calibration_score is not None and not Decimal(
            "0"
        ) <= self.calibration_score <= Decimal("1"):
            raise ValueError("model calibration score must be between zero and one")
        return self


class AgentRun(ArtifactModel):
    mission_id: UUID
    role: str
    mode: str
    snapshot_id: UUID
    provider: str | None = None
    model_route: str | None = None
    prompt_version: str | None = None
    tool_versions: tuple[str, ...] = ()
    input_artifact_ids: tuple[UUID, ...] = ()
    output_artifact_ids: tuple[UUID, ...] = ()
    latency_ms: int = Field(ge=0)
    token_count: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))

    @model_validator(mode="after")
    def validate_agent_run_metadata(self) -> AgentRun:
        if not self.role.strip() or not self.mode.strip():
            raise ValueError("agent runs require a role and operating mode")
        if (self.provider is None) != (self.model_route is None):
            raise ValueError("agent run provider and model route must be supplied together")
        if self.provider is not None and not self.provider.strip():
            raise ValueError("agent run provider cannot be blank")
        if self.model_route is not None and not self.model_route.strip():
            raise ValueError("agent run model route cannot be blank")
        if self.prompt_version is not None and not self.prompt_version.strip():
            raise ValueError("agent run prompt version cannot be blank")
        if any(not item.strip() for item in self.tool_versions):
            raise ValueError("agent run tool versions cannot be blank")
        if len(self.tool_versions) != len(set(self.tool_versions)):
            raise ValueError("agent run tool versions must be unique")
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("agent run input artifact IDs must be unique")
        if len(self.output_artifact_ids) != len(set(self.output_artifact_ids)):
            raise ValueError("agent run output artifact IDs must be unique")
        return self


class CapabilityLifecycle(StrEnum):
    GAP = "gap"
    SCOUT = "scout"
    PIN = "pin"
    INSPECT = "inspect"
    SANDBOX = "sandbox"
    WRAP_BUILD = "wrap_build"
    CONTRACT_TESTED = "contract_tested"
    SECURITY_TESTED = "security_tested"
    PERFORMANCE_BENCHMARKED = "performance_benchmarked"
    SHADOW = "shadow"
    ACTIVE_READ = "active_read"
    ACTIVE_WRITE_LIMITED = "active_write_limited"
    DEPRECATED = "deprecated"


class CapabilityCard(ArtifactModel):
    name: str
    capability_version: str
    lifecycle: CapabilityLifecycle = CapabilityLifecycle.GAP
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    allowed_actions: tuple[str, ...] = ()
    secrets_required: tuple[str, ...] = ()
    network_required: bool = False
    resource_envelope: str
    latency_class: str
    deterministic: bool
    source_grade: SourceGrade | None = None
    failure_modes: tuple[str, ...] = ()
    test_references: tuple[str, ...] = ()
    score: Decimal | None = None

    @field_validator("allowed_actions")
    @classmethod
    def prohibit_trading_authority(cls, actions: tuple[str, ...]) -> tuple[str, ...]:
        normalized_values = tuple(normalize_authority_action(action) for action in actions)
        normalized = set(normalized_values)
        forbidden = {action for action in normalized if is_forbidden_authority_action(action)}
        if forbidden:
            raise ValueError(f"capability cannot receive trading authority: {sorted(forbidden)}")
        if len(actions) != len(set(normalized)) or any(not action.strip() for action in actions):
            raise ValueError("capability actions must be unique and non-blank")
        return normalized_values

    @model_validator(mode="after")
    def validate_capability_metadata(self) -> CapabilityCard:
        if not self.name.strip() or not self.capability_version.strip():
            raise ValueError("capability name and version are required")
        if not self.inputs or not self.outputs:
            raise ValueError("capabilities require typed inputs and outputs")
        if not self.resource_envelope.strip() or not self.latency_class.strip():
            raise ValueError("capabilities require resource and latency metadata")
        return self


def canonical_payload(model: ArtifactModel) -> dict[str, Any]:
    """Return a deterministic JSON-compatible payload for hashing and persistence."""

    return model.model_dump(mode="json", round_trip=True)
