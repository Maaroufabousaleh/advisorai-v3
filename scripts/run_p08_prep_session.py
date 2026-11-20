"""P08 prep session: end-to-end paper run on the pinned revision (NOT qualification).

Runs the real integrated stack (supervisor + runtime + reference decision
model + RiskKernel + OMS + paper venue + reconciliation) through clean
cycles, a clean stop, a restart, and more cycles, then writes a per-session
evidence report following the P10 checklist format.

Explicitly LABELED REHEARSAL, never qualification evidence: the decision
model is the `reference` adapter, not an approved strategy (P06 open), and
inputs are fixtures. Asserts only structural invariants: terminal stages,
no duplicate order ids, reconciliation clean after every cycle, no open
orders at the end, leases released, restart links the prior end.
Optional `--route-cycles N` adds a third phase after the restart: LONG-stance
decisions through a passing fixture council with the reference adapter
explicitly approved (rehearsal-only; NOT a strategy approval), exercising
`orders_routed`/`risk_rejected` plus cancel → acknowledge → reconcile to a
clean close. Default 0 preserves the abstain-only rehearsal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from advisorai.agents import EvidenceCouncil, MissionRequest  # noqa: E402
from advisorai.agents.router import MissionKind  # noqa: E402
from advisorai.api.service import AdvisorService  # noqa: E402
from advisorai.contracts import (  # noqa: E402
    AssetClass,
    InstrumentIdentity,
    RiskLimit,
    RiskPolicy,
    Snapshot,
)
from advisorai.decision import (  # noqa: E402
    DecisionModelArtifact,
    DecisionStance,
    ReferenceDecisionAdapter,
)
from advisorai.execution import (  # noqa: E402
    AccountState,
    OrderManager,
    PaperVenueAdapter,
    RiskMarketState,
)
from advisorai.ledger import SqliteLedgers  # noqa: E402
from advisorai.runtime import PaperRuntime, PaperRuntimeConfig  # noqa: E402
from advisorai.session import SessionStopChecks, SessionSupervisor  # noqa: E402
from advisorai.session.application import PaperOwnerApplication  # noqa: E402

STRATEGY_LABEL = "reference-v1-NOT-APPROVED (P06 open; rehearsal only)"
TERMINAL_STAGES = {"abstained", "no_trade", "orders_routed", "risk_rejected"}


def _ledgers_and_supervisor(state_dir: Path, label: str, code_hash: str, config_hash: str):
    ledger_path = state_dir / f"{label}.sqlite3"
    ledgers = SqliteLedgers(ledger_path)
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir / "supervisor",
        lock_identity="p08-prep",
        code_hash=code_hash,
        config_hash=config_hash,
    )
    return supervisor, ledgers, ledger_path


def _runtime(
    ledgers,
    run_id: str,
    code_hash: str,
    config_hash: str,
    *,
    council=None,
    stance=None,
    mission_kind=MissionKind.TRADE,
    user_text="p08 prep session",
    approved_strategy_adapter: str | None = None,
    risk_limits: tuple | None = None,
):
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="p08-prep-fixture")
    account = AccountState(cash=Decimal("100000"), as_of=cutoff, marks={"BTC": Decimal("50000")})
    market = RiskMarketState(marks={"BTC": Decimal("50000")})
    artifact = DecisionModelArtifact(
        model_name="reference",
        adapter_name="reference-v1",
        code_hash=code_hash,
        config_hash=config_hash,
        snapshot_id=snapshot.artifact_id,
    )
    service = AdvisorService(
        council if council is not None else EvidenceCouncil({}),
        ledgers,
        decision_model=ReferenceDecisionAdapter(artifact, directional_stance=stance),
    )
    assert isinstance(service.decision_model, ReferenceDecisionAdapter)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=cutoff,
        hard_limits=(
            risk_limits
            if risk_limits is not None
            else (RiskLimit(name="gross", limit=Decimal("50000"), unit="USD"),)
        ),
        approved_by="owner",
    )
    orders = OrderManager(ledgers, PaperVenueAdapter())
    return PaperRuntime(
        config=PaperRuntimeConfig(
            run_id=run_id,
            environment="paper",
            approved_strategy_adapter=approved_strategy_adapter,
        ),
        snapshot_provider=lambda cutoff: snapshot,
        market_provider=lambda snapshot: market,
        decision_builder=lambda snapshot, account, market: service.build_decision(
            request=MissionRequest(kind=mission_kind, user_text=user_text),
            snapshot=snapshot,
            account=account,
            market=market,
            targets={btc: Decimal("0")},
            risk_policy_version="risk-v1",
        ),
        account=account,
        risk_policy=policy,
        orders=orders,
        ledgers=ledgers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, help="Out-of-tree evidence root.")
    parser.add_argument("--cycles", type=int, default=3, help="Cycles per leg (pre/post restart).")
    parser.add_argument(
        "--route-cycles",
        type=int,
        default=0,
        help=(
            "Optional third phase: LONG-stance route rehearsal with the "
            "reference adapter explicitly approved (rehearsal-only, NOT a "
            "strategy approval). Exercises orders_routed/risk_rejected plus "
            "cancel-to-clean through the same stack. Default 0 preserves the "
            "abstain-only rehearsal."
        ),
    )
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    wall_start = datetime.now(UTC)

    revision = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    config_hash = hashlib.sha256(
        b"risk-v1|gross=50000USD|reference-v1|paper|BTC=0-targets"
    ).hexdigest()
    code_hash = hashlib.sha256(f"p08-prep:{revision}".encode()).hexdigest()

    supervisor, ledgers, ledger_path = _ledgers_and_supervisor(
        state_dir, "p08-prep", code_hash, config_hash
    )
    start, _recovery = supervisor.start(reason="p08 prep session")
    cycles: list[dict] = []
    order_ids: list[str] = []
    base_cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    for leg in (0, 1):
        runtime = _runtime(ledgers, "p08-prep-run", code_hash, config_hash)
        for i in range(args.cycles):
            cutoff = base_cutoff + timedelta(hours=leg * args.cycles + i)
            cycle = runtime.run_once(cutoff)
            assert cycle.stage.value in TERMINAL_STAGES, cycle.stage.value
            recon = runtime.reconcile_once()
            assert recon.reconciled, recon.discrepancies
            order_ids.extend(str(item) for item in cycle.order_ids)
            cycles.append(
                {
                    "leg": leg,
                    "cycle_id": str(cycle.cycle_id),
                    "stage": cycle.stage.value,
                    "decision_id": str(cycle.decision_id) if cycle.decision_id else None,
                    "risk_decision_id": str(cycle.risk_decision_id)
                    if cycle.risk_decision_id
                    else None,
                    "order_ids": [str(item) for item in cycle.order_ids],
                    "reconciliation_id": str(recon.artifact_id),
                    "reconciled": recon.reconciled,
                }
            )
        if leg == 0:
            supervisor.checkpoint()
            end = supervisor.stop()
            assert end.outcome.value == "clean", end.outcome.value
            supervisor2 = SessionSupervisor(
                SqliteLedgers(ledger_path),
                state_dir=state_dir / "supervisor",
                lock_identity="p08-prep",
                code_hash=code_hash,
                config_hash=config_hash,
            )
            start2, recovery = supervisor2.start(reason="p08 prep restart")
            assert start2.prior_end_record_id == end.artifact_id
            assert recovery.recovery_mode.value == "normal", recovery.recovery_mode.value
            supervisor = supervisor2
    route_cycles: list[dict] = []
    route_order_ids: list[str] = []
    if args.route_cycles > 0:
        # Rehearsal-only route leg: the reference adapter is explicitly
        # approved here so the run exercises routing, risk decisions, and
        # cancel-to-clean. This is NOT a strategy approval (P06 open).
        route_runtime = _runtime(
            ledgers,
            "p08-prep-route-run",
            code_hash,
            config_hash,
            council=PaperOwnerApplication._council(),
            stance=DecisionStance.LONG,
            mission_kind=MissionKind.RECOMMENDATION,
            user_text="scheduled owner paper evaluation",
            approved_strategy_adapter="reference-v1",
            risk_limits=(
                RiskLimit(name="max_gross_notional", limit=Decimal("500000"), unit="USD"),
                RiskLimit(name="max_order_notional", limit=Decimal("500000"), unit="USD"),
            ),
        )
        route_base = base_cutoff + timedelta(hours=2 * args.cycles)
        for i in range(args.route_cycles):
            cutoff = route_base + timedelta(hours=i)
            cycle = route_runtime.run_once(cutoff)
            assert cycle.stage.value in TERMINAL_STAGES, cycle.stage.value
            recon = route_runtime.reconcile_once()
            assert recon.reconciled, recon.discrepancies
            order_ids.extend(str(item) for item in cycle.order_ids)
            route_order_ids.extend(str(item) for item in cycle.order_ids)
            route_cycles.append(
                {
                    "leg": "route",
                    "cycle_id": str(cycle.cycle_id),
                    "stage": cycle.stage.value,
                    "decision_id": str(cycle.decision_id) if cycle.decision_id else None,
                    "risk_decision_id": str(cycle.risk_decision_id)
                    if cycle.risk_decision_id
                    else None,
                    "order_ids": [str(item) for item in cycle.order_ids],
                    "reconciliation_id": str(recon.artifact_id),
                    "reconciled": recon.reconciled,
                }
            )
        # Close every routed order: cancel → acknowledge → reconcile, so the
        # session ends with no open OMS orders, then verify clean reconcile.
        for item in route_cycles:
            for raw in item["order_ids"]:
                order_id = UUID(raw)
                route_runtime.orders.cancel(order_id)
                route_runtime.orders.acknowledge_cancel(order_id)
                route_runtime.orders.reconcile(order_id)
        closing = route_runtime.reconcile_once()
        assert closing.reconciled, closing.discrepancies
        assert all(
            order.state.value == "reconciled" for order in route_runtime.orders.orders.values()
        ), [order.state.value for order in route_runtime.orders.orders.values()]
    assert len(set(order_ids)) == len(order_ids), "duplicate order ids"
    supervisor.checkpoint()
    # With routed orders in the ledger, the default stop checks require an
    # explicit current order/account verifier; verify against the live route
    # runtime (absent in the default abstain-only path, where it stays None).
    stop_checks = None
    if args.route_cycles > 0:
        stop_checks = SessionStopChecks(
            current_order_account_verifier=lambda ledgers: route_runtime.reconcile_once().reconciled
        )
    final_end = supervisor.stop(stop_checks=stop_checks)
    assert final_end.leases_released is True

    stages: dict[str, int] = {}
    for entry in cycles:
        stages[entry["stage"]] = stages.get(entry["stage"], 0) + 1
    route_stages: dict[str, int] = {}
    for entry in route_cycles:
        route_stages[entry["stage"]] = route_stages.get(entry["stage"], 0) + 1
    report = {
        "label": "P08-PREP-REHEARSAL (not qualification evidence)",
        "strategy": STRATEGY_LABEL,
        "route_phase_approval": (
            "reference-v1 (rehearsal-only explicit approval; NOT a strategy approval)"
            if args.route_cycles > 0
            else None
        ),
        "revision": revision,
        "config_hash": config_hash,
        "venue_profile": "paper (PaperVenueAdapter, in-memory)",
        "session_id": str(start.session_id),
        "run_id": "p08-prep-run",
        "wall_start": wall_start.isoformat(),
        "wall_end": datetime.now(UTC).isoformat(),
        "operator": "integration-lead-agent",
        "stage_counts": stages,
        "cycles": cycles,
        "route_stage_counts": route_stages,
        "route_cycles": route_cycles,
        "order_ids": order_ids,
        "end_outcome": final_end.outcome.value,
        "leases_released": final_end.leases_released,
        "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
    }
    (state_dir / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
