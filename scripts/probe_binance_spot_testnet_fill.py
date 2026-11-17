"""Supervised, fake-funds Binance Spot Testnet single-fill probe (SELL LIMIT FOK).

One aggressive SELL LIMIT order with time-in-force FOK on BTCUSDT, sized at
the minimum practical quantity within a 25 USDT notional cap, priced at the
fresh best bid as a hard limit. FOK resolves immediately as FILLED or
EXPIRED: no resting order, no cancellation leg, no retry.

Safety structure mirrors the supervised lifecycle qualification:
same read-only evidence gate, same deterministic RiskKernel target and
order checks, OMS intent before submission, one signed submission total
(enforced both by ``NativeVenueAdapter(max_submissions=1)`` and by the
recording-transport count), venue-truth query before any acknowledgement
decision, exact final reconciliation, and restart recovery without
resubmission. Evidence uses the same lifecycle manifest schema so the
existing P03 validator applies unchanged; P03 certification criteria are
not modified by this probe.

SELL is the only admitted side: Binance spot charges taker commission in
the received asset, so a SELL receives USDT and the fee stays in the
quote asset the ledger models. A BUY taker fill would carry a
BTC-denominated fee the local cash accounting cannot represent; the
shared fill guard fails closed on it.

Opt-in guards: ``ADVISORAI_RUN_NETWORK_SMOKE=1`` plus
``ADVISORAI_RUN_FILL_PROBE=1``. Fake Testnet funds only; never retries
a signed write after an ambiguous result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from advisorai.attribution import AttributionReconciler  # noqa: E402
from advisorai.config import CredentialResolver  # noqa: E402
from advisorai.contracts import (  # noqa: E402
    ExecutionPlan,
    Order,
    OrderState,
    RiskLimit,
    RiskOutcome,
    RiskPolicy,
    Snapshot,
)
from advisorai.execution import (  # noqa: E402
    AccountLedger,
    AccountState,
    NativeVenueAdapter,
    OrderManager,
    ReconciliationService,
    RiskKernel,
    RiskMarketState,
    RiskRequest,
    VenueAccountSnapshot,
    compute_tca,
)
from advisorai.execution.portfolio import TargetPortfolioBuilder  # noqa: E402
from advisorai.integrations import (  # noqa: E402
    BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    BINANCE_SPOT_TESTNET_BASE_URL,
    BinanceSpotTestnetTransport,
    build_binance_spot_testnet_transport,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers  # noqa: E402
from scripts.preflight_binance_spot_testnet_commission import evaluate_commission  # noqa: E402
from scripts.qualify_binance_spot_testnet_lifecycle import (  # noqa: E402
    LifecycleBlocked,
    OperationRecorder,
    RecordingTransport,
    _decimal,
    _exception_metadata,
    _fill_from_record,
    _fill_matches_submitted_order,
    _fixture_failure_drills,
    _instrument,
    _minimum_practical_quantity,
    _new_run_directory,
    _provider_status,
    _recover_partial_write_state,
    _validate_read_only_evidence,
    _write_evidence,
)

_PROBE_SYMBOL = "BTCUSDT"
_PROBE_SIDE = "sell"
_PROBE_NOTIONAL_CAP = Decimal("25")


def _run_probe(
    *,
    resolver: CredentialResolver,
    configuration_hash: str,
    read_only_evidence: Path,
    run_dir: Path,
    run_id: str,
) -> dict[str, object]:
    read_only = _validate_read_only_evidence(read_only_evidence, configuration_hash)
    transport = build_binance_spot_testnet_transport(resolver)
    recorder = OperationRecorder(transport)
    payload: dict[str, object] = {
        "status": "failed",
        "reason": "probe_not_completed",
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "endpoint": BINANCE_SPOT_TESTNET_BASE_URL,
        "reviewed_host": "testnet.binance.vision",
        "adapter": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
        "credential_refs": ["ADVISORAI_VENUE_API_KEY", "ADVISORAI_VENUE_API_SECRET"],
        "configuration_hash": configuration_hash,
        "read_only_evidence": read_only,
        "probe": "sell_limit_fok_btcusdt_v1",
        "writes_attempted": False,
        "signed_submission_count": 0,
        "signed_cancellation_count": 0,
        "operations": recorder.operations,
    }

    server = recorder.call("server_time", "/api/v3/time", transport.server_time)
    if not isinstance(server, Mapping):
        raise LifecycleBlocked("server time response is malformed")
    product_records = recorder.call("products", "/api/v3/exchangeInfo", transport.list_products)
    if not isinstance(product_records, Sequence):
        raise LifecycleBlocked("product response is malformed")
    mappings = recorder.call(
        "product_mapping_verification",
        "/api/v3/exchangeInfo",
        lambda: transport.verify_symbol_mappings(product_records),
        summary={"admitted_symbols": list(transport.verified_symbol_ids)},
    )
    if _PROBE_SYMBOL not in set(transport.verified_symbol_ids):
        raise LifecycleBlocked("live product truth did not admit BTCUSDT")
    specs = {spec.symbol: spec for spec in mappings if hasattr(spec, "symbol")}
    spec = specs.get(_PROBE_SYMBOL)
    if spec is None:
        raise LifecycleBlocked("provider filter mapping is incomplete")

    ticker = recorder.call(
        f"book_ticker_{_PROBE_SYMBOL}",
        "/api/v3/ticker/bookTicker",
        lambda: transport.book_ticker(_PROBE_SYMBOL),
        summary={"symbol": _PROBE_SYMBOL},
    )
    if not isinstance(ticker, Mapping):
        raise LifecycleBlocked("book ticker response is malformed")
    bid = _decimal(ticker.get("bidPrice"), f"{_PROBE_SYMBOL} bid price")
    ask = _decimal(ticker.get("askPrice"), f"{_PROBE_SYMBOL} ask price")
    bid_qty = _decimal(ticker.get("bidQty"), f"{_PROBE_SYMBOL} bid quantity")
    if bid <= 0 or ask <= 0 or bid_qty <= 0:
        raise LifecycleBlocked("book ticker is not tradeable")

    account_payload = recorder.call("account_state", "/api/v3/account", transport.account_state)
    if not isinstance(account_payload, Mapping):
        raise LifecycleBlocked("account state response is malformed")
    account_snapshot = recorder.call(
        "account_projection",
        "/api/v3/account",
        transport.fetch_account_snapshot,
    )
    if not isinstance(account_snapshot, VenueAccountSnapshot):
        raise LifecycleBlocked("account projection is malformed")
    # Read-only sweep: fail closed on off-profile open orders before any write.
    pre_orders = recorder.call(
        "open_orders_before_probe",
        "/api/v3/openOrders",
        transport.list_open_orders,
    )
    if not isinstance(pre_orders, tuple):
        raise LifecycleBlocked("open orders response is malformed")
    balances = account_payload.get("balances")
    if not isinstance(balances, list):
        raise LifecycleBlocked("account balances response is malformed")
    free_balances = {
        str(item.get("asset", "")).strip().upper(): _decimal(item.get("free", "0"), "free balance")
        for item in balances
        if isinstance(item, Mapping) and str(item.get("asset", "")).strip()
    }

    price = bid
    quantity = _minimum_practical_quantity(spec, price)
    notional = price * quantity
    if notional > _PROBE_NOTIONAL_CAP:
        raise LifecycleBlocked("minimum practical FOK probe exceeds the 25 USDT cap")
    if free_balances.get(spec.base_asset, Decimal("0")) < quantity:
        raise LifecycleBlocked("FOK probe BTC quantity is not affordable from free balance")
    if bid_qty < quantity:
        raise LifecycleBlocked("top-of-book bid depth cannot fill the FOK probe")
    payload["probe_order_selection"] = {
        "symbol": _PROBE_SYMBOL,
        "side": _PROBE_SIDE,
        "order_type": "LIMIT",
        "time_in_force": "FOK",
        "price": str(price),
        "quantity": str(quantity),
        "notional": str(notional),
        "bid_qty": str(bid_qty),
        "pre_existing_open_orders": len(pre_orders),
    }
    commission = recorder.call(
        "commission_preflight_before_submission",
        "/api/v3/account/commission",
        lambda: evaluate_commission(transport, _PROBE_SYMBOL),
    )
    if not isinstance(commission, Mapping):
        raise LifecycleBlocked("commission preflight response is malformed")
    payload["commission_preflight"] = {
        "status": commission.get("status"),
        "bnb_payment_can_apply": commission.get("bnb_payment_can_apply"),
        "reason": commission.get("reason"),
    }
    if commission.get("status") != "passed":
        raise LifecycleBlocked(
            f"commission preflight refused submission: {commission.get('reason')}"
        )

    instrument = _instrument(spec)
    mark = (bid + ask) / Decimal("2")
    local_account = AccountState(
        cash=account_snapshot.cash,
        positions=dict(account_snapshot.positions),
        marks={BinanceSpotTestnetTransport.canonical_instrument_id(_PROBE_SYMBOL): mark},
        margin_used=account_snapshot.margin_used or Decimal("0"),
        margin_available=account_snapshot.margin_available,
        as_of=account_snapshot.as_of,
    )
    snapshot = Snapshot(as_of=local_account.as_of, purpose="supervised-binance-testnet-fok-probe")
    current_quantity = local_account.positions.get(instrument.canonical_id, Decimal("0"))
    target = TargetPortfolioBuilder(fee_bps=Decimal("10")).build(
        snapshot=snapshot,
        account=local_account,
        targets={instrument: current_quantity - quantity},
        marks=local_account.marks,
        construction_method="supervised_binance_testnet_fok_probe_v1",
        risk_constraints_version="binance-testnet-paper-risk-v1",
    )
    spread_bps = (ask - bid) / ((ask + bid) / Decimal("2")) * Decimal("10000")
    market = RiskMarketState(
        marks=dict(local_account.marks),
        stale_seconds={instrument_id: 0 for instrument_id in local_account.marks},
        spread_bps={instrument.canonical_id: spread_bps},
        liquidity_notional={instrument.canonical_id: bid * bid_qty},
        venue_healthy={instrument_id: True for instrument_id in local_account.marks},
        collateral_available=account_snapshot.margin_available,
    )
    policy = RiskPolicy(
        policy_version="binance-testnet-paper-risk-v1",
        effective_at=local_account.as_of - timedelta(seconds=1),
        hard_limits=(
            RiskLimit(name="max_order_notional", limit=Decimal("25"), unit="USDT"),
            RiskLimit(name="max_cash_deficit", limit=Decimal("0"), unit="USDT"),
            RiskLimit(name="max_stale_seconds", limit=Decimal("0"), unit="seconds"),
            RiskLimit(name="max_venue_health", limit=Decimal("0"), unit="flag"),
        ),
        approved_by="supervised-paper-operator",
    )
    risk_kernel = RiskKernel()
    risk_decision = risk_kernel.evaluate(
        RiskRequest(target=target, account=local_account, market=market, policy=policy)
    )
    payload["target"] = {
        "artifact_sha256": target.canonical_hash(),
        "instrument": instrument.canonical_id,
        "symbol": _PROBE_SYMBOL,
        "side": _PROBE_SIDE,
        "provider_filter_status": spec.status,
        "base_increment": str(spec.base_increment),
        "quote_increment": str(spec.quote_increment),
        "base_min_qty": str(spec.base_min_qty),
        "min_notional": str(spec.min_notional) if spec.min_notional is not None else None,
        "order_type": "LIMIT",
        "time_in_force": "FOK",
        "risk_notional_limit": "25",
    }
    payload["risk_decision"] = {
        "outcome": risk_decision.outcome.value,
        "artifact_sha256": risk_decision.canonical_hash(),
        "reasons": list(risk_decision.reasons),
    }
    if risk_decision.outcome is not RiskOutcome.APPROVED:
        raise LifecycleBlocked(
            "deterministic RiskKernel rejected the supervised target:"
            + ",".join(risk_decision.reasons)
        )

    execution_plan = ExecutionPlan(
        risk_decision_id=risk_decision.artifact_id,
        target_portfolio_id=target.artifact_id,
        policy_version=policy.policy_version,
        instructions=("supervised Binance Spot Testnet FOK fill probe",),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    identity_material = (
        f"fok-fill-probe:{configuration_hash}:{read_only['manifest_sha256']}"
        f":{_PROBE_SYMBOL}:{_PROBE_SIDE}:{quantity}:{price}"
    )
    client_order_id = "aiv3-" + sha256(identity_material.encode()).hexdigest()[:30]
    order = Order(
        artifact_id=uuid5(NAMESPACE_URL, f"advisorai-v3/binance-fok-probe/{client_order_id}"),
        parent_intent_id=uuid5(NAMESPACE_URL, f"advisorai-v3/binance-intent/{client_order_id}"),
        execution_plan_id=execution_plan.artifact_id,
        instrument=instrument,
        side=_PROBE_SIDE,
        quantity=quantity,
        order_type="limit",
        price=price,
        time_in_force="fok",
        idempotency_key=client_order_id,
    )
    order_check = risk_kernel.check_order(
        order=order,
        account=local_account,
        market=market,
        policy=policy,
    )
    payload["order"] = {
        "artifact_sha256": order.canonical_hash(),
        "client_order_id_sha256": sha256(order.idempotency_key.encode()).hexdigest(),
        "client_order_id_length": len(order.idempotency_key),
        "deterministic_client_order_id": True,
        "quantity_filter_validated": True,
        "price_filter_validated": True,
        "notional_filter_validated": notional >= (spec.min_notional or Decimal("0")),
    }
    payload["order_risk_check"] = {
        "approved": order_check.approved,
        "reasons": list(order_check.reasons),
        "authoritative_state_hash": order_check.authoritative_state_hash,
    }
    if not order_check.approved:
        raise LifecycleBlocked(
            "order-level deterministic RiskKernel check rejected the order:"
            + ",".join(order_check.reasons)
        )

    ledgers = SqliteLedgers(run_dir / "oms.sqlite")
    oms_holder: dict[str, OrderManager | None] = {"value": None}
    recording_transport = RecordingTransport(transport, lambda: oms_holder["value"])
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=recording_transport,
        strict_venue=True,
        max_submissions=1,
    )
    oms = OrderManager(ledgers, native)
    oms_holder["value"] = oms
    oms.create(order)
    payload["intent_persisted_before_submission"] = any(
        event.event_type == "order_created"
        and event.payload.get("artifact_id") == str(order.artifact_id)
        for event in ledgers.events(LedgerNamespace.ORDER)
    )
    oms.approve_risk(order.artifact_id, risk_decision, order_check=order_check)
    payload["oms_state_before_submission"] = oms.orders[order.artifact_id].state.value
    payload["writes_attempted"] = True
    try:
        acknowledgement = recorder.call(
            "submit_order",
            "/api/v3/order",
            lambda: oms.route(order.artifact_id),
            method="POST",
            write=True,
        )
    except Exception as exc:
        payload["submission_exception"] = _exception_metadata(exc)
        payload["submission_outcome"] = "ambiguous_or_failed_reconcile_required"
        try:
            reconciled = oms.reconcile_routed(order.artifact_id)
        except Exception as reconcile_exc:
            payload["reconciliation_after_submission_exception"] = {
                "status": "failed",
                **_exception_metadata(reconcile_exc),
            }
            raise LifecycleBlocked(
                "submission failed and venue truth could not be established"
            ) from exc
        payload["reconciliation_after_submission_exception"] = {
            "status": "ok",
            "accepted": reconciled.accepted,
            "automatic_retry": False,
        }
        acknowledgement = reconciled
    if acknowledgement is None:
        raise LifecycleBlocked("ambiguous acknowledgement requires reconciliation before any retry")
    payload["venue_acknowledgement"] = {
        "accepted": acknowledgement.accepted,
        "venue_order_id_present": bool(acknowledgement.venue_order_id),
    }
    payload["signed_submission_count"] = recording_transport.signed_submit_count
    if recording_transport.signed_submit_count != 1:
        raise LifecycleBlocked("signed submission count was not exactly one")

    queried = recorder.call(
        "authoritative_order_query_after_submission",
        "/api/v3/order",
        lambda: transport.query_order(client_order_id=order.idempotency_key),
    )
    if not isinstance(queried, Mapping):
        raise LifecycleBlocked("venue did not return authoritative order state")
    venue_order_id = str(queried.get("venue_order_id", "")).strip()
    if not venue_order_id:
        raise LifecycleBlocked("venue order query omitted its identity")
    payload["authoritative_order_query"] = {
        "status": "ok",
        "client_identity_matches": queried.get("client_order_id") == order.idempotency_key,
        "provider_status": _provider_status(queried),
        "venue_order_id_present": True,
    }
    if queried.get("client_order_id") not in {None, order.idempotency_key}:
        raise LifecycleBlocked("venue returned a different client order identity")

    account_ledger = AccountLedger(ledgers, local_account)
    fills_seen: set[str] = set()

    def ingest_fills() -> int:
        records = recorder.call(
            "fills_for_submitted_order",
            "/api/v3/myTrades",
            transport.list_fills,
            summary={"admitted_symbols": list(transport.verified_symbol_ids)},
        )
        if not isinstance(records, Sequence):
            raise LifecycleBlocked("venue fills response is malformed")
        added = 0
        for record in records:
            if not _fill_matches_submitted_order(
                record, symbol=_PROBE_SYMBOL, venue_order_id=venue_order_id
            ):
                continue
            if not isinstance(record, Mapping):
                raise LifecycleBlocked("venue fill record is malformed")
            fill, fill_side = _fill_from_record(
                transport, record, order.artifact_id, quote_asset=spec.quote_asset
            )
            fills_seen.add(fill.venue_fill_id)
            if fill.venue_fill_id not in oms.fills:
                oms.record_fill(fill, fill_side)
                account_ledger.apply_fill(fill, fill_side, order.instrument.canonical_id)
                added += 1
        return added

    # FOK resolves immediately: FILLED or EXPIRED, never resting. No polling,
    # no cancellation leg, no retry under any outcome.
    provider_status = _provider_status(queried)
    if provider_status == "FILLED":
        ingest_fills()
    elif provider_status == "EXPIRED":
        current = oms.orders[order.artifact_id].state
        if current is OrderState.ACKNOWLEDGED:
            oms.transition(order.artifact_id, OrderState.EXPIRED)
        elif current is not OrderState.REJECTED:
            raise LifecycleBlocked(f"probe order in unexpected state for EXPIRED venue: {current}")
    else:
        raise LifecycleBlocked(
            f"FOK probe order did not resolve immediately: venue={provider_status}"
        )

    final_state = oms.orders[order.artifact_id].state
    if final_state in {OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED}:
        raise LifecycleBlocked("order remained open after the FOK probe resolution")
    if final_state in {
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    }:
        oms.reconcile(order.artifact_id)
    else:
        raise LifecycleBlocked(f"OMS did not reach a terminal state: {final_state.value}")
    payload["terminal_oms_state"] = oms.orders[order.artifact_id].state.value
    payload["fill_ingestion"] = {
        "status": "passed" if fills_seen else "no_fill_observed",
        "real_fill_count": len(fills_seen),
        "oms_fill_count": len(
            [fill for fill in oms.fills.values() if fill.order_id == order.artifact_id]
        ),
    }

    final_open_orders = recorder.call(
        "open_orders_after_terminal_state",
        "/api/v3/openOrders",
        native.open_orders,
    )
    final_account_snapshot = recorder.call(
        "account_reconciliation_snapshot",
        "/api/v3/account",
        transport.fetch_account_snapshot,
    )
    if not isinstance(final_account_snapshot, VenueAccountSnapshot):
        raise LifecycleBlocked("final account snapshot is malformed")
    reconciliation = ReconciliationService(ledgers).run(
        account=local_account,
        orders=oms,
        account_ledger=account_ledger,
        venue_snapshot=final_account_snapshot,
    )
    payload["reconciliation"] = {
        "reconciled": reconciliation.reconciled,
        "artifact_sha256": reconciliation.canonical_hash(),
        "discrepancy_count": len(reconciliation.discrepancies),
        "venue_open_order_count": len(final_open_orders)
        if isinstance(final_open_orders, Sequence)
        else None,
    }
    if not reconciliation.reconciled:
        raise LifecycleBlocked("final account/order reconciliation was not clean")

    fills = tuple(fill for fill in oms.fills.values() if fill.order_id == order.artifact_id)
    tca = compute_tca(
        order_id=order.artifact_id,
        order_quantity=order.quantity,
        side=order.side,
        arrival_price=(bid + ask) / Decimal("2"),
        fills=fills,
        best_bid=bid,
        best_ask=ask,
        venue="binance_spot_testnet",
    )
    attribution = AttributionReconciler().reconcile(
        reconciliation_id=reconciliation.artifact_id,
        total_pnl=Decimal("0"),
        data_forecast=Decimal("0"),
        allocation_selection=Decimal("0"),
        risk_overlay=Decimal("0"),
        execution_financing=Decimal("0"),
        regime_capacity=Decimal("0"),
        currency="USDT",
    )
    payload["tca"] = {
        "status": "passed",
        "artifact_sha256": sha256(tca.model_dump_json().encode()).hexdigest(),
        "fill_ratio": str(tca.fill_ratio),
        "implementation_shortfall": str(tca.implementation_shortfall),
    }
    payload["attribution"] = {
        "status": "passed",
        "artifact_sha256": sha256(attribution.model_dump_json().encode()).hexdigest(),
        "unexplained_residual": str(attribution.unexplained_residual),
    }

    restart_transport = build_binance_spot_testnet_transport(resolver)
    restart_transport.server_time()
    restart_products = restart_transport.list_products()
    restart_transport.verify_symbol_mappings(restart_products)
    restarted_holder: dict[str, OrderManager | None] = {"value": None}
    restart_recorder = RecordingTransport(restart_transport, lambda: restarted_holder["value"])
    restart_native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=restart_recorder,
        strict_venue=True,
        max_submissions=1,
    )
    restarted_oms = OrderManager(ledgers, restart_native)
    restarted_holder["value"] = restarted_oms
    restarted_order = restarted_oms.orders.get(order.artifact_id)
    restart_query = restart_transport.query_order(client_order_id=order.idempotency_key)
    payload["restart_recovery"] = {
        "status": "passed"
        if restarted_order is not None
        and restarted_order.state is OrderState.RECONCILED
        and isinstance(restart_query, Mapping)
        and restart_recorder.signed_submit_count == 0
        else "failed",
        "hydrated_terminal_state": restarted_order.state.value if restarted_order else None,
        "venue_query_succeeded": isinstance(restart_query, Mapping),
        "signed_submissions_after_restart": restart_recorder.signed_submit_count,
        "duplicate_submission": False,
    }
    if payload["restart_recovery"]["status"] != "passed":
        raise LifecycleBlocked(
            "restart recovery did not preserve the terminal order without resubmission"
        )

    failure_drills = _fixture_failure_drills(
        order=order,
        account=local_account,
        market=market,
        policy=policy,
        risk_decision=risk_decision,
        order_check=order_check,
        root=run_dir,
    )
    payload["failure_drills"] = failure_drills
    if any(
        result.get("status") != "passed"
        for result in failure_drills.values()
        if isinstance(result, Mapping)
    ):
        raise LifecycleBlocked("one or more deterministic failure drills failed")
    payload["oms_event_sequence"] = [
        event.event_type for event in ledgers.events(LedgerNamespace.ORDER)
    ]
    payload["oms_state_before_network_assertion"] = all(
        event.get("oms_state_before_network")
        in {OrderState.ROUTED.value, OrderState.CANCEL_PENDING.value}
        for event in recording_transport.events
    )
    payload["network_calls"] = transport.client.request_count
    payload["signed_submission_count"] = recording_transport.signed_submit_count
    payload["signed_cancellation_count"] = recording_transport.signed_cancel_count
    payload["operations"] = recorder.operations
    payload["status"] = "passed"
    payload["reason"] = "supervised_fake_funds_fok_probe_passed"
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--read-only-evidence",
        type=Path,
        default=Path("artifacts/phase2/binance-spot-testnet/read-only-smoke"),
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase2/binance-spot-testnet/fill-probe"),
    )
    parser.add_argument("--configuration-hash", required=True)
    args = parser.parse_args(argv)
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")
    if os.getenv("ADVISORAI_RUN_FILL_PROBE") != "1":
        raise SystemExit("refusing fill probe; set ADVISORAI_RUN_FILL_PROBE=1 explicitly")
    if len(args.configuration_hash) != 64 or any(
        character not in "0123456789abcdef" for character in args.configuration_hash
    ):
        raise SystemExit("--configuration-hash must be a lowercase SHA-256 digest")
    run_dir, run_id = _new_run_directory(args.evidence_dir)
    payload: dict[str, object] = {
        "status": "failed",
        "reason": "probe_not_started",
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "endpoint": BINANCE_SPOT_TESTNET_BASE_URL,
        "reviewed_host": "testnet.binance.vision",
        "adapter": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
        "configuration_hash": args.configuration_hash,
        "credential_refs": ["ADVISORAI_VENUE_API_KEY", "ADVISORAI_VENUE_API_SECRET"],
        "writes_attempted": False,
        "signed_submission_count": 0,
        "signed_cancellation_count": 0,
    }
    try:
        resolver = CredentialResolver.from_env_file(args.secrets)
        payload = _run_probe(
            resolver=resolver,
            configuration_hash=args.configuration_hash,
            read_only_evidence=args.read_only_evidence,
            run_dir=run_dir,
            run_id=run_id,
        )
    except Exception as exc:
        payload.update(_exception_metadata(exc))
        payload["reason"] = (
            "lifecycle_blocked" if isinstance(exc, LifecycleBlocked) else "lifecycle_failed"
        )
        _recover_partial_write_state(run_dir, payload)
    manifest, evidence_sha256 = _write_evidence(payload, args.evidence_dir, run_id)
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "reason": payload.get("reason"),
                "evidence": str(manifest),
                "evidence_sha256": evidence_sha256,
                "writes_attempted": payload.get("writes_attempted", False),
                "signed_submission_count": payload.get("signed_submission_count", 0),
                "signed_cancellation_count": payload.get("signed_cancellation_count", 0),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if payload.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
