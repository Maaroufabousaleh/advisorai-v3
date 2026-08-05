"""Policy-controlled mission mode selection and budget assignment."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.config import MissionMode
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class MissionKind(StrEnum):
    TRADE = "trade"
    RECOMMENDATION = "recommendation"
    RESEARCH = "research"
    BUILD = "build"
    RECOVERY = "recovery"


class MissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID = Field(default_factory=uuid4)
    kind: MissionKind
    user_text: str = Field(min_length=1)
    requested_mode: MissionMode | None = None
    high_value: bool = False
    unresolved_disagreement: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("user_text")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("mission user_text cannot be blank")
        return value.strip()

    @field_validator("created_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mission timestamp must include a timezone")
        return value.astimezone(UTC)


class RoutedMission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    mode: MissionMode
    remote_llm_budget: int = Field(ge=0)
    role_budget: int = Field(ge=0)
    allow_hermes: bool
    allow_browser: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("mission route requires a reason")
        return value

    @model_validator(mode="after")
    def enforce_mode_capabilities(self) -> RoutedMission:
        if self.mode is MissionMode.TRADE_FAST and (
            self.remote_llm_budget or self.allow_hermes or self.allow_browser
        ):
            raise ValueError("Trade/Fast routes cannot admit remote, Hermes, or browser work")
        if self.allow_hermes and self.mode not in {
            MissionMode.DEEP,
            MissionMode.BUILDER,
            MissionMode.RECOVERY,
        }:
            raise ValueError("Hermes is allowed only in Deep, Builder, or Recovery mode")
        if self.allow_browser:
            raise ValueError("browser work requires a separate browser service admission")
        remote_limits = {
            MissionMode.TRADE_FAST: 0,
            MissionMode.STANDARD: 2,
            MissionMode.DEEP: 4,
            MissionMode.BUILDER: 0,
            MissionMode.RECOVERY: 0,
        }
        if self.remote_llm_budget > remote_limits[self.mode]:
            raise ValueError("mission remote LLM budget exceeds its mode envelope")
        role_limits = {
            MissionMode.TRADE_FAST: 2,
            MissionMode.STANDARD: 8,
            MissionMode.DEEP: 16,
            MissionMode.BUILDER: 4,
            MissionMode.RECOVERY: 3,
        }
        if self.role_budget > role_limits[self.mode]:
            raise ValueError("mission role budget exceeds its mode envelope")
        return self


class WorkCandidate(BaseModel):
    """One optional wave candidate scored by expected information value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    expected_uncertainty_reduction: Decimal = Field(ge=Decimal("0"))
    decision_value: Decimal = Field(ge=Decimal("0"))
    latency_ms: int = Field(gt=0)
    resource_cost: Decimal = Field(gt=0)
    api_cost: Decimal = Field(ge=Decimal("0"))

    @field_validator("name")
    @classmethod
    def require_candidate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("work candidates require a name")
        return value.strip()

    @model_validator(mode="after")
    def require_finite_costs(self) -> WorkCandidate:
        values = (
            self.expected_uncertainty_reduction,
            self.decision_value,
            self.resource_cost,
            self.api_cost,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("work candidate scoring inputs must be finite")
        return self

    @property
    def priority_score(self) -> Decimal:
        # Deterministic work has no API charge; use one unit as the neutral
        # denominator rather than allowing a free candidate to divide by zero.
        api_factor = self.api_cost if self.api_cost > 0 else Decimal("1")
        return (
            self.expected_uncertainty_reduction
            * self.decision_value
            / (Decimal(self.latency_ms) * self.resource_cost * api_factor)
        )


class WorkScheduler:
    """Rank and bound optional work before a council wave is admitted."""

    @staticmethod
    def rank(candidates: tuple[WorkCandidate, ...]) -> tuple[WorkCandidate, ...]:
        names = [candidate.name for candidate in candidates]
        if len(names) != len(set(names)):
            raise ValueError("work candidate names must be unique")
        return tuple(sorted(candidates, key=lambda item: (-item.priority_score, item.name)))

    @classmethod
    def select(
        cls, candidates: tuple[WorkCandidate, ...], *, budget: int
    ) -> tuple[WorkCandidate, ...]:
        if budget < 0:
            raise ValueError("work scheduler budget cannot be negative")
        return cls.rank(candidates)[:budget]


class MissionRouter:
    """Deterministic policy selects mode; an LLM cannot select its own budget."""

    def __init__(self, ledgers: SqliteLedgers | None = None) -> None:
        self.ledgers = ledgers

    def route(self, request: MissionRequest) -> RoutedMission:
        if request.kind is MissionKind.RECOVERY:
            mode = MissionMode.RECOVERY
            reason = "recovery policy takes precedence"
        elif request.kind is MissionKind.BUILD:
            mode = MissionMode.BUILDER
            reason = "builder policy requires isolated build mode"
        elif request.kind is MissionKind.TRADE:
            mode = MissionMode.TRADE_FAST
            reason = "trade mission is bounded to the deterministic fast path"
        elif request.requested_mode is not None:
            mode = request.requested_mode
            reason = "explicit policy-approved requested mode"
        elif request.high_value or request.unresolved_disagreement:
            mode = MissionMode.DEEP
            reason = "high value or unresolved disagreement requires deep evidence"
        else:
            mode = MissionMode.STANDARD
            reason = "ordinary mission uses standard evidence budget"
        budgets = {
            MissionMode.TRADE_FAST: (0, 2, False, False),
            MissionMode.STANDARD: (2, 8, False, False),
            MissionMode.DEEP: (4, 16, True, False),
            MissionMode.BUILDER: (0, 4, True, False),
            # Recovery may use one isolated Hermes diagnosis task, but never a
            # remote decision route or browser write path.
            MissionMode.RECOVERY: (0, 3, True, False),
        }
        remote_llm_budget, role_budget, allow_hermes, allow_browser = budgets[mode]
        routed = RoutedMission(
            mission_id=request.mission_id,
            mode=mode,
            remote_llm_budget=remote_llm_budget,
            role_budget=role_budget,
            allow_hermes=allow_hermes,
            allow_browser=allow_browser,
            reason=reason,
        )
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MISSION,
                    event_type="mission_routed",
                    idempotency_key=f"mission-route:{request.mission_id}",
                    payload={
                        "request": request.model_dump(mode="json", round_trip=True),
                        "routed": routed.model_dump(mode="json", round_trip=True),
                    },
                )
            )
        return routed

    def routed_missions(self) -> tuple[RoutedMission, ...]:
        """Rebuild the latest deterministic routes from the mission ledger."""

        if self.ledgers is None:
            return ()
        result: list[RoutedMission] = []
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            if event.event_type != "mission_routed":
                continue
            payload = event.payload.get("routed")
            if not isinstance(payload, dict):
                raise ValueError("mission ledger contains an invalid routed payload")
            result.append(RoutedMission.model_validate(payload))
        return tuple(result)
