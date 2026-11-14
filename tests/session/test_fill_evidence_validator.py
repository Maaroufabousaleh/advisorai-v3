"""Regression for the offline P03 fill-evidence validator.

Builds synthetic paper-lifecycle run directories (manifest + oms.sqlite)
through the real OMS, account ledger, and reconciliation service with
synthetic fills only. No network, no credentials, no venue writes.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, "scripts")

from validate_binance_fill_evidence import validate_run

from advisorai.contracts import ExecutionPlan, Fill, InstrumentIdentity, Order, OrderState
from advisorai.execution import (
    AccountLedger,
    AccountState,
    OrderManager,
    ReconciliationService,
)
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.ledger import SqliteLedgers
from tests.session.test_binance_spot_owner_adapter import _owner_adapter, _Transport

_NOW = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)


def _order() -> Order:
    instrument = InstrumentIdentity(
        canonical_id="BTC",
        asset_class="crypto",
        venue="paper",
        venue_symbol="BTCUSDT",
    )
    plan = ExecutionPlan(
        created_at=_NOW,
        risk_decision_id=uuid4(),
        target_portfolio_id=uuid4(),
        policy_version="p03-validator-v1",
        instructions=("passive_limit",),
        expires_at=_NOW + timedelta(minutes=5),
    )
    return Order(
        parent_intent_id=uuid4(),
        execution_plan_id=plan.artifact_id,
        instrument=instrument,
        side="buy",
        quantity=Decimal("0.01"),
        order_type="limit",
        price=Decimal("50000"),
        time_in_force="GTC",
        idempotency_key=f"p03-validator-{uuid4()}",
    )


def _write_run(
    run_dir: Path,
    *,
    ledgers: SqliteLedgers,
    fill_ingestion: dict,
    terminal_state: str,
    reconciled: bool,
    restart: dict,
    sequence: list[str],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "advisorai.phase2.binance-spot-testnet.paper-lifecycle.v1",
        "run_id": run_dir.name,
        "recorded_at": _NOW.isoformat(),
        "result": {
            "status": "passed",
            "reason": "supervised_fake_funds_lifecycle_passed",
            "signed_submission_count": 1,
            "signed_cancellation_count": 0,
            "terminal_oms_state": terminal_state,
            "fill_ingestion": fill_ingestion,
            "reconciliation": {
                "reconciled": reconciled,
                "artifact_sha256": "0" * 64,
                "discrepancy_count": 0 if reconciled else 1,
                "venue_open_order_count": 0,
            },
            "restart_recovery": restart,
            "oms_event_sequence": sequence,
        },
    }
    (run_dir / "binance-spot-testnet-paper-lifecycle.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _fill_run(tmp_path: Path, *, fee_mismatch: bool = False) -> Path:
    run_dir = tmp_path / "synthetic-fill-run"
    ledgers = SqliteLedgers(run_dir / "oms.sqlite")
    transport = _Transport()
    adapter = _owner_adapter(ledgers, transport)
    oms = OrderManager(ledgers, adapter)
    order = _order()
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    oms.transition(order.artifact_id, OrderState.ROUTED)
    oms.transition(order.artifact_id, OrderState.ACKNOWLEDGED)
    fill = Fill(
        order_id=order.artifact_id,
        venue_fill_id="BTCUSDT:synthetic-1",
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
        fee=Decimal("0.05"),
        fee_currency="USDT",
        occurred_at=_NOW,
    )
    oms.record_fill(fill, order.side)
    account = AccountState(
        cash=Decimal("1000"),
        marks={"BTC": Decimal("50000")},
        margin_used=Decimal("0"),
        margin_available=Decimal("1000"),
        as_of=_NOW,
    )
    account_ledger = AccountLedger(ledgers, account)
    applied_fee = Decimal("0.06") if fee_mismatch else Decimal("0.05")
    account_ledger.apply_fill(
        fill.model_copy(update={"fee": applied_fee}), order.side, "BTC"
    )
    oms.reconcile(order.artifact_id)
    venue = VenueAccountSnapshot(
        as_of=_NOW,
        cash=account.cash,
        positions={"BTC": Decimal("0.01")},
        margin_used=Decimal("0"),
        margin_available=account.margin_available,
        venue_open_order_ids=frozenset(),
    )
    result = ReconciliationService(ledgers).run(
        account=account, orders=oms, account_ledger=account_ledger, venue_snapshot=venue
    )
    assert result.reconciled
    _write_run(
        run_dir,
        ledgers=ledgers,
        fill_ingestion={"status": "fill_observed", "real_fill_count": 1, "oms_fill_count": 1},
        terminal_state="reconciled",
        reconciled=True,
        restart={
            "status": "passed",
            "hydrated_terminal_state": "reconciled",
            "venue_query_succeeded": True,
            "signed_submissions_after_restart": 0,
            "duplicate_submission": False,
        },
        sequence=[
            "order_created",
            "order_risk_approved",
            "order_routed",
            "order_acknowledged",
            "fill_recorded",
            "order_filled",
            "order_reconciled",
            "reconciliation_recorded",
        ],
    )
    return run_dir


def test_validator_certifies_synthetic_fill_pass(tmp_path) -> None:
    report = validate_run(_fill_run(tmp_path))
    assert report.fill_pass is True
    assert report.failed == []
    assert report.p03_certified is True


def test_validator_rejects_fee_mismatch_between_ledgers(tmp_path) -> None:
    report = validate_run(_fill_run(tmp_path, fee_mismatch=True))
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "account_ledger_matches_fills" in names


def test_validator_reports_cancellation_only_without_certifying(tmp_path) -> None:
    run_dir = tmp_path / "synthetic-cancel-run"
    ledgers = SqliteLedgers(run_dir / "oms.sqlite")
    _write_run(
        run_dir,
        ledgers=ledgers,
        fill_ingestion={"status": "no_fill_observed", "real_fill_count": 0, "oms_fill_count": 0},
        terminal_state="reconciled",
        reconciled=True,
        restart={
            "status": "passed",
            "hydrated_terminal_state": "reconciled",
            "venue_query_succeeded": True,
            "signed_submissions_after_restart": 0,
            "duplicate_submission": False,
        },
        sequence=[
            "order_created",
            "order_risk_approved",
            "order_routed",
            "order_acknowledged",
            "order_cancel_pending",
            "order_cancelled",
            "order_reconciled",
            "reconciliation_recorded",
        ],
    )
    report = validate_run(run_dir)
    assert report.fill_pass is False
    assert report.p03_certified is False
