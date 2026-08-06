"""Optional WSS feed that spools raw messages before interpretation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WebSocketTransportError(RuntimeError):
    pass


class RawMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(min_length=64, max_length=64)
    sequence: int = Field(ge=0)
    received_at: str = Field(min_length=1)
    payload_b64: str = Field(min_length=1)

    @model_validator(mode="after")
    def verify_payload_digest(self) -> RawMessage:
        try:
            payload = base64.b64decode(self.payload_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("raw WebSocket payload is not valid base64") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if self.message_id != digest:
            raise ValueError("raw WebSocket message_id does not match payload digest")
        return self

    @property
    def received_at_datetime(self) -> datetime:
        try:
            parsed = datetime.fromisoformat(self.received_at)
        except ValueError as exc:
            raise ValueError("raw WebSocket received_at is malformed") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("raw WebSocket received_at must include a timezone")
        return parsed.astimezone(UTC)


class RawMessageSpool:
    """Crash-safe raw-byte spool written before any feed parser runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._messages: dict[str, RawMessage] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    message = RawMessage.model_validate_json(line)
                    prior = self._messages.get(message.message_id)
                    if prior is not None and prior != message:
                        raise RuntimeError(
                            "raw WebSocket spool reuses a message hash with different metadata"
                        )
                    self._messages[message.message_id] = message

    def append(self, payload: bytes, *, received_at: datetime, sequence: int) -> RawMessage:
        message = RawMessage(
            message_id=hashlib.sha256(payload).hexdigest(),
            sequence=sequence,
            received_at=received_at.astimezone(UTC).isoformat(),
            payload_b64=base64.b64encode(payload).decode("ascii"),
        )
        prior = self._messages.get(message.message_id)
        if prior is not None:
            if prior != message:
                raise ValueError("raw WebSocket message hash was reused with different metadata")
            return prior
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(message.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._messages[message.message_id] = message
        return message

    def read(self) -> tuple[tuple[int, bytes], ...]:
        return tuple(
            (message.sequence, base64.b64decode(message.payload_b64))
            for message in sorted(
                self._messages.values(), key=lambda item: (item.sequence, item.message_id)
            )
        )

    def read_records(self) -> tuple[tuple[int, datetime, bytes], ...]:
        """Return sequence, original receipt time, and bytes for deterministic replay."""

        return tuple(
            (message.sequence, message.received_at_datetime, base64.b64decode(message.payload_b64))
            for message in sorted(
                self._messages.values(), key=lambda item: (item.sequence, item.message_id)
            )
        )


class RawWebSocketFeed:
    """Async WSS reader; reconnect policy is explicit and bounded by the caller."""

    def __init__(
        self, url: str, *, allowed_hosts: tuple[str, ...], spool: RawMessageSpool | None = None
    ) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise ValueError("market feed must use an absolute WSS URL")
        if parsed.hostname.lower().rstrip(".") not in {
            item.lower().rstrip(".") for item in allowed_hosts
        }:
            raise ValueError("market feed host is not reviewed")
        self.url = url
        self.spool = spool

    async def messages(
        self, *, subscription: Mapping[str, object] | None = None
    ) -> AsyncIterator[bytes]:
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover - optional live integration
            raise WebSocketTransportError(
                "install the transition extra for WSS collection"
            ) from exc
        try:
            async with connect(
                self.url, open_timeout=15, close_timeout=5, max_size=4 * 1024 * 1024
            ) as socket:
                if subscription is not None:
                    await socket.send(
                        json.dumps(subscription, sort_keys=True, separators=(",", ":"))
                    )
                async for sequence, message in _enumerated(socket):
                    raw = message.encode() if isinstance(message, str) else bytes(message)
                    if self.spool is not None:
                        self.spool.append(raw, received_at=datetime.now(UTC), sequence=sequence)
                    yield raw
        except (TimeoutError, OSError) as exc:
            raise WebSocketTransportError("market WSS connection failed") from exc

    async def market_events(
        self,
        *,
        instrument_id: str | None = None,
        subscription: Mapping[str, object] | None = None,
    ) -> AsyncIterator[object]:
        """Yield typed market events after each raw message is durably spooled.

        Parsing happens after :meth:`messages` has written the raw bytes.  A
        malformed provider payload therefore remains replayable for diagnosis,
        while no invalid event can reach the execution boundary.
        """

        from advisorai.execution.events import NativeMarketMessageParser

        parser = NativeMarketMessageParser()
        sequence = 0
        async for raw in self.messages(subscription=subscription):
            sequence += 1
            received_at = datetime.now(UTC)
            for event in parser.parse_many(
                raw,
                instrument_id=instrument_id,
                received_at=received_at,
                sequence=sequence,
            ):
                yield event

    def replay_market_events(self, *, instrument_id: str | None = None) -> tuple[object, ...]:
        """Replay the persisted raw feed with original receive timestamps."""

        if self.spool is None:
            raise RuntimeError("market event replay requires a raw message spool")
        from advisorai.execution.events import NativeMarketMessageParser

        parser = NativeMarketMessageParser()
        events: list[object] = []
        for sequence, received_at, raw in self.spool.read_records():
            events.extend(
                parser.parse_many(
                    raw,
                    instrument_id=instrument_id,
                    received_at=received_at,
                    sequence=sequence,
                )
            )
        return tuple(events)


async def _enumerated(source):
    sequence = 0
    async for item in source:
        sequence += 1
        yield sequence, item


__all__ = ["RawMessage", "RawMessageSpool", "RawWebSocketFeed", "WebSocketTransportError"]
