"""Transport-neutral human notification and approval boundaries.

Notifications carry information and approval requests; they never carry
execution authority.  This module deliberately stops at typed, hashed,
append-only contracts.  A future transport must prove authenticated human
identity before an approval can be bridged to :class:`HumanAuthorization`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .authorization import (
    ActorType,
    AuthorizationExpiryMode,
    HumanAuthorization,
    authorization_is_valid,
    normalize_action_type,
)
from .decisions import DecisionOutcome
from .hashing import canonical_sha256
from .scope import ScopeDecisionOutcome


class NotificationValidationError(ValueError):
    """Raised when a notification or approval contract fails closed."""


class NotificationClass(StrEnum):
    INFO = "INFO"
    OPPORTUNITY = "OPPORTUNITY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    RISK_WARNING = "RISK_WARNING"
    CRITICAL_INCIDENT = "CRITICAL_INCIDENT"
    EMERGENCY_ACTION_TAKEN = "EMERGENCY_ACTION_TAKEN"
    SYSTEM_HEALTH = "SYSTEM_HEALTH"


class NotificationPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HumanResponseType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    DEFER = "DEFER"
    EXPIRED = "EXPIRED"


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DEFERRED = "DEFERRED"
    EXPIRED = "EXPIRED"


class TransportKind(StrEnum):
    MOBILE_PUSH = "MOBILE_PUSH"
    DASHBOARD = "DASHBOARD"
    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"
    NULL = "NULL"
    IN_MEMORY = "IN_MEMORY"


class NotificationTiming(StrEnum):
    BEFORE_ACTION = "BEFORE_ACTION"
    AFTER_ACTION = "AFTER_ACTION"
    LOG_ONLY = "LOG_ONLY"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_TEXT_RE = re.compile(
    r"(?:api[\s_-]*key|api[\s_-]*secret|access[\s_-]*token|auth(?:entication)?[\s_-]*token|"
    r"broker[\s_-]*(?:credential|secret)|client[\s_-]*secret|password|private[\s_-]*key|"
    r"signing[\s_-]*(?:key|material)|withdrawal[\s_-]*address|webhook[\s_-]*(?:secret|token))",
    re.IGNORECASE,
)


def _timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NotificationValidationError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _digest(value: str, field_name: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise NotificationValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _safe_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise NotificationValidationError(f"{field_name} cannot be blank")
    if _SENSITIVE_TEXT_RE.search(normalized):
        raise NotificationValidationError(
            f"{field_name} contains prohibited credential or signing material"
        )
    return normalized


def _safe_refs(value: tuple[str, ...], field_name: str = "evidence_refs") -> tuple[str, ...]:
    normalized = tuple(_safe_text(item, field_name) for item in value)
    if len(normalized) != len(set(normalized)):
        raise NotificationValidationError(f"{field_name} must contain unique references")
    return normalized


def _model_hash(model: BaseModel, field_name: str) -> str:
    return canonical_sha256(model.model_dump(mode="json", exclude={field_name}))


class TransportCapabilities(_StrictModel):
    """Declared transport capability; it contains no transport credentials."""

    transport: TransportKind
    can_send_info: bool = False
    can_send_critical: bool = False
    can_receive_authenticated_approval: bool = False
    supports_interactive_actions: bool = False
    supports_encryption: bool = False
    secrets_required: bool = False

    @model_validator(mode="after")
    def validate_approval_capability(self) -> TransportCapabilities:
        if self.can_receive_authenticated_approval and not (
            self.supports_interactive_actions and self.supports_encryption
        ):
            raise NotificationValidationError(
                "authenticated approval requires interactive actions and encryption"
            )
        return self


# These are capability declarations only.  No provider is configured in V1,
# and none can receive an approval until a reviewed authentication adapter sets
# ``can_receive_authenticated_approval`` to true.
DECLARED_FUTURE_TRANSPORTS: tuple[TransportCapabilities, ...] = (
    TransportCapabilities(
        transport=TransportKind.MOBILE_PUSH,
        can_send_info=True,
        can_send_critical=True,
        supports_interactive_actions=True,
        supports_encryption=True,
        secrets_required=True,
    ),
    TransportCapabilities(
        transport=TransportKind.DASHBOARD,
        can_send_info=True,
        can_send_critical=True,
        supports_interactive_actions=True,
        supports_encryption=True,
        secrets_required=True,
    ),
    TransportCapabilities(
        transport=TransportKind.TELEGRAM,
        can_send_info=True,
        can_send_critical=True,
        supports_interactive_actions=True,
        supports_encryption=True,
        secrets_required=True,
    ),
    TransportCapabilities(
        transport=TransportKind.EMAIL,
        can_send_info=True,
        can_send_critical=True,
        supports_encryption=True,
        secrets_required=True,
    ),
)


class NotificationRequest(_StrictModel):
    """Immutable information event with no executable payload."""

    notification_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    expires_at: datetime | None = None
    notification_class: NotificationClass
    priority: NotificationPriority
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    governed_action: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=64, max_length=64)
    input_snapshot_hash: str = Field(min_length=64, max_length=64)
    evidence_refs: tuple[str, ...] = ()
    approval_request_id: UUID | None = None
    dedup_key: str = ""
    request_hash: str = ""

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None, info) -> datetime | None:
        return None if value is None else _timestamp(value, info.field_name)

    @field_validator("subject", "body")
    @classmethod
    def validate_safe_content(cls, value: str, info) -> str:
        return _safe_text(value, info.field_name)

    @field_validator("reason_code", "governed_action")
    @classmethod
    def normalize_codes(cls, value: str) -> str:
        normalized = normalize_action_type(value)
        if not normalized:
            raise NotificationValidationError("notification codes cannot be blank")
        return normalized

    @field_validator("policy_hash", "input_snapshot_hash")
    @classmethod
    def validate_hashes(cls, value: str, info) -> str:
        return _digest(value, info.field_name)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_refs(value)

    @model_validator(mode="after")
    def finalize(self) -> NotificationRequest:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise NotificationValidationError("notification expiry must be after creation")
        expected_dedup = canonical_sha256(
            {
                "notification_class": self.notification_class.value,
                "subject": self.subject,
                "reason_code": self.reason_code,
                "governed_action": self.governed_action,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "policy_hash": self.policy_hash,
            }
        )
        if self.dedup_key and self.dedup_key != expected_dedup:
            raise NotificationValidationError("dedup_key does not match notification identity")
        object.__setattr__(self, "dedup_key", expected_dedup)
        expected_hash = _model_hash(self, "request_hash")
        if self.request_hash and self.request_hash != expected_hash:
            raise NotificationValidationError("notification request_hash does not match content")
        object.__setattr__(self, "request_hash", expected_hash)
        return self


class ApprovalRequest(_StrictModel):
    """Immutable request for an explicit human decision."""

    request_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    expires_at: datetime
    action_type: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    requested_change: str = Field(min_length=1)
    current_value: str | None = None
    proposed_value: str = Field(min_length=1)
    decision_context_hash: str = Field(min_length=64, max_length=64)
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=64, max_length=64)
    governance_decision_ref: str | None = None
    trading_scope_decision_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    urgency: NotificationPriority = NotificationPriority.NORMAL
    expected_decision_window: timedelta | None = None
    human_authorization_required: bool = True
    request_hash: str = ""

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return _timestamp(value, info.field_name)

    @field_validator("action_type")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        normalized = normalize_action_type(value)
        if not normalized:
            raise NotificationValidationError("approval action_type cannot be blank")
        return normalized

    @field_validator("subject", "requested_change", "proposed_value")
    @classmethod
    def validate_safe_content(cls, value: str, info) -> str:
        return _safe_text(value, info.field_name)

    @field_validator("current_value")
    @classmethod
    def validate_current_value(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, "current_value")

    @field_validator("decision_context_hash", "policy_hash")
    @classmethod
    def validate_hashes(cls, value: str, info) -> str:
        return _digest(value, info.field_name)

    @field_validator("governance_decision_ref", "trading_scope_decision_ref")
    @classmethod
    def validate_refs(cls, value: str | None, info) -> str | None:
        return None if value is None else _safe_text(value, info.field_name)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_refs(value)

    @field_validator("expected_decision_window")
    @classmethod
    def validate_window(cls, value: timedelta | None) -> timedelta | None:
        if value is not None and value <= timedelta(0):
            raise NotificationValidationError("decision window must be positive")
        return value

    @model_validator(mode="after")
    def finalize(self) -> ApprovalRequest:
        if self.expires_at <= self.created_at:
            raise NotificationValidationError("approval expiry must be after creation")
        if self.expected_decision_window is not None:
            expected_expiry = self.created_at + self.expected_decision_window
            if self.expires_at != expected_expiry:
                raise NotificationValidationError(
                    "expires_at must equal created_at plus expected_decision_window"
                )
        if not self.human_authorization_required:
            raise NotificationValidationError(
                "approval requests always require human authorization"
            )
        expected_hash = _model_hash(self, "request_hash")
        if self.request_hash and self.request_hash != expected_hash:
            raise NotificationValidationError("approval request_hash does not match content")
        object.__setattr__(self, "request_hash", expected_hash)
        return self

    def is_expired(self, at: datetime) -> bool:
        return _timestamp(at, "approval check time") >= self.expires_at


class HumanResponse(_StrictModel):
    """A response whose authenticity is asserted by a future transport layer."""

    response_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    received_at: datetime
    actor_type: ActorType
    actor_identity: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    response: HumanResponseType
    response_channel: TransportKind
    request_hash: str = Field(min_length=64, max_length=64)
    policy_hash: str = Field(min_length=64, max_length=64)
    authentication_established: bool = False
    authentication_method: str | None = None
    response_hash: str = ""

    @field_validator("received_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _timestamp(value, "received_at")

    @field_validator("actor_identity", "authentication_method")
    @classmethod
    def validate_identity_text(cls, value: str | None, info) -> str | None:
        return None if value is None else _safe_text(value, info.field_name)

    @field_validator("action_type")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        normalized = normalize_action_type(value)
        if not normalized:
            raise NotificationValidationError("response action_type cannot be blank")
        return normalized

    @field_validator("request_hash", "policy_hash")
    @classmethod
    def validate_hashes(cls, value: str, info) -> str:
        return _digest(value, info.field_name)

    @model_validator(mode="after")
    def finalize(self) -> HumanResponse:
        if self.authentication_established and not self.authentication_method:
            raise NotificationValidationError(
                "authenticated responses require an authentication method"
            )
        expected_hash = _model_hash(self, "response_hash")
        if self.response_hash and self.response_hash != expected_hash:
            raise NotificationValidationError("response_hash does not match content")
        object.__setattr__(self, "response_hash", expected_hash)
        return self


class ApprovalResolution(_StrictModel):
    request_id: UUID
    state: ApprovalState
    request_hash: str
    response_id: UUID | None = None
    human_authorization_hash: str | None = None
    resolved_at: datetime
    resolution_hash: str = ""

    @field_validator("resolved_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _timestamp(value, "resolved_at")

    @field_validator("request_hash", "human_authorization_hash")
    @classmethod
    def validate_hashes(cls, value: str | None, info) -> str | None:
        return None if value is None else _digest(value, info.field_name)

    @model_validator(mode="after")
    def finalize(self) -> ApprovalResolution:
        expected_hash = _model_hash(self, "resolution_hash")
        if self.resolution_hash and self.resolution_hash != expected_hash:
            raise NotificationValidationError("resolution_hash does not match content")
        object.__setattr__(self, "resolution_hash", expected_hash)
        return self


class ApprovalAuditRecord(_StrictModel):
    """One append-only response/expiry observation."""

    request_id: UUID
    request_hash: str
    response_id: UUID | None = None
    response: HumanResponseType
    state: ApprovalState
    actor_type: ActorType
    actor_identity: str
    recorded_at: datetime
    human_authorization_hash: str | None = None
    record_hash: str = ""

    @field_validator("request_hash", "human_authorization_hash")
    @classmethod
    def validate_hashes(cls, value: str | None, info) -> str | None:
        return None if value is None else _digest(value, info.field_name)

    @field_validator("recorded_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _timestamp(value, "recorded_at")

    @field_validator("actor_identity")
    @classmethod
    def validate_actor_identity(cls, value: str) -> str:
        return _safe_text(value, "actor_identity")

    @model_validator(mode="after")
    def finalize(self) -> ApprovalAuditRecord:
        expected_hash = _model_hash(self, "record_hash")
        if self.record_hash and self.record_hash != expected_hash:
            raise NotificationValidationError("record_hash does not match content")
        object.__setattr__(self, "record_hash", expected_hash)
        return self


def _approval_state(response: HumanResponseType) -> ApprovalState:
    return {
        HumanResponseType.APPROVE: ApprovalState.APPROVED,
        HumanResponseType.REJECT: ApprovalState.REJECTED,
        HumanResponseType.ACKNOWLEDGE: ApprovalState.ACKNOWLEDGED,
        HumanResponseType.DEFER: ApprovalState.DEFERRED,
        HumanResponseType.EXPIRED: ApprovalState.EXPIRED,
    }[response]


def _assert_model_hash(model: BaseModel, field_name: str, expected: str) -> None:
    if getattr(model, field_name) != expected or _model_hash(model, field_name) != expected:
        raise NotificationValidationError(f"{field_name} does not match immutable content")


def bridge_approval(
    request: ApprovalRequest,
    response: HumanResponse,
    *,
    transport_capabilities: TransportCapabilities,
    expected_policy_id: str,
    expected_policy_version: str,
    expected_policy_hash: str,
    repository_commit: str | None = None,
    existing_authorization: HumanAuthorization | None = None,
    prior_terminal_response: bool = False,
    at: datetime | None = None,
) -> HumanAuthorization:
    """Validate an approval before producing a human authorization artifact.

    ``authentication_established`` is intentionally an interface assertion,
    not a cryptographic implementation.  A provider integration must set it
    only after authenticating a human response; otherwise this function fails
    closed.
    """

    now = _timestamp(at or response.received_at, "approval validation time")
    _assert_model_hash(request, "request_hash", request.request_hash)
    _assert_model_hash(response, "response_hash", response.response_hash)
    if prior_terminal_response:
        raise NotificationValidationError("approval request already has a terminal response")
    if request.is_expired(now):
        raise NotificationValidationError("approval request is expired")
    if response.received_at < request.created_at:
        raise NotificationValidationError("response cannot precede approval request")
    if response.request_id != request.request_id:
        raise NotificationValidationError("response targets a different approval request")
    if response.request_hash != request.request_hash:
        raise NotificationValidationError("response request_hash does not match request")
    if response.action_type != request.action_type:
        raise NotificationValidationError("response action does not match request")
    if response.policy_hash != request.policy_hash:
        raise NotificationValidationError("response policy hash does not match request")
    if (
        request.policy_id != expected_policy_id
        or request.policy_version != expected_policy_version
        or request.policy_hash != expected_policy_hash
    ):
        raise NotificationValidationError("approval request is bound to a stale policy identity")
    if response.actor_type is not ActorType.HUMAN:
        raise NotificationValidationError("only a HUMAN response can approve an action")
    if response.response is not HumanResponseType.APPROVE:
        raise NotificationValidationError("only APPROVE responses can bridge authorization")
    if not response.authentication_established:
        raise NotificationValidationError("human response authenticity is not established")
    if response.response_channel is not transport_capabilities.transport:
        raise NotificationValidationError("response channel does not match transport")
    if not transport_capabilities.can_receive_authenticated_approval:
        raise NotificationValidationError(
            "transport is not authorized to receive authenticated approvals"
        )

    if existing_authorization is None:
        if repository_commit is None:
            raise NotificationValidationError(
                "repository_commit is required to create an authorization artifact"
            )
        authorization = HumanAuthorization(
            created_at=response.received_at,
            actor_type=ActorType.HUMAN,
            actor_identity=response.actor_identity,
            action_type=request.action_type,
            target=request.subject,
            previous_value=request.current_value,
            approved_value=request.proposed_value,
            scope=("notification-approval", request.policy_id),
            expires_at=request.expires_at,
            expiry_mode=AuthorizationExpiryMode.FIXED_EXPIRATION,
            policy_version=request.policy_version,
            repository_commit=repository_commit,
            reason=request.requested_change,
            evidence_refs=request.evidence_refs,
        )
    else:
        authorization = existing_authorization
        if not authorization_is_valid(
            authorization,
            at=now,
            action_type=request.action_type,
            policy_version=request.policy_version,
        ):
            raise NotificationValidationError("existing authorization is not valid for request")
        if (
            authorization.actor_type is not ActorType.HUMAN
            or authorization.target != request.subject
            or authorization.approved_value != request.proposed_value
            or authorization.previous_value != request.current_value
            or authorization.expires_at != request.expires_at
        ):
            raise NotificationValidationError("authorization content does not match request")
    return authorization


class ApprovalLedger:
    """Small append-only approval ledger for development and future adapters."""

    _TERMINAL_STATES = frozenset(
        {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED}
    )

    def __init__(self) -> None:
        self._requests: dict[UUID, ApprovalRequest] = {}
        self._records: list[ApprovalAuditRecord] = []

    @property
    def requests(self) -> tuple[ApprovalRequest, ...]:
        return tuple(self._requests.values())

    @property
    def records(self) -> tuple[ApprovalAuditRecord, ...]:
        return tuple(self._records)

    def append_request(self, request: ApprovalRequest) -> None:
        _assert_model_hash(request, "request_hash", request.request_hash)
        if request.request_id in self._requests:
            raise NotificationValidationError("approval request_id already exists")
        self._requests[request.request_id] = request

    def _request(self, request_id: UUID) -> ApprovalRequest:
        try:
            return self._requests[request_id]
        except KeyError as exc:
            raise NotificationValidationError("approval request does not exist") from exc

    def _latest(self, request_id: UUID) -> ApprovalAuditRecord | None:
        for record in reversed(self._records):
            if record.request_id == request_id:
                return record
        return None

    def is_terminal(self, request_id: UUID) -> bool:
        latest = self._latest(request_id)
        return latest is not None and latest.state in self._TERMINAL_STATES

    def status(self, request_id: UUID, *, at: datetime | None = None) -> ApprovalState:
        request = self._request(request_id)
        latest = self._latest(request_id)
        if latest is not None:
            return latest.state
        if at is not None and request.is_expired(at):
            return ApprovalState.EXPIRED
        return ApprovalState.PENDING

    def expire(self, request_id: UUID, *, at: datetime) -> ApprovalResolution:
        request = self._request(request_id)
        now = _timestamp(at, "approval expiry time")
        if self.is_terminal(request_id):
            raise NotificationValidationError("approval request already has a terminal response")
        if now < request.expires_at:
            raise NotificationValidationError("approval cannot expire before its deadline")
        record = ApprovalAuditRecord(
            request_id=request.request_id,
            request_hash=request.request_hash,
            response=HumanResponseType.EXPIRED,
            state=ApprovalState.EXPIRED,
            actor_type=ActorType.SYSTEM,
            actor_identity="approval-ledger",
            recorded_at=now,
        )
        self._records.append(record)
        return self._resolution(request, record)

    def record_response(
        self,
        response: HumanResponse,
        *,
        transport_capabilities: TransportCapabilities,
        expected_policy_id: str,
        expected_policy_version: str,
        expected_policy_hash: str,
        repository_commit: str | None = None,
        existing_authorization: HumanAuthorization | None = None,
        at: datetime | None = None,
    ) -> ApprovalResolution:
        request = self._request(response.request_id)
        _assert_model_hash(response, "response_hash", response.response_hash)
        if self.is_terminal(request.request_id):
            raise NotificationValidationError("approval request already has a terminal response")
        if response.request_hash != request.request_hash:
            raise NotificationValidationError("response request_hash does not match request")
        if response.action_type != request.action_type:
            raise NotificationValidationError("response action does not match request")
        if response.policy_hash != request.policy_hash:
            raise NotificationValidationError("response policy hash does not match request")
        if (
            request.policy_id != expected_policy_id
            or request.policy_version != expected_policy_version
            or request.policy_hash != expected_policy_hash
        ):
            raise NotificationValidationError(
                "approval request is bound to a stale policy identity"
            )
        now = _timestamp(at or response.received_at, "response processing time")
        if now < request.created_at:
            raise NotificationValidationError("response cannot precede approval request")
        if request.is_expired(now):
            record = ApprovalAuditRecord(
                request_id=request.request_id,
                request_hash=request.request_hash,
                response_id=response.response_id,
                response=response.response,
                state=ApprovalState.EXPIRED,
                actor_type=response.actor_type,
                actor_identity=response.actor_identity,
                recorded_at=now,
            )
            self._records.append(record)
            return self._resolution(request, record)

        if (
            response.response
            in {
                HumanResponseType.APPROVE,
                HumanResponseType.REJECT,
                HumanResponseType.ACKNOWLEDGE,
                HumanResponseType.DEFER,
            }
            and response.actor_type is not ActorType.HUMAN
        ):
            raise NotificationValidationError(
                "only a HUMAN response can resolve an approval request"
            )

        authorization = None
        if response.response is HumanResponseType.APPROVE:
            authorization = bridge_approval(
                request,
                response,
                transport_capabilities=transport_capabilities,
                expected_policy_id=expected_policy_id,
                expected_policy_version=expected_policy_version,
                expected_policy_hash=expected_policy_hash,
                repository_commit=repository_commit,
                existing_authorization=existing_authorization,
                at=now,
            )
        elif response.response is HumanResponseType.EXPIRED:
            raise NotificationValidationError("EXPIRED is only valid at or after request expiry")

        record = ApprovalAuditRecord(
            request_id=request.request_id,
            request_hash=request.request_hash,
            response_id=response.response_id,
            response=response.response,
            state=_approval_state(response.response),
            actor_type=response.actor_type,
            actor_identity=response.actor_identity,
            recorded_at=now,
            human_authorization_hash=authorization.authorization_hash if authorization else None,
        )
        self._records.append(record)
        return self._resolution(request, record)

    @staticmethod
    def _resolution(request: ApprovalRequest, record: ApprovalAuditRecord) -> ApprovalResolution:
        return ApprovalResolution(
            request_id=request.request_id,
            state=record.state,
            request_hash=request.request_hash,
            response_id=record.response_id,
            human_authorization_hash=record.human_authorization_hash,
            resolved_at=record.recorded_at,
        )


_PRIORITY_RANK = {
    NotificationPriority.LOW: 0,
    NotificationPriority.NORMAL: 1,
    NotificationPriority.HIGH: 2,
    NotificationPriority.CRITICAL: 3,
}


class NotificationDeduplicator:
    """Suppress unresolved repeats but allow deterministic severity escalation."""

    def __init__(self) -> None:
        self._seen: dict[str, NotificationPriority] = {}

    def should_deliver(self, notification: NotificationRequest) -> bool:
        previous = self._seen.get(notification.dedup_key)
        if (
            previous is not None
            and _PRIORITY_RANK[notification.priority] <= _PRIORITY_RANK[previous]
        ):
            return False
        self._seen[notification.dedup_key] = notification.priority
        return True

    def clear(self, dedup_key: str) -> None:
        self._seen.pop(dedup_key, None)


class TransportReceipt(_StrictModel):
    transport: TransportKind
    accepted: bool
    delivered: bool
    recorded_at: datetime
    notification_id: UUID | None = None
    approval_request_id: UUID | None = None

    @field_validator("recorded_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _timestamp(value, "transport receipt time")


class NotificationDeliveryRecord(_StrictModel):
    """Append-only local delivery observation; it has no execution effect."""

    notification_id: UUID
    dedup_key: str
    transport: TransportKind
    accepted: bool
    delivered: bool
    suppressed: bool = False
    recorded_at: datetime
    record_hash: str = ""

    @field_validator("dedup_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _digest(value, "dedup_key")

    @field_validator("recorded_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _timestamp(value, "delivery record time")

    @model_validator(mode="after")
    def finalize(self) -> NotificationDeliveryRecord:
        expected_hash = _model_hash(self, "record_hash")
        if self.record_hash and self.record_hash != expected_hash:
            raise NotificationValidationError("delivery record hash does not match content")
        object.__setattr__(self, "record_hash", expected_hash)
        return self


def recommended_routes(notification_class: NotificationClass) -> tuple[TransportKind, ...]:
    """Return reviewed route classes without selecting a provider or secret."""

    if notification_class in {
        NotificationClass.CRITICAL_INCIDENT,
        NotificationClass.EMERGENCY_ACTION_TAKEN,
        NotificationClass.APPROVAL_REQUIRED,
        NotificationClass.RISK_WARNING,
    }:
        return (TransportKind.MOBILE_PUSH, TransportKind.DASHBOARD)
    return (TransportKind.DASHBOARD,)


class NotificationTransport(Protocol):
    """Minimal notification interface; intentionally no order methods exist."""

    @property
    def kind(self) -> TransportKind: ...

    @property
    def capabilities(self) -> TransportCapabilities: ...

    def send_notification(self, notification: NotificationRequest) -> TransportReceipt: ...

    def send_approval_request(self, request: ApprovalRequest) -> TransportReceipt: ...

    def ingest_response(self, response: HumanResponse) -> HumanResponse: ...


class NullTransport:
    """Safe no-op transport for tests and installations without a provider."""

    kind = TransportKind.NULL
    capabilities = TransportCapabilities(transport=TransportKind.NULL)

    def send_notification(self, notification: NotificationRequest) -> TransportReceipt:
        return TransportReceipt(
            transport=self.kind,
            accepted=False,
            delivered=False,
            recorded_at=datetime.now(UTC),
            notification_id=notification.notification_id,
        )

    def send_approval_request(self, request: ApprovalRequest) -> TransportReceipt:
        return TransportReceipt(
            transport=self.kind,
            accepted=False,
            delivered=False,
            recorded_at=datetime.now(UTC),
            approval_request_id=request.request_id,
        )

    def ingest_response(self, response: HumanResponse) -> HumanResponse:
        raise NotificationValidationError("NullTransport cannot authenticate a human response")


class InMemoryTransport:
    """Deterministic local transport with no network or execution authority."""

    def __init__(
        self,
        *,
        kind: TransportKind = TransportKind.IN_MEMORY,
        capabilities: TransportCapabilities | None = None,
    ) -> None:
        self.kind = kind
        self.capabilities = capabilities or TransportCapabilities(
            transport=kind,
            can_send_info=True,
            can_send_critical=True,
        )
        if self.capabilities.transport is not kind:
            raise NotificationValidationError("transport capability identity does not match kind")
        self.notifications: list[NotificationRequest] = []
        self.approval_requests: list[ApprovalRequest] = []
        self.responses: list[HumanResponse] = []

    def send_notification(self, notification: NotificationRequest) -> TransportReceipt:
        permitted = (
            self.capabilities.can_send_critical
            if notification.priority is NotificationPriority.CRITICAL
            else self.capabilities.can_send_info
        )
        if permitted:
            self.notifications.append(notification)
        return TransportReceipt(
            transport=self.kind,
            accepted=permitted,
            delivered=permitted,
            recorded_at=datetime.now(UTC),
            notification_id=notification.notification_id,
        )

    def send_approval_request(self, request: ApprovalRequest) -> TransportReceipt:
        if self.capabilities.can_send_info:
            self.approval_requests.append(request)
        return TransportReceipt(
            transport=self.kind,
            accepted=self.capabilities.can_send_info,
            delivered=self.capabilities.can_send_info,
            recorded_at=datetime.now(UTC),
            approval_request_id=request.request_id,
        )

    def ingest_response(self, response: HumanResponse) -> HumanResponse:
        if not self.capabilities.can_receive_authenticated_approval:
            raise NotificationValidationError(
                "transport cannot receive authenticated approval responses"
            )
        self.responses.append(response)
        return response


class NotificationRouter:
    """Routes typed events and records only delivery observations."""

    def __init__(
        self,
        transports: Iterable[NotificationTransport],
        *,
        deduplicator: NotificationDeduplicator | None = None,
    ) -> None:
        self._transports = tuple(transports)
        self._deduplicator = deduplicator or NotificationDeduplicator()
        self._delivery_records: list[NotificationDeliveryRecord] = []

    @property
    def delivery_records(self) -> tuple[NotificationDeliveryRecord, ...]:
        return tuple(self._delivery_records)

    def publish(
        self,
        notification: NotificationRequest,
        *,
        routes: tuple[TransportKind, ...] | None = None,
    ) -> tuple[TransportReceipt, ...]:
        _assert_model_hash(notification, "request_hash", notification.request_hash)
        route_kinds = routes or recommended_routes(notification.notification_class)
        if not self._deduplicator.should_deliver(notification):
            self._delivery_records.append(
                NotificationDeliveryRecord(
                    notification_id=notification.notification_id,
                    dedup_key=notification.dedup_key,
                    transport=TransportKind.NULL,
                    accepted=False,
                    delivered=False,
                    suppressed=True,
                    recorded_at=datetime.now(UTC),
                )
            )
            return ()
        receipts: list[TransportReceipt] = []
        for transport in self._transports:
            if transport.kind not in route_kinds:
                continue
            receipt = transport.send_notification(notification)
            receipts.append(receipt)
            self._delivery_records.append(
                NotificationDeliveryRecord(
                    notification_id=notification.notification_id,
                    dedup_key=notification.dedup_key,
                    transport=receipt.transport,
                    accepted=receipt.accepted,
                    delivered=receipt.delivered,
                    recorded_at=receipt.recorded_at,
                )
            )
        return tuple(receipts)

    def request_approval(
        self,
        request: ApprovalRequest,
        *,
        routes: tuple[TransportKind, ...] | None = None,
    ) -> tuple[NotificationRequest, tuple[TransportReceipt, ...]]:
        _assert_model_hash(request, "request_hash", request.request_hash)
        notification = approval_notification(request)
        route_kinds = routes or recommended_routes(notification.notification_class)
        if not self._deduplicator.should_deliver(notification):
            self._delivery_records.append(
                NotificationDeliveryRecord(
                    notification_id=notification.notification_id,
                    dedup_key=notification.dedup_key,
                    transport=TransportKind.NULL,
                    accepted=False,
                    delivered=False,
                    suppressed=True,
                    recorded_at=datetime.now(UTC),
                )
            )
            return notification, ()
        receipts: list[TransportReceipt] = []
        for transport in self._transports:
            if transport.kind not in route_kinds:
                continue
            receipt = transport.send_approval_request(request)
            receipts.append(receipt)
            self._delivery_records.append(
                NotificationDeliveryRecord(
                    notification_id=notification.notification_id,
                    dedup_key=notification.dedup_key,
                    transport=receipt.transport,
                    accepted=receipt.accepted,
                    delivered=receipt.delivered,
                    recorded_at=receipt.recorded_at,
                )
            )
        return notification, receipts


def approval_notification(request: ApprovalRequest) -> NotificationRequest:
    """Render an approval request as a safe notification event."""

    body_parts = [request.requested_change]
    if request.current_value is not None:
        body_parts.append(f"Current: {request.current_value}")
    body_parts.append(f"Proposed: {request.proposed_value}")
    return NotificationRequest(
        created_at=request.created_at,
        expires_at=request.expires_at,
        notification_class=NotificationClass.APPROVAL_REQUIRED,
        priority=request.urgency,
        subject=f"Approval required: {request.action_type} for {request.subject}",
        body="; ".join(body_parts),
        reason_code="HUMAN_APPROVAL_REQUIRED",
        governed_action=request.action_type,
        policy_id=request.policy_id,
        policy_version=request.policy_version,
        policy_hash=request.policy_hash,
        input_snapshot_hash=request.decision_context_hash,
        evidence_refs=request.evidence_refs,
        approval_request_id=request.request_id,
    )


class ActionNotificationPlan(_StrictModel):
    """Describes notification timing; it does not authorize an action."""

    action_allowed: bool
    notification_required_before_action: bool
    timing: NotificationTiming
    notification_class: NotificationClass | None = None
    priority: NotificationPriority = NotificationPriority.LOW
    routes: tuple[TransportKind, ...] = ()
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _safe_text(value, "reason")


def plan_action_notification(
    governance_outcome: DecisionOutcome,
    scope_outcome: ScopeDecisionOutcome,
    *,
    risk_reducing: bool = False,
    urgent: bool = False,
) -> ActionNotificationPlan:
    """Keep notification off the execution critical path.

    The caller must still execute the existing GovernanceDecision,
    TradingScopeDecision, RiskKernel, and OMS gates.  This helper only states
    whether a notification is informational, a prerequisite human request, or
    an after-action record.
    """

    scope_allows = scope_outcome is ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE
    if (
        risk_reducing
        and governance_outcome
        in {
            DecisionOutcome.DERISK_ONLY,
            DecisionOutcome.ALLOW_AUTONOMOUS,
        }
        and scope_allows
    ):
        return ActionNotificationPlan(
            action_allowed=True,
            notification_required_before_action=False,
            timing=NotificationTiming.AFTER_ACTION,
            notification_class=NotificationClass.EMERGENCY_ACTION_TAKEN,
            priority=NotificationPriority.CRITICAL,
            routes=recommended_routes(NotificationClass.EMERGENCY_ACTION_TAKEN),
            reason="deterministic protective action precedes notification",
        )
    if governance_outcome is DecisionOutcome.ALLOW_AUTONOMOUS and scope_allows:
        notification_class = NotificationClass.OPPORTUNITY
        priority = NotificationPriority.HIGH if urgent else NotificationPriority.NORMAL
        return ActionNotificationPlan(
            action_allowed=True,
            notification_required_before_action=False,
            timing=NotificationTiming.AFTER_ACTION,
            notification_class=notification_class,
            priority=priority,
            routes=recommended_routes(notification_class),
            reason="governance and scope allow action; notification is after-action",
        )
    if governance_outcome is DecisionOutcome.REQUIRE_HUMAN or scope_outcome in {
        ScopeDecisionOutcome.REQUIRE_HUMAN,
        ScopeDecisionOutcome.REQUIRE_HUMAN_AND_TECHNICAL_GATE,
        ScopeDecisionOutcome.REQUIRE_TECHNICAL_GATE,
    }:
        return ActionNotificationPlan(
            action_allowed=False,
            notification_required_before_action=True,
            timing=NotificationTiming.BEFORE_ACTION,
            notification_class=NotificationClass.APPROVAL_REQUIRED,
            priority=NotificationPriority.HIGH if urgent else NotificationPriority.NORMAL,
            routes=recommended_routes(NotificationClass.APPROVAL_REQUIRED),
            reason="human or technical approval is required before new risk",
        )
    if governance_outcome is DecisionOutcome.ABSTAIN:
        return ActionNotificationPlan(
            action_allowed=False,
            notification_required_before_action=False,
            timing=NotificationTiming.LOG_ONLY,
            reason="abstention is logged without approval-notification spam",
        )
    return ActionNotificationPlan(
        action_allowed=False,
        notification_required_before_action=False,
        timing=NotificationTiming.LOG_ONLY,
        notification_class=NotificationClass.RISK_WARNING,
        priority=NotificationPriority.HIGH,
        routes=recommended_routes(NotificationClass.RISK_WARNING),
        reason="deterministic governance or scope block remains authoritative",
    )
