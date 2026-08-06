"""Append-only raw market events and deterministic replay."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MarketEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    event_type: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    occurred_at: datetime
    sequence: int = Field(ge=0)
    price: Decimal | None = Field(default=None, gt=Decimal("0"))
    quantity: Decimal | None = Field(default=None, gt=Decimal("0"))
    bid: Decimal | None = Field(default=None, gt=Decimal("0"))
    ask: Decimal | None = Field(default=None, gt=Decimal("0"))
    payload: tuple[tuple[str, str], ...] = ()

    @field_validator("event_type", "instrument_id")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("market event identity fields cannot be blank")
        return value.strip()

    @field_validator("occurred_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market event timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_quote(self) -> MarketEvent:
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("market event ask cannot be below bid")
        return self

    @classmethod
    def from_raw(
        cls,
        *,
        event_type: str,
        instrument_id: str,
        occurred_at: datetime,
        sequence: int,
        raw_payload: bytes,
        price: Decimal | None = None,
        quantity: Decimal | None = None,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
    ) -> MarketEvent:
        digest = hashlib.sha256(raw_payload).hexdigest()
        event_id = uuid5(
            UUID("f5cc89b3-4b00-4cf0-a78d-c2a42f71b5ae"),
            f"{event_type}:{instrument_id}:{occurred_at.isoformat()}:{digest}:{sequence}",
        )
        return cls(
            event_id=event_id,
            event_type=event_type,
            instrument_id=instrument_id,
            occurred_at=occurred_at,
            sequence=sequence,
            price=price,
            quantity=quantity,
            bid=bid,
            ask=ask,
            payload=(("raw_sha256", digest),),
        )


class NativeMarketMessageParser:
    """Normalize reviewed native venue messages into :class:`MarketEvent`.

    Venue APIs use different field names and often wrap messages in ``data`` or
    ``result`` envelopes.  This parser deliberately accepts only the small
    V3-Core market vocabulary (trade, book, bar, funding, and open interest),
    keeps the raw-message hash, and never copies arbitrary provider payloads
    into a typed artifact.  The same bytes therefore produce the same event ID
    during a WebSocket run, REST bootstrap, or offline replay.
    """

    _TYPE_ALIASES = {
        "trade": "trade",
        "execution": "trade",
        "aggtrade": "trade",
        "book": "book",
        "orderbook": "book",
        "depth": "book",
        "quote": "book",
        "ticker": "book",
        "bar": "bar",
        "kline": "bar",
        "candle": "bar",
        "ohlcv": "bar",
        "funding": "funding",
        "fundingrate": "funding",
        "funding_rate": "funding",
        "openinterest": "open_interest",
        "open_interest": "open_interest",
        "oi": "open_interest",
    }

    def parse(
        self,
        raw: bytes | str | Mapping[str, object],
        *,
        instrument_id: str | None = None,
        received_at: datetime | None = None,
        sequence: int | None = None,
    ) -> MarketEvent:
        events = self.parse_many(
            raw,
            instrument_id=instrument_id,
            received_at=received_at,
            sequence=sequence,
        )
        if len(events) != 1:
            raise ValueError("market message contains multiple records; use parse_many")
        return events[0]

    def parse_many(
        self,
        raw: bytes | str | Mapping[str, object] | Iterable[Mapping[str, object]],
        *,
        instrument_id: str | None = None,
        received_at: datetime | None = None,
        sequence: int | None = None,
    ) -> tuple[MarketEvent, ...]:
        raw_bytes, payload = self._decode(raw)
        received = self._aware(received_at) if received_at is not None else None
        records = self._records(payload)
        if isinstance(payload, Mapping):
            inherited = {
                key: payload[key]
                for key in (
                    "event_type",
                    "type",
                    "event",
                    "e",
                    "channel",
                    "instrument_id",
                    "symbol",
                    "s",
                )
                if key in payload
            }
            if inherited:
                records = tuple({**inherited, **record} for record in records)
        events: list[MarketEvent] = []
        for offset, record in enumerate(records):
            event_sequence = self._sequence(record, sequence, offset)
            events.append(
                self._parse_record(
                    record,
                    raw_payload=raw_bytes,
                    instrument_id=instrument_id,
                    received_at=received,
                    sequence=event_sequence,
                )
            )
        return tuple(events)

    @staticmethod
    def _decode(
        raw: bytes | str | Mapping[str, object] | Iterable[Mapping[str, object]],
    ) -> tuple[bytes, object]:
        if isinstance(raw, bytes):
            try:
                return raw, json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("native market message must be valid JSON") from exc
        if isinstance(raw, str):
            try:
                encoded = raw.encode("utf-8")
                return encoded, json.loads(raw)
            except (UnicodeEncodeError, json.JSONDecodeError) as exc:
                raise ValueError("native market message must be valid JSON") from exc
        if isinstance(raw, Mapping):
            encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
            return encoded, raw
        if isinstance(raw, Iterable):
            records = tuple(raw)
            if any(not isinstance(item, Mapping) for item in records):
                raise ValueError("native market message records must be objects")
            encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
            return encoded, records
        raise ValueError("native market message must be JSON bytes or an object")

    @staticmethod
    def _records(payload: object) -> tuple[Mapping[str, object], ...]:
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, tuple):
            records = list(payload)
        elif isinstance(payload, Mapping):
            nested = payload.get("data", payload.get("result", payload))
            if nested is payload:
                records = [payload]
            elif isinstance(nested, list):
                records = nested
            elif isinstance(nested, Mapping):
                records = [nested]
            else:
                raise ValueError("native market envelope data/result must be an object or list")
        else:
            raise ValueError("native market message must be an object or list")
        if any(not isinstance(item, Mapping) for item in records):
            raise ValueError("native market records must be objects")
        return tuple(records)

    @classmethod
    def _parse_record(
        cls,
        record: Mapping[str, object],
        *,
        raw_payload: bytes,
        instrument_id: str | None,
        received_at: datetime | None,
        sequence: int,
    ) -> MarketEvent:
        event_type = cls._event_type(record)
        instrument = cls._text(
            record,
            "instrument_id",
            "symbol",
            "s",
            "instrument",
            default=instrument_id,
        )
        if not instrument:
            raise ValueError("native market message requires an instrument identifier")
        occurred_at = cls._timestamp(record, received_at)
        if received_at is not None and occurred_at > received_at:
            raise ValueError("native market event is newer than its receipt time")

        price: Decimal | None = None
        quantity: Decimal | None = None
        bid: Decimal | None = None
        ask: Decimal | None = None
        normalized: dict[str, str] = {"event_type": event_type, "instrument_id": instrument}
        if event_type == "trade":
            price = cls._positive(record, "price", "p", "last", "close")
            quantity = cls._positive(record, "quantity", "qty", "q", "size", "volume", "v")
            if price is None or quantity is None:
                raise ValueError("trade messages require positive price and quantity")
            normalized.update(price=str(price), quantity=str(quantity))
        elif event_type == "book":
            bid = cls._positive(record, "bid", "bid_price", "bidPrice", "best_bid", "b")
            ask = cls._positive(record, "ask", "ask_price", "askPrice", "best_ask", "a")
            price = cls._positive(record, "price", "last", "close")
            if bid is None and ask is None and price is None:
                raise ValueError("book messages require a quote or last price")
            if bid is not None:
                normalized["bid"] = str(bid)
            if ask is not None:
                normalized["ask"] = str(ask)
            if price is not None:
                normalized["price"] = str(price)
        elif event_type == "bar":
            price = cls._positive(record, "close", "c", "price", "last")
            quantity = cls._positive(record, "volume", "v", "quantity", "qty")
            if price is None:
                raise ValueError("bar messages require a positive close/price")
            normalized.update(close=str(price))
            for key, aliases in {
                "open": ("open", "o"),
                "high": ("high", "h"),
                "low": ("low", "l"),
                "volume": ("volume", "v", "quantity", "qty"),
            }.items():
                value = cls._positive(record, *aliases)
                if value is not None:
                    normalized[key] = str(value)
        elif event_type == "funding":
            rate = cls._number(record, "funding_rate", "fundingRate", "rate", "r")
            if rate is None:
                raise ValueError("funding messages require a funding rate")
            normalized["funding_rate"] = str(rate)
            price = cls._positive(record, "mark_price", "markPrice", "price", "p")
            if price is not None:
                normalized["mark_price"] = str(price)
        else:  # open_interest
            open_interest = cls._positive(record, "open_interest", "openInterest", "oi", "value")
            if open_interest is None:
                raise ValueError("open-interest messages require a positive value")
            quantity = open_interest
            normalized["open_interest"] = str(open_interest)
            price = cls._positive(record, "mark_price", "markPrice", "price", "p")
            if price is not None:
                normalized["mark_price"] = str(price)

        normalized["sequence"] = str(sequence)
        event = MarketEvent.from_raw(
            event_type=event_type,
            instrument_id=instrument,
            occurred_at=occurred_at,
            sequence=sequence,
            raw_payload=raw_payload,
            price=price,
            quantity=quantity,
            bid=bid,
            ask=ask,
        )
        return event.model_copy(
            update={"payload": tuple(sorted({**dict(event.payload), **normalized}.items()))}
        )

    @classmethod
    def _event_type(cls, record: Mapping[str, object]) -> str:
        raw = cls._text(record, "event_type", "type", "event", "e", "channel")
        if not raw:
            raise ValueError("native market message requires an event type")
        normalized = raw.strip().lower().replace("-", "_").replace("/", "_")
        normalized = normalized.split(":")[-1].split(".")[-1]
        compact = normalized.replace("_", "")
        try:
            return cls._TYPE_ALIASES[normalized]
        except KeyError:
            try:
                return cls._TYPE_ALIASES[compact]
            except KeyError as exc:
                raise ValueError(f"unsupported native market event type {raw!r}") from exc

    @classmethod
    def _sequence(cls, record: Mapping[str, object], base: int | None, offset: int) -> int:
        value = cls._number(record, "sequence", "seq", "u", "U", "lastUpdateId")
        if value is not None:
            if value != value.to_integral_value() or value < 0:
                raise ValueError("native market sequence must be a non-negative integer")
            return int(value)
        if base is None:
            raise ValueError("native market message requires sequence metadata")
        if base < 0:
            raise ValueError("native market sequence must be non-negative")
        return base + offset

    @classmethod
    def _timestamp(cls, record: Mapping[str, object], received_at: datetime | None) -> datetime:
        raw = cls._value(record, "timestamp_ms", "ts", "timestamp", "time", "T", "E")
        if raw is None:
            if received_at is None:
                raise ValueError("native market message requires event timestamp metadata")
            return received_at
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    numeric = Decimal(raw)
                except Exception as exc:
                    raise ValueError("native market timestamp is invalid") from exc
                return cls._numeric_timestamp(numeric)
            return cls._aware(parsed)
        try:
            return cls._numeric_timestamp(Decimal(str(raw)))
        except Exception as exc:
            raise ValueError("native market timestamp is invalid") from exc

    @staticmethod
    def _numeric_timestamp(value: Decimal) -> datetime:
        if not value.is_finite() or value < 0:
            raise ValueError("native market timestamp must be finite and non-negative")
        # Epoch values above 1e11 are milliseconds; smaller values are seconds.
        seconds = value / Decimal("1000") if value >= Decimal("100000000000") else value
        return datetime.fromtimestamp(float(seconds), tz=UTC)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("native market timestamp must include a timezone")
        return value.astimezone(UTC)

    @classmethod
    def _value(cls, record: Mapping[str, object], *keys: str) -> object | None:
        for key in keys:
            if key in record and record[key] is not None:
                return record[key]
        return None

    @classmethod
    def _text(
        cls, record: Mapping[str, object], *keys: str, default: str | None = None
    ) -> str | None:
        value = cls._value(record, *keys)
        if value is None:
            return default.strip() if isinstance(default, str) and default.strip() else None
        if not isinstance(value, str):
            value = str(value)
        normalized = value.strip()
        if normalized:
            return normalized
        return default.strip() if isinstance(default, str) and default.strip() else None

    @classmethod
    def _number(cls, record: Mapping[str, object], *keys: str) -> Decimal | None:
        value = cls._value(record, *keys)
        if value is None or isinstance(value, bool):
            return None
        try:
            number = Decimal(str(value))
        except Exception:
            return None
        return number if number.is_finite() else None

    @classmethod
    def _positive(cls, record: Mapping[str, object], *keys: str) -> Decimal | None:
        value = cls._number(record, *keys)
        if value is None:
            return None
        return value if value > 0 else None


class RawEventSpool:
    """Crash-safe append-only JSONL spool with duplicate event protection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: dict[UUID, MarketEvent] = {}
        if self.path.exists():
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    event = MarketEvent.model_validate(json.loads(line))
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"raw event spool is corrupted at line {line_number}"
                    ) from exc
                prior = self._events.get(event.event_id)
                if prior is not None and prior != event:
                    raise RuntimeError(
                        f"raw event spool reuses event ID {event.event_id} with different content"
                    )
                self._events[event.event_id] = event

    def append(self, event: MarketEvent) -> bool:
        prior = self._events.get(event.event_id)
        if prior is not None:
            if prior != event:
                raise ValueError(f"event ID {event.event_id} is reused for different content")
            return False
        payload = event.model_dump(mode="json", round_trip=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._events[event.event_id] = event
        return True

    def read(self) -> tuple[MarketEvent, ...]:
        if not self.path.exists():
            return ()
        events: dict[UUID, MarketEvent] = {}
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                event = MarketEvent.model_validate(json.loads(line))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"raw event spool is corrupted at line {line_number}") from exc
            prior = events.get(event.event_id)
            if prior is not None and prior != event:
                raise RuntimeError(
                    f"raw event spool reuses event ID {event.event_id} with different content"
                )
            events[event.event_id] = event
        return tuple(events.values())


class ReplayEngine:
    """Deterministic event ordering used by the Nautilus adapter boundary."""

    engine_name = "nautilus_trader"

    def replay(
        self,
        events: Iterable[MarketEvent],
        handler: Callable[[MarketEvent], None],
    ) -> int:
        unique: dict[UUID, MarketEvent] = {}
        for event in events:
            prior = unique.get(event.event_id)
            if prior is not None and prior != event:
                raise ValueError(f"replay received conflicting event ID {event.event_id}")
            unique[event.event_id] = event
        ordered = sorted(
            unique.values(), key=lambda item: (item.occurred_at, item.sequence, str(item.event_id))
        )
        for event in ordered:
            handler(event)
        return len(ordered)
