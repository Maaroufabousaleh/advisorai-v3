"""Supervised, fake-funds Binance Spot Testnet lifecycle qualification.

This command is intentionally opt-in.  It validates the immutable authenticated
read-only gate, builds one typed target/order through the existing deterministic
RiskKernel and OMS, and then permits at most one signed order submission.  An
ambiguous write is reconciled by query and is never retried automatically.

The command resolves only ``CredentialScope.PAPER_VENUE``.  It never exposes
transport write methods to a model, agent, dashboard, browser, or capability.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.attribution import AttributionReconciler
from advisorai.config import CredentialResolver
from advisorai.contracts import (
    ExecutionPlan,
    Fill,
    InstrumentIdentity,
    Order,
    OrderState,
    RiskDecision,
    RiskLimit,
    RiskOutcome,
    RiskPolicy,
    Snapshot,
)
from advisorai.execution import (
    AccountLedger,
    AccountState,
    KillSwitch,
    NativeVenueAdapter,
    OrderManager,
    OrderStateError,
    PaperVenueAdapter,
    ReconciliationService,
    RiskKernel,
    RiskMarketState,
    RiskRequest,
    VenueAccountSnapshot,
    compute_tca,
)
from advisorai.execution.portfolio import TargetPortfolioBuilder
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    BINANCE_SPOT_TESTNET_BASE_URL,
    BinanceSpotSymbolSpec,
    BinanceSpotTestnetTransport,
    build_binance_spot_testnet_transport,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers

READ_ONLY_SCHEMA = "advisorai.phase2.binance-spot-testnet.read-only-smoke.v1"
LIFECYCLE_SCHEMA = "advisorai.phase2.binance-spot-testnet.paper-lifecycle.v1"
_REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
_TERMINAL_PROVIDER_STATES = frozenset(
    {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}
)


class LifecycleBlocked(RuntimeError):
    """The lifecycle cannot proceed without weakening a safety invariant."""


def _digest(value: object) -> str:
    return sha256(str(value).encode("utf-8")).hexdigest()


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (TypeError, ValueError):
        raise LifecycleBlocked(f"malformed {label}") from None
    if not parsed.is_finite():
        raise LifecycleBlocked(f"non-finite {label}")
    return parsed


def _exception_metadata(exc: Exception) -> dict[str, object]:
    """Return only non-sensitive exception metadata for evidence."""

    metadata: dict[str, object] = {
        "error_class": type(exc).__name__,
        "status_code": getattr(exc, "status_code", None),
    }
    if isinstance(exc, LifecycleBlocked):
        # LifecycleBlocked messages are generated only by this runner and
        # contain classifications/reasons, never provider response bodies.
        metadata["blocker"] = str(exc)
    return metadata


def _new_run_directory(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = base
    suffix = 1
    while (root / run_id).exists():
        suffix += 1
        run_id = f"{base}-{suffix}"
    run_dir = root / run_id
    run_dir.mkdir()
    return run_dir, run_id


def _write_evidence(
    payload: Mapping[str, object], evidence_dir: Path, run_id: str
) -> tuple[Path, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": LIFECYCLE_SCHEMA,
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "result": dict(payload),
    }
    encoded = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest = evidence_dir / run_id / "binance-spot-testnet-paper-lifecycle.json"
    manifest.write_bytes(encoded)
    evidence_sha256 = sha256(encoded).hexdigest()
    (evidence_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema": "advisorai.phase2.binance-spot-testnet.paper-lifecycle.latest.v1",
                "run_id": run_id,
                "manifest_sha256": evidence_sha256,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest, evidence_sha256


def _recover_partial_write_state(run_dir: Path, payload: dict[str, object]) -> None:
    """Conservatively recover write markers if the runner fails mid-lifecycle."""

    database = run_dir / "oms.sqlite"
    if not database.exists():
        return
    try:
        events = SqliteLedgers(database).events(LedgerNamespace.ORDER)
    except Exception:
        payload["partial_ledger_state"] = "unreadable"
        payload["writes_attempted"] = True
        return
    event_types = [event.event_type for event in events]
    payload["oms_event_sequence"] = event_types
    if "order_routed" in event_types or "order_ack_ambiguous" in event_types:
        # A routed/ambiguous durable state is conservatively treated as a
        # possible signed write even if the process died before its in-memory
        # transport callback could append an operation record.
        payload["writes_attempted"] = True
        payload["signed_submission_count"] = max(int(payload.get("signed_submission_count", 0)), 1)
    if "order_cancel_pending" in event_types:
        payload["signed_cancellation_count"] = max(
            int(payload.get("signed_cancellation_count", 0)), 1
        )


def _validate_read_only_evidence(path: Path, configuration_hash: str) -> dict[str, object]:
    """Validate the pointer, immutable hash, and all required read operations."""

    pointer_path = path / "latest.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        run_id = str(pointer["run_id"])
        manifest = path / run_id / "binance-spot-testnet-read-only-smoke.json"
        encoded = manifest.read_bytes()
        manifest_sha256 = sha256(encoded).hexdigest()
        if manifest_sha256 != pointer["manifest_sha256"]:
            raise LifecycleBlocked("read-only evidence pointer hash does not match its manifest")
        record = json.loads(encoded)
        if record.get("schema") != READ_ONLY_SCHEMA:
            raise LifecycleBlocked("read-only evidence schema is not admitted")
        result = record.get("result")
        if not isinstance(result, Mapping):
            raise LifecycleBlocked("read-only evidence result is malformed")
        if result.get("status") != "passed" or result.get("writes_attempted") is not False:
            raise LifecycleBlocked("read-only evidence did not pass without writes")
        if result.get("config_hash") != configuration_hash:
            raise LifecycleBlocked("read-only evidence configuration hash is not current")
        if result.get("endpoint") != BINANCE_SPOT_TESTNET_BASE_URL:
            raise LifecycleBlocked("read-only evidence endpoint is not the reviewed testnet")
        operations = result.get("operations")
        if not isinstance(operations, list):
            raise LifecycleBlocked("read-only evidence operations are malformed")
        by_name = {
            str(operation.get("name")): operation
            for operation in operations
            if isinstance(operation, Mapping)
        }
        required = {
            "server_time",
            "products",
            "product_mapping_verification",
            "account_state",
            "balances",
            "positions",
            "open_orders",
            "fills",
        }
        if set(by_name) != required or any(
            operation.get("status") != "ok" for operation in by_name.values()
        ):
            raise LifecycleBlocked("read-only evidence is missing a required successful operation")
        product_operation = by_name["products"]
        mapping_operation = by_name["product_mapping_verification"]
        if set(product_operation.get("required_symbols", ())) != set(_REQUIRED_SYMBOLS):
            raise LifecycleBlocked("read-only evidence does not contain BTCUSDT and ETHUSDT")
        if set(mapping_operation.get("admitted_symbols", ())) != set(_REQUIRED_SYMBOLS):
            raise LifecycleBlocked("read-only evidence did not admit both required symbols")
        if result.get("credential_refs") != [
            "ADVISORAI_VENUE_API_KEY",
            "ADVISORAI_VENUE_API_SECRET",
        ]:
            raise LifecycleBlocked("read-only evidence credential references are not scoped")
        return {
            "run_id": run_id,
            "manifest": str(manifest),
            "manifest_sha256": manifest_sha256,
            "network_calls": result.get("network_calls"),
            "adapter_source_sha256": result.get("adapter_source_sha256"),
        }
    except LifecycleBlocked:
        raise
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LifecycleBlocked("read-only evidence could not be validated") from exc


class OperationRecorder:
    """Capture sanitized operation metadata without retaining response bodies."""

    def __init__(self, transport: BinanceSpotTestnetTransport) -> None:
        self.transport = transport
        self.operations: list[dict[str, object]] = []

    def call(
        self,
        name: str,
        endpoint: str,
        operation: Callable[[], object],
        *,
        method: str = "GET",
        write: bool = False,
        summary: Mapping[str, object] | None = None,
    ) -> object:
        started = time.perf_counter()
        before = self.transport.client.request_count
        try:
            value = operation()
        except Exception as exc:
            record: dict[str, object] = {
                "name": name,
                "method": method,
                "endpoint": endpoint,
                "write": write,
                "status": "failed",
                "network_calls": self.transport.client.request_count - before,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            record.update(_exception_metadata(exc))
            self.operations.append(record)
            raise
        record = {
            "name": name,
            "method": method,
            "endpoint": endpoint,
            "write": write,
            "status": "ok",
            "response_type": type(value).__name__,
            "record_count": len(value) if isinstance(value, (Mapping, Sequence)) else None,
            "network_calls": self.transport.client.request_count - before,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if summary:
            record.update(summary)
        self.operations.append(record)
        return value


class RecordingTransport:
    """Internal guard that proves OMS state before delegating to Binance."""

    def __init__(
        self,
        delegate: BinanceSpotTestnetTransport,
        oms_getter: Callable[[], OrderManager | None],
    ) -> None:
        self.delegate = delegate
        self.oms_getter = oms_getter
        self.events: list[dict[str, object]] = []
        self.signed_submit_count = 0
        self.signed_cancel_count = 0

    @property
    def client(self):
        return self.delegate.client

    def _state(self, payload: Mapping[str, object]) -> str | None:
        raw = payload.get("order_id")
        if raw is None:
            return None
        try:
            order_id = UUID(str(raw))
        except (TypeError, ValueError):
            return None
        oms = self.oms_getter()
        if oms is None or order_id not in oms.orders:
            return None
        return oms.orders[order_id].state.value

    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        state = self._state(payload)
        self.signed_submit_count += 1
        self.events.append(
            {
                "event": "signed_submission_entered",
                "oms_state_before_network": state,
                "client_order_id_sha256": _digest(payload.get("client_order_id", "")),
            }
        )
        if self.signed_submit_count != 1 or state != OrderState.ROUTED.value:
            raise LifecycleBlocked("signed submission guard rejected an unsafe route")
        return self.delegate.submit_order(payload)

    def cancel_order(self, *, client_order_id: str) -> Mapping[str, object]:
        oms = self.oms_getter()
        state = None
        if oms is not None:
            state = next(
                (
                    order.state.value
                    for order in oms.orders.values()
                    if order.idempotency_key == client_order_id
                ),
                None,
            )
        self.signed_cancel_count += 1
        self.events.append(
            {
                "event": "signed_cancellation_entered",
                "oms_state_before_network": state,
                "client_order_id_sha256": _digest(client_order_id),
            }
        )
        if state != OrderState.CANCEL_PENDING.value:
            raise LifecycleBlocked("signed cancellation was not preceded by OMS cancel intent")
        return self.delegate.cancel_order(client_order_id=client_order_id)

    def query_order(self, *, client_order_id: str) -> Mapping[str, object] | None:
        return self.delegate.query_order(client_order_id=client_order_id)

    def list_open_orders(self) -> tuple[Mapping[str, object], ...]:
        return self.delegate.list_open_orders()

    def fetch_account_snapshot(self) -> VenueAccountSnapshot:
        return self.delegate.fetch_account_snapshot()

    def fill_contract_values(self, record: Mapping[str, object]) -> dict[str, object]:
        return self.delegate.fill_contract_values(record)


class _CountingPaperAdapter(PaperVenueAdapter):
    """Fixture-only transport counter for non-inducible failure drills."""

    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0

    def submit(self, order: Order):
        self.submit_calls += 1
        return super().submit(order)


def _round_up(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def _passive_price(bid: Decimal, ask: Decimal, tick: Decimal, side: str) -> Decimal:
    if side == "buy":
        candidate = bid if bid < ask else bid - tick
        return (candidate / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    candidate = ask if bid < ask else ask + tick
    return (candidate / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _minimum_practical_quantity(spec: BinanceSpotSymbolSpec, price: Decimal) -> Decimal:
    minimum_notional = spec.min_notional or Decimal("0")
    quantity = _round_up((minimum_notional * Decimal("1.02")) / price, spec.base_increment)
    return max(quantity, spec.base_min_qty)


def _instrument(spec: BinanceSpotSymbolSpec) -> InstrumentIdentity:
    return InstrumentIdentity(
        canonical_id=BinanceSpotTestnetTransport.canonical_instrument_id(spec.symbol),
        asset_class="crypto",
        venue="binance_spot_testnet",
        venue_symbol=spec.symbol,
        base_asset=spec.base_asset,
        quote_asset=spec.quote_asset,
    )


def _provider_status(record: Mapping[str, object] | None) -> str:
    if record is None:
        return "MISSING"
    return str(record.get("status", record.get("state", ""))).strip().upper()


def _fill_from_record(
    transport: BinanceSpotTestnetTransport,
    record: Mapping[str, object],
    order_id: UUID,
) -> tuple[Fill, str]:
    values = transport.fill_contract_values(record)
    venue_fill_id = str(values["venue_fill_id"])
    fill = Fill(
        artifact_id=uuid5(NAMESPACE_URL, f"advisorai-v3/binance-fill/{venue_fill_id}"),
        order_id=order_id,
        venue_fill_id=venue_fill_id,
        quantity=values["quantity"],
        price=values["price"],
        fee=values["fee"],
        occurred_at=values["occurred_at"],
    )
    return fill, str(values["side"])


def _fixture_failure_drills(
    *,
    order: Order,
    account: AccountState,
    market: RiskMarketState,
    policy: RiskPolicy,
    risk_decision: RiskDecision,
    order_check,
    root: Path,
) -> dict[str, object]:
    """Exercise non-inducible failure behavior without provider writes."""

    results: dict[str, object] = {}

    ambiguous_ledgers = SqliteLedgers(root / "failure-drills-ambiguous.sqlite")
    ambiguous_adapter = _CountingPaperAdapter()
    ambiguous_oms = OrderManager(ambiguous_ledgers, ambiguous_adapter)
    fixture_order = order.model_copy(update={"state": OrderState.CREATED})
    ambiguous_oms.create(fixture_order)
    ambiguous_oms.approve_risk(fixture_order.artifact_id, risk_decision, order_check=order_check)
    ambiguous_adapter.inject_ambiguous_ack_once(fixture_order.idempotency_key)
    first = ambiguous_oms.route(fixture_order.artifact_id)
    reconciled_ack = ambiguous_oms.reconcile_ambiguous(fixture_order.artifact_id)
    results["ambiguous_acknowledgement"] = {
        "status": "passed",
        "first_route_result": first is None,
        "reconciled": reconciled_ack.accepted,
        "submit_calls": ambiguous_adapter.submit_calls,
        "automatic_retry": False,
    }

    duplicate_adapter = _CountingPaperAdapter()
    duplicate_adapter.submit(fixture_order)
    duplicate_same = duplicate_adapter.submit(fixture_order)
    changed = fixture_order.model_copy(update={"quantity": fixture_order.quantity + Decimal("1")})
    changed_result = "rejected"
    try:
        duplicate_adapter.submit(changed)
    except ValueError:
        pass
    else:
        changed_result = "accepted"
    results["duplicate_and_changed_payload"] = {
        "status": "passed"
        if duplicate_same.accepted and changed_result == "rejected"
        else "failed",
        "same_identity_idempotent": duplicate_same.accepted,
        "changed_payload_rejected": changed_result == "rejected",
        "submit_calls": duplicate_adapter.submit_calls,
    }

    outage_ledgers = SqliteLedgers(root / "failure-drills-outage.sqlite")
    outage_adapter = _CountingPaperAdapter()
    outage_oms = OrderManager(outage_ledgers, outage_adapter)
    outage_order = order.model_copy(update={"state": OrderState.CREATED})
    outage_oms.create(outage_order)
    outage_oms.approve_risk(outage_order.artifact_id, risk_decision, order_check=order_check)
    outage_adapter.inject_outage_once()
    outage_failed_closed = False
    try:
        outage_oms.route(outage_order.artifact_id)
    except RuntimeError:
        outage_failed_closed = (
            outage_oms.orders[outage_order.artifact_id].state is OrderState.ROUTED
        )
    second_route_rejected = False
    try:
        outage_oms.route(outage_order.artifact_id)
    except OrderStateError:
        second_route_rejected = True
    outage_oms.expire_unacknowledged(outage_order.artifact_id)
    outage_oms.reconcile(outage_order.artifact_id)
    results["network_interruption"] = {
        "status": "passed" if outage_failed_closed and second_route_rejected else "failed",
        "failed_closed": outage_failed_closed,
        "duplicate_route_rejected": second_route_rejected,
    }

    race_ledgers = SqliteLedgers(root / "failure-drills-cancel-race.sqlite")
    race_adapter = _CountingPaperAdapter()
    race_oms = OrderManager(race_ledgers, race_adapter)
    race_order = order.model_copy(update={"state": OrderState.CREATED})
    race_oms.create(race_order)
    race_oms.approve_risk(race_order.artifact_id, risk_decision, order_check=order_check)
    race_oms.route(race_order.artifact_id)
    race_oms.cancel(race_order.artifact_id)
    race_fill = Fill(
        order_id=race_order.artifact_id,
        venue_fill_id="fixture-cancel-race-fill",
        quantity=race_order.quantity,
        price=race_order.price or Decimal("1"),
        fee=Decimal("0"),
        occurred_at=account.as_of,
    )
    race_oms.record_fill(race_fill, race_order.side)
    cancel_ack_rejected = False
    try:
        race_oms.acknowledge_cancel(race_order.artifact_id)
    except OrderStateError:
        cancel_ack_rejected = True
    race_oms.reconcile(race_order.artifact_id)
    results["cancel_race"] = {
        "status": "passed" if cancel_ack_rejected else "failed",
        "fill_won_race": race_oms.orders[race_order.artifact_id].state is OrderState.RECONCILED,
        "fill_ingested_through_oms": len(race_oms.fills) == 1,
        "cancel_ack_after_fill_rejected": cancel_ack_rejected,
    }

    kill_switch = KillSwitch()
    kill_switch.trip("fixture kill-switch drill")
    kill_check = RiskKernel(kill_switch).check_order(
        order=order,
        account=account,
        market=market,
        policy=policy,
    )
    results["kill_switch"] = {
        "status": "passed"
        if not kill_check.approved and any("kill_switch" in reason for reason in kill_check.reasons)
        else "failed",
        "risk_veto": not kill_check.approved,
        "no_route_attempted": True,
    }

    divergence_ledgers = SqliteLedgers(root / "failure-drills-divergence.sqlite")
    divergence_oms = OrderManager(divergence_ledgers, PaperVenueAdapter())
    divergent_snapshot = VenueAccountSnapshot(
        as_of=account.as_of,
        cash=account.cash + Decimal("1"),
        positions=dict(account.positions),
    )
    divergence = ReconciliationService().run(
        account=account,
        orders=divergence_oms,
        venue_snapshot=divergent_snapshot,
    )
    results["local_venue_divergence"] = {
        "status": "passed" if not divergence.reconciled else "failed",
        "reconciliation_failed_closed": not divergence.reconciled,
    }
    return results


def _run_lifecycle(
    *,
    resolver: CredentialResolver,
    configuration_hash: str,
    read_only_evidence: Path,
    run_dir: Path,
    run_id: str,
    fill_wait_seconds: int,
) -> dict[str, object]:
    read_only = _validate_read_only_evidence(read_only_evidence, configuration_hash)
    transport = build_binance_spot_testnet_transport(resolver)
    recorder = OperationRecorder(transport)
    payload: dict[str, object] = {
        "status": "failed",
        "reason": "lifecycle_not_completed",
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "endpoint": BINANCE_SPOT_TESTNET_BASE_URL,
        "reviewed_host": "testnet.binance.vision",
        "adapter": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
        "credential_refs": ["ADVISORAI_VENUE_API_KEY", "ADVISORAI_VENUE_API_SECRET"],
        "configuration_hash": configuration_hash,
        "read_only_evidence": read_only,
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
    if not isinstance(mappings, Sequence) or set(transport.verified_symbol_ids) != set(
        _REQUIRED_SYMBOLS
    ):
        raise LifecycleBlocked("live product truth did not admit both required symbols")
    specs = {spec.symbol: spec for spec in mappings if isinstance(spec, BinanceSpotSymbolSpec)}
    if set(specs) != set(_REQUIRED_SYMBOLS):
        raise LifecycleBlocked("provider filter mapping is incomplete")

    ticker_by_symbol: dict[str, Mapping[str, object]] = {}
    for symbol in _REQUIRED_SYMBOLS:
        ticker = recorder.call(
            f"book_ticker_{symbol}",
            "/api/v3/ticker/bookTicker",
            lambda symbol=symbol: transport.book_ticker(symbol),
            summary={"symbol": symbol},
        )
        if not isinstance(ticker, Mapping):
            raise LifecycleBlocked(f"{symbol} book ticker response is malformed")
        ticker_by_symbol[symbol] = ticker

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
    balances = account_payload.get("balances")
    if not isinstance(balances, list):
        raise LifecycleBlocked("account balances response is malformed")
    free_balances = {
        str(item.get("asset", "")).strip().upper(): _decimal(item.get("free", "0"), "free balance")
        for item in balances
        if isinstance(item, Mapping) and str(item.get("asset", "")).strip()
    }

    candidates: list[dict[str, object]] = []
    for symbol in _REQUIRED_SYMBOLS:
        spec = specs[symbol]
        ticker = ticker_by_symbol[symbol]
        bid = _decimal(ticker.get("bidPrice"), f"{symbol} bid price")
        ask = _decimal(ticker.get("askPrice"), f"{symbol} ask price")
        for side in ("buy", "sell"):
            price = _passive_price(bid, ask, spec.quote_increment, side)
            if price <= 0:
                continue
            quantity = _minimum_practical_quantity(spec, price)
            notional = price * quantity
            if side == "buy":
                affordable = free_balances.get(spec.quote_asset, Decimal("0")) >= notional
            else:
                affordable = free_balances.get(spec.base_asset, Decimal("0")) >= quantity
            candidates.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "notional": notional,
                    "affordable": affordable,
                    "bid": bid,
                    "ask": ask,
                }
            )
    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate["affordable"] and candidate["notional"] <= Decimal("25")
        ),
        None,
    )
    if selected is None:
        raise LifecycleBlocked(
            "no provider-filtered BTC/ETH order is affordable within the test limit"
        )

    symbol = str(selected["symbol"])
    side = str(selected["side"])
    spec = specs[symbol]
    price = selected["price"]
    quantity = selected["quantity"]
    notional = selected["notional"]
    bid = selected["bid"]
    ask = selected["ask"]
    instrument = _instrument(spec)
    marks = {
        item_symbol: (
            _decimal(ticker_by_symbol[item_symbol]["bidPrice"], "bid price")
            + _decimal(ticker_by_symbol[item_symbol]["askPrice"], "ask price")
        )
        / Decimal("2")
        for item_symbol in _REQUIRED_SYMBOLS
    }
    local_account = AccountState(
        cash=account_snapshot.cash,
        positions=dict(account_snapshot.positions),
        marks={
            BinanceSpotTestnetTransport.canonical_instrument_id(item_symbol): mark
            for item_symbol, mark in marks.items()
        },
        margin_used=account_snapshot.margin_used or Decimal("0"),
        margin_available=account_snapshot.margin_available,
        as_of=account_snapshot.as_of,
    )
    snapshot = Snapshot(as_of=local_account.as_of, purpose="supervised-binance-testnet-lifecycle")
    current_quantity = local_account.positions.get(instrument.canonical_id, Decimal("0"))
    signed_quantity = quantity if side == "buy" else -quantity
    target_quantities: dict[InstrumentIdentity, Decimal] = {}
    for item_symbol in _REQUIRED_SYMBOLS:
        target_instrument = instrument if item_symbol == symbol else _instrument(specs[item_symbol])
        target_quantities[target_instrument] = local_account.positions.get(
            target_instrument.canonical_id, Decimal("0")
        )
    target_quantities[instrument] = current_quantity + signed_quantity
    target = TargetPortfolioBuilder(fee_bps=Decimal("10")).build(
        snapshot=snapshot,
        account=local_account,
        targets=target_quantities,
        marks=local_account.marks,
        construction_method="supervised_binance_testnet_target_v1",
        risk_constraints_version="binance-testnet-paper-risk-v1",
    )
    spread_bps = (ask - bid) / ((ask + bid) / Decimal("2")) * Decimal("10000")
    market = RiskMarketState(
        marks=dict(local_account.marks),
        stale_seconds={instrument_id: 0 for instrument_id in local_account.marks},
        spread_bps={instrument.canonical_id: spread_bps},
        liquidity_notional={instrument.canonical_id: max(bid, ask) * Decimal("1")},
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
        "symbol": symbol,
        "side": side,
        "provider_filter_status": spec.status,
        "base_increment": str(spec.base_increment),
        "quote_increment": str(spec.quote_increment),
        "base_min_qty": str(spec.base_min_qty),
        "min_notional": str(spec.min_notional) if spec.min_notional is not None else None,
        "passive_order_type": "LIMIT_MAKER",
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
        instructions=("supervised Binance Spot Testnet passive lifecycle",),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    identity_material = (
        f"{configuration_hash}:{read_only['manifest_sha256']}:{symbol}:{side}:{quantity}:{price}"
    )
    client_order_id = "aiv3-" + sha256(identity_material.encode()).hexdigest()[:30]
    order = Order(
        artifact_id=uuid5(NAMESPACE_URL, f"advisorai-v3/binance-order/{client_order_id}"),
        parent_intent_id=uuid5(NAMESPACE_URL, f"advisorai-v3/binance-intent/{client_order_id}"),
        execution_plan_id=execution_plan.artifact_id,
        instrument=instrument,
        side=side,
        quantity=quantity,
        order_type="passive_limit",
        price=price,
        time_in_force="gtc",
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
        "client_order_id_sha256": _digest(order.idempotency_key),
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
            if not isinstance(record, Mapping) or str(record.get("orderId", "")) != venue_order_id:
                continue
            fill, fill_side = _fill_from_record(transport, record, order.artifact_id)
            fills_seen.add(fill.venue_fill_id)
            if fill.venue_fill_id not in oms.fills:
                oms.record_fill(fill, fill_side)
                account_ledger.apply_fill(fill, fill_side, order.instrument.canonical_id)
                added += 1
        return added

    deadline = time.monotonic() + max(0, fill_wait_seconds)
    poll = 0
    while (
        _provider_status(queried) not in _TERMINAL_PROVIDER_STATES and time.monotonic() < deadline
    ):
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
        poll += 1
        queried = recorder.call(
            f"authoritative_order_query_poll_{poll}",
            "/api/v3/order",
            lambda: transport.query_order(client_order_id=order.idempotency_key),
        )
        if not isinstance(queried, Mapping):
            raise LifecycleBlocked("venue order disappeared during lifecycle polling")
    provider_status = _provider_status(queried)
    if provider_status in {"FILLED", "PARTIALLY_FILLED"}:
        ingest_fills()
    if provider_status not in {
        "FILLED",
        "PARTIALLY_FILLED",
        "CANCELED",
        "CANCELLED",
        "REJECTED",
        "EXPIRED",
        "EXPIRED_IN_MATCH",
    }:
        if oms.orders[order.artifact_id].state not in {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
        }:
            raise LifecycleBlocked("local OMS state is not cancellable after provider query")
        try:
            recorder.call(
                "cancel_order",
                "/api/v3/order",
                lambda: oms.cancel(order.artifact_id),
                method="DELETE",
                write=True,
            )
        except Exception as exc:
            payload["cancel_exception"] = _exception_metadata(exc)
        payload["signed_cancellation_count"] = recording_transport.signed_cancel_count
        queried = recorder.call(
            "authoritative_order_query_after_cancel",
            "/api/v3/order",
            lambda: transport.query_order(client_order_id=order.idempotency_key),
        )
        if not isinstance(queried, Mapping):
            raise LifecycleBlocked("venue truth was unavailable after cancellation")
        provider_status = _provider_status(queried)
        if provider_status in {"FILLED", "PARTIALLY_FILLED"}:
            ingest_fills()
        if (
            provider_status in {"CANCELED", "CANCELLED"}
            and oms.orders[order.artifact_id].state is OrderState.CANCEL_PENDING
        ):
            oms.acknowledge_cancel(order.artifact_id)
    elif provider_status in {"CANCELED", "CANCELLED"}:
        if oms.orders[order.artifact_id].state is OrderState.CANCEL_PENDING:
            oms.acknowledge_cancel(order.artifact_id)
    elif provider_status == "REJECTED" and oms.orders[order.artifact_id].state is OrderState.ROUTED:
        oms.reconcile_routed(order.artifact_id)

    final_state = oms.orders[order.artifact_id].state
    if final_state in {OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED}:
        raise LifecycleBlocked("order remained open after the supervised cancel/reconcile window")
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
        "artifact_sha256": _digest(tca.model_dump_json()),
        "fill_ratio": str(tca.fill_ratio),
        "implementation_shortfall": str(tca.implementation_shortfall),
    }
    payload["attribution"] = {
        "status": "passed",
        "artifact_sha256": _digest(attribution.model_dump_json()),
        "unexplained_residual": str(attribution.unexplained_residual),
    }

    restart_transport = build_binance_spot_testnet_transport(resolver)
    restart_transport.server_time()
    restart_products = restart_transport.list_products()
    restart_transport.verify_symbol_mappings(restart_products)
    restart_recorder = RecordingTransport(restart_transport, lambda: restarted_holder["value"])
    restart_native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=restart_recorder,
        strict_venue=True,
    )
    restarted_holder: dict[str, OrderManager | None] = {"value": None}
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
    payload["reason"] = "supervised_fake_funds_lifecycle_passed"
    return payload


def main() -> int:
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
        default=Path("artifacts/phase2/binance-spot-testnet/paper-lifecycle"),
    )
    parser.add_argument("--configuration-hash", required=True)
    parser.add_argument("--fill-wait-seconds", type=int, default=10)
    args = parser.parse_args()
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")
    if os.getenv("ADVISORAI_RUN_PAPER_LIFECYCLE") != "1":
        raise SystemExit("refusing order lifecycle; set ADVISORAI_RUN_PAPER_LIFECYCLE=1 explicitly")
    if len(args.configuration_hash) != 64 or any(
        character not in "0123456789abcdef" for character in args.configuration_hash
    ):
        raise SystemExit("--configuration-hash must be a lowercase SHA-256 digest")
    run_dir, run_id = _new_run_directory(args.evidence_dir)
    payload: dict[str, object] = {
        "status": "failed",
        "reason": "lifecycle_not_started",
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
        payload = _run_lifecycle(
            resolver=resolver,
            configuration_hash=args.configuration_hash,
            read_only_evidence=args.read_only_evidence,
            run_dir=run_dir,
            run_id=run_id,
            fill_wait_seconds=args.fill_wait_seconds,
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
