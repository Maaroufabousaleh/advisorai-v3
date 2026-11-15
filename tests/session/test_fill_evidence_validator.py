"""Regression for the offline P03 fill-evidence validator.

Builds synthetic paper-lifecycle run directories (manifest + oms.sqlite)
through the real OMS, account ledger, and reconciliation service with
synthetic fills only. No network, no credentials, no venue writes.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, "scripts")

from validate_binance_fill_evidence import main, validate_run

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
    account_ledger.apply_fill(fill.model_copy(update={"fee": applied_fee}), order.side, "BTC")
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


def test_validator_classifies_fill_from_ledger_without_ingestion_block(
    tmp_path,
) -> None:
    """Runs that raised before writing fill_ingestion still classify by ledger.

    Mirrors the pre-fix failed fill run: no ingestion block in the
    manifest, but a real fill linkage in oms.sqlite. The validator must
    report a fill pass (not cancellation-only) while refusing to certify
    because the lifecycle itself did not pass.
    """
    run_dir = _fill_run(tmp_path)
    manifest_path = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result"]["status"] = "failed"
    manifest["result"]["reason"] = "lifecycle_blocked"
    del manifest["result"]["fill_ingestion"]
    del manifest["result"]["reconciliation"]
    del manifest["result"]["restart_recovery"]
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    observed = next(check for check in report.checks if check.name == "fill_observed")
    assert observed.passed is True
    assert "source=ledger" in observed.detail


def test_cli_exit_codes_distinguish_input_fill_and_failure(tmp_path, capsys) -> None:
    """CLI: 2 on missing evidence dir, 3 on cancel-only, 0 on fill pass."""
    assert main(["--evidence-dir", str(tmp_path / "absent")]) == 2

    cancel_dir = tmp_path / "synthetic-cancel-run"
    ledgers = SqliteLedgers(cancel_dir / "oms.sqlite")
    _write_run(
        cancel_dir,
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
    assert main(["--evidence-dir", str(cancel_dir)]) == 3
    assert main(["--evidence-dir", str(_fill_run(tmp_path))]) == 0


_PRESERVED_FAILED_RUN = Path(
    "artifacts/phase2/binance-spot-testnet/paper-lifecycle/20261114T005138.117654Z"
)
requires_preserved_failed_run = pytest.mark.skipif(
    not (_PRESERVED_FAILED_RUN / "binance-spot-testnet-paper-lifecycle.json").is_file()
    or not (_PRESERVED_FAILED_RUN / "oms.sqlite").is_file(),
    reason="phase-2 venue evidence is generated locally and not committed",
)


@requires_preserved_failed_run
def test_preserved_failed_run_shows_venue_fill_during_cancel_race() -> None:
    """P03 race label: the venue reproduced fill-during-cancel in 005138.

    Reads the preserved failed paper-lifecycle run (manifest + oms.sqlite
    copy semantics via validate_run) and pins the venue race label:
    `order_cancel_pending` precedes `fill_recorded`, the terminal OMS
    state is `filled` with zero cancel-ack events, and final
    reconciliation failed closed. The validator must report a fill
    pass (source=ledger, no ingestion block) while refusing P03
    certification. Read-only; no network, no credentials, no venue writes.
    """
    run_dir = _PRESERVED_FAILED_RUN
    report = validate_run(run_dir)
    observed = next(check for check in report.checks if check.name == "fill_observed")
    assert report.fill_pass is True
    assert observed.passed is True
    assert "source=ledger" in observed.detail
    assert report.p03_certified is False
    terminal = next(check for check in report.checks if check.name == "terminal_fill_chain")
    assert terminal.passed is True
    sequence = next(check for check in report.checks if check.name == "event_sequence_consistent")
    assert "order_cancel_pending" in sequence.detail
    assert "fill_recorded" in sequence.detail
    assert sequence.detail.index("order_cancel_pending") < sequence.detail.index("fill_recorded")


def test_validator_rejects_ingestion_count_mismatch(tmp_path) -> None:
    """P03: manifest fill counts that disagree refuse certification.

    The manifest claims one real fill but two OMS fills — an ingestion
    accounting defect. The validator must still see the fill (fill pass)
    but refuse P03 certification naming ``fill_counts_agree``.
    Synthetic only; no network, no credentials, no venue writes.
    """
    run_dir = _fill_run(tmp_path)
    manifest_path = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result"]["fill_ingestion"]["oms_fill_count"] = 2
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "fill_counts_agree" in names


def test_validator_rejects_multiple_submissions(tmp_path) -> None:
    """P03: a run with two signed submissions refuses certification.

    The single-order lifecycle admits exactly one signed submission; a
    manifest recording two is a resubmission/no-duplicate defect. The
    validator must still see the fill (fill pass) but refuse P03
    certification naming ``single_submission``. Synthetic only; no
    network, no credentials, no venue writes.
    """
    run_dir = _fill_run(tmp_path)
    manifest_path = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result"]["signed_submission_count"] = 2
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "single_submission" in names


def test_validator_rejects_restart_resubmission(tmp_path) -> None:
    """P03: a restart that resubmitted refuses certification.

    Restart recovery must rehydrate the terminal state with zero new
    signed submissions; a manifest recording one resubmission after
    restart is a no-duplicate defect. The validator must still see the
    fill (fill pass) but refuse P03 certification naming
    ``restart_no_duplicate``. Synthetic only; no network, no
    credentials, no venue writes.
    """
    run_dir = _fill_run(tmp_path)
    manifest_path = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result"]["restart_recovery"]["signed_submissions_after_restart"] = 1
    manifest["result"]["restart_recovery"]["duplicate_submission"] = True
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "restart_no_duplicate" in names


def test_validator_rejects_unclean_manifest_reconciliation(tmp_path) -> None:
    """P03: a manifest reporting dirty reconciliation refuses certification.

    The ledger-side fill linkage may be exact while the manifest's own
    reconciliation block reports ``reconciled=False`` with a nonzero
    discrepancy count — a manifest/ledger consistency defect. The
    validator must still see the fill (fill pass) but refuse P03
    certification naming ``manifest_reconciliation_clean``. Synthetic
    only; no network, no credentials, no venue writes.
    """
    run_dir = _fill_run(tmp_path)
    manifest_path = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result"]["reconciliation"]["reconciled"] = False
    manifest["result"]["reconciliation"]["discrepancy_count"] = 1
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "manifest_reconciliation_clean" in names


def test_validator_rejects_sequence_missing_fill_transition(tmp_path) -> None:
    """P03: a sequence missing the fill transition refuses certification.

    The ledger-side fill linkage may be exact while the manifest's
    ``oms_event_sequence`` omits ``order_filled`` — a manifest/ledger
    consistency defect. The validator must still see the fill (fill
    pass) but refuse P03 certification naming
    ``event_sequence_consistent``. Synthetic only; no network, no
    credentials, no venue writes.
    """
    run_dir = _fill_run(tmp_path)
    manifest_path = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sequence = manifest["result"]["oms_event_sequence"]
    assert "order_filled" in sequence
    manifest["result"]["oms_event_sequence"] = [
        event for event in sequence if event != "order_filled"
    ]
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "event_sequence_consistent" in names


def test_validator_rejects_fill_quantity_mismatch(tmp_path) -> None:
    """P03: fills totaling less than the order refuse certification.

    A partial fill (``fill_recorded`` quantity below the order quantity)
    is a genuine fill path the validator must see (fill pass) while
    refusing P03 certification naming ``fill_quantity_matches_order``.
    Built through the real OMS + account ledger with a synthetic
    half-quantity fill; no network, no credentials, no venue writes.
    """
    run_dir = _fill_run(tmp_path)
    ledger_path = run_dir / "oms.sqlite"
    connection = sqlite3.connect(ledger_path)
    try:
        row = connection.execute(
            "SELECT payload_json FROM order_events WHERE event_type='fill_recorded'"
        ).fetchone()
        payload = json.loads(row[0])
        assert str(payload["artifact"]["quantity"]) == "0.01"
        payload["artifact"]["quantity"] = "0.005"
        connection.execute(
            "UPDATE order_events SET payload_json=? WHERE event_type='fill_recorded'",
            (json.dumps(payload),),
        )
        connection.commit()
    finally:
        connection.close()
    report = validate_run(run_dir)
    assert report.fill_pass is True
    assert report.p03_certified is False
    names = [check.name for check in report.failed]
    assert "fill_quantity_matches_order" in names
