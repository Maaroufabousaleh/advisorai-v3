from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from advisorai.api.dashboard import (
    CommandKind,
    DashboardCommandRequest,
    DashboardProjection,
    build_demo_overview,
    build_ledger_overview,
    create_dashboard_app,
)
from advisorai.api.security import AuthConfiguration, LoginRateLimiter, SessionStore, TotpService
from advisorai.config import ConfigBundleStore
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.observability import Incident, IncidentLedger, IncidentSeverity


def test_demo_projection_is_explicitly_synthetic_and_live_locked():
    overview = build_demo_overview(datetime(2026, 8, 5, 15, 0, tzinfo=UTC))

    assert overview.synthetic
    assert overview.environment.value == "paper_testnet"
    assert overview.live_readiness.state == "paper only"
    assert len(overview.live_readiness.blockers) == 3
    assert overview.status.synthetic


def test_sensitive_commands_require_confirmation_and_step_up():
    with pytest.raises(ValidationError, match="explicit confirmation"):
        DashboardCommandRequest(
            command=CommandKind.HALT_PAPER,
            idempotency_key="halt-command-1234",
            reason="safety test",
        )

    with pytest.raises(ValidationError, match="step-up"):
        DashboardCommandRequest(
            command=CommandKind.SET_MODE,
            idempotency_key="mode-command-1234",
            reason="switch to deep",
            requested_mode="deep",
            confirmed=True,
        )


def test_control_projection_is_idempotent_and_never_leaves_paper_state():
    projection = DashboardProjection(build_demo_overview())
    request = DashboardCommandRequest(
        command=CommandKind.HALT_PAPER,
        idempotency_key="halt-command-5678",
        reason="operator safety test",
        confirmed=True,
        step_up_token="test-step-up",
    )

    first = projection.execute(request, actor="owner")
    second = projection.execute(request, actor="owner")

    assert first == second
    assert projection.overview().status.kill_switch == "engaged"
    assert projection.overview().status.environment.value == "paper_testnet"
    assert first.safe_state == "paper_only"


def test_dashboard_halt_is_forwarded_to_runtime_control_when_bound():
    class RuntimeControl:
        def __init__(self):
            self.halted = []

        def halt(self, reason):
            self.halted.append(reason)

        def resume(self, *, approved_by):
            self.halted.pop()

    runtime = RuntimeControl()
    projection = DashboardProjection(build_demo_overview(), runtime=runtime)
    request = DashboardCommandRequest(
        command=CommandKind.HALT_PAPER,
        idempotency_key="runtime-halt-1234",
        reason="forward safety halt",
        confirmed=True,
        step_up_token="test-step-up",
    )
    projection.execute(request, actor="owner")
    assert runtime.halted == ["forward safety halt"]


def test_control_receipt_is_rehydrated_from_authoritative_ledger(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "dashboard.sqlite3")
    request = DashboardCommandRequest(
        command=CommandKind.HALT_PAPER,
        idempotency_key="durable-halt-1234",
        reason="durable safety test",
        confirmed=True,
        step_up_token="test-step-up",
    )
    first = DashboardProjection(build_demo_overview(), ledgers=ledgers).execute(
        request, actor="owner"
    )
    restarted = DashboardProjection(build_demo_overview(), ledgers=ledgers)

    assert restarted.execute(request, actor="owner") == first
    assert restarted.overview().audit[0].event_type == "halt_paper"
    assert restarted.overview().status.kill_switch == "engaged"


def test_ledger_dashboard_halt_is_consumed_by_a_restarted_runtime(tmp_path: Path):
    from tests.runtime.test_paper_runtime import _runtime

    ledgers = SqliteLedgers(tmp_path / "dashboard-runtime.sqlite3")
    request = DashboardCommandRequest(
        command=CommandKind.HALT_PAPER,
        idempotency_key="runtime-durable-halt-1234",
        reason="dashboard safety halt",
        confirmed=True,
        step_up_token="test-step-up",
    )
    receipt = DashboardProjection(ledgers=ledgers).execute(request, actor="owner")
    assert receipt.status == "accepted"

    runtime, cutoff, _, _ = _runtime(
        tmp_path, admitted=True, database_name="dashboard-runtime.sqlite3"
    )
    result = runtime.run_once(cutoff)

    assert result.stage.value == "abstained"
    assert runtime.risk_kernel.kill_switch.tripped
    assert not runtime.orders.orders


def test_ledger_dashboard_projection_refreshes_after_new_events(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "dashboard-refresh.sqlite3")
    projection = DashboardProjection(ledgers=ledgers)
    assert projection.overview().metrics[1].value == "0"

    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.ORDER,
            event_type="order_created",
            idempotency_key="dashboard-refresh-order",
            payload={"artifact": {}},
        )
    )

    assert projection.overview().metrics[1].value == "1"


def test_ledger_dashboard_projects_only_the_latest_incident_revision(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "dashboard-incidents.sqlite3")
    timestamp = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    incident = Incident(
        severity=IncidentSeverity.HIGH,
        owner="operator",
        summary="venue outage",
        runbook="reconcile venue",
        containment="halt paper",
        opened_at=timestamp,
    )
    incident_ledger = IncidentLedger(ledgers)
    incident_ledger.record(incident)
    incident_ledger.close(
        incident.incident_id,
        root_cause="provider outage",
        corrective_test="outage fixture",
        rollback_link="runbook://paper",
        closed_at=timestamp,
    )

    overview = build_ledger_overview(ledgers, timestamp)

    assert overview.metrics[2].value == "0"
    assert overview.status.reconciliation == "no run recorded"
    assert len(overview.incidents) == 1
    assert overview.incidents[0].status == "closed"


def test_ledger_projection_is_explicitly_non_synthetic_and_does_not_invent_pnl(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "dashboard.sqlite3")
    overview = build_ledger_overview(ledgers, datetime(2026, 8, 5, 15, 0, tzinfo=UTC))
    assert not overview.synthetic
    assert overview.metrics[-1].value == "not calculated"
    assert overview.status.api_state == "ledger-backed"


def test_ledger_projection_exposes_recorded_positions_and_service_catalog(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "dashboard.sqlite3")
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="fill_applied",
            idempotency_key="fill:dashboard-1",
            payload={
                "instrument_id": "BTC-PERP",
                "side": "buy",
                "quantity": "2",
                "price": "100",
            },
        )
    )
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="mark_applied",
            idempotency_key="mark:dashboard-1",
            payload={"instrument_id": "BTC-PERP", "price": "105"},
        )
    )
    overview = build_ledger_overview(ledgers, datetime(2026, 8, 5, 15, 0, tzinfo=UTC))
    assert overview.exposures[0].instrument == "BTC-PERP"
    assert overview.exposures[0].notional == "210.00000000"
    assert overview.exposures[0].pnl == "not calculated"
    assert any(service.name == "market-node" for service in overview.services)


def test_dashboard_config_proposal_stages_and_rollback_activates_known_bundle(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "dashboard.sqlite3")
    bundle_store = ConfigBundleStore(tmp_path / "config")
    initial = bundle_store.create({"mode": "standard", "risk_policy": "risk-v1"})
    bundle_store.activate(initial.content_hash, actor="owner", reason="initial fixture")
    projection = DashboardProjection(
        build_demo_overview(datetime(2026, 8, 5, 15, 0, tzinfo=UTC)),
        ledgers=ledgers,
        config_store=bundle_store,
    )
    proposal = DashboardCommandRequest(
        command=CommandKind.PROPOSE_CONFIG,
        idempotency_key="config-proposal-1234",
        reason="stage a reviewed mode change",
        confirmed=True,
        step_up_token="step-up-token",
        config_patch={"mode": "deep"},
    )
    staged = projection.execute(proposal, actor="owner")
    assert staged.status == "accepted"
    assert staged.config_hash is not None
    assert bundle_store.active().content["mode"] == "standard"
    assert bundle_store.get(staged.config_hash).content["mode"] == "deep"

    rollback = DashboardCommandRequest(
        command=CommandKind.ROLLBACK_CONFIG,
        idempotency_key="config-rollback-1234",
        reason="restore the approved baseline",
        confirmed=True,
        step_up_token="step-up-token",
        config_patch={"content_hash": initial.content_hash},
    )
    receipt = projection.execute(rollback, actor="owner")
    assert receipt.status == "accepted"
    assert bundle_store.active().content == initial.content


def test_rollback_command_requires_an_explicit_bundle_hash():
    with pytest.raises(ValidationError, match="content_hash"):
        DashboardCommandRequest(
            command=CommandKind.ROLLBACK_CONFIG,
            idempotency_key="config-rollback-invalid",
            reason="missing target",
            confirmed=True,
            step_up_token="step-up-token",
        )


def test_totp_accepts_current_window_and_rejects_wrong_code():
    secret = TotpService.new_secret()
    now = datetime(2026, 8, 5, 15, 0, 15, tzinfo=UTC)
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    import base64

    code = TotpService._code(base64.b32decode(padded), int(now.timestamp() // 30))

    assert TotpService.verify(secret, code, at=now)
    assert not TotpService.verify(secret, "000000", at=now)


def test_session_store_expires_idle_sessions():
    config = AuthConfiguration(session_ttl_seconds=120, idle_ttl_seconds=60)
    store = SessionStore(config)
    start = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    session_id, csrf = store.create("owner", now=start)

    assert store.get(session_id, now=start) is not None
    assert store.csrf_matches(session_id, csrf)
    assert store.get(session_id, now=datetime(2026, 8, 5, 15, 1, 1, tzinfo=UTC)) is None


def test_session_store_step_up_is_session_bound_and_one_time():
    config = AuthConfiguration(session_ttl_seconds=120, idle_ttl_seconds=60, step_up_ttl_seconds=30)
    store = SessionStore(config)
    start = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    session_id, _ = store.create("owner", now=start)
    other_session_id, _ = store.create("owner", now=start)

    token, expires_at = store.issue_step_up(session_id, now=start)
    assert expires_at > start
    assert not store.consume_step_up(other_session_id, token, now=start)
    assert store.consume_step_up(session_id, token, now=start)
    assert not store.consume_step_up(session_id, token, now=start)

    token, _ = store.issue_step_up(session_id, now=start)
    assert not store.consume_step_up(session_id, token, now=start.replace(minute=1))


def test_login_rate_limiter_blocks_and_resets():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=30, block_seconds=60)
    assert limiter.allowed("127.0.0.1", now=0)
    limiter.record_failure("127.0.0.1", now=0)
    assert limiter.allowed("127.0.0.1", now=1)
    limiter.record_failure("127.0.0.1", now=1)
    assert not limiter.allowed("127.0.0.1", now=2)
    assert limiter.retry_after("127.0.0.1", now=2) == 59
    limiter.reset("127.0.0.1")
    assert limiter.allowed("127.0.0.1", now=2)


def test_fastapi_route_contract_keeps_login_json_and_request_context():
    pytest.importorskip("fastapi")
    app = create_dashboard_app(config=AuthConfiguration(auth_required=False))
    login = next(
        route for route in app.routes if getattr(route, "path", None) == "/api/v1/auth/login"
    )
    overview = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/dashboard/overview"
    )

    assert [item.name for item in login.dependant.body_params] == ["payload"]
    assert not login.dependant.query_params
    assert not overview.dependant.query_params
