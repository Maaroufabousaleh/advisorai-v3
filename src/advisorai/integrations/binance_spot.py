"""Paper-safe Binance Spot Testnet REST transport.

The adapter owns only Binance authentication, provider schemas, and the
reviewed testnet boundary.  Portfolio construction, ``RiskKernel``, OMS state,
and reconciliation remain owned by the existing AdvisorAI execution services.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit

from pydantic import SecretStr

from advisorai.config import CredentialResolver, CredentialScope, SecretSettings
from advisorai.execution.native import NativeTransport
from advisorai.execution.reconciliation import VenueAccountSnapshot

from .http import HttpClientConfig, HttpTransportError, Requester, SafeHttpClient
from .venue import VenueTransportError

BINANCE_SPOT_TESTNET_HOST = "testnet.binance.vision"
BINANCE_SPOT_TESTNET_BASE_URL = f"https://{BINANCE_SPOT_TESTNET_HOST}"
BINANCE_SPOT_TESTNET_WS_API_URL = "wss://ws-api.testnet.binance.vision/ws-api/v3"
BINANCE_SPOT_TESTNET_STREAM_URL = "wss://stream.testnet.binance.vision/ws"
BINANCE_SPOT_TESTNET_ADAPTER_VERSION = "binance-spot-testnet-v1"

_BINANCE_SPOT_TESTNET_WS_URLS = frozenset(
    {BINANCE_SPOT_TESTNET_WS_API_URL, BINANCE_SPOT_TESTNET_STREAM_URL}
)
_FORBIDDEN_PATH_PARTS = frozenset(
    {
        "withdraw",
        "withdrawal",
        "withdrawals",
        "transfer",
        "transfers",
        "sapi",
        "fapi",
        "dapi",
        "papi",
        "live",
        "prod",
        "production",
    }
)
_TERMINAL_ORDER_STATES = frozenset(
    {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}
)


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise VenueTransportError(f"Binance response is missing {label}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise VenueTransportError(f"Binance response has malformed {label}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise VenueTransportError(f"Binance response has invalid {label}")
    return parsed


def _milliseconds(value: object, label: str) -> datetime:
    timestamp = _decimal(value, label, positive=True)
    try:
        return datetime.fromtimestamp(float(timestamp) / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise VenueTransportError(f"Binance response has malformed {label}") from exc


def _total_balance(record: Mapping[str, object], asset: str) -> Decimal:
    free = _decimal(record.get("free", "0"), f"{asset} free balance")
    locked = _decimal(record.get("locked", "0"), f"{asset} locked balance")
    total = free + locked
    if total < 0:
        raise VenueTransportError(f"Binance response has negative {asset} balance")
    return total


def _is_multiple(value: Decimal, increment: Decimal) -> bool:
    remainder = value % increment
    return remainder == 0


@dataclass(frozen=True, slots=True)
class BinanceSpotSigner:
    """Binance Spot HMAC-SHA256 query signer."""

    api_key: str
    api_secret: SecretStr

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("Binance API key is required")
        if not self.api_secret.get_secret_value().strip():
            raise ValueError("Binance API secret is required")

    def signed_query(self, params: Mapping[str, object]) -> str:
        pairs = [(str(key), str(value)) for key, value in params.items() if value is not None]
        query = urlencode(sorted(pairs))
        signature = hmac.new(
            self.api_secret.get_secret_value().encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{query}&signature={signature}"

    def headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key.strip()}


@dataclass(frozen=True, slots=True)
class BinanceSpotSymbolSpec:
    """Provider-truth symbol and order-filter projection."""

    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    base_increment: Decimal
    quote_increment: Decimal
    base_min_qty: Decimal
    base_max_qty: Decimal | None
    min_notional: Decimal | None

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> BinanceSpotSymbolSpec:
        symbol = str(record.get("symbol", "")).strip().upper()
        base_asset = str(record.get("baseAsset", "")).strip().upper()
        quote_asset = str(record.get("quoteAsset", "")).strip().upper()
        status = str(record.get("status", "")).strip().upper()
        if not symbol or not base_asset or not quote_asset:
            raise VenueTransportError("Binance symbol is missing its identity")
        if symbol != f"{base_asset}{quote_asset}":
            raise VenueTransportError("Binance symbol identity does not match its assets")
        filters = record.get("filters", ())
        if not isinstance(filters, Sequence) or isinstance(filters, (str, bytes, bytearray)):
            raise VenueTransportError(f"Binance symbol {symbol} filters are malformed")
        by_type = {
            str(item.get("filterType", "")).upper(): item
            for item in filters
            if isinstance(item, Mapping)
        }
        lot = by_type.get("LOT_SIZE")
        price = by_type.get("PRICE_FILTER")
        if lot is None or price is None:
            raise VenueTransportError(f"Binance symbol {symbol} lacks order filters")
        base_increment = _decimal(lot.get("stepSize"), f"{symbol} base increment", positive=True)
        quote_increment = _decimal(
            price.get("tickSize"), f"{symbol} quote increment", positive=True
        )
        min_notional_record = by_type.get("NOTIONAL") or by_type.get("MIN_NOTIONAL")
        min_notional = None
        if min_notional_record and min_notional_record.get("minNotional") is not None:
            min_notional = _decimal(
                min_notional_record.get("minNotional"), f"{symbol} minimum notional", positive=True
            )
        return cls(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            status=status,
            base_increment=base_increment,
            quote_increment=quote_increment,
            base_min_qty=_decimal(lot.get("minQty"), f"{symbol} minimum quantity", positive=True),
            base_max_qty=(
                _decimal(lot.get("maxQty"), f"{symbol} maximum quantity", positive=True)
                if lot.get("maxQty") is not None
                else None
            ),
            min_notional=min_notional,
        )


class BinanceSpotTestnetTransport(NativeTransport):
    """REST execution/account transport restricted to Binance Spot Testnet."""

    venue_name = "binance_spot_testnet"
    environment = "paper_testnet"

    def __init__(
        self,
        client: SafeHttpClient,
        signer: BinanceSpotSigner,
        *,
        timestamp_provider: Callable[[], int] | None = None,
        recv_window: int = 5000,
    ) -> None:
        if client.base_url != BINANCE_SPOT_TESTNET_BASE_URL:
            raise ValueError("Binance adapter accepts only the Spot Testnet base URL")
        if tuple(client.config.allowed_hosts) != (BINANCE_SPOT_TESTNET_HOST,):
            raise ValueError("Binance adapter requires an exact Spot Testnet host allowlist")
        if recv_window < 1 or recv_window > 60_000:
            raise ValueError("Binance recvWindow must be between 1 and 60000 milliseconds")
        self.client = client
        self.signer = signer
        self._timestamp_provider = timestamp_provider or (lambda: int(time.time() * 1000))
        self.recv_window = recv_window
        self._verified_symbols: dict[str, BinanceSpotSymbolSpec] = {}
        self._catalogue_symbols: dict[str, Mapping[str, object]] = {}
        self._local_to_venue: dict[str, str] = {}
        self._local_to_symbol: dict[str, str] = {}
        self._last_server_time: datetime | None = None

    @property
    def verified_symbol_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._verified_symbols))

    @property
    def catalogue_symbol_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogue_symbols))

    @staticmethod
    def canonical_instrument_id(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("Binance symbol cannot be blank")
        return f"crypto:{normalized}:binance_spot_testnet:spot"

    def _path_url(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        authenticated: bool = False,
    ) -> tuple[str, str, dict[str, str]]:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
            raise VenueTransportError("Binance adapter requires an absolute API path")
        normalized_path = parsed.path.rstrip("/") or "/"
        segments = {part.lower() for part in normalized_path.strip("/").split("/") if part}
        if not normalized_path.startswith("/api/v3/") or segments.intersection(
            _FORBIDDEN_PATH_PARTS
        ):
            raise VenueTransportError("Binance Spot Testnet adapter rejected a prohibited endpoint")
        if parsed.query:
            raise VenueTransportError("Binance adapter requires query parameters separately")
        values = dict(params or {})
        headers: dict[str, str] = {"Accept": "application/json"}
        if authenticated:
            timestamp = self._timestamp_provider()
            if not isinstance(timestamp, int) or timestamp <= 0:
                raise VenueTransportError("Binance request timestamp must be positive milliseconds")
            values.setdefault("timestamp", timestamp)
            values.setdefault("recvWindow", self.recv_window)
            query = self.signer.signed_query(values)
            headers.update(self.signer.headers())
        else:
            query = urlencode(sorted((str(key), str(value)) for key, value in values.items()))
        url = f"{BINANCE_SPOT_TESTNET_BASE_URL}{normalized_path}"
        if query:
            url = f"{url}?{query}"
        return normalized_path, url, headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        authenticated: bool = False,
        write: bool = False,
    ) -> object:
        _request_path, url, headers = self._path_url(path, params, authenticated=authenticated)
        try:
            response = self.client.request(
                method,
                url,
                headers=headers,
                acceptable_statuses=frozenset({200, 201, 202, 204}),
                # A signed write must never be retried automatically: a timeout
                # leaves execution status ambiguous and the OMS must reconcile.
                max_retries=0 if write else None,
            )
        except HttpTransportError as exc:
            raise VenueTransportError(
                "Binance Spot Testnet request failed", status_code=exc.status_code
            ) from exc
        if not response.body:
            return {}
        try:
            return json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise VenueTransportError("Binance Spot Testnet returned malformed JSON") from exc

    @staticmethod
    def _records(value: object, label: str) -> tuple[Mapping[str, object], ...]:
        if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
            raise VenueTransportError(f"Binance {label} response must be an array of objects")
        return tuple(dict(item) for item in value)

    def server_time(self) -> Mapping[str, object]:
        payload = self._request("GET", "/api/v3/time")
        if not isinstance(payload, Mapping):
            raise VenueTransportError("Binance time response must be an object")
        self._last_server_time = _milliseconds(payload.get("serverTime"), "server time")
        return dict(payload)

    def list_products(self) -> tuple[Mapping[str, object], ...]:
        payload = self._request("GET", "/api/v3/exchangeInfo")
        if not isinstance(payload, Mapping):
            raise VenueTransportError("Binance exchange info response must be an object")
        records = self._records(payload.get("symbols"), "symbols")
        self._catalogue_symbols = {
            str(item.get("symbol", "")).strip().upper(): item
            for item in records
            if str(item.get("symbol", "")).strip()
        }
        return records

    def verify_symbol_mappings(
        self,
        symbols: Sequence[Mapping[str, object]],
        *,
        required: Sequence[str] = ("BTCUSDT", "ETHUSDT"),
    ) -> tuple[BinanceSpotSymbolSpec, ...]:
        raw_by_id = {
            str(record.get("symbol", "")).strip().upper(): record
            for record in symbols
            if str(record.get("symbol", "")).strip()
        }
        self._catalogue_symbols = dict(raw_by_id)
        admitted: dict[str, BinanceSpotSymbolSpec] = {}
        for requested in required:
            symbol = requested.strip().upper()
            record = raw_by_id.get(symbol)
            if record is None:
                raise VenueTransportError(
                    f"Binance Spot Testnet product list does not contain {symbol}"
                )
            spec = BinanceSpotSymbolSpec.from_record(record)
            if spec.status != "TRADING":
                raise VenueTransportError(f"Binance Spot Testnet symbol {symbol} is not trading")
            admitted[symbol] = spec
        quote_assets = {spec.quote_asset for spec in admitted.values()}
        if len(quote_assets) != 1:
            raise VenueTransportError("Binance required symbols do not share one quote asset")
        self._verified_symbols = admitted
        return tuple(admitted[key] for key in sorted(admitted))

    def _verified_symbol(self, symbol: str) -> BinanceSpotSymbolSpec:
        normalized = symbol.strip().upper()
        try:
            return self._verified_symbols[normalized]
        except KeyError as exc:
            raise VenueTransportError("Binance symbol is not admitted from provider truth") from exc

    def account_state(self) -> Mapping[str, object]:
        payload = self._request("GET", "/api/v3/account", authenticated=True)
        if not isinstance(payload, Mapping):
            raise VenueTransportError("Binance account response must be an object")
        if str(payload.get("accountType", "")).strip().upper() != "SPOT":
            raise VenueTransportError("Binance account is not a Spot account")
        if payload.get("canTrade") is not True:
            raise VenueTransportError("Binance Spot Testnet account is not trade-enabled")
        return dict(payload)

    @staticmethod
    def _balances(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
        return BinanceSpotTestnetTransport._records(payload.get("balances"), "balances")

    def list_balances(self) -> tuple[Mapping[str, object], ...]:
        return self._balances(self.account_state())

    def list_positions(self) -> tuple[Mapping[str, object], ...]:
        payload = self.account_state()
        balances = self._balances(payload)
        by_asset = {str(item.get("asset", "")).strip().upper(): item for item in balances}
        positions: list[Mapping[str, object]] = []
        for spec in self._verified_symbols.values():
            record = by_asset.get(spec.base_asset)
            if record is None:
                continue
            quantity = _total_balance(record, spec.base_asset)
            if quantity:
                positions.append(
                    {
                        "asset": spec.base_asset,
                        "quantity": str(quantity),
                        "symbol": spec.symbol,
                        "source_endpoint": "/api/v3/account",
                        "position_type": "spot_balance_projection",
                    }
                )
        return tuple(positions)

    def fetch_account_snapshot(self) -> VenueAccountSnapshot:
        payload = self.account_state()
        balances = self._balances(payload)
        by_asset = {str(item.get("asset", "")).strip().upper(): item for item in balances}
        verified = tuple(self._verified_symbols.values())
        if not verified:
            raise VenueTransportError("Binance account snapshot requires verified symbols")
        quote_assets = {spec.quote_asset for spec in verified}
        if len(quote_assets) != 1:
            raise VenueTransportError("Binance account snapshot requires one quote asset")
        quote_asset = next(iter(quote_assets))
        quote_record = by_asset.get(quote_asset)
        if quote_record is None:
            raise VenueTransportError(f"Binance account has no {quote_asset} balance")
        cash = _total_balance(quote_record, quote_asset)
        positions: dict[str, Decimal] = {}
        for spec in verified:
            record = by_asset.get(spec.base_asset)
            if record is None:
                continue
            quantity = _total_balance(record, spec.base_asset)
            if quantity:
                positions[self.canonical_instrument_id(spec.symbol)] = quantity
        as_of = self._last_server_time
        if as_of is None:
            self.server_time()
            as_of = self._last_server_time
        assert as_of is not None
        return VenueAccountSnapshot(
            as_of=as_of,
            cash=cash,
            positions=positions,
            margin_used=Decimal("0"),
            margin_available=_decimal(quote_record.get("free", "0"), f"{quote_asset} free balance"),
        )

    @staticmethod
    def _normalize_order(record: Mapping[str, object]) -> Mapping[str, object]:
        venue_order_id = str(record.get("orderId", record.get("id", ""))).strip()
        if not venue_order_id:
            raise VenueTransportError("Binance order is missing its venue identity")
        client_order_id = str(record.get("clientOrderId", "")).strip()
        state = str(record.get("status", "")).strip().upper()
        normalized = dict(record)
        normalized.update(
            {
                "venue_order_id": venue_order_id,
                "accepted": state not in _TERMINAL_ORDER_STATES,
            }
        )
        if client_order_id:
            normalized["client_order_id"] = client_order_id
        return normalized

    def list_open_orders(self) -> tuple[Mapping[str, object], ...]:
        records: list[Mapping[str, object]] = []
        for symbol in self.verified_symbol_ids:
            payload = self._request(
                "GET", "/api/v3/openOrders", params={"symbol": symbol}, authenticated=True
            )
            records.extend(
                self._normalize_order(item) for item in self._records(payload, "open orders")
            )
        for record in records:
            client_order_id = str(record.get("client_order_id", "")).strip()
            if client_order_id:
                self._local_to_venue[client_order_id] = str(record["venue_order_id"])
                self._local_to_symbol[client_order_id] = str(record.get("symbol", "")).upper()
        return tuple(records)

    def query_order(self, *, client_order_id: str) -> Mapping[str, object] | None:
        local_id = client_order_id.strip()
        if not local_id:
            raise VenueTransportError("Binance order query requires a client order ID")
        symbol = self._local_to_symbol.get(local_id)
        symbols = (symbol,) if symbol is not None else self.verified_symbol_ids
        for candidate in symbols:
            try:
                payload = self._request(
                    "GET",
                    "/api/v3/order",
                    params={"symbol": candidate, "origClientOrderId": local_id},
                    authenticated=True,
                )
            except VenueTransportError as exc:
                # Binance reports an absent order as HTTP 400 rather than a
                # resource-shaped 404. During restart recovery it is safe to
                # try the other admitted symbols, but all other failures stay
                # fail-closed.
                if exc.status_code == 400:
                    continue
                raise
            if not isinstance(payload, Mapping):
                raise VenueTransportError("Binance order query response must be an object")
            normalized = self._normalize_order(payload)
            returned = str(normalized.get("client_order_id", "")).strip()
            if returned and returned != local_id:
                raise VenueTransportError("Binance order query returned a different client ID")
            self._local_to_symbol[local_id] = candidate
            self._local_to_venue[local_id] = str(normalized["venue_order_id"])
            return normalized
        if symbols:
            return None
        # If the caller has not hydrated provider truth yet, the open-order
        # projection is the only safe fallback; it never guesses from a venue
        # order ID or promotes an unknown external order.
        for record in self.list_open_orders():
            if str(record.get("client_order_id", "")).strip() == local_id:
                return record
        return None

    def _validate_order(
        self, payload: Mapping[str, object]
    ) -> tuple[BinanceSpotSymbolSpec, str, str]:
        local_id = str(payload.get("client_order_id", "")).strip()
        symbol = str(payload.get("symbol", "")).strip().upper()
        side = str(payload.get("side", "")).strip().upper()
        if not local_id or not symbol or side not in {"BUY", "SELL"}:
            raise VenueTransportError("Binance order requires client ID, symbol, and side")
        if len(local_id) > 36 or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in local_id
        ):
            raise VenueTransportError("Binance order client ID is not provider-admissible")
        spec = self._verified_symbol(symbol)
        quantity = _decimal(payload.get("quantity"), "order quantity", positive=True)
        if not _is_multiple(quantity, spec.base_increment):
            raise VenueTransportError("Binance order quantity violates the admitted step size")
        if quantity < spec.base_min_qty:
            raise VenueTransportError("Binance order is below the admitted minimum quantity")
        if spec.base_max_qty is not None and quantity > spec.base_max_qty:
            raise VenueTransportError("Binance order exceeds the admitted maximum quantity")
        order_type = str(payload.get("order_type", "")).strip().lower()
        if order_type not in {"limit", "passive_limit", "market"}:
            raise VenueTransportError("Binance order type is not admitted")
        price = payload.get("price")
        if order_type in {"limit", "passive_limit"}:
            if price is None:
                raise VenueTransportError("Binance limit order requires a price")
            parsed_price = _decimal(price, "order price", positive=True)
            if not _is_multiple(parsed_price, spec.quote_increment):
                raise VenueTransportError("Binance order price violates the admitted tick size")
            if spec.min_notional is not None and parsed_price * quantity < spec.min_notional:
                raise VenueTransportError("Binance order is below the admitted minimum notional")
        return spec, local_id, side

    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        spec, local_id, side = self._validate_order(payload)
        order_type = str(payload.get("order_type", "")).strip().lower()
        quantity = _decimal(payload.get("quantity"), "order quantity", positive=True)
        params: dict[str, object] = {
            "symbol": spec.symbol,
            "side": side,
            "type": "LIMIT_MAKER" if order_type == "passive_limit" else order_type.upper(),
            "quantity": format(quantity, "f"),
            "newClientOrderId": local_id,
        }
        if order_type in {"limit", "passive_limit"}:
            params["price"] = format(
                _decimal(payload.get("price"), "order price", positive=True), "f"
            )
            if order_type == "limit":
                params["timeInForce"] = str(payload.get("time_in_force", "GTC")).strip().upper()
        response = self._request(
            "POST", "/api/v3/order", params=params, authenticated=True, write=True
        )
        if not isinstance(response, Mapping):
            raise VenueTransportError("Binance order acknowledgement must be an object")
        normalized = dict(self._normalize_order(response))
        if str(normalized.get("status", "")).upper() in {"REJECTED", "EXPIRED"}:
            raise VenueTransportError("Binance Spot Testnet rejected the order")
        self._local_to_venue[local_id] = str(normalized["venue_order_id"])
        self._local_to_symbol[local_id] = spec.symbol
        normalized["client_order_id"] = local_id
        normalized["accepted"] = True
        return normalized

    def cancel_order(self, *, client_order_id: str) -> Mapping[str, object]:
        local_id = client_order_id.strip()
        if not local_id:
            raise VenueTransportError("Binance cancellation requires a client order ID")
        symbol = self._local_to_symbol.get(local_id)
        if symbol is None:
            record = self.query_order(client_order_id=local_id)
            symbol = str(record.get("symbol", "")).upper() if record else ""
        if not symbol:
            return {"client_order_id": local_id, "venue_order_id": "unknown", "cancelled": False}
        response = self._request(
            "DELETE",
            "/api/v3/order",
            params={"symbol": symbol, "origClientOrderId": local_id},
            authenticated=True,
            write=True,
        )
        if not isinstance(response, Mapping):
            raise VenueTransportError("Binance cancellation response must be an object")
        normalized = self._normalize_order(response)
        return {
            "client_order_id": local_id,
            "venue_order_id": normalized["venue_order_id"],
            "cancelled": str(response.get("status", "")).upper() in {"CANCELED", "CANCELLED"},
        }

    def list_fills(self) -> tuple[Mapping[str, object], ...]:
        fills: dict[str, Mapping[str, object]] = {}
        for symbol in self.verified_symbol_ids:
            payload = self._request(
                "GET", "/api/v3/myTrades", params={"symbol": symbol}, authenticated=True
            )
            for record in self._records(payload, "fills"):
                fill_id = str(record.get("id", "")).strip()
                if not fill_id:
                    raise VenueTransportError("Binance fill is missing its trade identity")
                fills[f"{symbol}:{fill_id}"] = record
        return tuple(fills[key] for key in sorted(fills))

    def fill_contract_values(self, record: Mapping[str, object]) -> dict[str, Any]:
        return {
            "venue_fill_id": f"{str(record.get('symbol', '')).upper()}:{record.get('id', '')}",
            "order_id": str(record.get("orderId", "")).strip(),
            "symbol": str(record.get("symbol", "")).strip().upper(),
            "side": "buy" if bool(record.get("isBuyer", False)) else "sell",
            "quantity": _decimal(record.get("qty"), "fill quantity", positive=True),
            "price": _decimal(record.get("price"), "fill price", positive=True),
            "fee": _decimal(record.get("commission", "0"), "fill fee"),
            "occurred_at": _milliseconds(record.get("time"), "fill time"),
        }


def build_binance_spot_testnet_transport(
    resolver: CredentialResolver,
    *,
    requester: Requester | None = None,
    timestamp_provider: Callable[[], int] | None = None,
) -> BinanceSpotTestnetTransport:
    """Bind only the ``PAPER_VENUE`` scope to the Binance testnet adapter."""

    scoped = resolver.resolve(CredentialScope.PAPER_VENUE)
    settings = SecretSettings.from_mapping(scoped)
    if settings.venue_name != "binance_spot_testnet":
        raise ValueError("Binance adapter requires venue identity binance_spot_testnet")
    if settings.venue_environment not in {"paper", "testnet", "paper_testnet"}:
        raise ValueError("Binance adapter accepts paper/testnet environments only")
    if settings.venue_base_url != BINANCE_SPOT_TESTNET_BASE_URL:
        raise ValueError("Binance adapter refuses non-testnet or production endpoints")
    if settings.venue_ws_url and settings.venue_ws_url not in _BINANCE_SPOT_TESTNET_WS_URLS:
        raise ValueError("Binance adapter refuses non-testnet or production WebSocket endpoints")
    api_key = scoped.get("ADVISORAI_VENUE_API_KEY", "").strip()
    api_secret = scoped.get("ADVISORAI_VENUE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise ValueError("Binance Spot Testnet API key and secret are required")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,),
            user_agent=f"advisorai-v3/{BINANCE_SPOT_TESTNET_ADAPTER_VERSION}",
        ),
        base_url=BINANCE_SPOT_TESTNET_BASE_URL,
        requester=requester,
        secret_values={
            "ADVISORAI_VENUE_API_KEY": api_key,
            "ADVISORAI_VENUE_API_SECRET": api_secret,
        },
    )
    return BinanceSpotTestnetTransport(
        client,
        BinanceSpotSigner(api_key=api_key, api_secret=SecretStr(api_secret)),
        timestamp_provider=timestamp_provider,
    )


__all__ = [
    "BINANCE_SPOT_TESTNET_ADAPTER_VERSION",
    "BINANCE_SPOT_TESTNET_BASE_URL",
    "BINANCE_SPOT_TESTNET_HOST",
    "BINANCE_SPOT_TESTNET_STREAM_URL",
    "BINANCE_SPOT_TESTNET_WS_API_URL",
    "BinanceSpotSigner",
    "BinanceSpotSymbolSpec",
    "BinanceSpotTestnetTransport",
    "build_binance_spot_testnet_transport",
]
