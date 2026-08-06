from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from advisorai.api.dashboard import (
    CommandKind,
    DashboardCommandRequest,
    DashboardProjection,
    build_demo_overview,
    create_dashboard_app,
)
from advisorai.api.security import AuthConfiguration, LoginRateLimiter, SessionStore, TotpService
from advisorai.ledger import SqliteLedgers


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
    login = next(route for route in app.routes if getattr(route, "path", None) == "/api/v1/auth/login")
    overview = next(
        route for route in app.routes if getattr(route, "path", None) == "/api/v1/dashboard/overview"
    )

    assert [item.name for item in login.dependant.body_params] == ["payload"]
    assert not login.dependant.query_params
    assert not overview.dependant.query_params
