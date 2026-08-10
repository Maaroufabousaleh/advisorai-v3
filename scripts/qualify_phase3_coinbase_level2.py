#!/usr/bin/env python3
"""Run a bounded, read-only Coinbase Exchange Sandbox level-2 qualification.

The level-2 feed is kept separate from the ticker probe because it has a
different delivery contract: Coinbase documents level-2 as the delivery-
guaranteeing order-book channel.  This runner uses the existing raw-first
WebSocket transport, validates a book snapshot and updates, and replays the
same raw bytes through a deterministic reducer.  It never loads credentials,
authenticates a socket, submits an order, or calls a production host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from advisorai.integrations import (
    COINBASE_EXCHANGE_PRODUCTION_HOST,
    COINBASE_EXCHANGE_PRODUCTION_WS_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_URL,
    RawMessageSpool,
    RawWebSocketFeed,
    WebSocketTransportError,
)

SCHEMA = "advisorai.phase3.coinbase-level2-qualification.v1"
DEFAULT_PRODUCT_ID = "BTC-USD"
DEFAULT_CONNECTION_SECONDS = 12
MAX_CONNECTION_SECONDS = 120
MAX_EVENT_AGE_SECONDS = 30.0
MAX_HEARTBEAT_INTERVAL_SECONDS = 2.5
SUPPORTED_CHANNELS = ("level2", "level2_batch")
DEFAULT_CHANNEL = "level2"
SUBSCRIPTION = {
    "type": "subscribe",
    "product_ids": [DEFAULT_PRODUCT_ID],
    "channels": ["heartbeat", DEFAULT_CHANNEL],
}


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _write_immutable_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _validated_ws_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "wss" or not host:
        raise ValueError("Coinbase source must use an absolute WSS URL")
    if host in {COINBASE_EXCHANGE_PRODUCTION_HOST, COINBASE_EXCHANGE_PRODUCTION_WS_HOST}:
        raise ValueError("production Coinbase endpoints are prohibited")
    if host != COINBASE_EXCHANGE_SANDBOX_WS_HOST:
        raise ValueError("Coinbase WSS host is not the reviewed sandbox host")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Coinbase WSS URL cannot contain a path, query, or fragment")
    if parsed.username or parsed.password:
        raise ValueError("Coinbase WSS URL cannot contain credentials")
    return f"wss://{COINBASE_EXCHANGE_SANDBOX_WS_HOST}"


def _safe_error(exc: Exception) -> dict[str, str]:
    return {"error_class": type(exc).__name__}


def _decimal(value: object, *, positive: bool) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("level2 numeric value is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("level2 numeric value is invalid") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        raise ValueError("level2 numeric value is outside the allowed range")
    return parsed


def _level(payload: object, *, width: int) -> tuple[object, ...]:
    if not isinstance(payload, (list, tuple)) or len(payload) != width:
        raise ValueError("level2 price level has an invalid shape")
    return tuple(payload)


class _BookState:
    """Small typed reducer for Coinbase level-2 snapshot/update messages."""

    def __init__(self) -> None:
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.snapshot_seen = False

    def apply_snapshot(self, payload: dict[str, object]) -> None:
        if self.snapshot_seen:
            raise ValueError("level2 stream contains more than one snapshot")
        bids = payload.get("bids")
        asks = payload.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list) or not bids and not asks:
            raise ValueError("level2 snapshot requires non-empty bids or asks arrays")
        for level in bids:
            price, size = _level(level, width=2)
            self._set(self.bids, price, size, allow_zero=False)
        for level in asks:
            price, size = _level(level, width=2)
            self._set(self.asks, price, size, allow_zero=False)
        self.snapshot_seen = True
        self._validate_crossing()

    def apply_update(self, payload: dict[str, object]) -> None:
        if not self.snapshot_seen:
            raise ValueError("level2 update arrived before snapshot")
        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("level2 update requires a non-empty changes array")
        for change in changes:
            side, price, size = _level(change, width=3)
            if not isinstance(side, str) or side not in {"buy", "sell"}:
                raise ValueError("level2 update side is invalid")
            book = self.bids if side == "buy" else self.asks
            self._set(book, price, size, allow_zero=True)
        self._validate_crossing()

    @staticmethod
    def _set(
        book: dict[Decimal, Decimal], price: object, size: object, *, allow_zero: bool
    ) -> None:
        parsed_price = _decimal(price, positive=True)
        parsed_size = _decimal(size, positive=not allow_zero)
        if parsed_size == 0:
            book.pop(parsed_price, None)
        else:
            if parsed_price in book and not allow_zero:
                raise ValueError("level2 snapshot contains a duplicate price level")
            book[parsed_price] = parsed_size

    def _validate_crossing(self) -> None:
        if self.bids and self.asks and max(self.bids) >= min(self.asks):
            raise ValueError("level2 book has a crossed best bid/ask")

    def digest(self) -> str:
        encoded = json.dumps(
            {
                "snapshot_seen": self.snapshot_seen,
                "bids": [[str(price), str(size)] for price, size in sorted(self.bids.items())],
                "asks": [[str(price), str(size)] for price, size in sorted(self.asks.items())],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(encoded)

    def summary(self) -> dict[str, object]:
        return {
            "snapshot_seen": self.snapshot_seen,
            "bid_level_count": len(self.bids),
            "ask_level_count": len(self.asks),
            "best_bid": str(max(self.bids)) if self.bids else None,
            "best_ask": str(min(self.asks)) if self.asks else None,
            "book_state_sha256": self.digest(),
        }


def _message_metadata(raw: bytes, *, product_id: str) -> dict[str, object]:
    result: dict[str, object] = {
        "raw_sha256": _sha256(raw),
        "payload_bytes": len(raw),
        "type": "invalid",
        "product_id": None,
        "event_time_present": False,
        "change_count": None,
    }
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    message_type = payload.get("type")
    if isinstance(message_type, str) and message_type.strip():
        result["type"] = message_type.strip().lower()[:64]
    raw_product = payload.get("product_id")
    if isinstance(raw_product, str) and raw_product.strip():
        result["product_id"] = raw_product.strip().upper()[:64]
    result["event_time_present"] = isinstance(payload.get("time"), str)
    changes = payload.get("changes")
    if isinstance(changes, list):
        result["change_count"] = len(changes)
    if result["product_id"] is not None and result["product_id"] != product_id:
        result["unexpected_product"] = True
    return result


def _decode_object(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("level2 message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("level2 message is not an object")
    return payload


def _apply_message(state: _BookState, raw: bytes, *, product_id: str) -> str:
    payload = _decode_object(raw)
    message_type = payload.get("type")
    if message_type in {"subscriptions", "heartbeat"}:
        return str(message_type)
    if payload.get("product_id") != product_id:
        raise ValueError("level2 message has an unexpected product")
    if message_type == "snapshot":
        state.apply_snapshot(payload)
        return "snapshot"
    if message_type == "l2update":
        state.apply_update(payload)
        return "l2update"
    if message_type == "error":
        raise ValueError("provider returned a level2 error")
    raise ValueError("unsupported level2 message type")


def _freshness_summary(spool: RawMessageSpool, *, product_id: str) -> dict[str, object]:
    ages: list[float] = []
    heartbeat_receipts: list[datetime] = []
    event_time_present = 0
    malformed_event_times = 0
    future_event_times = 0
    for _sequence, received_at, raw in spool.read_records():
        try:
            payload = _decode_object(raw)
        except ValueError:
            continue
        if payload.get("product_id") != product_id:
            continue
        if payload.get("type") == "heartbeat":
            heartbeat_receipts.append(received_at)
        if payload.get("type") != "l2update":
            continue
        event_time = payload.get("time")
        if not isinstance(event_time, str):
            continue
        event_time_present += 1
        try:
            parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except ValueError:
            malformed_event_times += 1
            continue
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            malformed_event_times += 1
            continue
        age_seconds = (received_at - parsed.astimezone(UTC)).total_seconds()
        if age_seconds < 0:
            future_event_times += 1
        else:
            ages.append(age_seconds)
    heartbeat_intervals = [
        (current - prior).total_seconds()
        for prior, current in zip(heartbeat_receipts, heartbeat_receipts[1:], strict=False)
    ]
    max_age = round(max(ages), 3) if ages else None
    max_heartbeat_interval = round(max(heartbeat_intervals), 3) if heartbeat_intervals else None
    sufficient = len(heartbeat_receipts) >= 2 and bool(ages)
    return {
        "event_time_present_count": event_time_present,
        "malformed_event_time_count": malformed_event_times,
        "future_event_time_count": future_event_times,
        "event_age_seconds_max": max_age,
        "heartbeat_count": len(heartbeat_receipts),
        "heartbeat_interval_seconds_max": max_heartbeat_interval,
        "max_event_age_limit_seconds": MAX_EVENT_AGE_SECONDS,
        "max_heartbeat_interval_limit_seconds": MAX_HEARTBEAT_INTERVAL_SECONDS,
        "state": (
            "pass"
            if sufficient
            and malformed_event_times == 0
            and future_event_times == 0
            and max_age is not None
            and max_age <= MAX_EVENT_AGE_SECONDS
            and (
                max_heartbeat_interval is None
                or max_heartbeat_interval <= MAX_HEARTBEAT_INTERVAL_SECONDS
            )
            else "insufficient_observations"
            if not sufficient
            else "stale_or_malformed"
        ),
    }


def _replay(spool: RawMessageSpool, *, product_id: str) -> tuple[_BookState, Counter[str]]:
    state = _BookState()
    counts: Counter[str] = Counter()
    for _sequence, _received_at, raw in spool.read_records():
        message_type = _apply_message(state, raw, product_id=product_id)
        counts[message_type] += 1
    return state, counts


async def _collect_connection(
    *,
    url: str,
    spool_path: Path,
    product_id: str,
    channel: str,
    duration_seconds: int,
) -> dict[str, object]:
    spool = RawMessageSpool(spool_path)
    feed = RawWebSocketFeed(url, allowed_hosts=(COINBASE_EXCHANGE_SANDBOX_WS_HOST,), spool=spool)
    state = _BookState()
    type_counts: Counter[str] = Counter()
    validation_failures: Counter[str] = Counter()
    started = datetime.now(UTC)
    monotonic_started = time.monotonic()
    termination = "unknown"
    error: dict[str, str] | None = None
    try:
        async with asyncio.timeout(duration_seconds):
            async for raw in feed.messages(
                subscription={
                    **SUBSCRIPTION,
                    "product_ids": [product_id],
                    "channels": ["heartbeat", channel],
                }
            ):
                metadata = _message_metadata(raw, product_id=product_id)
                if metadata.get("unexpected_product"):
                    validation_failures["unexpected_product"] += 1
                    continue
                try:
                    type_counts[_apply_message(state, raw, product_id=product_id)] += 1
                except ValueError as exc:
                    validation_failures[type(exc).__name__] += 1
    except TimeoutError:
        termination = "duration_elapsed"
    except WebSocketTransportError as exc:
        termination = "transport_failure"
        error = _safe_error(exc)
    except Exception as exc:  # report class only; never provider text or payloads
        termination = "unexpected_failure"
        error = _safe_error(exc)
    elapsed_seconds = round(time.monotonic() - monotonic_started, 3)
    replay_error: dict[str, str] | None = None
    replay_state = _BookState()
    replay_counts: Counter[str] = Counter()
    try:
        replay_state, replay_counts = _replay(spool, product_id=product_id)
    except (TypeError, ValueError, RuntimeError) as exc:
        replay_error = _safe_error(exc)
    replay_match = (
        replay_error is None
        and replay_state.digest() == state.digest()
        and replay_counts == type_counts
    )
    freshness = _freshness_summary(spool, product_id=product_id)
    passed = (
        termination == "duration_elapsed"
        and type_counts["subscriptions"] >= 1
        and type_counts["snapshot"] >= 1
        and type_counts["l2update"] >= 1
        and type_counts["heartbeat"] >= 2
        and not validation_failures
        and replay_match
        and freshness["state"] == "pass"
    )
    result: dict[str, object] = {
        "started_at": started.isoformat(),
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "termination": termination,
        "passed": passed,
        "raw_message_count": len(spool.read_records()),
        "raw_message_sha256": [
            _sha256(raw) for _sequence, _received_at, raw in spool.read_records()
        ],
        "message_type_counts": dict(sorted(type_counts.items())),
        "validation_failure_counts": dict(sorted(validation_failures.items())),
        "replay_match": replay_match,
        "live_book": state.summary(),
        "replay_book": replay_state.summary(),
        "freshness": freshness,
        "raw_spool": str(spool_path.name),
    }
    if error is not None:
        result["error"] = error
    if replay_error is not None:
        result["replay_error"] = replay_error
    return result


def run_evidence(
    output_root: Path,
    *,
    ws_url: str = COINBASE_EXCHANGE_SANDBOX_WS_URL,
    product_id: str = DEFAULT_PRODUCT_ID,
    channel: str = DEFAULT_CHANNEL,
    duration_seconds: int = DEFAULT_CONNECTION_SECONDS,
) -> tuple[Path, dict[str, object], str]:
    """Run one bounded public level-2 read and write immutable evidence."""

    url = _validated_ws_url(ws_url)
    product_id = product_id.strip().upper()
    if product_id != DEFAULT_PRODUCT_ID:
        raise ValueError("the Phase-3 Coinbase level2 probe is pinned to BTC-USD")
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError("the Phase-3 Coinbase level2 probe has an unsupported channel")
    if not 1 <= duration_seconds <= MAX_CONNECTION_SECONDS:
        raise ValueError("connection duration is outside the bounded qualification limit")

    output_root = output_root.expanduser().resolve()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_directory = output_root / run_id
    suffix = 1
    while run_directory.exists():
        suffix += 1
        run_directory = output_root / f"{run_id}-{suffix}"
    run_directory.mkdir(parents=True, exist_ok=False)
    connection = asyncio.run(
        _collect_connection(
            url=url,
            spool_path=run_directory / "raw-ws.jsonl",
            product_id=product_id,
            channel=channel,
            duration_seconds=duration_seconds,
        )
    )
    passed = bool(connection["passed"])
    report: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_directory.name,
        "measured_at": datetime.now(UTC).isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "websocket_transport_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/integrations/websocket.py"
            ).read_bytes()
        ),
        "network_calls": 1,
        "venue_identity": "coinbase_exchange_sandbox",
        "venue_environment": "paper_testnet",
        "reviewed_ws_host": COINBASE_EXCHANGE_SANDBOX_WS_HOST,
        "ws_endpoint": url,
        "product_id": product_id,
        "channel": channel,
        "subscription": {
            **SUBSCRIPTION,
            "product_ids": [product_id],
            "channels": ["heartbeat", channel],
        },
        "connection": connection,
        "passed": passed,
        "gate_state": (
            "EXTERNALLY_MEASURED / QUALIFIED_FOR_LEVEL2_SOURCE_SMOKE"
            if passed
            else "EXTERNALLY_MEASURED / PENDING_EXTERNAL_EVIDENCE"
        ),
        "admission_opened": False,
        "credential_references": [],
        "notes": (
            "Public unauthenticated market-data feed only; no order, account, or execution authority.",
            "Coinbase level2 snapshot/update state was validated and replayed from the raw spool.",
            "The level2 channel's provider delivery guarantee does not replace source freshness, recovery, or Phase-3 admission evidence.",
        ),
    }
    report_path = run_directory / "phase3-coinbase-level2-qualification.json"
    _write_immutable_json(report_path, report)
    evidence_sha256 = _sha256(report_path.read_bytes())
    _write_immutable_json(
        run_directory / "evidence-manifest.json",
        {
            "schema": f"{SCHEMA}.manifest",
            "run_id": run_directory.name,
            "report": report_path.name,
            "evidence_sha256": evidence_sha256,
            "raw_spool": "raw-ws.jsonl",
        },
    )
    latest_path = output_root / "latest.json"
    temporary = output_root / ".latest.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema": f"{SCHEMA}.latest",
                    "run_id": run_directory.name,
                    "evidence_sha256": evidence_sha256,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, latest_path)
    return report_path, report, evidence_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="allow public WSS reads")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase3/coinbase-level2-qualification"),
    )
    parser.add_argument("--ws-url", default=COINBASE_EXCHANGE_SANDBOX_WS_URL)
    parser.add_argument("--product-id", default=DEFAULT_PRODUCT_ID)
    parser.add_argument("--channel", choices=SUPPORTED_CHANNELS, default=DEFAULT_CHANNEL)
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_CONNECTION_SECONDS)
    args = parser.parse_args()
    if not args.real:
        parser.error("network reads require explicit --real")
    path, report, evidence_sha256 = run_evidence(
        args.evidence_dir,
        ws_url=args.ws_url,
        product_id=args.product_id,
        channel=args.channel,
        duration_seconds=args.duration_seconds,
    )
    print(
        json.dumps(
            {
                "report": str(path),
                "evidence_sha256": evidence_sha256,
                "network_calls": report["network_calls"],
                "message_type_counts": report["connection"]["message_type_counts"],
                "passed": report["passed"],
                "gate_state": report["gate_state"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
