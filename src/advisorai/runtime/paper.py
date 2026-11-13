"""The transition decision loop: real inputs, deterministic paper authority."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.api.service import DecisionPipelineResult
from advisorai.contracts import (
    ExecutionPlan,
    Fill,
    Order,
    OrderState,
    RiskOutcome,
    RiskPolicy,
    Snapshot,
)
from advisorai.execution import (
    AccountLedger,
    AccountState,
    DeterministicExecutionPolicy,
    ExecutionPolicyKind,
    KillSwitch,
    OrderManager,
    QuoteState,
    ReconciliationService,
    RiskKernel,
    RiskMarketState,
    RiskRequest,
    build_order_from_choice,
)
from advisorai.learning import PaperDecisionRecord, PaperLearningLoop
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers

from .cadence import CadenceGate


class RuntimeStage(StrEnum):
    STARTED = "started"
    ABSTAINED = "abstained"
    RISK_REJECTED = "risk_rejected"
    NO_TRADE = "no_trade"
    ORDERS_ROUTED = "orders_routed"
    RECONCILIATION_FAILED = "reconciliation_failed"
    COMPLETE = "complete"
    FAILED = "failed"


class PaperRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str = "paper_testnet"
    instruments: tuple[str, ...] = ("BTC", "ETH")
    observation_interval_seconds: int = Field(default=300, ge=60, le=3600)
    decision_interval_seconds: int = Field(default=3600, ge=300, le=86_400)
    run_id: str = Field(default_factory=lambda: f"paper-{uuid4()}", min_length=1)
    max_order_count_per_cycle: int = Field(default=8, ge=0, le=100)
    owner_session_id: UUID | None = None

    @field_validator("environment")
    @classmethod
    def paper_only(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError("PaperRuntime accepts only paper/testnet environments")
        return normalized

    @field_validator("instruments")
    @classmethod
    def v3_core_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value if item.strip())
        if not normalized or any(item not in {"BTC", "ETH"} for item in normalized):
            raise ValueError("transition runtime is fixed to BTC and ETH")
        if len(normalized) != len(set(normalized)):
            raise ValueError("runtime instruments must be unique")
        return normalized


class RuntimeCycle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle_id: UUID = Field(default_factory=uuid4)
    run_id: str = Field(min_length=1)
    owner_session_id: UUID | None = None
    cutoff: datetime
    stage: RuntimeStage
    snapshot_id: UUID | None = None
    decision_id: UUID | None = None
    risk_decision_id: UUID | None = None
    order_ids: tuple[UUID, ...] = ()
    reasons: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("cutoff", "created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime timestamps must include a timezone")
        return value.astimezone(UTC)


SnapshotProvider = Callable[[datetime], Snapshot]
MarketProvider = Callable[[Snapshot], RiskMarketState]
DecisionBuilder = Callable[[Snapshot, AccountState, RiskMarketState], DecisionPipelineResult]
OrderFactory = Callable[[DecisionPipelineResult, AccountState, RiskMarketState], Sequence[Order]]
ObservationCutoffProvider = Callable[[datetime], Sequence[datetime]]


def build_default_orders(
    result: DecisionPipelineResult,
    account: AccountState,
    market: RiskMarketState,
    *,
    policy: ExecutionPolicyKind = ExecutionPolicyKind.PASSIVE_LIMIT,
) -> tuple[Order, ...]:
    """Create deterministic passive/IOC paper orders from an admitted target.

    This helper only materializes orders.  The runtime still re-checks every
    order against the current RiskKernel state before routing it.
    """

    risk = result.risk_decision
    if risk is None or risk.outcome is RiskOutcome.REJECTED:
        return ()
    target = result.decision.target_portfolio
    plan = ExecutionPlan(
        artifact_id=uuid5(
            NAMESPACE_URL,
            f"advisorai-v3/paper-plan/{result.decision.mission_id}/"
            f"{result.decision.snapshot_id}/{target.artifact_id}",
        ),
        risk_decision_id=risk.artifact_id,
        target_portfolio_id=target.artifact_id,
        policy_version=target.risk_constraints_version,
        instructions=("deterministic paper target rebalance",),
        expires_at=result.decision.expires_at,
        created_at=result.decision.created_at,
    )
    chooser = DeterministicExecutionPolicy()
    orders: list[Order] = []
    for position in target.positions:
        instrument_id = position.instrument.canonical_id
        delta = position.target_quantity - account.positions.get(instrument_id, Decimal("0"))
        quote = QuoteState(mark=market.marks[instrument_id])
        choice = chooser.choose(signed_delta=delta, quote=quote, policy=policy)
        if choice is None:
            continue
        fingerprint = sha256(
            f"{result.decision.mission_id}:{result.decision.snapshot_id}:"
            f"{target.artifact_id}:{instrument_id}:{choice.side}:{choice.quantity}".encode()
        ).hexdigest()[:32]
        order = build_order_from_choice(
            choice=choice,
            parent_intent_id=result.decision.mission_id,
            execution_plan=plan,
            instrument=position.instrument,
            idempotency_key=f"paper:{fingerprint}",
        )
        orders.append(
            order.model_copy(
                update={
                    "artifact_id": uuid5(NAMESPACE_URL, f"advisorai-v3/paper-order/{fingerprint}")
                }
            )
        )
    return tuple(orders)


class PaperRuntime:
    """Single-owner loop that wires data → target → risk → OMS → paper venue."""

    def __init__(
        self,
        *,
        config: PaperRuntimeConfig,
        snapshot_provider: SnapshotProvider,
        market_provider: MarketProvider,
        decision_builder: DecisionBuilder,
        account: AccountState,
        account_ledger: AccountLedger | None = None,
        risk_policy: RiskPolicy,
        orders: OrderManager,
        ledgers: SqliteLedgers,
        order_factory: OrderFactory = build_default_orders,
        risk_kernel: RiskKernel | None = None,
        reconciliation: ReconciliationService | None = None,
        learning: PaperLearningLoop | None = None,
        cadence: CadenceGate | None = None,
        observation_cutoff_provider: ObservationCutoffProvider | None = None,
    ) -> None:
        self.config = config
        self.snapshot_provider = snapshot_provider
        self.market_provider = market_provider
        self.decision_builder = decision_builder
        self.account = account
        self.account_ledger = account_ledger
        self.risk_policy = risk_policy
        self.orders = orders
        self.ledgers = ledgers
        self.order_factory = order_factory
        self.risk_kernel = risk_kernel or RiskKernel(kill_switch=KillSwitch(ledgers))
        self.reconciliation = reconciliation or ReconciliationService(ledgers)
        self.learning = learning
        if (cadence is None) != (observation_cutoff_provider is None):
            raise ValueError("cadence and observation_cutoff_provider must be supplied together")
        self.cadence = cadence
        self.observation_cutoff_provider = observation_cutoff_provider
        self._stop = threading.Event()
        self._sync_dashboard_control()

    def run_once(self, cutoff: datetime) -> RuntimeCycle:
        cutoff = self._closed_cutoff(cutoff)
        prior = self._prior_cycle(cutoff)
        if prior is not None:
            return prior
        cycle_id = uuid5(
            NAMESPACE_URL,
            f"advisorai-v3/runtime-cycle/{self.config.run_id}/{cutoff.isoformat()}",
        )
        cycle = RuntimeCycle(
            cycle_id=cycle_id,
            run_id=self.config.run_id,
            owner_session_id=self.config.owner_session_id,
            cutoff=cutoff,
            stage=RuntimeStage.STARTED,
            created_at=cutoff,
        )
        self._record(cycle)
        try:
            self._sync_dashboard_control()
            if self.risk_kernel.kill_switch.tripped:
                return self._finish(
                    cycle,
                    RuntimeStage.ABSTAINED,
                    reasons=(f"kill_switch:{self.risk_kernel.kill_switch.reason}",),
                )
            if self.cadence is not None and self.observation_cutoff_provider is not None:
                readiness = self.cadence.check(
                    cutoff=cutoff,
                    observed_cutoffs=tuple(self.observation_cutoff_provider(cutoff)),
                )
                if not readiness.passed:
                    return self._finish(cycle, RuntimeStage.ABSTAINED, reasons=readiness.reasons)
            snapshot = self.snapshot_provider(cutoff)
            if snapshot.as_of > cutoff:
                return self._finish(
                    cycle,
                    RuntimeStage.ABSTAINED,
                    reasons=("snapshot_after_cutoff",),
                    snapshot_id=snapshot.artifact_id,
                )
            if snapshot.data_quality_state.lower() not in {"validated", "gold"}:
                return self._finish(
                    cycle,
                    RuntimeStage.ABSTAINED,
                    reasons=(f"data_quality:{snapshot.data_quality_state}",),
                    snapshot_id=snapshot.artifact_id,
                )
            market = self.market_provider(snapshot)
            result = self.decision_builder(snapshot, self.account, market)
            decision_id = self._decision_id(result)
            base = cycle.model_copy(
                update={
                    "snapshot_id": snapshot.artifact_id,
                    "decision_id": decision_id,
                }
            )
            if result.decision.abstained or not result.decision.gate.passed:
                self._record_learning_decision(result, snapshot=snapshot, risk=None, order_ids=())
                return self._finish(
                    base,
                    RuntimeStage.ABSTAINED,
                    reasons=result.decision.missing_evidence or result.decision.gate.reasons,
                )
            risk = self.risk_kernel.evaluate(
                RiskRequest(
                    target=result.decision.target_portfolio,
                    account=self.account,
                    market=market,
                    policy=self.risk_policy,
                )
            )
            with_risk = base.model_copy(update={"risk_decision_id": risk.artifact_id})
            if risk.outcome is RiskOutcome.REJECTED:
                self._record_learning_decision(result, snapshot=snapshot, risk=risk, order_ids=())
                return self._finish(with_risk, RuntimeStage.RISK_REJECTED, reasons=risk.reasons)
            admitted = DecisionPipelineResult(decision=result.decision, risk_decision=risk)
            materialized = tuple(self.order_factory(admitted, self.account, market))
            if len(materialized) > self.config.max_order_count_per_cycle:
                self._record_learning_decision(result, snapshot=snapshot, risk=risk, order_ids=())
                return self._finish(
                    with_risk, RuntimeStage.RISK_REJECTED, reasons=("max_order_count_per_cycle",)
                )
            routed: list[UUID] = []
            for order in materialized:
                created = self.orders.create(order)
                check = self.risk_kernel.check_order(
                    order=created, account=self.account, market=market, policy=self.risk_policy
                )
                if not check.approved:
                    self.orders.transition(created.artifact_id, OrderState.REJECTED)
                    self._record_learning_decision(
                        result, snapshot=snapshot, risk=risk, order_ids=tuple(routed)
                    )
                    return self._finish(
                        with_risk,
                        RuntimeStage.RISK_REJECTED,
                        reasons=check.reasons,
                        order_ids=tuple(routed),
                    )
                self.orders.approve_risk(created.artifact_id, risk, order_check=check)
                acknowledgement = self.orders.route(created.artifact_id)
                if acknowledgement is None:
                    pending = tuple((*routed, created.artifact_id))
                    self._record_learning_decision(
                        result, snapshot=snapshot, risk=risk, order_ids=pending
                    )
                    return self._finish(
                        with_risk,
                        RuntimeStage.RECONCILIATION_FAILED,
                        reasons=("ambiguous_order_acknowledgement_pending",),
                        order_ids=pending,
                    )
                if not acknowledgement.accepted:
                    pending = tuple((*routed, created.artifact_id))
                    self._record_learning_decision(
                        result, snapshot=snapshot, risk=risk, order_ids=pending
                    )
                    return self._finish(
                        with_risk,
                        RuntimeStage.FAILED,
                        reasons=("venue_rejected_order",),
                        order_ids=pending,
                    )
                routed.append(created.artifact_id)
            fills = self._settle_paper_venue_fills(current_order_ids=frozenset(routed))
            for order_id, order in tuple(self.orders.orders.items()):
                if order.state in {
                    OrderState.FILLED,
                    OrderState.CANCELLED,
                    OrderState.REJECTED,
                    OrderState.EXPIRED,
                }:
                    self.orders.reconcile(order_id)
            reconciliation = self.reconcile_once()
            if not reconciliation.reconciled:
                self._record_learning_decision(
                    result,
                    snapshot=snapshot,
                    risk=risk,
                    order_ids=tuple(routed),
                    fill_ids=tuple(fill.artifact_id for fill in fills),
                    reconciliation_id=reconciliation.artifact_id,
                )
                return self._finish(
                    with_risk,
                    RuntimeStage.RECONCILIATION_FAILED,
                    reasons=reconciliation.discrepancies,
                    order_ids=tuple(routed),
                )
            stage = RuntimeStage.NO_TRADE if not routed else RuntimeStage.ORDERS_ROUTED
            self._record_learning_decision(
                result,
                snapshot=snapshot,
                risk=risk,
                order_ids=tuple(routed),
                fill_ids=tuple(fill.artifact_id for fill in fills),
                reconciliation_id=reconciliation.artifact_id,
            )
            return self._finish(with_risk, stage, order_ids=tuple(routed))
        except Exception as exc:
            return self._finish(cycle, RuntimeStage.FAILED, reasons=(type(exc).__name__,))

    def reconcile_once(self, *, venue_snapshot=None):
        if venue_snapshot is None:
            adapter = getattr(self.orders, "adapter", None)
            fetch_snapshot = getattr(adapter, "account_snapshot", None)
            if callable(fetch_snapshot):
                try:
                    venue_snapshot = fetch_snapshot()
                    open_orders = getattr(adapter, "open_orders", None)
                    if callable(open_orders):
                        acknowledgements = open_orders()
                        venue_snapshot = replace(
                            venue_snapshot,
                            venue_open_order_ids=frozenset(
                                acknowledgement.order_id for acknowledgement in acknowledgements
                            ),
                        )
                except Exception:
                    self.risk_kernel.kill_switch.trip("paper_venue_reconciliation_error")
                    raise
        result = self.reconciliation.run(
            account=self.account,
            orders=self.orders,
            account_ledger=self.account_ledger,
            venue_snapshot=venue_snapshot,
        )
        if not result.reconciled:
            self.risk_kernel.kill_switch.trip("paper_reconciliation_mismatch")
        return result

    def _settle_paper_venue_fills(self, *, current_order_ids: frozenset[UUID] = frozenset()):
        """Apply current adapter fills, including fills already present at the venue.

        Simulated venues may create fills synchronously through ``execute_pending``.
        Testnet adapters instead expose the provider's current fill projection.
        Both paths converge on the same idempotent OMS and account ledgers.
        """
        adapter = self.orders.adapter
        execute_pending = getattr(adapter, "execute_pending", None)
        created = execute_pending(at=datetime.now(UTC)) if callable(execute_pending) else ()
        current = getattr(adapter, "fills", ())
        if callable(current):
            current = current()
        observed = {}
        for fill in (*tuple(created or ()), *tuple(current or ())):
            if not isinstance(fill, Fill):
                raise RuntimeError("paper venue fill projection contains a non-Fill record")
            prior = observed.get(fill.venue_fill_id)
            if prior is not None and prior.model_dump(
                mode="json", exclude={"artifact_id", "created_at"}
            ) != fill.model_dump(mode="json", exclude={"artifact_id", "created_at"}):
                raise RuntimeError("paper venue reused a fill identity with changed contents")
            observed[fill.venue_fill_id] = fill
        if observed and self.account_ledger is None:
            raise RuntimeError("paper venue fills require a durable AccountLedger")
        current_order_fills = []
        for fill in observed.values():
            order = self.orders.orders.get(fill.order_id)
            if order is None:
                raise RuntimeError("paper venue fill references an unknown local order")
            prior_fill = self.orders.fills.get(fill.venue_fill_id)
            if prior_fill is None:
                self.orders.record_fill(fill, order.side)
            elif prior_fill.model_dump(
                mode="json", exclude={"artifact_id", "created_at"}
            ) != fill.model_dump(mode="json", exclude={"artifact_id", "created_at"}):
                raise RuntimeError("paper venue fill differs from the durable OMS fill")
            assert self.account_ledger is not None
            self.account_ledger.apply_fill(fill, order.side, order.instrument.canonical_id)
            if order.artifact_id in current_order_ids:
                current_order_fills.append(prior_fill or self.orders.fills[fill.venue_fill_id])
        return tuple(current_order_fills)

    def run_forever(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_cycles: int | None = None,
    ) -> tuple[RuntimeCycle, ...]:
        """Run the owner process until stopped; no live endpoint is ever admitted."""

        now = clock or (lambda: datetime.now(UTC))
        cycles: list[RuntimeCycle] = []
        while not self._stop.is_set() and (max_cycles is None or len(cycles) < max_cycles):
            current = now().astimezone(UTC)
            cutoff = current.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            cycles.append(self.run_once(cutoff))
            if max_cycles is not None and len(cycles) >= max_cycles:
                break
            sleep(self.config.decision_interval_seconds)
        return tuple(cycles)

    def stop(self) -> None:
        self._stop.set()

    def halt(self, reason: str) -> None:
        """Trip the independent kill switch and stop the scheduler."""

        self.risk_kernel.kill_switch.trip(reason)
        self.stop()

    def resume(self, *, approved_by: str) -> None:
        """Resume only after an explicit operator reset of the kill switch."""

        self.risk_kernel.kill_switch.reset(approved_by=approved_by)
        self._stop.clear()

    def _sync_dashboard_control(self) -> None:
        """Apply the newest accepted dashboard control after durable restart.

        Dashboard commands are persisted in the incident ledger even when the
        API process is not holding a runtime object.  A security event written
        after a command (for example a direct operator reset or a
        reconciliation trip) takes precedence over that older command.
        """

        events = self.ledgers.events(LedgerNamespace.INCIDENT)
        latest_command: tuple[int, str, str, str] | None = None
        latest_security_event = -1
        for index, event in enumerate(events):
            if event.event_type in {"kill_switch_tripped", "kill_switch_reset"}:
                latest_security_event = index
                continue
            if event.event_type != "dashboard_command_recorded":
                continue
            receipt = event.payload.get("receipt")
            if not isinstance(receipt, dict) or receipt.get("status") != "accepted":
                continue
            command = str(receipt.get("command", ""))
            if command not in {"halt_paper", "resume_paper"}:
                continue
            latest_command = (
                index,
                command,
                str(event.payload.get("reason", "dashboard control")),
                str(event.payload.get("actor", "owner")),
            )
        if latest_command is None or latest_command[0] <= latest_security_event:
            return
        _, command, reason, actor = latest_command
        if command == "halt_paper":
            self.risk_kernel.kill_switch.trip(reason)
            self.stop()
        elif self.risk_kernel.kill_switch.tripped:
            self.risk_kernel.kill_switch.reset(approved_by=actor)
            self._stop.clear()

    @staticmethod
    def _decision_id(result: DecisionPipelineResult) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"advisorai-v3/decision/{result.decision.mission_id}/{result.decision.snapshot_id}",
        )

    def _record_learning_decision(
        self,
        result: DecisionPipelineResult,
        *,
        snapshot: Snapshot,
        risk,
        order_ids: Sequence[UUID],
        fill_ids: Sequence[UUID] = (),
        reconciliation_id: UUID | None = None,
    ) -> None:
        if self.learning is None:
            return
        target = result.decision.target_portfolio
        assets = tuple(sorted({position.instrument.canonical_id for position in target.positions}))
        record = PaperDecisionRecord(
            decision_id=self._decision_id(result),
            mission_id=result.decision.mission_id,
            snapshot_id=snapshot.artifact_id,
            evidence_ids=result.decision.evidence_ids,
            target_id=target.artifact_id,
            risk_decision_id=risk.artifact_id if risk is not None else None,
            order_ids=tuple(order_ids),
            fill_ids=tuple(fill_ids),
            reconciliation_id=reconciliation_id,
            subject="decision-pipeline",
            subject_version="paper-runtime-v1",
            role="synthesizer",
            asset=",".join(assets) or "BTC,ETH",
            horizon="1h",
            cutoff=result.decision.created_at,
            horizon_end=result.decision.expires_at,
        )
        self.learning.record_decision(record)

    @staticmethod
    def _closed_cutoff(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision cutoff must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized.minute or normalized.second or normalized.microsecond:
            raise ValueError("paper decisions require a closed one-hour cutoff")
        return normalized

    def _record(self, cycle: RuntimeCycle) -> None:
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="paper_runtime_cycle",
                idempotency_key=f"runtime:{self.config.run_id}:{cycle.cycle_id}:{cycle.stage.value}",
                occurred_at=cycle.created_at,
                payload={"cycle": cycle.model_dump(mode="json", round_trip=True)},
            )
        )

    def _prior_cycle(self, cutoff: datetime) -> RuntimeCycle | None:
        prior: RuntimeCycle | None = None
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            if event.event_type != "paper_runtime_cycle":
                continue
            payload = event.payload.get("cycle")
            if not isinstance(payload, dict):
                continue
            try:
                cycle = RuntimeCycle.model_validate(payload)
            except Exception:
                continue
            if cycle.run_id == self.config.run_id and cycle.cutoff == cutoff:
                prior = cycle
        if prior is None:
            return None
        if prior.stage is not RuntimeStage.STARTED:
            return prior
        return self._finish(
            prior,
            RuntimeStage.FAILED,
            reasons=("interrupted_cycle_requires_recovery",),
        )

    def _finish(
        self,
        cycle: RuntimeCycle,
        stage: RuntimeStage,
        *,
        reasons: Sequence[str] = (),
        snapshot_id: UUID | None = None,
        order_ids: Sequence[UUID] = (),
    ) -> RuntimeCycle:
        finished = cycle.model_copy(
            update={
                "stage": stage,
                "reasons": tuple(dict.fromkeys(item for item in reasons if item)),
                "snapshot_id": snapshot_id or cycle.snapshot_id,
                "order_ids": tuple(order_ids),
            }
        )
        self._record(finished)
        return finished


__all__ = [
    "PaperRuntime",
    "PaperRuntimeConfig",
    "RuntimeCycle",
    "RuntimeStage",
    "build_default_orders",
]
