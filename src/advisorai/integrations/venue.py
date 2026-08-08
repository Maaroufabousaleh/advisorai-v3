"""Generic HMAC REST transport for one reviewed paper/testnet venue."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from pydantic import SecretStr

from advisorai.config.secrets import SecretSettings
from advisorai.execution.native import NativeTransport

from .http import HttpTransportError, SafeHttpClient


class VenueTransportError(RuntimeError):
    """A paper/testnet venue response or policy failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class HmacVenueSigner:
    api_key: str
    api_secret: SecretStr
    passphrase: SecretStr | None = None

    def sign(self, *, method: str, path: str, timestamp: str, body: bytes) -> dict[str, str]:
        if (
            not self.api_key.strip()
            or not self.api_secret.get_secret_value().strip()
            or not path.startswith("/")
        ):
            raise ValueError("venue signer requires an API key, secret, and absolute path")
        message = f"{timestamp}{method.upper()}{path}".encode() + body
        digest = hmac.new(
            self.api_secret.get_secret_value().encode(), message, hashlib.sha256
        ).digest()
        headers = {
            "X-API-KEY": self.api_key.strip(),
            "X-API-TIMESTAMP": timestamp,
            "X-API-SIGNATURE": base64.b64encode(digest).decode("ascii"),
        }
        if self.passphrase is not None and self.passphrase.get_secret_value():
            headers["X-API-PASSPHRASE"] = self.passphrase.get_secret_value()
        return headers


class PaperTestnetVenueTransport(NativeTransport):
    """Concrete ``NativeTransport`` that cannot target live/transfer endpoints."""

    def __init__(
        self,
        client: SafeHttpClient,
        settings: SecretSettings,
        *,
        signer: HmacVenueSigner | None = None,
        orders_path: str = "/orders",
        cancel_path: str | None = None,
    ) -> None:
        if settings.venue_environment not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError("native transport is paper/testnet only")
        if not settings.venue_name or not settings.venue_base_url:
            raise ValueError("venue name and reviewed base URL are required")
        if not orders_path.startswith("/") or any(
            token in orders_path.lower() for token in ("withdraw", "transfer", "live", "prod")
        ):
            raise ValueError("venue order path is not admitted")
        if cancel_path is not None and (
            not cancel_path.startswith("/")
            or any(
                token in cancel_path.lower() for token in ("withdraw", "transfer", "live", "prod")
            )
            or "{client_order_id}" not in cancel_path
        ):
            raise ValueError(
                "venue cancellation path must be absolute, paper-safe, and include {client_order_id}"
            )
        self.client = client
        self.settings = settings
        self.signer = signer
        self.orders_path = orders_path.rstrip("/")
        self.cancel_path = (cancel_path or f"{self.orders_path}/{{client_order_id}}").rstrip("/")

    def _request_payload(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> object:
        if any(token in path.lower() for token in ("withdraw", "transfer", "live", "prod")):
            raise VenueTransportError("paper venue transport rejected a prohibited endpoint")
        body = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if payload is not None
            else b""
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.signer is not None:
            headers.update(
                self.signer.sign(
                    method=method, path=path, timestamp=str(int(time.time() * 1000)), body=body
                )
            )
        try:
            if method == "POST":
                response = self.client.post_json(self._url(path), payload or {}, headers=headers)
            else:
                response = self.client.request(
                    method, self._url(path), headers=headers, acceptable_statuses=frozenset({200})
                )
            decoded = json.loads(response.body or b"{}")
        except HttpTransportError as exc:
            raise VenueTransportError(
                "paper venue request failed", status_code=exc.status_code
            ) from exc
        except json.JSONDecodeError as exc:
            raise VenueTransportError("paper venue returned malformed JSON") from exc
        if not isinstance(decoded, Mapping):
            raise VenueTransportError("paper venue response must be an object")
        return decoded.get("result", decoded)

    def _request(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> Mapping[str, object]:
        result = self._request_payload(method, path, payload)
        if not isinstance(result, Mapping):
            raise VenueTransportError("paper venue response result must be an object")
        return result

    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        client_order_id = str(payload.get("client_order_id", "")).strip()
        if not client_order_id:
            raise VenueTransportError("paper venue orders require the local idempotency key")
        return self._request("POST", self.orders_path, payload)

    def cancel_order(self, *, client_order_id: str) -> Mapping[str, object]:
        if not client_order_id.strip():
            raise VenueTransportError("paper venue cancellation requires a client order ID")
        path = self.cancel_path.replace("{client_order_id}", quote(client_order_id, safe=""))
        return self._request("DELETE", path)

    def query_order(self, *, client_order_id: str) -> Mapping[str, object] | None:
        if not client_order_id.strip():
            raise VenueTransportError("paper venue reconciliation requires a client order ID")
        path = f"{self.orders_path}/{quote(client_order_id, safe='')}"
        try:
            return self._request("GET", path)
        except VenueTransportError as exc:
            if exc.status_code == 404:
                return None
            raise

    def list_open_orders(self) -> tuple[Mapping[str, object], ...]:
        """Optional venue projection; local OMS remains authoritative."""
        try:
            result = self._request_payload("GET", self.orders_path)
        except VenueTransportError as exc:
            raise VenueTransportError(
                "paper venue open-order request failed", status_code=exc.status_code
            ) from exc
        if isinstance(result, list):
            raw = result
        elif isinstance(result, Mapping):
            raw = result.get("orders", result.get("data", []))
        else:
            raw = []
        if not isinstance(raw, list):
            raise VenueTransportError("paper venue open-order response must contain a list")
        if not all(isinstance(item, Mapping) for item in raw):
            raise VenueTransportError("paper venue open-order records must be objects")
        return tuple(raw)

    def account_state(self, *, path: str = "/account") -> Mapping[str, object]:
        """Fetch a read-only account projection for reconciliation."""

        return self._request("GET", self._safe_read_path(path))

    def list_fills(self, *, path: str = "/fills") -> tuple[Mapping[str, object], ...]:
        return self._read_collection(path, "fills", "data")

    def list_positions(self, *, path: str = "/positions") -> tuple[Mapping[str, object], ...]:
        return self._read_collection(path, "positions", "data")

    def list_balances(self, *, path: str = "/balances") -> tuple[Mapping[str, object], ...]:
        return self._read_collection(path, "balances", "data")

    def fetch_account_snapshot(self, *, path: str = "/account"):
        """Map a conservative venue account response to the reconciliation port.

        Provider-specific field names are intentionally limited to common
        read-only aliases.  A malformed account response fails closed instead
        of creating a partial local account projection.
        """

        from advisorai.execution.reconciliation import VenueAccountSnapshot

        payload = self.account_state(path=path)
        as_of = self._timestamp(payload.get("as_of", payload.get("timestamp", payload.get("ts"))))
        cash_value = payload.get("cash", payload.get("balance", payload.get("equity")))
        cash = self._decimal(cash_value, "account cash")
        raw_positions = payload.get("positions", {})
        positions: dict[str, Decimal] = {}
        if isinstance(raw_positions, Mapping):
            for instrument, value in raw_positions.items():
                positions[str(instrument)] = self._decimal(value, "position quantity")
        elif isinstance(raw_positions, list):
            for item in raw_positions:
                if not isinstance(item, Mapping):
                    raise VenueTransportError("paper venue positions must be objects")
                instrument = str(
                    item.get("instrument_id", item.get("symbol", item.get("instrument", "")))
                ).strip()
                if not instrument:
                    raise VenueTransportError("paper venue positions require instrument IDs")
                positions[instrument] = self._decimal(
                    item.get("quantity", item.get("qty", item.get("position"))),
                    "position quantity",
                )
        else:
            raise VenueTransportError("paper venue account positions must be a mapping or list")
        margin_used = self._optional_decimal(payload.get("margin_used"), "margin used")
        margin_available = self._optional_decimal(
            payload.get("margin_available"), "margin available"
        )
        return VenueAccountSnapshot(
            as_of=as_of,
            cash=cash,
            positions=positions,
            margin_used=margin_used,
            margin_available=margin_available,
        )

    @staticmethod
    def _safe_read_path(path: str) -> str:
        normalized = path.strip()
        if not normalized.startswith("/") or any(
            token in normalized.lower() for token in ("withdraw", "transfer", "live", "prod")
        ):
            raise VenueTransportError("paper venue read path is not admitted")
        return normalized

    def _read_collection(self, path: str, *keys: str) -> tuple[Mapping[str, object], ...]:
        result = self._request("GET", self._safe_read_path(path))
        raw: object = result
        for key in keys:
            if isinstance(raw, Mapping) and key in raw:
                raw = raw[key]
                break
        if isinstance(raw, Mapping):
            raw = raw.get("items", raw.get("records", []))
        if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
            raise VenueTransportError("paper venue response must contain a collection")
        return tuple(raw)

    @staticmethod
    def _decimal(value: object, label: str) -> Decimal:
        if value is None or isinstance(value, bool):
            raise VenueTransportError(f"paper venue response is missing {label}")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise VenueTransportError(f"paper venue {label} is malformed") from exc
        if not parsed.is_finite():
            raise VenueTransportError(f"paper venue {label} is not finite")
        return parsed

    @classmethod
    def _optional_decimal(cls, value: object, label: str) -> Decimal | None:
        return None if value is None else cls._decimal(value, label)

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if value is None:
            raise VenueTransportError("paper venue account response requires a timestamp")
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise VenueTransportError("paper venue account timestamp must include timezone")
            return value.astimezone(UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                try:
                    value = Decimal(value)
                except InvalidOperation as exc:
                    raise VenueTransportError("paper venue account timestamp is malformed") from exc
            else:
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise VenueTransportError("paper venue account timestamp must include timezone")
                return parsed.astimezone(UTC)
        try:
            numeric = Decimal(str(value))
            seconds = numeric / Decimal("1000") if numeric >= Decimal("100000000000") else numeric
            return datetime.fromtimestamp(float(seconds), tz=UTC)
        except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
            raise VenueTransportError("paper venue account timestamp is malformed") from exc

    def _url(self, path: str) -> str:
        return f"{self.settings.venue_base_url.rstrip('/')}/{path.lstrip('/')}"


__all__ = ["HmacVenueSigner", "PaperTestnetVenueTransport", "VenueTransportError"]
