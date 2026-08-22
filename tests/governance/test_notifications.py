from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from advisorai.governance import (
    DECLARED_FUTURE_TRANSPORTS,
    ActorType,
    ApprovalLedger,
    ApprovalRequest,
    ApprovalState,
    DecisionOutcome,
    HumanResponse,
    HumanResponseType,
    InMemoryTransport,
    NotificationClass,
    NotificationPriority,
    NotificationRequest,
    NotificationRouter,
    NotificationTiming,
    NotificationValidationError,
    ScopeDecisionOutcome,
    TransportCapabilities,
    TransportKind,
    approval_notification,
    plan_action_notification,
    recommended_routes,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
POLICY_ID = "human-governance"
POLICY_VERSION = "v1"
POLICY_HASH = "a" * 64
CONTEXT_HASH = "b" * 64
REPOSITORY_COMMIT = "c" * 40

APPROVAL_CAPABILITIES = TransportCapabilities(
    transport=TransportKind.DASHBOARD,
    can_send_info=True,
    can_send_critical=True,
    can_receive_authenticated_approval=True,
    supports_interactive_actions=True,
    supports_encryption=True,
)


def _approval_request(**overrides) -> ApprovalRequest:
    values: dict[str, object] = {
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
        "expected_decision_window": timedelta(minutes=10),
        "action_type": "PROMOTE_MODEL",
        "subject": "chronos-2-small",
        "requested_change": "Promote the qualified candidate to the paper role",
        "current_value": "CHALLENGER",
        "proposed_value": "PAPER",
        "decision_context_hash": CONTEXT_HASH,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "governance_decision_ref": "governance-decision-1",
        "trading_scope_decision_ref": "scope-decision-1",
        "evidence_refs": ("evidence://decision-1",),
        "urgency": "NORMAL",
    }
    values.update(overrides)
    return ApprovalRequest(**values)


def _response(
    request: ApprovalRequest,
    *,
    response: HumanResponseType = HumanResponseType.APPROVE,
    actor_type: ActorType = ActorType.HUMAN,
    policy_hash: str = POLICY_HASH,
    received_at: datetime = NOW + timedelta(minutes=1),
    authenticated: bool = True,
    request_id=None,
    response_channel: TransportKind = TransportKind.DASHBOARD,
) -> HumanResponse:
    return HumanResponse(
        request_id=request.request_id if request_id is None else request_id,
        received_at=received_at,
        actor_type=actor_type,
        actor_identity="operator-1",
        action_type=request.action_type,
        response=response,
        response_channel=response_channel,
        request_hash=request.request_hash,
        policy_hash=policy_hash,
        authentication_established=authenticated,
        authentication_method="future-dashboard-identity" if authenticated else None,
    )


def _notification(*, priority: str = "NORMAL", notification_class: str = "SYSTEM_HEALTH"):
    return NotificationRequest(
        created_at=NOW,
        notification_class=notification_class,
        priority=priority,
        subject="market-data-source",
        body="Source health changed",
        reason_code="DATA_DEGRADED",
        governed_action="OBSERVE",
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
        policy_hash=POLICY_HASH,
        input_snapshot_hash=CONTEXT_HASH,
    )


def test_urgent_autonomous_action_does_not_wait_for_notification():
    plan = plan_action_notification(
        DecisionOutcome.ALLOW_AUTONOMOUS,
        ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE,
        urgent=True,
    )

    assert plan.action_allowed
    assert not plan.notification_required_before_action
    assert plan.timing is NotificationTiming.AFTER_ACTION
    assert plan.notification_class is NotificationClass.OPPORTUNITY


def test_emergency_protection_precedes_notification():
    plan = plan_action_notification(
        DecisionOutcome.DERISK_ONLY,
        ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE,
        risk_reducing=True,
    )

    assert plan.action_allowed
    assert not plan.notification_required_before_action
    assert plan.timing is NotificationTiming.AFTER_ACTION
    assert plan.notification_class is NotificationClass.EMERGENCY_ACTION_TAKEN
    assert plan.priority.value == "CRITICAL"


def test_require_human_creates_approval_notification_without_execution_authority():
    plan = plan_action_notification(
        DecisionOutcome.REQUIRE_HUMAN,
        ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE,
    )
    request = _approval_request()
    notification = approval_notification(request)

    assert not plan.action_allowed
    assert plan.notification_required_before_action
    assert plan.timing is NotificationTiming.BEFORE_ACTION
    assert notification.notification_class is NotificationClass.APPROVAL_REQUIRED
    assert notification.approval_request_id == request.request_id


def test_abstention_is_log_only_and_does_not_create_approval_spam():
    plan = plan_action_notification(
        DecisionOutcome.ABSTAIN,
        ScopeDecisionOutcome.HARD_BLOCK,
    )

    assert not plan.action_allowed
    assert plan.timing is NotificationTiming.LOG_ONLY
    assert plan.notification_class is None
    assert plan.routes == ()


def test_human_approval_bridges_only_after_authenticated_validation():
    request = _approval_request()
    ledger = ApprovalLedger()
    ledger.append_request(request)

    resolution = ledger.record_response(
        _response(request),
        transport_capabilities=APPROVAL_CAPABILITIES,
        expected_policy_id=POLICY_ID,
        expected_policy_version=POLICY_VERSION,
        expected_policy_hash=POLICY_HASH,
        repository_commit=REPOSITORY_COMMIT,
    )

    assert resolution.state is ApprovalState.APPROVED
    assert resolution.human_authorization_hash
    assert ledger.is_terminal(request.request_id)
    assert ledger.records[-1].actor_type is ActorType.HUMAN


def test_rejection_records_no_authorization():
    request = _approval_request()
    ledger = ApprovalLedger()
    ledger.append_request(request)

    resolution = ledger.record_response(
        _response(request, response=HumanResponseType.REJECT),
        transport_capabilities=APPROVAL_CAPABILITIES,
        expected_policy_id=POLICY_ID,
        expected_policy_version=POLICY_VERSION,
        expected_policy_hash=POLICY_HASH,
    )

    assert resolution.state is ApprovalState.REJECTED
    assert resolution.human_authorization_hash is None


@pytest.mark.parametrize("actor_type", [ActorType.AGENT, ActorType.LLM])
def test_agent_or_llm_response_cannot_approve(actor_type: ActorType):
    request = _approval_request()
    ledger = ApprovalLedger()
    ledger.append_request(request)

    with pytest.raises(NotificationValidationError, match="HUMAN"):
        ledger.record_response(
            _response(request, actor_type=actor_type),
            transport_capabilities=APPROVAL_CAPABILITIES,
            expected_policy_id=POLICY_ID,
            expected_policy_version=POLICY_VERSION,
            expected_policy_hash=POLICY_HASH,
            repository_commit=REPOSITORY_COMMIT,
        )


@pytest.mark.parametrize("actor_type", [ActorType.AGENT, ActorType.LLM])
def test_agent_or_llm_cannot_record_rejection_or_deferral(actor_type: ActorType):
    request = _approval_request()
    ledger = ApprovalLedger()
    ledger.append_request(request)

    with pytest.raises(NotificationValidationError, match="HUMAN"):
        ledger.record_response(
            _response(request, actor_type=actor_type, response=HumanResponseType.REJECT),
            transport_capabilities=APPROVAL_CAPABILITIES,
            expected_policy_id=POLICY_ID,
            expected_policy_version=POLICY_VERSION,
            expected_policy_hash=POLICY_HASH,
        )


def test_unauthenticated_human_response_fails_closed():
    request = _approval_request()
    with pytest.raises(NotificationValidationError, match="authenticity"):
        from advisorai.governance.notifications import bridge_approval

        bridge_approval(
            request,
            _response(request, authenticated=False),
            transport_capabilities=APPROVAL_CAPABILITIES,
            expected_policy_id=POLICY_ID,
            expected_policy_version=POLICY_VERSION,
            expected_policy_hash=POLICY_HASH,
            repository_commit=REPOSITORY_COMMIT,
        )


def test_response_channel_must_match_authenticated_transport():
    request = _approval_request()
    response = _response(request, response_channel=TransportKind.TELEGRAM)

    with pytest.raises(NotificationValidationError, match="channel"):
        from advisorai.governance.notifications import bridge_approval

        bridge_approval(
            request,
            response,
            transport_capabilities=APPROVAL_CAPABILITIES,
            expected_policy_id=POLICY_ID,
            expected_policy_version=POLICY_VERSION,
            expected_policy_hash=POLICY_HASH,
            repository_commit=REPOSITORY_COMMIT,
        )


def test_expired_request_cannot_authorize_new_risk():
    request = _approval_request()
    ledger = ApprovalLedger()
    ledger.append_request(request)

    resolution = ledger.record_response(
        _response(request, received_at=NOW + timedelta(minutes=11)),
        transport_capabilities=APPROVAL_CAPABILITIES,
        expected_policy_id=POLICY_ID,
        expected_policy_version=POLICY_VERSION,
        expected_policy_hash=POLICY_HASH,
        repository_commit=REPOSITORY_COMMIT,
        at=NOW + timedelta(minutes=11),
    )

    assert resolution.state is ApprovalState.EXPIRED
    assert resolution.human_authorization_hash is None
    assert ledger.is_terminal(request.request_id)


def test_wrong_request_and_stale_policy_are_rejected():
    request = _approval_request()
    ledger = ApprovalLedger()
    ledger.append_request(request)
    with pytest.raises(NotificationValidationError, match="does not exist"):
        ledger.record_response(
            _response(request, request_id=uuid4()),
            transport_capabilities=APPROVAL_CAPABILITIES,
            expected_policy_id=POLICY_ID,
            expected_policy_version=POLICY_VERSION,
            expected_policy_hash=POLICY_HASH,
        )

    with pytest.raises(NotificationValidationError, match="policy hash"):
        ledger.record_response(
            _response(request, policy_hash="d" * 64),
            transport_capabilities=APPROVAL_CAPABILITIES,
            expected_policy_id=POLICY_ID,
            expected_policy_version=POLICY_VERSION,
            expected_policy_hash=POLICY_HASH,
            repository_commit=REPOSITORY_COMMIT,
        )


def test_malformed_request_hash_is_rejected_at_construction():
    with pytest.raises(ValidationError, match="request_hash"):
        _approval_request(request_hash="d" * 64)


def test_duplicate_alerts_are_suppressed_and_priority_escalates():
    dashboard = InMemoryTransport(
        kind=TransportKind.DASHBOARD,
        capabilities=TransportCapabilities(
            transport=TransportKind.DASHBOARD,
            can_send_info=True,
            can_send_critical=True,
        ),
    )
    router = NotificationRouter((dashboard,))

    assert len(router.publish(_notification())) == 1
    assert router.publish(_notification()) == ()
    assert len(router.publish(_notification(priority="HIGH"))) == 1
    assert len(dashboard.notifications) == 2
    assert router.delivery_records[1].suppressed


def test_critical_routing_requests_push_and_dashboard():
    mobile = InMemoryTransport(
        kind=TransportKind.MOBILE_PUSH,
        capabilities=TransportCapabilities(
            transport=TransportKind.MOBILE_PUSH,
            can_send_info=True,
            can_send_critical=True,
        ),
    )
    dashboard = InMemoryTransport(
        kind=TransportKind.DASHBOARD,
        capabilities=TransportCapabilities(
            transport=TransportKind.DASHBOARD,
            can_send_info=True,
            can_send_critical=True,
        ),
    )
    router = NotificationRouter((mobile, dashboard))

    receipts = router.publish(
        _notification(priority="CRITICAL", notification_class="CRITICAL_INCIDENT")
    )

    assert recommended_routes(NotificationClass.CRITICAL_INCIDENT) == (
        TransportKind.MOBILE_PUSH,
        TransportKind.DASHBOARD,
    )
    assert {receipt.transport for receipt in receipts} == {
        TransportKind.MOBILE_PUSH,
        TransportKind.DASHBOARD,
    }


def test_approval_routing_uses_transport_approval_method():
    dashboard = InMemoryTransport(
        kind=TransportKind.DASHBOARD,
        capabilities=TransportCapabilities(
            transport=TransportKind.DASHBOARD,
            can_send_info=True,
            can_send_critical=True,
        ),
    )
    router = NotificationRouter((dashboard,))

    _, receipts = router.request_approval(_approval_request())

    assert len(receipts) == 1
    assert len(dashboard.approval_requests) == 1
    assert dashboard.approval_requests[0].action_type == "PROMOTE_MODEL"
    assert not dashboard.notifications


def test_transport_has_no_order_or_policy_mutation_authority():
    transport = InMemoryTransport()

    assert not hasattr(transport, "submit_order")
    assert not hasattr(transport, "cancel_order")
    assert not hasattr(transport, "modify_risk_kernel")
    assert not hasattr(transport, "modify_oms")


def test_approval_capability_requires_interactive_encrypted_transport():
    with pytest.raises(ValidationError, match="interactive actions"):
        TransportCapabilities(
            transport=TransportKind.TELEGRAM,
            can_receive_authenticated_approval=True,
        )


def test_future_provider_metadata_is_declared_but_not_authoritative():
    assert {capability.transport for capability in DECLARED_FUTURE_TRANSPORTS} == {
        TransportKind.MOBILE_PUSH,
        TransportKind.DASHBOARD,
        TransportKind.TELEGRAM,
        TransportKind.EMAIL,
    }
    assert all(
        not capability.can_receive_authenticated_approval
        for capability in DECLARED_FUTURE_TRANSPORTS
    )


def test_sensitive_notification_content_is_rejected():
    with pytest.raises(ValidationError, match="credential"):
        _notification_body_with_secret()


def _notification_body_with_secret() -> NotificationRequest:
    return NotificationRequest(
        created_at=NOW,
        notification_class=NotificationClass.INFO,
        priority=NotificationPriority.LOW,
        subject="operator notice",
        body="api_key=do-not-store",
        reason_code="INFO",
        governed_action="OBSERVE",
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
        policy_hash=POLICY_HASH,
        input_snapshot_hash=CONTEXT_HASH,
    )
