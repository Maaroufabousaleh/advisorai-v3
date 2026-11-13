"""Long-lived owner process for the paper-only application profile."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from inspect import getattr_static
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import psutil

from advisorai.agents import EvidenceCouncil, MissionRequest
from advisorai.agents.council import RoleResult
from advisorai.agents.router import MissionKind
from advisorai.api.service import AdvisorService
from advisorai.contracts import (
    AssetClass,
    Evidence,
    Fill,
    InstrumentIdentity,
    Order,
    RiskLimit,
    RiskPolicy,
    Snapshot,
)
from advisorai.decision import (
    DecisionModelArtifact,
    DecisionModelPort,
    DecisionStance,
    ReferenceDecisionAdapter,
)
from advisorai.execution import (
    AccountLedger,
    AccountState,
    OrderManager,
    RiskMarketState,
    SimulatedPaperVenueAdapter,
    VenueAccountSnapshot,
    VenueAcknowledgement,
)
from advisorai.learning import PaperDecisionRecord, PaperLearningLoop, PaperOutcome
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.runtime import PaperRuntime, PaperRuntimeConfig

from .records import (
    DataGapRecord,
    DataGapResolutionRecord,
    PITSnapshotVerification,
    RecoveryMode,
    SessionOutcome,
)
from .supervisor import SessionRecoveryChecks, SessionStopChecks, SessionSupervisor, WorkerRegistry

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_BTC = InstrumentIdentity(
    canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper", venue_symbol="BTC-USD"
)


class OwnerDataAdapter(Protocol):
    """Data boundary required by the supervised paper owner.

    Repeated ``snapshot_at`` calls for a closed cutoff must return the same
    artifact identity. The adapter must expose current-state verification
    methods as well as snapshot builders. Durable records alone never verify
    recovery.
    """

    source: str
    dataset: str
    watermark_key: str

    def snapshot_at(self, cutoff: datetime) -> Snapshot: ...

    def market_at(self, snapshot: Snapshot) -> RiskMarketState: ...

    def watermark_value(self, snapshot: Snapshot) -> str: ...

    def verify_current_watermarks(
        self, ledgers: SqliteLedgers, watermarks: dict[str, str]
    ) -> bool: ...

    def verify_current_pit(
        self, ledgers: SqliteLedgers, verification: PITSnapshotVerification
    ) -> bool: ...

    def verify_gap_coverage(
        self,
        ledgers: SqliteLedgers,
        gap: DataGapRecord,
        resolution: DataGapResolutionRecord,
    ) -> bool: ...


class OwnerPaperVenueAdapter(Protocol):
    """Current-state paper adapter consumed by OMS and recovery checks."""

    venue: str

    def submit(self, order: Order) -> VenueAcknowledgement: ...

    def reconcile(self, order: Order) -> dict[str, object] | None: ...

    def open_orders(self) -> tuple[VenueAcknowledgement, ...]: ...

    def cancel(self, order: Order) -> bool: ...

    def account_snapshot(self) -> VenueAccountSnapshot: ...

    @property
    def fills(self) -> tuple[Fill, ...]: ...


class OwnerOutcomeAdapter(Protocol):
    """Profile-specific realized-value, fill-quality, and cost semantics."""

    def build_outcome(
        self,
        *,
        decision: PaperDecisionRecord,
        as_of: datetime,
        snapshot: Snapshot,
        market: RiskMarketState,
        fills: tuple[Fill, ...],
        orders: Mapping[UUID, Order],
        initial_marks: Mapping[str, Decimal],
    ) -> PaperOutcome: ...


@dataclass(frozen=True, slots=True)
class OwnerApplicationAdapters:
    """Reviewed adapters composed under one local, paper-only owner session.

    ``initial_account`` is the profile's durable baseline. The injected venue
    must report the same current state after OMS/account-ledger recovery. This
    seam does not load plugins, credentials, or endpoints from configuration.
    """

    data: OwnerDataAdapter
    evidence_council: EvidenceCouncil
    decision_model_factory: Callable[[Snapshot], DecisionModelPort]
    paper_venue: OwnerPaperVenueAdapter
    outcome_adapter: OwnerOutcomeAdapter
    initial_account: AccountState
    instrument: InstrumentIdentity = _BTC

    def __post_init__(self) -> None:
        if not self.data.source.strip() or not self.data.dataset.strip():
            raise ValueError("owner data adapter requires source and dataset identities")
        if self.data.watermark_key != f"{self.data.source}/{self.data.dataset}":
            raise ValueError("owner data adapter watermark identity is inconsistent")
        data_methods = (
            "snapshot_at",
            "market_at",
            "watermark_value",
            "verify_current_watermarks",
            "verify_current_pit",
            "verify_gap_coverage",
        )
        if any(not callable(getattr(self.data, name, None)) for name in data_methods):
            raise ValueError("owner data adapter is missing a required current-state method")
        if not callable(self.decision_model_factory):
            raise ValueError("owner profile requires a decision model factory")
        if not callable(getattr(self.outcome_adapter, "build_outcome", None)):
            raise ValueError("owner profile requires explicit outcome semantics")
        if getattr(self.paper_venue, "venue", None) != "paper":
            raise ValueError("owner application only accepts the paper venue identity")
        venue_methods = ("submit", "reconcile", "open_orders", "cancel", "account_snapshot")
        if any(not callable(getattr(self.paper_venue, name, None)) for name in venue_methods):
            raise ValueError("paper venue adapter is missing a current-state method")
        try:
            getattr_static(self.paper_venue, "fills")
        except AttributeError:
            raise ValueError("paper venue adapter must expose authoritative fills") from None
        if not isinstance(self.initial_account, AccountState):
            raise ValueError("owner profile requires an AccountState baseline")
        if self.instrument.venue != "paper":
            raise ValueError("owner instrument must use the supervised paper venue alias")
        if self.instrument.canonical_id not in self.initial_account.marks:
            raise ValueError("owner account baseline must mark the configured instrument")


class LocalPaperOutcomeAdapter:
    """Local mark-to-market outcome semantics without fabricated forecasts."""

    def build_outcome(
        self,
        *,
        decision: PaperDecisionRecord,
        as_of: datetime,
        snapshot: Snapshot,
        market: RiskMarketState,
        fills: tuple[Fill, ...],
        orders: Mapping[UUID, Order],
        initial_marks: Mapping[str, Decimal],
    ) -> PaperOutcome:
        mark = market.marks[decision.asset]
        related_fills = tuple(fill for fill in fills if fill.artifact_id in decision.fill_ids)
        costs = sum((fill.fee for fill in related_fills), Decimal("0"))
        position = sum(
            (
                fill.quantity if orders[fill.order_id].side == "buy" else -fill.quantity
                for fill in related_fills
            ),
            Decimal("0"),
        )
        baseline_mark = initial_marks.get(decision.asset, mark)
        return PaperOutcome(
            decision_id=decision.decision_id,
            as_of=as_of,
            forecast_value=None,
            realized_value=mark,
            net_utility=(mark - baseline_mark) * position - costs,
            cost_usd=costs,
            fill_quality=(
                Decimal("1") if related_fills or not decision.order_ids else Decimal("0")
            ),
            data_reliability=Decimal("1"),
            latency_ms=0,
            failure_rate=Decimal("0"),
        )


class LocalPaperDataAdapter:
    """Offline deterministic data profile used by the CLI and acceptance tests."""

    source = "local-paper-fixture"
    dataset = "BTC-USD-1h"

    def __init__(self, owner: PaperOwnerApplication) -> None:
        self.owner = owner

    @property
    def watermark_key(self) -> str:
        return f"{self.source}/{self.dataset}"

    def snapshot_at(self, cutoff: datetime) -> Snapshot:
        return self.owner._snapshot_at(cutoff)

    def market_at(self, snapshot: Snapshot) -> RiskMarketState:
        return self.owner._market_at(snapshot)

    @staticmethod
    def watermark_value(snapshot: Snapshot) -> str:
        return snapshot.as_of.isoformat()

    def verify_current_watermarks(
        self, _ledgers: SqliteLedgers, watermarks: dict[str, str]
    ) -> bool:
        try:
            for key, value in watermarks.items():
                if key != self.watermark_key:
                    return False
                cutoff = datetime.fromisoformat(value).astimezone(UTC)
                if self.watermark_value(self.snapshot_at(cutoff)) != value:
                    return False
            return True
        except Exception:
            return False

    def verify_current_pit(
        self, _ledgers: SqliteLedgers, verification: PITSnapshotVerification
    ) -> bool:
        try:
            if verification.source != self.source:
                return False
            snapshot = self.snapshot_at(verification.cutoff)
            return (
                snapshot.artifact_id == verification.snapshot_id
                and self.owner._snapshot_hash(snapshot) == verification.snapshot_hash
                and verification.first_available_at <= verification.cutoff
                and self.watermark_value(snapshot)
                == verification.watermarks.get(self.watermark_key)
            )
        except Exception:
            return False

    def verify_gap_coverage(
        self,
        _ledgers: SqliteLedgers,
        gap: DataGapRecord,
        resolution: DataGapResolutionRecord,
    ) -> bool:
        try:
            if (
                gap.source != self.source
                or resolution.source != self.source
                or gap.instrument != "BTC"
                or resolution.instrument != "BTC"
                or resolution.covered_through < gap.gap_end
            ):
                return False
            snapshot = self.snapshot_at(resolution.covered_through)
            return self.watermark_value(snapshot) == resolution.watermark
        except Exception:
            return False


@dataclass(frozen=True, slots=True)
class OwnerApplicationConfig:
    scenario: str = "quiet"
    interval_seconds: float = 3600.0
    accelerated_clock: bool = False
    max_rss_mb: int = 2048

    def __post_init__(self) -> None:
        if self.scenario not in {"quiet", "qualification"}:
            raise ValueError(
                "owner application supports only quiet or qualification paper scenarios"
            )
        if not 0.1 <= self.interval_seconds <= 86_400:
            raise ValueError("scheduler interval must be between 0.1 seconds and one day")
        if not 256 <= self.max_rss_mb <= 8192:
            raise ValueError("owner process RSS limit must be between 256 and 8192 MiB")


class PaperOwnerApplication:
    """Own a durable, scheduled, resource-bounded paper session.

    The CLI uses deterministic local evidence with the normal Evidence
    Council, Decision Model, Portfolio Constructor, RiskKernel, OMS, and
    simulated paper venue. Python callers may inject reviewed paper-scoped
    adapters through :class:`OwnerApplicationAdapters`; they remain under the
    same lock, resource envelope, and current-state recovery requirements.
    """

    def __init__(
        self,
        *,
        ledgers: SqliteLedgers,
        state_dir: Path,
        lock_identity: str,
        code_hash: str,
        config_hash: str,
        config: OwnerApplicationConfig,
        supervisor: SessionSupervisor | None = None,
        stop_requested: threading.Event | None = None,
        adapters: OwnerApplicationAdapters | None = None,
    ) -> None:
        self.ledgers = ledgers
        self.state_dir = state_dir
        self.lock_identity = lock_identity
        self.config = config
        self.supervisor = supervisor or SessionSupervisor(
            ledgers,
            state_dir=state_dir,
            lock_identity=lock_identity,
            code_hash=code_hash,
            config_hash=config_hash,
        )
        self.stop_requested = stop_requested or threading.Event()
        self._scheduler_done = threading.Event()
        self._shutdown_finalized = threading.Event()
        self._scheduler_error: BaseException | None = None
        self._scheduler_thread: threading.Thread | None = None
        self._owner_session_id: UUID | None = None
        self._snapshots: dict[datetime, Snapshot] = {}
        self._worker_registry = WorkerRegistry()
        self._lease_id = None

        self._marks = {"BTC": Decimal("50000")}
        if adapters is None:
            venue: OwnerPaperVenueAdapter = SimulatedPaperVenueAdapter(
                ledgers,
                venue="paper",
                initial_cash=Decimal("100000"),
                marks=self._marks,
                as_of=_EPOCH,
                auto_fill=config.scenario == "qualification",
                fee_bps=Decimal("1"),
            )
            account = AccountState(
                cash=Decimal("100000"),
                marks=dict(self._marks),
                as_of=_EPOCH,
            )
            adapters = OwnerApplicationAdapters(
                data=LocalPaperDataAdapter(self),
                evidence_council=(
                    self._council() if config.scenario == "qualification" else EvidenceCouncil({})
                ),
                decision_model_factory=self._reference_decision_model,
                paper_venue=venue,
                outcome_adapter=LocalPaperOutcomeAdapter(),
                initial_account=account,
            )
        self.adapters = adapters
        self.venue = adapters.paper_venue
        self.account = deepcopy(adapters.initial_account)
        self._marks = dict(self.account.marks) or dict(self._marks)
        self._last_cutoff: datetime | None = self._checkpoint_cutoff()
        self.account_ledger = AccountLedger(ledgers, self.account)
        self.orders = OrderManager(ledgers, self.venue)
        self.learning = PaperLearningLoop(ledgers)
        self.runtime = PaperRuntime(
            config=PaperRuntimeConfig(
                environment="paper",
                instruments=(self.adapters.instrument.canonical_id,),
                observation_interval_seconds=300,
                decision_interval_seconds=3600,
                run_id="advisorai-owner-paper",
                max_order_count_per_cycle=1,
            ),
            snapshot_provider=self.adapters.data.snapshot_at,
            market_provider=self.adapters.data.market_at,
            decision_builder=self._decision_at,
            account=self.account,
            account_ledger=self.account_ledger,
            risk_policy=RiskPolicy(
                artifact_id=uuid5(NAMESPACE_URL, "advisorai-v3/owner-paper-risk-policy-v1"),
                policy_version="owner-paper-v1",
                effective_at=_EPOCH,
                hard_limits=(
                    RiskLimit(name="max_order_notional", limit=Decimal("100000"), unit="USD"),
                    RiskLimit(name="max_gross_notional", limit=Decimal("250000"), unit="USD"),
                ),
                approved_by="owner-paper-reference-policy",
            ),
            orders=self.orders,
            ledgers=ledgers,
            learning=self.learning,
        )

    @property
    def owner_record_path(self) -> Path:
        return self.state_dir / "owner.json"

    def stop_request_path(self, session_id: UUID) -> Path:
        return self.state_dir / f"stop-request-{session_id}.json"

    def run(self, *, reason: str = "owner application start") -> int:
        """Hold the process lock until a graceful request or worker failure."""
        self._apply_resource_envelope()
        try:
            start, recovery = self.supervisor.start(
                reason=reason,
                recovery_checks=self._recovery_checks(),
            )
        except RuntimeError as exc:
            print(f"SESSION START REFUSED: {exc}", flush=True)
            return 2
        self._owner_session_id = start.session_id
        self.runtime.config = self.runtime.config.model_copy(
            update={"owner_session_id": start.session_id}
        )
        print(f"session_id={start.session_id}", flush=True)
        print(f"classification={start.recovery_classification}", flush=True)
        print(f"recovery_mode={recovery.recovery_mode.value}", flush=True)
        for decision in recovery.decisions:
            print(f"decision={decision}", flush=True)
        if recovery.recovery_mode is RecoveryMode.ABSTAIN:
            print("RECOVERY INCOMPLETE: paper proposals remain disabled.", flush=True)
            self.supervisor.stop(
                outcome=SessionOutcome.UNCLEAN,
                unfinished_work=("recovery_checks_failed",),
                stop_checks=self._stop_checks(),
            )
            return 2
        try:
            self._prepare_current_state()
            self._lease_id = self.supervisor.acquire_lease(
                worker_identity="thread:paper-scheduler",
                resource_envelope=(
                    f"workers=1;rss_mb={self.config.max_rss_mb};nofile=256;paper_only=true"
                ),
            ).lease_id
            self._scheduler_thread = threading.Thread(
                target=self._scheduler,
                name="advisorai-paper-scheduler",
                daemon=False,
            )
            self._worker_registry.register_worker("thread:paper-scheduler", self._scheduler_thread)
            self._write_owner_record(start.session_id)
            self.supervisor.checkpoint(pending_work=("paper_scheduler_active",))
            self._scheduler_thread.start()
            print("SESSION READY. Stop with: python -m advisorai.app stop", flush=True)
            return self._wait_and_stop(start.session_id)
        except BaseException as exc:
            self._scheduler_error = exc
            self.stop_requested.set()
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                self._scheduler_thread.join(timeout=30)
            scheduler_alive = (
                self._scheduler_thread is not None and self._scheduler_thread.is_alive()
            )
            if not scheduler_alive:
                self._worker_registry.deregister("thread:paper-scheduler")
            if self.supervisor.active_session_id() is not None:
                end = self.supervisor.stop(
                    outcome=SessionOutcome.UNCLEAN,
                    unfinished_work=(f"startup_or_runtime_error:{type(exc).__name__}",),
                    stop_checks=self._stop_checks(),
                    worker_registry=self._worker_registry,
                )
                self._shutdown_finalized.set()
                if not scheduler_alive:
                    self._remove_owner_record(start.session_id)
                print(f"outcome={end.outcome.value}", flush=True)
            raise

    def _wait_and_stop(self, session_id: UUID) -> int:
        requested_outcome = SessionOutcome.CLEAN
        while not self.stop_requested.wait(0.25):
            request = self._consume_stop_request(session_id)
            if request is not None:
                requested_outcome = request
                self.stop_requested.set()
                break
            if self._scheduler_done.is_set():
                requested_outcome = SessionOutcome.UNCLEAN
                self.stop_requested.set()
                break
            try:
                rss = psutil.Process().memory_info().rss
            except psutil.Error:
                requested_outcome = SessionOutcome.UNCLEAN
                self._scheduler_error = RuntimeError("owner process resource monitor failed")
                self.stop_requested.set()
                break
            if rss > self.config.max_rss_mb * 1024 * 1024:
                requested_outcome = SessionOutcome.UNCLEAN
                self._scheduler_error = MemoryError("owner process exceeded RSS resource envelope")
                self.stop_requested.set()
                break

        self.runtime.stop()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=30)
        scheduler_alive = self._scheduler_thread is not None and self._scheduler_thread.is_alive()
        if not scheduler_alive:
            self._worker_registry.deregister("thread:paper-scheduler")
        open_orders = tuple(item.order_id for item in self.venue.open_orders())
        unfinished = ()
        if self._scheduler_error is not None:
            requested_outcome = SessionOutcome.UNCLEAN
            unfinished = (f"worker_error:{type(self._scheduler_error).__name__}",)
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            requested_outcome = SessionOutcome.UNCLEAN
            unfinished = (*unfinished, "paper_scheduler_did_not_stop")
        if open_orders:
            requested_outcome = SessionOutcome.UNCLEAN
            unfinished = (*unfinished, "paper_open_orders_remain")
        end = self.supervisor.stop(
            outcome=requested_outcome,
            open_orders=open_orders,
            unfinished_work=unfinished,
            stop_checks=self._stop_checks(),
            worker_registry=self._worker_registry,
        )
        self._shutdown_finalized.set()
        if not scheduler_alive:
            self._remove_owner_record(session_id)
        print(f"session_id={end.session_id}", flush=True)
        print(f"outcome={end.outcome.value}", flush=True)
        print(f"reconciliation={end.reconciliation_state}", flush=True)
        print(f"leases_released={end.leases_released}", flush=True)
        return 0 if end.outcome is SessionOutcome.CLEAN else 2

    def _scheduler(self) -> None:
        try:
            while not self.stop_requested.is_set():
                cutoff = self._next_cutoff()
                if self._last_cutoff is None or cutoff > self._last_cutoff:
                    snapshot = self.adapters.data.snapshot_at(cutoff)
                    cycle = self.runtime.run_once(cutoff)
                    # ``run_once`` marks a durable STARTED cycle as failed
                    # instead of replaying it.  Recovery has already
                    # reconciled the venue, orders, fills, and account before
                    # this scheduler starts, so preserve the failed cycle and
                    # move forward to the next cutoff without repeating work.
                    watermark = self.adapters.data.watermark_value(snapshot)
                    if cycle.snapshot_id not in {None, snapshot.artifact_id}:
                        raise RuntimeError(
                            "data adapter returned inconsistent snapshots for one paper cutoff"
                        )
                    marks = {self.adapters.data.watermark_key: watermark}
                    self.supervisor.record_watermark(
                        source=self.adapters.data.source,
                        dataset=self.adapters.data.dataset,
                        value=watermark,
                        observed_at=cutoff,
                    )
                    self.supervisor.record_pit_snapshot(
                        snapshot_id=snapshot.artifact_id,
                        snapshot_hash=self._snapshot_hash(snapshot),
                        source=self.adapters.data.source,
                        cutoff=cutoff,
                        first_available_at=cutoff,
                        watermarks=marks,
                    )
                    self._record_due_outcomes(cutoff)
                    self.supervisor.checkpoint(
                        artifact_ids=(snapshot.artifact_id,),
                        watermarks=marks,
                        pending_work=("paper_scheduler_active",),
                    )
                    self._last_cutoff = cutoff
                if self.stop_requested.wait(self.config.interval_seconds):
                    break
        except BaseException as exc:
            self._scheduler_error = exc
            self.stop_requested.set()
        finally:
            self._scheduler_done.set()
            if self._shutdown_finalized.is_set() and self._owner_session_id is not None:
                self._remove_owner_record(self._owner_session_id)

    def _prepare_current_state(self) -> None:
        """Recover OMS/account projections from the configured paper venue."""
        for order_id in tuple(self.orders.ambiguous_orders):
            self.orders.reconcile_ambiguous(order_id)
        for order_id, order in tuple(self.orders.orders.items()):
            if order.state.value == "routed":
                self.orders.reconcile_routed(order_id)
        self._settle_venue_fills()
        for order_id, order in tuple(self.orders.orders.items()):
            if order.state.value in {"filled", "cancelled", "rejected", "expired"}:
                self.orders.reconcile(order_id)
        result = self.runtime.reconcile_once()
        if not result.reconciled:
            raise RuntimeError("current paper account/order reconciliation failed")

    def _settle_venue_fills(self) -> None:
        for fill in self.venue.fills:
            order = self.orders.orders.get(fill.order_id)
            if order is None:
                raise RuntimeError("paper venue contains a fill for an unknown local order")
            if order.state.value == "routed":
                self.orders.reconcile_routed(order.artifact_id)
                order = self.orders.orders[order.artifact_id]
            if order.state.value in {"acknowledged", "partially_filled", "cancel_pending"}:
                self.orders.record_fill(fill, order.side)
            self.account_ledger.apply_fill(fill, order.side, order.instrument.canonical_id)

    def _recovery_checks(self) -> SessionRecoveryChecks:
        return SessionRecoveryChecks(
            prepare_recovery=lambda _ledgers: self._prepare_current_state_result(),
            current_order_account_verifier=lambda _ledgers: self._verify_current_order_account(),
            current_watermark_verifier=self._verify_current_watermarks,
            current_pit_verifier=self._verify_current_pit,
            current_gap_coverage_verifier=self._verify_current_gap_coverage,
        )

    def _prepare_current_state_result(self) -> bool:
        try:
            self._prepare_current_state()
            return True
        except Exception:
            return False

    def _verify_current_order_account(self) -> bool:
        try:
            return bool(self.runtime.reconcile_once().reconciled)
        except Exception:
            return False

    def _stop_checks(self) -> SessionStopChecks:
        return SessionStopChecks(
            current_order_account_verifier=lambda _ledgers: (
                self._verify_current_order_account() and not self.venue.open_orders()
            ),
            current_watermark_verifier=self._verify_current_watermarks,
        )

    def _verify_current_watermarks(
        self, _ledgers: SqliteLedgers, watermarks: dict[str, str]
    ) -> bool:
        return self.adapters.data.verify_current_watermarks(_ledgers, watermarks)

    def _verify_current_gap_coverage(
        self,
        ledgers: SqliteLedgers,
        gap: DataGapRecord,
        resolution: DataGapResolutionRecord,
    ) -> bool:
        return self.adapters.data.verify_gap_coverage(ledgers, gap, resolution)

    def _verify_current_pit(self, _ledgers: SqliteLedgers, verification) -> bool:
        return self.adapters.data.verify_current_pit(_ledgers, verification)

    def _record_due_outcomes(self, cutoff: datetime) -> None:
        snapshot = self.adapters.data.snapshot_at(cutoff)
        market = self.adapters.data.market_at(snapshot)
        for decision in self.learning.unresolved_decisions(as_of=cutoff):
            relevant_orders = {
                order_id: self.orders.orders[order_id]
                for order_id in decision.order_ids
                if order_id in self.orders.orders
            }
            outcome = self.adapters.outcome_adapter.build_outcome(
                decision=decision,
                as_of=cutoff,
                snapshot=snapshot,
                market=market,
                fills=tuple(self.orders.fills.values()),
                orders=relevant_orders,
                initial_marks=self._marks,
            )
            self.learning.record_outcome(outcome)

    def _decision_at(self, snapshot: Snapshot, account: AccountState, market: RiskMarketState):
        service = AdvisorService(
            self.adapters.evidence_council,
            self.ledgers,
            self.adapters.decision_model_factory(snapshot),
        )
        mission_id = uuid5(
            NAMESPACE_URL, f"advisorai-v3/owner-paper-mission/{snapshot.as_of.isoformat()}"
        )
        return service.build_decision(
            request=MissionRequest(
                mission_id=mission_id,
                kind=MissionKind.RECOMMENDATION,
                user_text="scheduled owner paper evaluation",
                created_at=snapshot.as_of,
            ),
            snapshot=snapshot,
            account=account,
            market=market,
            targets={self.adapters.instrument: Decimal("0")},
            risk_policy_version="owner-paper-v1",
        )

    def _reference_decision_model(self, snapshot: Snapshot) -> DecisionModelPort:
        model_artifact = DecisionModelArtifact(
            artifact_id=uuid5(
                NAMESPACE_URL, f"advisorai-v3/owner-reference-model/{snapshot.artifact_id}"
            ),
            model_name="reference",
            adapter_name="owner-paper-reference-v1",
            code_hash=hashlib.sha256(b"advisorai-owner-paper-reference-v1").hexdigest(),
            config_hash=hashlib.sha256(self.config.scenario.encode()).hexdigest(),
            snapshot_id=snapshot.artifact_id,
            created_at=snapshot.as_of,
        )
        adapter = ReferenceDecisionAdapter(
            model_artifact,
            directional_stance=(
                DecisionStance.LONG if self.config.scenario == "qualification" else None
            ),
        )
        return adapter

    @staticmethod
    def _council() -> EvidenceCouncil:
        roles = {
            "data_verifier": ("local-market-a", "market"),
            "technical_flow": ("local-market-b", "market"),
            "derivatives_regime": ("local-market-c", "market"),
            "news_event": ("local-event-fixture", "news"),
            "skeptic_base_rate": ("local-research-fixture", "research"),
        }
        functions: dict[str, Callable[[Snapshot], RoleResult]] = {}
        for role, (origin, family) in roles.items():

            def run(snapshot: Snapshot, *, role=role, origin=origin, family=family):
                cutoff = snapshot.as_of
                evidence = Evidence(
                    artifact_id=uuid5(
                        NAMESPACE_URL,
                        f"advisorai-v3/owner-evidence/{role}/{snapshot.artifact_id}",
                    ),
                    claim=f"deterministic {role} fixture evidence",
                    source_family=family,
                    origin=origin,
                    observed_at=cutoff,
                    first_available_at=cutoff,
                    uncertainty=Decimal("0.1"),
                    expires_at=cutoff + timedelta(hours=1),
                    created_at=cutoff,
                )
                return RoleResult(role=role, evidence=(evidence,))

            functions[role] = run
        return EvidenceCouncil(functions)

    def _snapshot_at(self, cutoff: datetime) -> Snapshot:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("paper cutoff must include a timezone")
        cutoff = cutoff.astimezone(UTC)
        snapshot = Snapshot(
            artifact_id=uuid5(
                NAMESPACE_URL, f"advisorai-v3/owner-paper-snapshot/{cutoff.isoformat()}"
            ),
            as_of=cutoff,
            purpose=f"owner-paper-{self.config.scenario}",
            data_quality_state="validated",
            created_at=cutoff,
        )
        self._snapshots[cutoff] = snapshot
        return snapshot

    def _market_at(self, snapshot: Snapshot) -> RiskMarketState:
        elapsed_hours = max(
            0, int((snapshot.as_of - self._initial_cutoff()).total_seconds() // 3600)
        )
        price = Decimal("50000") + Decimal(elapsed_hours * 100)
        return RiskMarketState(
            marks={"BTC": price},
            volatility={"BTC": Decimal("0.01")},
            liquidity_notional={"BTC": Decimal("1000000")},
            venue_healthy={"BTC": True},
        )

    def _initial_cutoff(self) -> datetime:
        watermark = self._checkpoint_cutoff()
        if watermark is not None:
            return watermark
        current = datetime.now(UTC)
        return current.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)

    def _next_cutoff(self) -> datetime:
        if self.config.accelerated_clock:
            if self._last_cutoff is not None:
                return self._last_cutoff + timedelta(hours=1)
            return self._initial_cutoff()
        current = datetime.now(UTC)
        cutoff = current.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        if self._last_cutoff is not None:
            cutoff = max(cutoff, self._last_cutoff + timedelta(hours=1))
        return cutoff

    def _checkpoint_cutoff(self) -> datetime | None:
        events = self.ledgers.events(LedgerNamespace.MISSION)
        for event in reversed(events):
            if event.event_type != "session_checkpoint":
                continue
            payload = event.payload.get("checkpoint")
            if not isinstance(payload, dict):
                continue
            marks = payload.get("watermarks")
            if not isinstance(marks, dict):
                continue
            value = marks.get(self.adapters.data.watermark_key)
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value).astimezone(UTC)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _snapshot_hash(snapshot: Snapshot) -> str:
        encoded = snapshot.model_dump_json(exclude={"created_at"}).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _watermark_value(snapshot: Snapshot) -> str:
        return snapshot.as_of.isoformat()

    def _consume_stop_request(self, session_id: UUID) -> SessionOutcome | None:
        path = self.stop_request_path(session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return SessionOutcome.UNCLEAN
        try:
            if str(payload.get("session_id")) != str(session_id):
                return SessionOutcome.UNCLEAN
            outcome = SessionOutcome(str(payload.get("outcome", "clean")))
        except ValueError:
            return SessionOutcome.UNCLEAN
        path.unlink(missing_ok=True)
        return outcome

    def _write_owner_record(self, session_id: UUID) -> None:
        payload = {
            "pid": os.getpid(),
            "session_id": str(session_id),
            "lock_identity": self.lock_identity,
            "process_create_time": psutil.Process().create_time(),
            "lock_path": str(self.supervisor.lock_path),
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._write_private_json(self.owner_record_path, payload)

    def _remove_owner_record(self, session_id: UUID) -> None:
        try:
            payload = json.loads(self.owner_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if str(payload.get("session_id")) == str(session_id):
            self.owner_record_path.unlink(missing_ok=True)

    @staticmethod
    def _write_private_json(path: Path, payload: dict[str, object]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            except OSError:
                return
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _apply_resource_envelope() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            bounded = min(256, hard if hard >= 0 else 256)
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(soft, bounded), bounded))
        except (ImportError, OSError, ValueError):
            # RSS remains actively monitored on platforms without POSIX rlimits.
            pass


__all__ = [
    "LocalPaperDataAdapter",
    "LocalPaperOutcomeAdapter",
    "OwnerApplicationAdapters",
    "OwnerApplicationConfig",
    "OwnerDataAdapter",
    "OwnerOutcomeAdapter",
    "OwnerPaperVenueAdapter",
    "PaperOwnerApplication",
]
