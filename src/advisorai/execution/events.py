"""Append-only raw market events and deterministic replay."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
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
