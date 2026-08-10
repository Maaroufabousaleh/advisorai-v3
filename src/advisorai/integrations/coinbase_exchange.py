"""Coinbase Exchange Sandbox REST adapter.

This module is deliberately narrower than the generic paper venue transport.
Coinbase Exchange uses a specific HMAC contract, exposes spot positions as
account-balance projections, and has a single reviewed sandbox host.  The
adapter owns only transport and schema translation; ``RiskKernel`` and the OMS
remain the only order authorities.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from pydantic import SecretStr

from advisorai.config import CredentialResolver, CredentialScope, SecretSettings
from advisorai.execution.native import NativeTransport
from advisorai.execution.reconciliation import VenueAccountSnapshot

from .http import HttpClientConfig, HttpTransportError, Requester, SafeHttpClient
from .venue import VenueTransportError

COINBASE_EXCHANGE_SANDBOX_HOST = "api-public.sandbox.exchange.coinbase.com"
COINBASE_EXCHANGE_SANDBOX_BASE_URL = f"https://{COINBASE_EXCHANGE_SANDBOX_HOST}"
COINBASE_EXCHANGE_SANDBOX_WS_HOST = "ws-feed-public.sandbox.exchange.coinbase.com"
COINBASE_EXCHANGE_SANDBOX_WS_URL = f"wss://{COINBASE_EXCHANGE_SANDBOX_WS_HOST}"
COINBASE_EXCHANGE_PRODUCTION_HOST = "api.exchange.coinbase.com"
COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION = "coinbase-exchange-sandbox-v1"

_TERMINAL_ORDER_STATES = frozenset(
    {"done", "settled", "cancelled", "canceled", "rejected", "expired"}
)


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise VenueTransportError(f"Coinbase response is missing {label}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise VenueTransportError(f"Coinbase response has malformed {label}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise VenueTransportError(f"Coinbase response has invalid {label}")
    return parsed


def _iso_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise VenueTransportError(f"Coinbase response is missing {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VenueTransportError(f"Coinbase response has malformed {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise VenueTransportError(f"Coinbase response {label} must include a timezone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CoinbaseExchangeSigner:
    """Official Coinbase Exchange ``CB-ACCESS-*`` signer."""

    api_key: str
    api_secret: SecretStr
    passphrase: SecretStr

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("Coinbase Exchange API key is required")
        if not self.passphrase.get_secret_value().strip():
            raise ValueError("Coinbase Exchange passphrase is required")
        try:
            decoded = base64.b64decode(self.api_secret.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Coinbase Exchange API secret must be valid base64") from exc
        if not decoded:
            raise ValueError("Coinbase Exchange API secret cannot be empty")

    def sign(
        self,
        *,
        method: str,
        request_path: str,
        timestamp: str,
        body: bytes = b"",
    ) -> dict[str, str]:
        """Return the four required headers without exposing secret material.

        Coinbase defines ``requestPath`` as the endpoint path.  Query
        parameters remain on the URL but are intentionally excluded from the
        prehash path, matching the Exchange REST authentication contract.
        """

        parsed = urlsplit(request_path)
        path = parsed.path
        if not path.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("Coinbase request path must be an absolute path")
        try:
            timestamp_decimal = Decimal(timestamp)
        except InvalidOperation as exc:
            raise ValueError("Coinbase timestamp must be numeric seconds") from exc
        if not timestamp.strip() or not timestamp_decimal.is_finite() or timestamp_decimal <= 0:
            raise ValueError("Coinbase timestamp must be positive numeric seconds")
        method = method.strip().upper()
        if not method or any(character.isspace() for character in method):
            raise ValueError("Coinbase HTTP method is required")
        decoded_secret = base64.b64decode(self.api_secret.get_secret_value(), validate=True)
        prehash = f"{timestamp}{method}{path}".encode() + body
        digest = hmac.new(decoded_secret, prehash, hashlib.sha256).digest()
        return {
            "CB-ACCESS-KEY": self.api_key.strip(),
            "CB-ACCESS-SIGN": base64.b64encode(digest).decode("ascii"),
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-ACCESS-PASSPHRASE": self.passphrase.get_secret_value(),
        }


@dataclass(frozen=True, slots=True)
class CoinbaseProductSpec:
    """The subset of a sandbox product record needed for symbol admission."""

    product_id: str
    base_currency: str
    quote_currency: str
    base_increment: Decimal
    quote_increment: Decimal
    base_min_size: Decimal | None
    base_max_size: Decimal | None
    min_market_funds: Decimal | None
    status: str

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> CoinbaseProductSpec:
        product_id = str(record.get("id", "")).strip().upper()
        base = str(record.get("base_currency", "")).strip().upper()
        quote_currency = str(record.get("quote_currency", "")).strip().upper()
        if not product_id or not base or not quote_currency:
            raise VenueTransportError("Coinbase product is missing its identity")
        if product_id != f"{base}-{quote_currency}":
            raise VenueTransportError("Coinbase product identity does not match its currencies")
        status = str(record.get("status", "")).strip().lower()
        if status not in {"online", "auction"}:
            raise VenueTransportError(f"Coinbase product {product_id} is not online")
        return cls(
            product_id=product_id,
            base_currency=base,
            quote_currency=quote_currency,
            base_increment=_decimal(record.get("base_increment"), "base increment", positive=True),
            quote_increment=_decimal(
                record.get("quote_increment"), "quote increment", positive=True
            ),
            base_min_size=(
                _decimal(record["base_min_size"], "base minimum size", positive=True)
                if record.get("base_min_size") is not None
                else None
            ),
            base_max_size=(
                _decimal(record["base_max_size"], "base maximum size", positive=True)
                if record.get("base_max_size") is not None
                else None
            ),
            min_market_funds=(
                _decimal(record["min_market_funds"], "minimum market funds", positive=True)
                if record.get("min_market_funds") is not None
                else None
            ),
            status=status,
        )


class CoinbaseExchangeSandboxTransport(NativeTransport):
    """Paper/testnet-only Coinbase Exchange REST transport."""

    venue_name = "coinbase_exchange_sandbox"
    environment = "paper_testnet"

    def __init__(
        self,
        client: SafeHttpClient,
        signer: CoinbaseExchangeSigner,
        *,
        timestamp_provider: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if client.base_url != COINBASE_EXCHANGE_SANDBOX_BASE_URL:
            raise ValueError("Coinbase adapter accepts only the Exchange Sandbox base URL")
        if tuple(client.config.allowed_hosts) != (COINBASE_EXCHANGE_SANDBOX_HOST,):
            raise ValueError("Coinbase adapter requires an exact sandbox host allowlist")
        self.client = client
        self.signer = signer
        self._timestamp_provider = timestamp_provider or (lambda: f"{time.time():.3f}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._verified_products: dict[str, CoinbaseProductSpec] = {}
        self._catalogue_products: dict[str, CoinbaseProductSpec] = {}
        self._local_to_venue: dict[str, str] = {}
        self._last_server_time: datetime | None = None

    @property
    def verified_product_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._verified_products))

    @property
    def catalogue_product_ids(self) -> tuple[str, ...]:
        """Product IDs observed from provider truth, not necessarily admitted."""

        return tuple(sorted(self._catalogue_products))

    @staticmethod
    def canonical_instrument_id(product_id: str) -> str:
        normalized = product_id.strip().upper()
        if not normalized:
            raise ValueError("Coinbase product ID cannot be blank")
        return f"crypto:{normalized}:coinbase_exchange_sandbox:spot"

    def _path_url(self, path: str, params: Mapping[str, object] | None = None) -> tuple[str, str]:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
            raise VenueTransportError("Coinbase adapter requires an absolute endpoint path")
        normalized_path = parsed.path
        path_segments = set(normalized_path.lower().strip("/").split("/"))
        if path_segments.intersection(
            {
                "withdraw",
                "withdrawal",
                "withdrawals",
                "transfer",
                "transfers",
                "live",
                "prod",
                "production",
            }
        ):
            raise VenueTransportError("Coinbase sandbox adapter rejected a prohibited endpoint")
        query: dict[str, object] = {}
        if parsed.query:
            raise VenueTransportError("Coinbase adapter requires query parameters separately")
        if params:
            query.update(params)
        query_string = urlencode(sorted(query.items()))
        request_url = f"{COINBASE_EXCHANGE_SANDBOX_BASE_URL}{normalized_path}"
        if query_string:
            request_url = f"{request_url}?{query_string}"
        return normalized_path, request_url

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
        authenticated: bool = True,
    ) -> object:
        request_path, url = self._path_url(path, params)
        body = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            if payload is not None
            else None
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers.update(
                self.signer.sign(
                    method=method,
                    request_path=request_path,
                    timestamp=self._timestamp_provider(),
                    body=body or b"",
                )
            )
        try:
            response = self.client.request(
                method,
                url,
                headers=headers,
                body=body,
                acceptable_statuses=frozenset({200, 201, 202, 204}),
            )
        except HttpTransportError as exc:
            raise VenueTransportError(
                "Coinbase Exchange Sandbox request failed", status_code=exc.status_code
            ) from exc
        if not response.body:
            return {}
        try:
            return json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise VenueTransportError("Coinbase Exchange Sandbox returned malformed JSON") from exc

    @staticmethod
    def _records(payload: object, label: str) -> tuple[Mapping[str, object], ...]:
        if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
            raise VenueTransportError(f"Coinbase {label} response must be an array of objects")
        return tuple(dict(item) for item in payload)

    def server_time(self) -> Mapping[str, object]:
        payload = self._request("GET", "/time", authenticated=False)
        if not isinstance(payload, Mapping):
            raise VenueTransportError("Coinbase time response must be an object")
        epoch = _decimal(payload.get("epoch"), "server epoch", positive=True)
        self._last_server_time = datetime.fromtimestamp(float(epoch), tz=UTC)
        if payload.get("iso") is not None:
            _iso_datetime(payload["iso"], "server ISO time")
        return dict(payload)

    def list_products(self) -> tuple[Mapping[str, object], ...]:
        return self._records(self._request("GET", "/products", authenticated=False), "products")

    def verify_product_mappings(
        self,
        products: Sequence[Mapping[str, object]],
        *,
        required: Sequence[str] = ("BTC-USD", "ETH-USD"),
    ) -> tuple[CoinbaseProductSpec, ...]:
        raw_by_id: dict[str, Mapping[str, object]] = {}
        for record in products:
            product_id = str(record.get("id", "")).strip().upper()
            if product_id:
                raw_by_id[product_id] = record
        by_id: dict[str, CoinbaseProductSpec] = {}
        required_ids = tuple(product_id.strip().upper() for product_id in required)
        for product_id in required_ids:
            record = raw_by_id.get(product_id)
            if record is None:
                continue
            by_id[product_id] = CoinbaseProductSpec.from_record(record)
        self._catalogue_products = by_id
        admitted: dict[str, CoinbaseProductSpec] = {}
        for normalized in required_ids:
            spec = by_id.get(normalized)
            if spec is None:
                raise VenueTransportError(
                    f"Coinbase sandbox product list does not contain {normalized}"
                )
            if spec.product_id != normalized:
                raise VenueTransportError("Coinbase product mapping changed during verification")
            admitted[normalized] = spec
        self._verified_products = admitted
        return tuple(admitted[key] for key in sorted(admitted))

    def _verified_product(self, product_id: str) -> CoinbaseProductSpec:
        normalized = product_id.strip().upper()
        try:
            return self._verified_products[normalized]
        except KeyError as exc:
            raise VenueTransportError(
                "Coinbase product is not admitted from the sandbox product list"
            ) from exc

    def _catalogue_product(self, product_id: str) -> CoinbaseProductSpec:
        normalized = product_id.strip().upper()
        try:
            return self._catalogue_products[normalized]
        except KeyError as exc:
            raise VenueTransportError(
                "Coinbase product is not present in the observed sandbox catalogue"
            ) from exc

    def _accounts(self) -> tuple[Mapping[str, object], ...]:
        return self._records(self._request("GET", "/accounts"), "accounts")

    def account_state(self) -> Mapping[str, object]:
        accounts = self._accounts()
        return {"endpoint": "/accounts", "accounts": accounts}

    def list_balances(self) -> tuple[Mapping[str, object], ...]:
        return self._accounts()

    def list_positions(self) -> tuple[Mapping[str, object], ...]:
        positions: list[Mapping[str, object]] = []
        for account in self._accounts():
            currency = str(account.get("currency", "")).strip().upper()
            if not currency or currency == "USD":
                continue
            quantity = _decimal(account.get("balance"), f"{currency} balance")
            hold = _decimal(account.get("hold"), f"{currency} hold")
            if quantity == 0 and hold == 0:
                continue
            product_id = next(
                (
                    product.product_id
                    for product in self._verified_products.values()
                    if product.base_currency == currency and product.quote_currency == "USD"
                ),
                None,
            )
            positions.append(
                {
                    "currency": currency,
                    "quantity": str(quantity),
                    "hold": str(hold),
                    "product_id": product_id,
                    "source_endpoint": "/accounts",
                    "position_type": "spot_balance_projection",
                }
            )
        return tuple(positions)

    def fetch_account_snapshot(self) -> VenueAccountSnapshot:
        accounts = self._accounts()
        cash_account = next(
            (account for account in accounts if str(account.get("currency", "")).upper() == "USD"),
            None,
        )
        if cash_account is None:
            raise VenueTransportError("Coinbase sandbox account state has no USD account")
        cash = _decimal(cash_account.get("balance"), "USD balance")
        margin_available = _decimal(cash_account.get("available"), "USD available")
        positions: dict[str, Decimal] = {}
        for account in accounts:
            currency = str(account.get("currency", "")).strip().upper()
            if not currency or currency == "USD":
                continue
            quantity = _decimal(account.get("balance"), f"{currency} balance")
            if quantity == 0:
                continue
            product = next(
                (
                    item
                    for item in self._verified_products.values()
                    if item.base_currency == currency and item.quote_currency == "USD"
                ),
                None,
            )
            if product is None:
                raise VenueTransportError(
                    f"Coinbase account contains non-zero unmapped asset {currency}"
                )
            positions[self.canonical_instrument_id(product.product_id)] = quantity
        as_of = self._last_server_time
        if as_of is None:
            server = self.server_time()
            as_of = self._last_server_time
            assert as_of is not None
            del server
        return VenueAccountSnapshot(
            as_of=as_of,
            cash=cash,
            positions=positions,
            margin_used=Decimal("0"),
            margin_available=margin_available,
        )

    @staticmethod
    def _normalize_order(record: Mapping[str, object]) -> Mapping[str, object]:
        order_id = str(record.get("id", record.get("order_id", ""))).strip()
        if not order_id:
            raise VenueTransportError("Coinbase order is missing its venue identity")
        client_order_id = str(record.get("client_oid", record.get("client_order_id", ""))).strip()
        state = str(record.get("status", record.get("state", "open"))).strip().lower()
        normalized = dict(record)
        normalized["venue_order_id"] = order_id
        normalized["accepted"] = state not in _TERMINAL_ORDER_STATES
        if client_order_id:
            normalized["client_order_id"] = client_order_id
        return normalized

    def list_open_orders(self) -> tuple[Mapping[str, object], ...]:
        records = self._records(self._request("GET", "/orders"), "orders")
        normalized = tuple(self._normalize_order(record) for record in records)
        for record in normalized:
            client_order_id = record.get("client_order_id")
            venue_order_id = str(record["venue_order_id"])
            if isinstance(client_order_id, str) and client_order_id:
                self._local_to_venue[client_order_id] = venue_order_id
        return normalized

    def query_order(self, *, client_order_id: str) -> Mapping[str, object] | None:
        local_id = client_order_id.strip()
        if not local_id:
            raise VenueTransportError("Coinbase order query requires a client order ID")
        venue_id = self._local_to_venue.get(local_id)
        if venue_id:
            try:
                payload = self._request("GET", f"/orders/{quote(venue_id, safe='')}")
            except VenueTransportError as exc:
                if exc.status_code == 404:
                    return None
                raise
            if not isinstance(payload, Mapping):
                raise VenueTransportError("Coinbase order query response must be an object")
            normalized = self._normalize_order(payload)
            returned_client = normalized.get("client_order_id")
            if returned_client and str(returned_client) != local_id:
                raise VenueTransportError("Coinbase order query returned a different client ID")
            return normalized
        for record in self.list_open_orders():
            if str(record.get("client_order_id", "")) == local_id:
                return record
        return None

    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        local_id = str(payload.get("client_order_id", "")).strip()
        product_id = str(payload.get("symbol", "")).strip().upper()
        side = str(payload.get("side", "")).strip().lower()
        order_type = str(payload.get("order_type", "")).strip().lower()
        if not local_id or not product_id or side not in {"buy", "sell"}:
            raise VenueTransportError("Coinbase order requires client ID, product, and side")
        product = self._verified_product(product_id)
        quantity = _decimal(payload.get("quantity"), "order size", positive=True)
        if quantity < (product.base_min_size or product.base_increment):
            raise VenueTransportError("Coinbase order is below the admitted product minimum")
        if product.base_max_size is not None and quantity > product.base_max_size:
            raise VenueTransportError("Coinbase order exceeds the admitted product maximum")
        body: dict[str, object] = {
            "client_oid": local_id,
            "product_id": product.product_id,
            "side": side,
            "type": "limit" if order_type in {"limit", "passive_limit"} else order_type,
            "size": str(quantity),
        }
        price = payload.get("price")
        if price is not None:
            body["price"] = str(_decimal(price, "order price", positive=True))
        time_in_force = str(payload.get("time_in_force", "GTC")).strip().upper()
        if time_in_force:
            body["time_in_force"] = time_in_force
        if order_type == "passive_limit":
            body["post_only"] = True
        response = self._request("POST", "/orders", payload=body)
        if not isinstance(response, Mapping):
            raise VenueTransportError("Coinbase order acknowledgement must be an object")
        normalized = dict(self._normalize_order(response))
        if not normalized["accepted"]:
            raise VenueTransportError("Coinbase sandbox rejected the order")
        self._local_to_venue[local_id] = str(normalized["venue_order_id"])
        normalized["client_order_id"] = local_id
        normalized["accepted"] = True
        return normalized

    def cancel_order(self, *, client_order_id: str) -> Mapping[str, object]:
        local_id = client_order_id.strip()
        if not local_id:
            raise VenueTransportError("Coinbase cancellation requires a client order ID")
        venue_id = self._local_to_venue.get(local_id)
        if venue_id is None:
            record = self.query_order(client_order_id=local_id)
            venue_id = str(record.get("venue_order_id", "")) if record else ""
        if not venue_id:
            return {
                "client_order_id": local_id,
                "venue_order_id": "unknown",
                "cancelled": False,
            }
        response = self._request("DELETE", f"/orders/{quote(venue_id, safe='')}")
        cancelled = False
        if isinstance(response, list):
            cancelled = venue_id in {str(item) for item in response}
        elif isinstance(response, Mapping):
            cancelled = bool(response.get("cancelled", response.get("accepted", False)))
        return {
            "client_order_id": local_id,
            "venue_order_id": venue_id,
            "cancelled": cancelled,
        }

    def list_fills(self, *, product_id: str | None = None) -> tuple[Mapping[str, object], ...]:
        product_ids = (
            (self._catalogue_product(product_id).product_id,)
            if product_id is not None
            else self.verified_product_ids
        )
        if not product_ids:
            raise VenueTransportError("Coinbase fills require an admitted product filter")
        fills: dict[str, Mapping[str, object]] = {}
        for admitted_product_id in product_ids:
            records = self._records(
                self._request("GET", "/fills", params={"product_id": admitted_product_id}),
                "fills",
            )
            for record in records:
                fill_id = str(record.get("trade_id", record.get("id", ""))).strip()
                if not fill_id:
                    raise VenueTransportError("Coinbase fill is missing its trade identity")
                fills[fill_id] = record
        return tuple(fills[key] for key in sorted(fills))

    def fill_contract_values(self, record: Mapping[str, object]) -> dict[str, Any]:
        """Convert a Coinbase fill into values for the canonical ``Fill`` contract."""

        return {
            "venue_fill_id": str(record.get("trade_id", record.get("id", ""))).strip(),
            "order_id": str(record.get("order_id", "")).strip(),
            "product_id": str(record.get("product_id", "")).strip().upper(),
            "side": str(record.get("side", "")).strip().lower(),
            "quantity": _decimal(record.get("size"), "fill size", positive=True),
            "price": _decimal(record.get("price"), "fill price", positive=True),
            "fee": _decimal(record.get("fee", "0"), "fill fee"),
            "occurred_at": _iso_datetime(record.get("created_at"), "fill timestamp"),
        }


def build_coinbase_exchange_sandbox_transport(
    resolver: CredentialResolver,
    *,
    requester: Requester | None = None,
    timestamp_provider: Callable[[], str] | None = None,
) -> CoinbaseExchangeSandboxTransport:
    """Bind only the ``PAPER_VENUE`` credential scope to the adapter."""

    scoped = resolver.resolve(CredentialScope.PAPER_VENUE)
    settings = SecretSettings.from_mapping(scoped)
    if settings.venue_name != "coinbase_exchange_sandbox":
        raise ValueError("Coinbase adapter requires venue identity coinbase_exchange_sandbox")
    if settings.venue_environment not in {"paper", "testnet", "paper_testnet"}:
        raise ValueError("Coinbase adapter accepts paper/testnet environments only")
    if settings.venue_base_url != COINBASE_EXCHANGE_SANDBOX_BASE_URL:
        raise ValueError("Coinbase adapter refuses non-sandbox or production endpoints")
    if settings.venue_ws_url and settings.venue_ws_url != COINBASE_EXCHANGE_SANDBOX_WS_URL:
        raise ValueError("Coinbase adapter refuses non-sandbox or production WebSocket endpoints")
    api_key = scoped.get("ADVISORAI_VENUE_API_KEY", "").strip()
    api_secret = scoped.get("ADVISORAI_VENUE_API_SECRET", "").strip()
    passphrase = scoped.get("ADVISORAI_VENUE_PASSPHRASE", "").strip()
    if not api_key or not api_secret or not passphrase:
        raise ValueError("Coinbase Exchange sandbox key, secret, and passphrase are required")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(COINBASE_EXCHANGE_SANDBOX_HOST,),
            user_agent=f"advisorai-v3/{COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION}",
        ),
        base_url=COINBASE_EXCHANGE_SANDBOX_BASE_URL,
        requester=requester,
        secret_values={
            "ADVISORAI_VENUE_API_KEY": api_key,
            "ADVISORAI_VENUE_API_SECRET": api_secret,
            "ADVISORAI_VENUE_PASSPHRASE": passphrase,
        },
    )
    return CoinbaseExchangeSandboxTransport(
        client,
        CoinbaseExchangeSigner(
            api_key=api_key,
            api_secret=SecretStr(api_secret),
            passphrase=SecretStr(passphrase),
        ),
        timestamp_provider=timestamp_provider,
    )


__all__ = [
    "COINBASE_EXCHANGE_PRODUCTION_HOST",
    "COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION",
    "COINBASE_EXCHANGE_SANDBOX_BASE_URL",
    "COINBASE_EXCHANGE_SANDBOX_HOST",
    "COINBASE_EXCHANGE_SANDBOX_WS_HOST",
    "COINBASE_EXCHANGE_SANDBOX_WS_URL",
    "CoinbaseExchangeSandboxTransport",
    "CoinbaseExchangeSigner",
    "CoinbaseProductSpec",
    "build_coinbase_exchange_sandbox_transport",
]
