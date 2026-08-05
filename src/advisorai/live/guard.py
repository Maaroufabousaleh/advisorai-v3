"""Phase 10 gates; authorization is bounded, explicit, and auditable."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import Order, RiskDecision, RiskOutcome
from advisorai.execution.account import AccountState
from advisorai.execution.risk import KillSwitch, RiskKernel, RiskMarketState
from advisorai.gates import PhaseGateRegistry
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.soak import SoakGate


class LiveAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_id: UUID = Field(default_factory=uuid4)
    human_approver: str
    approved_at: datetime
    allowed_instruments: tuple[str, ...]
    fixed_loss_budget: Decimal = Field(gt=0)
    max_order_notional: Decimal = Field(gt=0)
    risk_policy_hash: str = Field(min_length=64, max_length=64)
    rollback_condition: str
    no_simultaneous_expansion: bool
    ai_services_can_be_stopped: bool
    owner: str | None = None
    venue: str | None = None
    expires_at: datetime | None = None
    approval_reference: str | None = None

    @field_validator("approved_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("live approval timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("risk_policy_hash")
    @classmethod
    def require_policy_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("risk_policy_hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("live authorization expiry must include a timezone")
        return value.astimezone(UTC)

    @field_validator("owner", "venue", "approval_reference")
    @classmethod
    def normalize_optional_identity(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("live authorization identity fields cannot be blank")
        return value.strip() if value is not None else None

    @field_validator("allowed_instruments")
    @classmethod
    def normalize_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("live approval instruments cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("live approval instruments must be unique")
        return normalized

    @model_validator(mode="after")
    def require_bounded_scope(self) -> LiveAuthorization:
        if not self.human_approver.strip():
            raise ValueError("live approval requires a named human approver")
        if not self.allowed_instruments:
            raise ValueError("live approval must name at least one instrument")
        if not self.rollback_condition.strip():
            raise ValueError("live approval requires a rollback condition")
        if self.expires_at is not None and self.expires_at <= self.approved_at:
            raise ValueError("live authorization expiry must be after approval")
        return self

    @property
    def effective_owner(self) -> str:
        return self.owner or self.human_approver

    def is_valid_at(self, at: datetime) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("live authorization check time must include a timezone")
        at = at.astimezone(UTC)
        return self.approved_at <= at and (self.expires_at is None or at < self.expires_at)


class LiveGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    reasons: tuple[str, ...] = ()
    authorization_id: UUID | None = None

    @model_validator(mode="after")
    def validate_gate_consistency(self) -> LiveGateResult:
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("live gate reasons cannot be blank")
        if self.passed and self.reasons:
            raise ValueError("a passed live gate cannot contain rejection reasons")
        if self.passed and self.authorization_id is None:
            raise ValueError("a passed live gate requires an authorization identity")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("live gate reasons must be unique")
        return self


class OfflineSafetyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_llm_stopped: bool
    hermes_stopped: bool
    browser_stopped: bool
    research_workers_stopped: bool
    cancel_exit_reconcile_risk_operational: bool

    @property
    def passed(self) -> bool:
        return all(self.model_dump().values())


class LiveReadinessGate:
    def evaluate(
        self,
        *,
        soak: SoakGate,
        authorization: LiveAuthorization | None,
        current_reconciliation_clean: bool,
        offline_safety: OfflineSafetyCheck,
        phase_gates: PhaseGateRegistry | None = None,
        evaluation_at: datetime | None = None,
    ) -> LiveGateResult:
        if evaluation_at is None:
            evaluation_at = datetime.now(UTC)
        elif evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("live readiness evaluation timestamp must include a timezone")
        else:
            evaluation_at = evaluation_at.astimezone(UTC)
        reasons: list[str] = []
        if not soak.passed:
            reasons.append("paper_soak_gate_not_passed")
        if phase_gates is not None and not phase_gates.is_admitted(7, at=evaluation_at):
            reasons.append("phase_7_soak_gate_not_admitted")
        if authorization is None:
            reasons.append("explicit_human_authorization_missing")
        else:
            if not authorization.is_valid_at(evaluation_at):
                reasons.append("live_authorization_expired_or_not_yet_active")
            if not authorization.no_simultaneous_expansion:
                reasons.append("simultaneous_expansion_not_frozen")
            if not authorization.ai_services_can_be_stopped:
                reasons.append("AI-offline safety not authorized")
        if not current_reconciliation_clean:
            reasons.append("current_reconciliation_not_clean")
        if not offline_safety.passed:
            reasons.append("offline_safety_check_failed")
        return LiveGateResult(
            passed=not reasons,
            reasons=tuple(reasons),
            authorization_id=authorization.authorization_id if authorization else None,
        )


class LiveOperatingState(StrEnum):
    PAPER = "paper"
    READY = "ready"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


class LiveControlStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: LiveOperatingState
    authorization_id: UUID | None = None
    changed_at: datetime
    rollback_reason: str | None = None

    @field_validator("changed_at")
    @classmethod
    def require_status_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("live control status timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("rollback_reason")
    @classmethod
    def normalize_rollback_reason(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("rollback reason cannot be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_rollback_reason_for_rollback(self) -> LiveControlStatus:
        if self.state is LiveOperatingState.ROLLED_BACK and self.rollback_reason is None:
            raise ValueError("rolled-back live status requires a reason")
        if self.state is not LiveOperatingState.ROLLED_BACK and self.rollback_reason is not None:
            raise ValueError("only rolled-back live status may contain a rollback reason")
        return self


class LiveControlPlane:
    """Durable paper/ready/live/rollback state for the explicit Phase 10 gate.

    The control plane never creates orders.  It records the human-approved
    authorization and keeps the default state paper; transitioning to active
    requires a passed readiness result and, when supplied, an admitted Phase 10
    phase record.  A rollback is monotonic back to paper until a new explicit
    readiness evaluation is performed.
    """

    def __init__(
        self,
        *,
        ledgers: SqliteLedgers | None = None,
        phase_gates: PhaseGateRegistry | None = None,
        readiness_gate: LiveReadinessGate | None = None,
    ) -> None:
        self.ledgers = ledgers
        self.phase_gates = phase_gates
        self.readiness_gate = readiness_gate or LiveReadinessGate()
        self.authorization: LiveAuthorization | None = None
        self._status = LiveControlStatus(
            state=LiveOperatingState.PAPER,
            changed_at=datetime.now(UTC),
        )
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self.ledgers is not None
        for event in self.ledgers.events(LedgerNamespace.INCIDENT):
            if event.event_type == "live_authorization_recorded":
                payload = event.payload.get("authorization")
                if not isinstance(payload, dict):
                    raise ValueError("live authorization ledger contains invalid payload")
                self.authorization = LiveAuthorization.model_validate(payload)
            elif event.event_type == "live_state_changed":
                payload = event.payload.get("status")
                if not isinstance(payload, dict):
                    raise ValueError("live state ledger contains invalid payload")
                status = LiveControlStatus.model_validate(payload)
                if status.state is LiveOperatingState.ACTIVE and (
                    self._status.state is not LiveOperatingState.READY
                ):
                    raise ValueError("live ledger contains an active state without readiness")
                if status.state in {LiveOperatingState.READY, LiveOperatingState.ACTIVE}:
                    if self.authorization is None:
                        raise ValueError("live ledger state requires a recorded authorization")
                    if status.authorization_id != self.authorization.authorization_id:
                        raise ValueError("live ledger state authorization does not match approval")
                self._status = status

    def record_authorization(self, authorization: LiveAuthorization) -> LiveAuthorization:
        if self.authorization is not None and self.authorization != authorization:
            raise ValueError("live authorization is immutable once recorded")
        self.authorization = authorization
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.INCIDENT,
                    event_type="live_authorization_recorded",
                    idempotency_key=f"live-authorization:{authorization.authorization_id}",
                    payload={
                        "authorization": authorization.model_dump(mode="json", round_trip=True)
                    },
                )
            )
        return authorization

    def evaluate_readiness(
        self,
        *,
        soak: SoakGate,
        current_reconciliation_clean: bool,
        offline_safety: OfflineSafetyCheck,
        evaluation_at: datetime | None = None,
    ) -> LiveGateResult:
        result = self.readiness_gate.evaluate(
            soak=soak,
            authorization=self.authorization,
            current_reconciliation_clean=current_reconciliation_clean,
            offline_safety=offline_safety,
            phase_gates=self.phase_gates,
            evaluation_at=evaluation_at,
        )
        if result.passed:
            self._transition(LiveOperatingState.READY, authorization_id=result.authorization_id)
        return result

    def start(self, *, evaluation_at: datetime | None = None) -> LiveControlStatus:
        if self.authorization is None:
            raise PermissionError("live start requires explicit human authorization")
        if evaluation_at is None:
            evaluation_at = datetime.now(UTC)
        elif evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("live start evaluation timestamp must include a timezone")
        else:
            evaluation_at = evaluation_at.astimezone(UTC)
        if not self.authorization.is_valid_at(evaluation_at):
            raise PermissionError("live authorization is expired or not yet active")
        if self.phase_gates is not None and not self.phase_gates.is_admitted(10, at=evaluation_at):
            raise PermissionError("live start requires an admitted Phase 10 gate")
        if self._status.state is not LiveOperatingState.READY:
            raise PermissionError("live start requires a passed readiness evaluation")
        return self._transition(LiveOperatingState.ACTIVE)

    def rollback(self, reason: str) -> LiveControlStatus:
        if not reason.strip():
            raise ValueError("live rollback requires a reason")
        return self._transition(
            LiveOperatingState.ROLLED_BACK,
            rollback_reason=reason.strip(),
        )

    def return_to_paper(self) -> LiveControlStatus:
        return self._transition(LiveOperatingState.PAPER)

    def status(self) -> LiveControlStatus:
        return self._status

    def _transition(
        self,
        state: LiveOperatingState,
        *,
        authorization_id: UUID | None = None,
        rollback_reason: str | None = None,
    ) -> LiveControlStatus:
        if (
            state is LiveOperatingState.ACTIVE
            and self._status.state is not LiveOperatingState.READY
        ):
            raise PermissionError("live active state must follow ready state")
        status = LiveControlStatus(
            state=state,
            authorization_id=authorization_id
            if authorization_id is not None
            else self._status.authorization_id,
            changed_at=datetime.now(UTC),
            rollback_reason=rollback_reason,
        )
        self._status = status
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.INCIDENT,
                    event_type="live_state_changed",
                    idempotency_key=(
                        f"live-state:{status.state.value}:{status.authorization_id}:"
                        f"{status.changed_at.isoformat()}"
                    ),
                    payload={"status": status.model_dump(mode="json", round_trip=True)},
                )
            )
        return status


class ControlledLiveOrderGuard:
    """Final bounded-order guard; it cannot create an order or loosen RiskKernel."""

    def __init__(
        self, authorization: LiveAuthorization, kill_switch: KillSwitch | None = None
    ) -> None:
        self.authorization = authorization
        self.kill_switch = kill_switch or KillSwitch()

    def approve(
        self,
        *,
        order: Order,
        account: AccountState,
        market: RiskMarketState,
        policy,
        risk_decision: RiskDecision,
        evaluation_at: datetime | None = None,
    ) -> LiveGateResult:
        if evaluation_at is None:
            evaluation_at = datetime.now(UTC)
        elif evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("live order evaluation timestamp must include a timezone")
        else:
            evaluation_at = evaluation_at.astimezone(UTC)
        reasons: list[str] = []
        if not self.authorization.is_valid_at(evaluation_at):
            reasons.append("live_authorization_expired_or_not_yet_active")
        if risk_decision.outcome is not RiskOutcome.APPROVED:
            reasons.append("risk_decision_not_approved")
        if risk_decision.risk_policy_id != policy.artifact_id:
            reasons.append("risk_decision_policy_mismatch")
        account_hash = account.snapshot().state_hash
        expected_hashes = {
            hashlib.sha256(f"{account_hash}:{market.effective_hash}".encode()).hexdigest()
        }
        if risk_decision.authoritative_state_hash not in expected_hashes:
            reasons.append("risk_decision_state_hash_mismatch")
        if policy.canonical_hash() != self.authorization.risk_policy_hash:
            reasons.append("live_authorization_policy_hash_mismatch")
        if order.instrument.canonical_id not in self.authorization.allowed_instruments:
            reasons.append("instrument_not_authorized")
        if (
            self.authorization.venue is not None
            and order.instrument.venue != self.authorization.venue
        ):
            reasons.append("venue_not_authorized")
        if (
            order.price is None
            or order.price * order.quantity > self.authorization.max_order_notional
        ):
            reasons.append("order_notional_exceeds_live_budget")
        realized_loss = max(
            Decimal("0"),
            -account.realized_pnl,
            -account.daily_realized_pnl,
            -account.rolling_realized_pnl,
            account.drawdown(),
        )
        if realized_loss > self.authorization.fixed_loss_budget:
            reasons.append("fixed_loss_budget_exceeded")
        check = RiskKernel(self.kill_switch).check_order(
            order=order, account=account, market=market, policy=policy
        )
        if not check.approved:
            reasons.extend(check.reasons)
        return LiveGateResult(
            passed=not reasons,
            reasons=tuple(reasons),
            authorization_id=self.authorization.authorization_id,
        )
