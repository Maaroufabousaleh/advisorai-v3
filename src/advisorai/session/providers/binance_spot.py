"""Binance Spot Testnet bridge for the owner application's paper adapter port.

This bridge composes the already reviewed Binance ``NativeVenueAdapter`` and
transport. It adds owner-ledger identity retention, canonical account/fill
projection, and the strict paper alias expected by the owner runtime. It does
not construct credentials, choose endpoints, or perform network calls during
construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.contracts import AssetClass, Fill, InstrumentIdentity, Order
from advisorai.execution import NativeVenueAdapter, NativeVenueProjectionError
from advisorai.execution.paper import VenueAcknowledgement
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.integrations.binance_spot import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
)
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class BinanceSpotTestnetOwnerAdapter:
    """Expose one reviewed Binance Spot Testnet symbol as owner venue ``paper``.

    The durable ``initial_account`` baseline and the instrument's canonical
    identity are supplied by the explicit owner profile. Non-quote-asset fees,
    unmapped positions/fills, unknown orders, and provider identity conflicts
    fail closed.
    """

    venue = "paper"
    adapter_identity = "binance_spot_testnet"

    def __init__(
        self,
        native: NativeVenueAdapter,
        ledgers: SqliteLedgers,
        *,
        canonical_instrument_id: str,
        symbol: str,
        quote_asset: str,
    ) -> None:
        transport = native.transport
        client = getattr(transport, "client", None)
        allowed_hosts = getattr(getattr(client, "config", None), "allowed_hosts", ())
        if (
            native.venue != self.adapter_identity
            or native.environment != "paper_testnet"
            or native.strict_venue
            or getattr(transport, "venue_name", None) != self.adapter_identity
            or getattr(transport, "environment", None) != "paper_testnet"
            or getattr(client, "base_url", None) != BINANCE_SPOT_TESTNET_BASE_URL
            or tuple(allowed_hosts) != (BINANCE_SPOT_TESTNET_HOST,)
        ):
            raise ValueError("owner Binance adapter requires the exact Spot Testnet boundary")
        required = (
            "verified_symbol_ids",
            "verified_symbol_spec",
            "canonical_instrument_id",
            "fetch_account_snapshot",
            "query_order",
            "list_fills",
            "fill_contract_values",
        )
        if any(not hasattr(transport, name) for name in required):
            raise ValueError("Binance Spot Testnet transport lacks a required read projection")
        normalized_symbol = symbol.strip().upper()
        normalized_id = canonical_instrument_id.strip()
        normalized_quote = quote_asset.strip().upper()
        if (
            not normalized_id
            or not normalized_quote
            or normalized_symbol not in tuple(transport.verified_symbol_ids)
            or tuple(transport.verified_symbol_ids) != (normalized_symbol,)
        ):
            raise ValueError("owner Binance profile requires exactly one verified instrument")
        symbol_spec = transport.verified_symbol_spec(normalized_symbol)
        if str(getattr(symbol_spec, "quote_asset", "")).strip().upper() != normalized_quote:
            raise ValueError("owner Binance quote asset differs from verified provider mapping")
        self.native = native
        self.transport = transport
        self.ledgers = ledgers
        self.canonical_instrument_id = normalized_id
        self.symbol = normalized_symbol
        self.quote_asset = normalized_quote
        self.provider_account_key = str(transport.canonical_instrument_id(normalized_symbol))
        self.instrument = InstrumentIdentity(
            canonical_id=normalized_id,
            asset_class=AssetClass.CRYPTO,
            venue="paper",
            venue_symbol=normalized_symbol,
        )
        self._orders: dict[UUID, Order] = {}
        self._provider_order_ids: dict[str, UUID] = {}
        self._local_provider_order_ids: dict[UUID, str] = {}
        self._local_client_order_ids: dict[UUID, str] = {}
        self._hydrate_order_identities()

    def bind_order(self, order: Order) -> None:
        if (
            order.instrument.canonical_id != self.canonical_instrument_id
            or order.instrument.venue != "paper"
            or order.instrument.venue_symbol != self.symbol
        ):
            raise NativeVenueProjectionError(
                "owner order instrument does not match the Binance paper profile"
            )
        prior = self._orders.get(order.artifact_id)
        if prior is not None and prior.model_dump(exclude={"state"}) != order.model_dump(
            exclude={"state"}
        ):
            raise NativeVenueProjectionError("local order identity changed after binding")
        durable_client_id = self._local_client_order_ids.get(order.artifact_id)
        if durable_client_id is not None and durable_client_id != order.idempotency_key:
            raise NativeVenueProjectionError("durable Binance client identity changed")
        mapped = self._local_provider_order_ids.get(order.artifact_id)
        if mapped is not None and self._provider_order_ids.get(mapped) != order.artifact_id:
            raise NativeVenueProjectionError("durable Binance order identity is inconsistent")
        self.native.bind_order(order)
        self._orders[order.artifact_id] = order

    def submit(self, order: Order) -> VenueAcknowledgement:
        self.bind_order(order)
        acknowledgement = self.native.submit(order)
        if acknowledgement.accepted:
            self._remember_order_identity(order, acknowledgement.venue_order_id)
        return acknowledgement

    def reconcile(self, order: Order) -> Mapping[str, object] | None:
        self.bind_order(order)
        response = self.native.reconcile(order)
        if isinstance(response, Mapping):
            if response.get("client_order_id") not in {None, order.idempotency_key}:
                raise NativeVenueProjectionError("Binance order query changed client identity")
            provider_id = response.get(
                "venue_order_id", response.get("orderId", response.get("id"))
            )
            if provider_id is not None and str(provider_id).strip():
                self._remember_order_identity(order, str(provider_id).strip())
        return response

    def open_orders(self) -> tuple[VenueAcknowledgement, ...]:
        acknowledgements = self.native.open_orders()
        for acknowledgement in acknowledgements:
            order = self._orders.get(acknowledgement.order_id)
            if order is None:
                raise NativeVenueProjectionError("Binance returned an order absent from the OMS")
            self._remember_order_identity(order, acknowledgement.venue_order_id)
        return acknowledgements

    def cancel(self, order: Order) -> bool:
        self.bind_order(order)
        return self.native.cancel(order)

    def account_snapshot(self) -> VenueAccountSnapshot:
        snapshot = self.transport.fetch_account_snapshot()
        if not isinstance(snapshot, VenueAccountSnapshot):
            raise NativeVenueProjectionError("Binance account read returned an invalid snapshot")
        positions: dict[str, Decimal] = {}
        for provider_id, quantity in snapshot.positions.items():
            if provider_id == self.provider_account_key:
                positions[self.canonical_instrument_id] = quantity
            elif quantity != 0:
                raise NativeVenueProjectionError(
                    "Binance account contains a nonzero unmapped spot position"
                )
        acknowledgements = self.open_orders()
        return VenueAccountSnapshot(
            as_of=snapshot.as_of,
            cash=snapshot.cash,
            positions=positions,
            margin_used=snapshot.margin_used,
            margin_available=snapshot.margin_available,
            venue_open_order_ids=frozenset(item.order_id for item in acknowledgements),
        )

    @property
    def fills(self) -> tuple[Fill, ...]:
        records = self.transport.list_fills()
        normalized: dict[str, Fill] = {}
        for record in records:
            if not isinstance(record, Mapping):
                raise NativeVenueProjectionError("Binance fill projection contains a non-object")
            values = self.transport.fill_contract_values(record)
            if str(values.get("symbol", "")).strip().upper() != self.symbol:
                raise NativeVenueProjectionError("Binance returned a fill for an unmapped symbol")
            provider_fill_id = str(values.get("venue_fill_id", "")).strip()
            provider_order_id = str(values.get("order_id", "")).strip()
            fee = Decimal(str(values.get("fee", "0")))
            commission_asset = str(record.get("commissionAsset", "")).strip().upper()
            if fee and commission_asset != self.quote_asset:
                raise NativeVenueProjectionError(
                    "Binance fill fee is not denominated in the profile quote asset"
                )
            order_id = self._resolve_local_order_id(provider_order_id)
            order = self._orders[order_id]
            if str(values.get("side", "")).strip().lower() != order.side:
                raise NativeVenueProjectionError("Binance fill side differs from the local order")
            fill_quantity = Decimal(str(values["quantity"]))
            if fill_quantity > order.quantity:
                raise NativeVenueProjectionError("Binance fill exceeds the local order quantity")
            fill = Fill(
                artifact_id=uuid5(
                    NAMESPACE_URL,
                    f"advisorai-v3/{self.adapter_identity}/fill/{provider_fill_id}",
                ),
                order_id=order_id,
                venue_fill_id=provider_fill_id,
                quantity=fill_quantity,
                price=Decimal(str(values["price"])),
                fee=fee,
                fee_currency=commission_asset or None,
                occurred_at=values["occurred_at"],
            )
            prior = normalized.get(provider_fill_id)
            if prior is not None and prior != fill:
                raise NativeVenueProjectionError("Binance reused a fill identity with new contents")
            normalized[provider_fill_id] = fill
        return tuple(normalized[key] for key in sorted(normalized))

    def _resolve_local_order_id(self, provider_order_id: str) -> UUID:
        local_id = self._provider_order_ids.get(provider_order_id)
        if local_id is not None:
            return local_id
        raise NativeVenueProjectionError("Binance fill references an unknown local OMS order")

    def _remember_order_identity(self, order: Order, provider_order_id: str) -> None:
        normalized_id = provider_order_id.strip()
        if not normalized_id:
            raise NativeVenueProjectionError("Binance order identity cannot be blank")
        prior_local = self._provider_order_ids.get(normalized_id)
        prior_provider = self._local_provider_order_ids.get(order.artifact_id)
        prior_client_id = self._local_client_order_ids.get(order.artifact_id)
        if (
            (prior_local is not None and prior_local != order.artifact_id)
            or (prior_provider is not None and prior_provider != normalized_id)
            or (prior_client_id is not None and prior_client_id != order.idempotency_key)
        ):
            raise NativeVenueProjectionError("Binance order identity conflicts with durable state")
        if prior_provider is not None:
            return
        identity = {
            "adapter": self.adapter_identity,
            "canonical_instrument_id": self.canonical_instrument_id,
            "symbol": self.symbol,
            "local_order_id": str(order.artifact_id),
            "client_order_id": order.idempotency_key,
            "provider_order_id": normalized_id,
        }
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.VENUE,
                event_type="owner_venue_order_identity_recorded",
                idempotency_key=(
                    f"owner-venue-order:{self.adapter_identity}:{order.idempotency_key}"
                ),
                payload={"identity": identity},
            )
        )
        self._provider_order_ids[normalized_id] = order.artifact_id
        self._local_provider_order_ids[order.artifact_id] = normalized_id
        self._local_client_order_ids[order.artifact_id] = order.idempotency_key

    def _hydrate_order_identities(self) -> None:
        for event in self.ledgers.events(LedgerNamespace.VENUE):
            if event.event_type != "owner_venue_order_identity_recorded":
                continue
            identity = event.payload.get("identity")
            if not isinstance(identity, dict) or identity.get("adapter") != self.adapter_identity:
                continue
            if (
                identity.get("canonical_instrument_id") != self.canonical_instrument_id
                or identity.get("symbol") != self.symbol
            ):
                continue
            try:
                local_id = UUID(str(identity["local_order_id"]))
                provider_id = str(identity["provider_order_id"]).strip()
                client_id = str(identity["client_order_id"]).strip()
            except (KeyError, TypeError, ValueError):
                raise NativeVenueProjectionError(
                    "durable Binance order identity record is malformed"
                ) from None
            if not provider_id or not client_id:
                raise NativeVenueProjectionError("durable Binance order identity is blank")
            if (
                local_id in self._local_provider_order_ids
                or provider_id in self._provider_order_ids
                or local_id in self._local_client_order_ids
            ):
                raise NativeVenueProjectionError("durable Binance order identity is duplicated")
            self._local_provider_order_ids[local_id] = provider_id
            self._provider_order_ids[provider_id] = local_id
            self._local_client_order_ids[local_id] = client_id


__all__ = ["BinanceSpotTestnetOwnerAdapter"]
