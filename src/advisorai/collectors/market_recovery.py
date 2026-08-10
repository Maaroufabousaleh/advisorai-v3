"""Provider-truth snapshot and incremental order-book recovery primitives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SequenceRecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str = Field(pattern="^(pass|failed_closed)$")
    symbol: str = Field(min_length=1)
    snapshot_last_update_id: int | None = Field(default=None, ge=0)
    first_applied_update_id: int | None = Field(default=None, ge=0)
    last_applied_update_id: int | None = Field(default=None, ge=0)
    received_update_count: int = Field(ge=0)
    discarded_before_sync_count: int = Field(ge=0)
    repeated_or_old_count: int = Field(ge=0)
    sequence_gap_count: int = Field(ge=0)
    malformed_update_count: int = Field(ge=0)
    snapshot_reacquired: bool
    local_book_invalidated: bool
    reconstructed_book_sha256: str | None = None
    validation_error: str | None = None


@dataclass(slots=True)
class _Book:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_update_id: int = 0

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> _Book:
        last = _positive_int(payload.get("lastUpdateId"), "snapshot lastUpdateId")
        book = cls(last_update_id=last)
        book._apply_levels(book.bids, payload.get("bids"), "snapshot bids")
        book._apply_levels(book.asks, payload.get("asks"), "snapshot asks")
        book._validate()
        return book

    @staticmethod
    def _apply_levels(
        destination: dict[Decimal, Decimal],
        raw_levels: object,
        label: str,
        *,
        allow_updates: bool = False,
    ) -> None:
        if not isinstance(raw_levels, list):
            raise ValueError(f"{label} must be an array")
        for level in raw_levels:
            if not isinstance(level, list) or len(level) != 2:
                raise ValueError(f"{label} contains an invalid level")
            price = _decimal(level[0], f"{label} price", positive=True)
            quantity = _decimal(level[1], f"{label} quantity", positive=False)
            if quantity == 0:
                destination.pop(price, None)
            elif price in destination and not allow_updates:
                raise ValueError(f"{label} contains a duplicate price")
            else:
                destination[price] = quantity

    def apply_update(self, payload: Mapping[str, Any]) -> None:
        self._apply_levels(self.bids, payload.get("b"), "update bids", allow_updates=True)
        self._apply_levels(self.asks, payload.get("a"), "update asks", allow_updates=True)
        self.last_update_id = _positive_int(payload.get("u"), "update u")
        self._validate()

    def _validate(self) -> None:
        if not self.bids or not self.asks:
            raise ValueError("reconstructed book must contain both sides")
        if max(self.bids) >= min(self.asks):
            raise ValueError("reconstructed book is crossed")

    def digest(self) -> str:
        encoded = json.dumps(
            {
                "last_update_id": self.last_update_id,
                "bids": [[str(price), str(size)] for price, size in sorted(self.bids.items())],
                "asks": [[str(price), str(size)] for price, size in sorted(self.asks.items())],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _decimal(value: object, label: str, *, positive: bool) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        raise ValueError(f"{label} is outside the allowed range")
    return parsed


def _event(payload: Mapping[str, Any], *, symbol: str) -> tuple[int, int]:
    if payload.get("e") != "depthUpdate":
        raise ValueError("incremental message is not a depth update")
    if str(payload.get("s", "")).upper() != symbol.upper():
        raise ValueError("incremental message has an unexpected symbol")
    first = _positive_int(payload.get("U"), "update U")
    last = _positive_int(payload.get("u"), "update u")
    if first > last:
        raise ValueError("incremental update range is inverted")
    return first, last


def recover_binance_depth(
    snapshot: Mapping[str, Any],
    updates: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    snapshot_reacquired: bool = True,
) -> tuple[SequenceRecoveryResult, str | None]:
    """Rebuild a book only after a provider-truth snapshot and contiguous updates.

    A gap invalidates the local book and returns ``failed_closed``.  Callers
    must reacquire a new snapshot and invoke this function again; no caller is
    allowed to continue applying updates to the uncertain book.
    """

    if not symbol.strip():
        raise ValueError("recovery symbol cannot be blank")
    received = 0
    discarded = 0
    repeated = 0
    gaps = 0
    malformed = 0
    first_applied: int | None = None
    last_applied: int | None = None
    book: _Book | None = None
    snapshot_id: int | None = None
    error: str | None = None
    try:
        book = _Book.from_snapshot(snapshot)
        snapshot_id = book.last_update_id
        expected = snapshot_id + 1
        for payload in updates:
            received += 1
            try:
                first, last = _event(payload, symbol=symbol)
            except ValueError:
                malformed += 1
                raise
            if last < expected:
                repeated += 1
                discarded += 1
                continue
            if first > expected:
                gaps += 1
                raise ValueError("incremental sequence gap requires snapshot recovery")
            book.apply_update(payload)
            first_applied = first if first_applied is None else first_applied
            last_applied = last
            expected = last + 1
        if first_applied is None:
            raise ValueError("incremental stream did not synchronize with snapshot")
    except (TypeError, ValueError, OverflowError) as exc:
        error = str(exc)
    result = SequenceRecoveryResult(
        state="pass" if error is None and book is not None else "failed_closed",
        symbol=symbol.upper(),
        snapshot_last_update_id=snapshot_id,
        first_applied_update_id=first_applied,
        last_applied_update_id=last_applied,
        received_update_count=received,
        discarded_before_sync_count=discarded,
        repeated_or_old_count=repeated,
        sequence_gap_count=gaps,
        malformed_update_count=malformed,
        snapshot_reacquired=snapshot_reacquired,
        local_book_invalidated=error is not None,
        reconstructed_book_sha256=book.digest() if error is None and book is not None else None,
        validation_error=error,
    )
    return result, result.reconstructed_book_sha256


def replay_equivalent(live: SequenceRecoveryResult, replay: SequenceRecoveryResult) -> bool:
    """Require the same successful reconstructed book, not merely message counts."""

    return (
        live.state == "pass"
        and replay.state == "pass"
        and live.reconstructed_book_sha256 is not None
        and live.reconstructed_book_sha256 == replay.reconstructed_book_sha256
    )


__all__ = ["SequenceRecoveryResult", "recover_binance_depth", "replay_equivalent"]
