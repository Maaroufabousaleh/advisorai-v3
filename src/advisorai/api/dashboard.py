"""Typed read and command boundary for the AdvisorAI operator console.

The projection is deliberately small and explicit.  It can be backed by the
existing ledgers/services in production, while the default projection provides
an honest synthetic paper snapshot so the UI can be developed and reviewed
before live transports exist.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisorai.api.security import (
    AuthConfiguration,
    LoginRateLimiter,
    PasswordService,
    Principal,
    SessionStore,
    TotpService,
    configured_password_hash,
    configured_totp_secret,
)
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class DashboardEnvironment(StrEnum):
    PAPER_TESTNET = "paper_testnet"
    LIVE_LOCKED = "live_locked"


class SystemTone(StrEnum):
    POSITIVE = "positive"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    NEUTRAL = "neutral"


class DashboardStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: DashboardEnvironment
    operating_mode: str = Field(min_length=1)
    kill_switch: str = Field(min_length=1)
    reconciliation: str = Field(min_length=1)
    data_freshness_seconds: int = Field(ge=0)
    resource_headroom_gib: float = Field(ge=0)
    last_ledger_event_at: datetime
    api_state: str = Field(min_length=1)
    synthetic: bool = False

    @model_validator(mode="after")
    def require_aware_ledger_time(self) -> DashboardStatus:
        if self.last_ledger_event_at.tzinfo is None or self.last_ledger_event_at.utcoffset() is None:
            raise ValueError("dashboard ledger timestamps must include a timezone")
        return self


class DashboardMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)
    delta: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    tone: SystemTone


class EquityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime
    value: float


class ExposureRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    asset_class: str = Field(min_length=1)
    side: str = Field(min_length=1)
    notional: str = Field(min_length=1)
    weight: str = Field(min_length=1)
    pnl: str = Field(min_length=1)
    tone: SystemTone
    mark: str = Field(min_length=1)


class RiskLimitView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    used: str = Field(min_length=1)
    limit: str = Field(min_length=1)
    utilization_pct: int = Field(ge=0, le=100)
    state: str = Field(min_length=1)
    policy: str = Field(min_length=1)


class DataQualityView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str = Field(min_length=1)
    freshness: str = Field(min_length=1)
    observations: str = Field(min_length=1)
    source_families: str = Field(min_length=1)
    state: str = Field(min_length=1)
    tone: SystemTone
    finding: str = Field(min_length=1)


class IncidentView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    status: str = Field(min_length=1)
    opened: str = Field(min_length=1)
    tone: SystemTone


class MissionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    state: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    confidence: str = Field(min_length=1)
    expires: str = Field(min_length=1)
    dissent: str = Field(min_length=1)
    tone: SystemTone


class ServiceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    state: str = Field(min_length=1)
    owns: str = Field(min_length=1)
    latency: str = Field(min_length=1)
    tone: SystemTone


class AuditEventView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    at: datetime
    actor: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    hash: str = Field(min_length=12)
    tone: SystemTone


class LiveReadinessView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str = Field(min_length=1)
    passed_checks: int = Field(ge=0)
    total_checks: int = Field(ge=1)
    blockers: tuple[str, ...] = ()
    approval: str = Field(min_length=1)


class DashboardOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "dashboard.v1"
    as_of: datetime
    environment: DashboardEnvironment
    synthetic: bool
    status: DashboardStatus
    metrics: tuple[DashboardMetric, ...]
    equity_curve: tuple[EquityPoint, ...]
    exposures: tuple[ExposureRow, ...]
    risk_limits: tuple[RiskLimitView, ...]
    data_quality: tuple[DataQualityView, ...]
    incidents: tuple[IncidentView, ...]
    missions: tuple[MissionView, ...]
    services: tuple[ServiceView, ...]
    audit: tuple[AuditEventView, ...]
    live_readiness: LiveReadinessView

    @model_validator(mode="after")
    def require_aware_as_of(self) -> DashboardOverview:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("dashboard as_of must include a timezone")
        return self


class CommandKind(StrEnum):
    HALT_PAPER = "halt_paper"
    RESUME_PAPER = "resume_paper"
    SET_MODE = "set_mode"
    PROPOSE_CONFIG = "propose_config"
    ROLLBACK_CONFIG = "rollback_config"
    REFRESH_DATA = "refresh_data"


class DashboardCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: CommandKind
    idempotency_key: str = Field(min_length=12, max_length=128)
    reason: str = Field(min_length=3, max_length=500)
    confirmed: bool = False
    step_up_token: str | None = Field(default=None, min_length=8, max_length=256)
    requested_mode: str | None = Field(default=None, min_length=1, max_length=32)
    config_patch: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_command_scope(self) -> DashboardCommandRequest:
        if self.command is CommandKind.SET_MODE and self.requested_mode is None:
            raise ValueError("set_mode requires requested_mode")
        if self.command is CommandKind.PROPOSE_CONFIG and not self.config_patch:
            raise ValueError("propose_config requires a non-empty config_patch")
        if self.command in {
            CommandKind.HALT_PAPER,
            CommandKind.RESUME_PAPER,
            CommandKind.SET_MODE,
            CommandKind.PROPOSE_CONFIG,
            CommandKind.ROLLBACK_CONFIG,
        } and not self.confirmed:
            raise ValueError("sensitive dashboard commands require explicit confirmation")
        if self.command in {
            CommandKind.HALT_PAPER,
            CommandKind.RESUME_PAPER,
            CommandKind.SET_MODE,
            CommandKind.PROPOSE_CONFIG,
            CommandKind.ROLLBACK_CONFIG,
        } and not self.step_up_token:
            raise ValueError("this command requires a recent step-up authentication token")
        return self

    @property
    def requires_step_up(self) -> bool:
        return self.command in {
            CommandKind.HALT_PAPER,
            CommandKind.RESUME_PAPER,
            CommandKind.SET_MODE,
            CommandKind.PROPOSE_CONFIG,
            CommandKind.ROLLBACK_CONFIG,
        }


class CommandReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str = Field(min_length=1)
    command: CommandKind
    status: str = Field(min_length=1)
    message: str = Field(min_length=1)
    accepted_at: datetime
    safe_state: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)
    requested_mode: str | None = None
    config_patch: dict[str, str] | None = None


def _hash_event(event_type: str, summary: str, at: datetime) -> str:
    return hashlib.sha256(f"{event_type}:{summary}:{at.isoformat()}".encode()).hexdigest()


def _event(
    *, event_id: str, at: datetime, actor: str, event_type: str, summary: str, tone: SystemTone
) -> AuditEventView:
    return AuditEventView(
        event_id=event_id,
        at=at,
        actor=actor,
        event_type=event_type,
        summary=summary,
        hash=_hash_event(event_type, summary, at),
        tone=tone,
    )


def build_demo_overview(now: datetime | None = None) -> DashboardOverview:
    """Return clearly-labelled synthetic state for local UI development."""

    current = now or datetime.now(UTC)
    curve_values = (100_000, 100_420, 100_180, 100_760, 101_120, 100_940, 101_360, 101_690)
    curve = tuple(
        EquityPoint(at=current - timedelta(hours=(len(curve_values) - index) * 3), value=value)
        for index, value in enumerate(curve_values)
    )
    audit = (
        _event(
            event_id="evt-9f3a",
            at=current - timedelta(seconds=14),
            actor="system",
            event_type="risk_snapshot",
            summary="Risk snapshot validated against risk-v3-core-v1",
            tone=SystemTone.POSITIVE,
        ),
        _event(
            event_id="evt-71c2",
            at=current - timedelta(minutes=3),
            actor="collector-node",
            event_type="quality_finding",
            summary="Macro vintage check pending; no trading impact",
            tone=SystemTone.WARNING,
        ),
        _event(
            event_id="evt-42bd",
            at=current - timedelta(minutes=9),
            actor="owner",
            event_type="mission_reviewed",
            summary="Standard mode council reviewed BTC/USDT state",
            tone=SystemTone.INFO,
        ),
    )
    return DashboardOverview(
        as_of=current,
        environment=DashboardEnvironment.PAPER_TESTNET,
        synthetic=True,
        status=DashboardStatus(
            environment=DashboardEnvironment.PAPER_TESTNET,
            operating_mode="standard",
            kill_switch="armed",
            reconciliation="clean",
            data_freshness_seconds=4,
            resource_headroom_gib=2.3,
            last_ledger_event_at=current - timedelta(seconds=14),
            api_state="healthy",
            synthetic=True,
        ),
        metrics=(
            DashboardMetric(
                key="nav",
                label="Net liquidation",
                value="$101,690.00",
                delta="+$1,690 / +1.69%",
                detail="Since paper baseline",
                tone=SystemTone.POSITIVE,
            ),
            DashboardMetric(
                key="pnl",
                label="Session P&L",
                value="+$428.16",
                delta="+0.42%",
                detail="Mark-to-market",
                tone=SystemTone.POSITIVE,
            ),
            DashboardMetric(
                key="gross",
                label="Gross exposure",
                value="$42,780",
                delta="42.1% of NAV",
                detail="Hard limit $100,000",
                tone=SystemTone.INFO,
            ),
            DashboardMetric(
                key="headroom",
                label="Risk headroom",
                value="57.9%",
                delta="All checks green",
                detail="Policy risk-v3-core-v1",
                tone=SystemTone.POSITIVE,
            ),
        ),
        equity_curve=curve,
        exposures=(
            ExposureRow(
                instrument="BTC/USDT",
                asset_class="crypto",
                side="LONG",
                notional="$18,460",
                weight="18.2%",
                pnl="+$286.42",
                tone=SystemTone.POSITIVE,
                mark="$67,420.00",
            ),
            ExposureRow(
                instrument="ETH/USDT",
                asset_class="crypto",
                side="LONG",
                notional="$11,980",
                weight="11.8%",
                pnl="+$98.12",
                tone=SystemTone.POSITIVE,
                mark="$3,482.10",
            ),
            ExposureRow(
                instrument="SPY",
                asset_class="equity",
                side="LONG",
                notional="$7,240",
                weight="7.1%",
                pnl="+$43.62",
                tone=SystemTone.INFO,
                mark="$524.18",
            ),
            ExposureRow(
                instrument="USD cash",
                asset_class="cash",
                side="FLAT",
                notional="$58,910",
                weight="58.0%",
                pnl="—",
                tone=SystemTone.NEUTRAL,
                mark="1.0000",
            ),
        ),
        risk_limits=(
            RiskLimitView(
                key="gross",
                label="Gross notional",
                used="$42,780",
                limit="$100,000",
                utilization_pct=43,
                state="within limit",
                policy="risk-v3-core-v1",
            ),
            RiskLimitView(
                key="order",
                label="Max order notional",
                used="$0",
                limit="$25,000",
                utilization_pct=0,
                state="awaiting order",
                policy="risk-v3-core-v1",
            ),
            RiskLimitView(
                key="turnover",
                label="Daily turnover",
                used="$18,420",
                limit="$50,000",
                utilization_pct=37,
                state="within limit",
                policy="risk-v3-core-v1",
            ),
            RiskLimitView(
                key="margin",
                label="Margin used",
                used="$0",
                limit="$50,000",
                utilization_pct=0,
                state="no margin",
                policy="risk-v3-core-v1",
            ),
        ),
        data_quality=(
            DataQualityView(
                dataset="market",
                freshness="4 sec",
                observations="18,240",
                source_families="venue · native",
                state="validated",
                tone=SystemTone.POSITIVE,
                finding="Execution-grade feed is current",
            ),
            DataQualityView(
                dataset="macro",
                freshness="18 min",
                observations="2,864",
                source_families="FRED · BLS",
                state="review",
                tone=SystemTone.WARNING,
                finding="One vintage availability check pending",
            ),
            DataQualityView(
                dataset="news",
                freshness="2 min",
                observations="1,192",
                source_families="RSS · GDELT",
                state="validated",
                tone=SystemTone.POSITIVE,
                finding="Origin and syndication fields complete",
            ),
        ),
        incidents=(
            IncidentView(
                incident_id="INC-014",
                severity="medium",
                summary="Macro vintage availability review",
                owner="data-writer",
                status="open",
                opened="09:41 UTC",
                tone=SystemTone.WARNING,
            ),
            IncidentView(
                incident_id="INC-011",
                severity="low",
                summary="Cold archive verification completed",
                owner="archive-worker",
                status="closed",
                opened="08:12 UTC",
                tone=SystemTone.POSITIVE,
            ),
        ),
        missions=(
            MissionView(
                mission_id="MSN-742",
                title="BTC/USDT regime review",
                mode="standard",
                state="risk approved",
                evidence="3 families / 6 artifacts",
                confidence="0.80",
                expires="in 52 min",
                dissent="1 skeptic note",
                tone=SystemTone.POSITIVE,
            ),
            MissionView(
                mission_id="MSN-739",
                title="Macro release impact scan",
                mode="deep",
                state="abstained",
                evidence="1 family / 2 artifacts",
                confidence="0.00",
                expires="expired",
                dissent="insufficient independence",
                tone=SystemTone.WARNING,
            ),
        ),
        services=(
            ServiceView(
                name="advisor-api",
                kind="always on",
                state="healthy",
                owns="mission routing · approval boundary",
                latency="18 ms",
                tone=SystemTone.POSITIVE,
            ),
            ServiceView(
                name="market-node",
                kind="always on",
                state="healthy",
                owns="events · RiskKernel · OMS",
                latency="6 ms",
                tone=SystemTone.POSITIVE,
            ),
            ServiceView(
                name="collector-node",
                kind="always on",
                state="degraded",
                owns="raw market · source health",
                latency="220 ms",
                tone=SystemTone.WARNING,
            ),
            ServiceView(
                name="resource-governor",
                kind="always on",
                state="healthy",
                owns="admission · load shedding",
                latency="4 ms",
                tone=SystemTone.POSITIVE,
            ),
        ),
        audit=audit,
        live_readiness=LiveReadinessView(
            state="paper only",
            passed_checks=3,
            total_checks=8,
            blockers=(
                "Phase 7 paper soak evidence not admitted",
                "Explicit human Phase 10 authorization missing",
                "Live venue credentials are disabled",
            ),
            approval="sealed by Phase 10 guard",
        ),
    )


class DashboardProjection:
    """Mutable projection and command receipt store for one API process."""

    _allowed_modes = {"trade_fast", "standard", "deep", "builder", "recovery"}

    def __init__(
        self,
        overview: DashboardOverview | None = None,
        *,
        ledgers: SqliteLedgers | None = None,
    ) -> None:
        self._overview = overview or build_demo_overview()
        self._ledgers = ledgers
        self._receipts: dict[str, CommandReceipt] = {}
        self._hydrate_commands()

    def _hydrate_commands(self) -> None:
        if self._ledgers is None:
            return
        hydrated_audit: list[AuditEventView] = []
        status = self._overview.status
        for event in self._ledgers.events(LedgerNamespace.INCIDENT):
            if event.event_type != "dashboard_command_recorded":
                continue
            payload = event.payload
            receipt_payload = payload.get("receipt")
            idempotency_key = payload.get("idempotency_key")
            if not isinstance(receipt_payload, dict) or not isinstance(idempotency_key, str):
                continue
            receipt = CommandReceipt.model_validate(receipt_payload)
            self._receipts[idempotency_key] = receipt
            status = status.model_copy(
                update={
                    "last_ledger_event_at": max(status.last_ledger_event_at, receipt.accepted_at)
                }
            )
            if receipt.status == "accepted":
                if receipt.command is CommandKind.HALT_PAPER:
                    status = status.model_copy(update={"kill_switch": "engaged"})
                elif receipt.command is CommandKind.RESUME_PAPER:
                    status = status.model_copy(update={"kill_switch": "armed"})
                elif receipt.command is CommandKind.SET_MODE and receipt.requested_mode:
                    status = status.model_copy(update={"operating_mode": receipt.requested_mode})
            summary = str(payload.get("reason", receipt.message))
            hydrated_audit.append(
                _event(
                    event_id=receipt.audit_event_id,
                    at=receipt.accepted_at,
                    actor=str(payload.get("actor", "owner")),
                    event_type=receipt.command.value,
                    summary=summary,
                    tone=SystemTone.CRITICAL
                    if receipt.command is CommandKind.HALT_PAPER
                    else SystemTone.INFO,
                )
            )
        if hydrated_audit:
            self._overview = self._overview.model_copy(
                update={
                    "status": status,
                    "audit": tuple(reversed(hydrated_audit)) + self._overview.audit,
                }
            )

    def overview(self) -> DashboardOverview:
        current = datetime.now(UTC)
        status = self._overview.status.model_copy(
            update={
                "data_freshness_seconds": max(
                    0, int((current - self._overview.status.last_ledger_event_at).total_seconds())
                )
            }
        )
        return self._overview.model_copy(update={"as_of": current, "status": status})

    def receipt_for(self, idempotency_key: str) -> CommandReceipt | None:
        return self._receipts.get(idempotency_key)

    def execute(self, command: DashboardCommandRequest, *, actor: str) -> CommandReceipt:
        prior = self._receipts.get(command.idempotency_key)
        if prior is not None:
            return prior
        now = datetime.now(UTC)
        command_id = f"cmd-{secrets.token_hex(6)}"
        status = self._overview.status
        message = "Command accepted and recorded in the local control projection."
        result_status = "accepted"
        if command.command is CommandKind.HALT_PAPER:
            status = status.model_copy(update={"kill_switch": "engaged"})
            message = "Paper activity halted; resume requires step-up authentication."
        elif command.command is CommandKind.RESUME_PAPER:
            if status.reconciliation != "clean":
                result_status = "rejected"
                message = "Resume rejected until reconciliation is clean."
            else:
                status = status.model_copy(update={"kill_switch": "armed"})
                message = "Paper activity resumed after the reconciliation guard."
        elif command.command is CommandKind.SET_MODE:
            assert command.requested_mode is not None
            if command.requested_mode not in self._allowed_modes:
                result_status = "rejected"
                message = f"Mode {command.requested_mode!r} is not admitted by the mode registry."
            else:
                status = status.model_copy(update={"operating_mode": command.requested_mode})
                message = f"Operating mode changed to {command.requested_mode}."
        elif command.command is CommandKind.PROPOSE_CONFIG:
            message = "Configuration revision staged for review; no policy is applied by this receipt."
        elif command.command is CommandKind.ROLLBACK_CONFIG:
            message = "Configuration rollback request recorded for the owning service."
        elif command.command is CommandKind.REFRESH_DATA:
            message = "Refresh request recorded; source workers retain acquisition authority."
        event_id = f"evt-{secrets.token_hex(4)}"
        receipt = CommandReceipt(
            command_id=command_id,
            command=command.command,
            status=result_status,
            message=message,
            accepted_at=now,
            safe_state="paper_only",
            audit_event_id=event_id,
            requested_mode=command.requested_mode,
            config_patch=command.config_patch,
        )
        self._receipts[command.idempotency_key] = receipt
        if self._ledgers is not None:
            self._ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.INCIDENT,
                    event_type="dashboard_command_recorded",
                    idempotency_key=f"dashboard-command:{command.idempotency_key}",
                    occurred_at=now,
                    payload={
                        "actor": actor,
                        "idempotency_key": command.idempotency_key,
                        "reason": command.reason,
                        "requested_mode": command.requested_mode,
                        "config_patch": command.config_patch,
                        "receipt": receipt.model_dump(mode="json", round_trip=True),
                    },
                )
            )
        if result_status == "accepted":
            self._overview = self._overview.model_copy(
                update={
                    "as_of": now,
                    "status": status,
                    "audit": (
                        _event(
                            event_id=event_id,
                            at=now,
                            actor=actor,
                            event_type=command.command.value,
                            summary=command.reason,
                            tone=SystemTone.CRITICAL
                            if command.command is CommandKind.HALT_PAPER
                            else SystemTone.INFO,
                        ),
                        *self._overview.audit,
                    )[:12],
                }
            )
        return receipt


def create_dashboard_app(
    *, projection: DashboardProjection | None = None, config: AuthConfiguration | None = None
) -> Any:
    """Create the optional FastAPI app without making FastAPI a core dependency."""

    try:
        from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
    except ImportError as exc:  # pragma: no cover - depends on installation extra
        raise RuntimeError(
            "fastapi is required to run the dashboard API; install the dashboard extra"
        ) from exc

    # The core package keeps FastAPI optional and uses postponed annotations.
    # Publish lazily imported framework classes in module globals so FastAPI can
    # resolve nested route annotations while registering decorators.
    globals()["Request"] = Request
    globals()["Response"] = Response

    auth_config = config or AuthConfiguration.from_environment()
    sessions = SessionStore(auth_config)
    ledger_path = os.getenv("ADVISORAI_DASHBOARD_LEDGER_PATH")
    ledger = SqliteLedgers(Path(ledger_path)) if ledger_path else None
    store = projection or DashboardProjection(ledgers=ledger)
    login_limiter = LoginRateLimiter()
    password_hash = configured_password_hash()
    totp_secret = configured_totp_secret()
    app = FastAPI(title="AdvisorAI V3 Dashboard API", version="1.0.0", docs_url=None)
    if auth_config.allowed_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(auth_config.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Accept", "Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    def principal_for(request: Request) -> Principal:
        if not auth_config.auth_required:
            return Principal(subject="local-development", authenticated_at=datetime.now(UTC))
        session = sessions.get(request.cookies.get("advisorai_session"))
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
        return session.principal

    def require_csrf(request: Request) -> Principal:
        principal = principal_for(request)
        if auth_config.auth_required and not sessions.csrf_matches(
            request.cookies.get("advisorai_session"), request.headers.get("X-CSRF-Token")
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")
        return principal

    # FastAPI resolves dependency annotations after the function is created.
    # These names are imported lazily to keep the core package optional-web
    # dependency free, so publish the runtime classes explicitly.
    principal_for.__annotations__["request"] = Request
    require_csrf.__annotations__["request"] = Request

    class LoginRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        password: str = Field(min_length=1, max_length=256)
        totp_code: str = Field(min_length=6, max_length=8)

    globals()["LoginRequest"] = LoginRequest

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": store.overview().environment.value}

    @app.get("/api/v1/auth/status")
    async def auth_status(request: Request) -> dict[str, bool | str]:
        principal = None
        if not auth_config.auth_required:
            principal = "local-development"
        else:
            session = sessions.get(request.cookies.get("advisorai_session"))
            principal = session.principal.subject if session else None
        return {
            "auth_required": auth_config.auth_required,
            "configured": bool(password_hash and totp_secret),
            "authenticated": principal is not None,
            "subject": principal or "",
        }

    auth_status.__annotations__["request"] = Request

    def client_key(request: Request) -> str:
        return request.client.host if request.client is not None else "unknown"

    def enforce_login_limit(request: Request) -> str:
        key = client_key(request)
        if not login_limiter.allowed(key):
            retry_after = login_limiter.retry_after(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many authentication attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )
        return key

    @app.post("/api/v1/auth/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        if not password_hash or not totp_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="dashboard authentication is not configured",
            )
        limiter_key = enforce_login_limit(request)
        if not PasswordService().verify(password_hash, payload.password) or not TotpService.verify(
            totp_secret, payload.totp_code
        ):
            login_limiter.record_failure(limiter_key)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
        login_limiter.reset(limiter_key)
        session_id, csrf_token = sessions.create(os.getenv("ADVISORAI_DASHBOARD_SUBJECT", "owner"))
        secure_cookie = os.getenv("ADVISORAI_DASHBOARD_COOKIE_SECURE", "1") == "1"
        response.set_cookie(
            "advisorai_session",
            session_id,
            max_age=auth_config.session_ttl_seconds,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        return {"csrf_token": csrf_token, "subject": os.getenv("ADVISORAI_DASHBOARD_SUBJECT", "owner")}

    login.__annotations__["payload"] = LoginRequest
    login.__annotations__["request"] = Request
    login.__annotations__["response"] = Response

    @app.post("/api/v1/auth/step-up")
    async def step_up(
        payload: LoginRequest,
        request: Request,
        _: Principal = Depends(require_csrf),  # noqa: B008
    ) -> dict[str, str]:
        if not auth_config.auth_required:
            expires_at = datetime.now(UTC) + timedelta(seconds=auth_config.step_up_ttl_seconds)
            return {"step_up_token": "local-development-stepup", "expires_at": expires_at.isoformat()}
        if not password_hash or not totp_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="dashboard authentication is not configured",
            )
        limiter_key = enforce_login_limit(request)
        if not PasswordService().verify(password_hash, payload.password) or not TotpService.verify(
            totp_secret, payload.totp_code
        ):
            login_limiter.record_failure(limiter_key)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid step-up credentials")
        login_limiter.reset(limiter_key)
        issued = sessions.issue_step_up(request.cookies.get("advisorai_session"))
        if issued is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
        token, expires_at = issued
        return {"step_up_token": token, "expires_at": expires_at.isoformat()}

    step_up.__annotations__["payload"] = LoginRequest
    step_up.__annotations__["request"] = Request

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, str]:
        sessions.revoke(request.cookies.get("advisorai_session"))
        response.delete_cookie("advisorai_session", path="/")
        return {"status": "signed_out"}

    logout.__annotations__["request"] = Request
    logout.__annotations__["response"] = Response

    @app.get("/api/v1/dashboard/overview", response_model=DashboardOverview)
    async def overview(_: Principal = Depends(principal_for)) -> DashboardOverview:  # noqa: B008
        return store.overview()

    @app.get("/api/v1/dashboard/services", response_model=tuple[ServiceView, ...])
    async def services(_: Principal = Depends(principal_for)) -> tuple[ServiceView, ...]:  # noqa: B008
        return store.overview().services

    @app.get("/api/v1/dashboard/audit", response_model=tuple[AuditEventView, ...])
    async def audit(_: Principal = Depends(principal_for)) -> tuple[AuditEventView, ...]:  # noqa: B008
        return store.overview().audit

    @app.post("/api/v1/control/command", response_model=CommandReceipt)
    async def command(
        payload: DashboardCommandRequest,
        request: Request,
        principal: Principal = Depends(require_csrf),  # noqa: B008
    ) -> CommandReceipt:
        prior = store.receipt_for(payload.idempotency_key)
        if prior is not None:
            return prior
        if auth_config.auth_required and payload.requires_step_up:
            if not sessions.consume_step_up(
                request.cookies.get("advisorai_session"), payload.step_up_token
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="a fresh step-up authentication token is required",
                )
        return store.execute(payload, actor=principal.subject)

    command.__annotations__["request"] = Request

    return app


__all__ = [
    "AuditEventView",
    "CommandKind",
    "CommandReceipt",
    "DashboardCommandRequest",
    "DashboardEnvironment",
    "DashboardOverview",
    "DashboardProjection",
    "DashboardStatus",
    "LiveReadinessView",
    "build_demo_overview",
    "create_dashboard_app",
]
