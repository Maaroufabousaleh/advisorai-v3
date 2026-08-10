#!/usr/bin/env python3
"""Run a bounded, read-only Coinbase Exchange Sandbox WSS qualification.

The probe uses the existing raw-first WebSocket transport and the public
market-data feed only.  It never loads credentials, authenticates a socket,
submits an order, or calls a production Coinbase host.  Each connection gets
its own raw spool so reconnect evidence remains append-only even when the
provider repeats an identical control message after reconnecting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from advisorai.execution.events import MarketEvent, NativeMarketMessageParser
from advisorai.integrations import (
    COINBASE_EXCHANGE_PRODUCTION_HOST,
    COINBASE_EXCHANGE_PRODUCTION_WS_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_URL,
    RawMessageSpool,
    RawWebSocketFeed,
    WebSocketTransportError,
)

SCHEMA = "advisorai.phase3.coinbase-wss-qualification.v1"
DEFAULT_PRODUCT_ID = "BTC-USD"
DEFAULT_CONNECTION_SECONDS = 12
DEFAULT_CONNECTIONS = 2
MAX_CONNECTION_SECONDS = 120
MAX_CONNECTIONS = 4
MAX_EVENT_AGE_SECONDS = 30.0
MAX_HEARTBEAT_INTERVAL_SECONDS = 2.5
SUBSCRIPTION = {
    "type": "subscribe",
    "product_ids": [DEFAULT_PRODUCT_ID],
    "channels": ["heartbeat", "ticker"],
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


def _safe_sequence(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _message_metadata(raw: bytes, *, product_id: str) -> dict[str, object]:
    """Return bounded metadata without copying provider message content."""

    result: dict[str, object] = {
        "raw_sha256": _sha256(raw),
        "payload_bytes": len(raw),
        "type": "invalid",
        "product_id": None,
        "sequence": None,
        "event_time_present": False,
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
    result["sequence"] = _safe_sequence(payload.get("sequence"))
    result["event_time_present"] = isinstance(payload.get("time"), str)
    if result["product_id"] is not None and result["product_id"] != product_id:
        result["unexpected_product"] = True
    return result


def _sequence_summary(metadata: list[dict[str, object]]) -> dict[str, object]:
    sequences: dict[str, list[int]] = defaultdict(list)
    for item in metadata:
        product = item.get("product_id")
        sequence = item.get("sequence")
        if isinstance(product, str) and isinstance(sequence, int):
            sequences[product].append(sequence)

    by_product: dict[str, dict[str, object]] = {}
    for product, values in sorted(sequences.items()):
        gaps = 0
        gap_total = 0
        out_of_order = 0
        repeated = 0
        for prior, current in zip(values, values[1:], strict=False):
            if current > prior + 1:
                gaps += 1
                gap_total += current - prior - 1
            elif current < prior:
                out_of_order += 1
            elif current == prior:
                repeated += 1
        by_product[product] = {
            "observations": len(values),
            "first": values[0],
            "last": values[-1],
            "gap_count": gaps,
            "gap_total": gap_total,
            "out_of_order_count": out_of_order,
            "repeated_count": repeated,
            "state": (
                "pass"
                if len(values) >= 2 and gaps == 0 and out_of_order == 0
                else "insufficient_observations"
                if len(values) < 2
                else "observed_gap_or_reordering"
            ),
        }
    return {
        "products": by_product,
        "state": (
            "pass"
            if by_product and all(item["state"] == "pass" for item in by_product.values())
            else "insufficient_observations"
            if not by_product
            else "observed_gap_or_reordering"
        ),
    }


def _provider_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _freshness_summary(spool: RawMessageSpool, *, product_id: str) -> dict[str, object]:
    ages: list[float] = []
    heartbeat_receipts: list[datetime] = []
    event_time_present = 0
    malformed_event_times = 0
    future_event_times = 0
    for _sequence, received_at, raw in spool.read_records():
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("product_id") != product_id:
            continue
        if payload.get("type") == "heartbeat":
            heartbeat_receipts.append(received_at)
        if "time" not in payload:
            continue
        event_time_present += 1
        event_time = _provider_time(payload.get("time"))
        if event_time is None:
            malformed_event_times += 1
            continue
        age_seconds = (received_at - event_time).total_seconds()
        if age_seconds < 0:
            future_event_times += 1
            continue
        ages.append(age_seconds)

    heartbeat_intervals = [
        (current - prior).total_seconds()
        for prior, current in zip(heartbeat_receipts, heartbeat_receipts[1:], strict=False)
    ]
    max_age = round(max(ages), 3) if ages else None
    max_heartbeat_interval = round(max(heartbeat_intervals), 3) if heartbeat_intervals else None
    sufficient = len(heartbeat_receipts) >= 2 and (
        bool(ages) or malformed_event_times > 0 or future_event_times > 0
    )
    state = (
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
    )
    return {
        "event_time_present_count": event_time_present,
        "malformed_event_time_count": malformed_event_times,
        "future_event_time_count": future_event_times,
        "event_age_seconds_max": max_age,
        "heartbeat_count": len(heartbeat_receipts),
        "heartbeat_interval_count": len(heartbeat_intervals),
        "heartbeat_interval_seconds_max": max_heartbeat_interval,
        "max_event_age_limit_seconds": MAX_EVENT_AGE_SECONDS,
        "max_heartbeat_interval_limit_seconds": MAX_HEARTBEAT_INTERVAL_SECONDS,
        "state": state,
    }


def _event_digest(event: MarketEvent) -> str:
    encoded = json.dumps(
        event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return _sha256(encoded)


def _replay_ticker_events(spool: RawMessageSpool, *, product_id: str) -> tuple[str, ...]:
    parser = NativeMarketMessageParser()
    digests: list[str] = []
    for _sequence, received_at, raw in spool.read_records():
        metadata = _message_metadata(raw, product_id=product_id)
        if metadata["type"] != "ticker" or metadata.get("product_id") != product_id:
            continue
        event = parser.parse(raw, instrument_id=product_id, received_at=received_at)
        digests.append(_event_digest(event))
    return tuple(digests)


def _safe_error(exc: Exception) -> dict[str, str]:
    return {"error_class": type(exc).__name__}


async def _collect_connection(
    *,
    url: str,
    subscription: dict[str, object],
    spool_path: Path,
    product_id: str,
    duration_seconds: int,
    connection_number: int,
) -> dict[str, object]:
    spool = RawMessageSpool(spool_path)
    feed = RawWebSocketFeed(url, allowed_hosts=(COINBASE_EXCHANGE_SANDBOX_WS_HOST,), spool=spool)
    metadata: list[dict[str, object]] = []
    parser = NativeMarketMessageParser()
    ticker_event_digests: list[str] = []
    parser_failures = 0
    started = datetime.now(UTC)
    monotonic_started = time.monotonic()
    termination = "unknown"
    error: dict[str, str] | None = None
    try:
        async with asyncio.timeout(duration_seconds):
            async for raw in feed.messages(subscription=subscription):
                item = _message_metadata(raw, product_id=product_id)
                metadata.append(item)
                if item["type"] == "ticker" and item.get("product_id") == product_id:
                    try:
                        event = parser.parse(
                            raw,
                            instrument_id=product_id,
                            received_at=datetime.now(UTC),
                        )
                        ticker_event_digests.append(_event_digest(event))
                    except (TypeError, ValueError):
                        parser_failures += 1
    except TimeoutError:
        termination = "duration_elapsed"
    except WebSocketTransportError as exc:
        termination = "transport_failure"
        error = _safe_error(exc)
    except Exception as exc:  # report class only; never provider text or payloads
        termination = "unexpected_failure"
        error = _safe_error(exc)
    elapsed_seconds = round(time.monotonic() - monotonic_started, 3)
    replay_digests: tuple[str, ...] = ()
    replay_error: dict[str, str] | None = None
    try:
        replay_digests = _replay_ticker_events(spool, product_id=product_id)
    except (TypeError, ValueError, RuntimeError) as exc:
        replay_error = _safe_error(exc)

    type_counts = Counter(item["type"] for item in metadata if isinstance(item.get("type"), str))
    unexpected_products = sum(bool(item.get("unexpected_product")) for item in metadata)
    sequence = _sequence_summary(metadata)
    freshness = _freshness_summary(spool, product_id=product_id)
    replay_match = tuple(ticker_event_digests) == replay_digests and replay_error is None
    passed = (
        termination == "duration_elapsed"
        and type_counts.get("subscriptions", 0) >= 1
        and type_counts.get("ticker", 0) >= 1
        and parser_failures == 0
        and unexpected_products == 0
        and replay_match
        and sequence["state"] == "pass"
        and freshness["state"] == "pass"
    )
    result: dict[str, object] = {
        "connection_number": connection_number,
        "started_at": started.isoformat(),
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "termination": termination,
        "passed": passed,
        "raw_message_count": len(metadata),
        "raw_payload_bytes": sum(int(item["payload_bytes"]) for item in metadata),
        "raw_message_sha256": [item["raw_sha256"] for item in metadata],
        "message_type_counts": dict(sorted(type_counts.items())),
        "ticker_event_count": len(ticker_event_digests),
        "parser_failure_count": parser_failures,
        "unexpected_product_count": unexpected_products,
        "replay_event_count": len(replay_digests),
        "replay_match": replay_match,
        "sequence": sequence,
        "freshness": freshness,
        "event_time_present_count": sum(bool(item["event_time_present"]) for item in metadata),
        "raw_spool": str(spool_path.name),
    }
    if error is not None:
        result["error"] = error
    if replay_error is not None:
        result["replay_error"] = replay_error
    return result


async def _collect_connections(
    *,
    url: str,
    output_directory: Path,
    product_id: str,
    duration_seconds: int,
    connections: int,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for number in range(1, connections + 1):
        results.append(
            await _collect_connection(
                url=url,
                subscription={
                    **SUBSCRIPTION,
                    "product_ids": [product_id],
                },
                spool_path=output_directory / f"connection-{number:02d}" / "raw-ws.jsonl",
                product_id=product_id,
                duration_seconds=duration_seconds,
                connection_number=number,
            )
        )
    return results


def run_evidence(
    output_root: Path,
    *,
    ws_url: str = COINBASE_EXCHANGE_SANDBOX_WS_URL,
    product_id: str = DEFAULT_PRODUCT_ID,
    duration_seconds: int = DEFAULT_CONNECTION_SECONDS,
    connections: int = DEFAULT_CONNECTIONS,
) -> tuple[Path, dict[str, object], str]:
    """Run bounded public WSS reads and write immutable sanitized evidence."""

    url = _validated_ws_url(ws_url)
    product_id = product_id.strip().upper()
    if product_id != DEFAULT_PRODUCT_ID:
        raise ValueError("the Phase-3 Coinbase WSS probe is pinned to BTC-USD")
    if not 1 <= duration_seconds <= MAX_CONNECTION_SECONDS:
        raise ValueError("connection duration is outside the bounded qualification limit")
    if not 1 <= connections <= MAX_CONNECTIONS:
        raise ValueError("connection count is outside the bounded qualification limit")

    output_root = output_root.expanduser().resolve()
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (output_root / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    run_directory = output_root / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    connection_results = asyncio.run(
        _collect_connections(
            url=url,
            output_directory=run_directory,
            product_id=product_id,
            duration_seconds=duration_seconds,
            connections=connections,
        )
    )
    type_counts: Counter[str] = Counter()
    for result in connection_results:
        type_counts.update(result["message_type_counts"])
    all_sequences = [
        result["sequence"]
        for result in connection_results
        if isinstance(result.get("sequence"), dict)
    ]
    sequence_state = (
        "observed_gap_or_reordering"
        if any(item.get("state") == "observed_gap_or_reordering" for item in all_sequences)
        else "insufficient_observations"
        if any(item.get("state") == "insufficient_observations" for item in all_sequences)
        else "pass"
    )
    passed = bool(connection_results) and all(
        bool(result["passed"]) for result in connection_results
    )
    measured_at = datetime.now(UTC)
    report: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": measured_at.isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "websocket_transport_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/integrations/websocket.py"
            ).read_bytes()
        ),
        "network_calls": len(connection_results),
        "venue_identity": "coinbase_exchange_sandbox",
        "venue_environment": "paper_testnet",
        "reviewed_ws_host": COINBASE_EXCHANGE_SANDBOX_WS_HOST,
        "ws_endpoint": url,
        "product_id": product_id,
        "subscription": {
            "type": "subscribe",
            "product_ids": [product_id],
            "channels": ["heartbeat", "ticker"],
        },
        "connection_window_seconds": duration_seconds,
        "connection_count_requested": connections,
        "connection_count_completed": len(connection_results),
        "message_type_counts": dict(sorted(type_counts.items())),
        "sequence_state": sequence_state,
        "connections": connection_results,
        "passed": passed,
        "gate_state": (
            "EXTERNALLY_MEASURED / QUALIFIED_FOR_WSS_SOURCE_SMOKE"
            if passed
            else "EXTERNALLY_MEASURED / PENDING_EXTERNAL_EVIDENCE"
        ),
        "admission_opened": False,
        "credential_references": [],
        "notes": (
            "Public unauthenticated market-data feed only; no order, account, or execution authority.",
            "Provider sequence gaps and out-of-order messages are recorded and fail this bounded probe.",
            "Freshness is measured from provider event time versus raw-spool receipt time; stale or malformed timestamps fail closed.",
            "A short probe is not a freshness soak or Phase-3 admission decision.",
        ),
    }
    report_path = run_directory / "phase3-coinbase-wss-qualification.json"
    _write_immutable_json(report_path, report)
    evidence_sha256 = _sha256(report_path.read_bytes())
    manifest = {
        "schema": f"{SCHEMA}.manifest",
        "run_id": run_id,
        "report": report_path.name,
        "evidence_sha256": evidence_sha256,
        "raw_spool_directories": [
            f"connection-{number:02d}" for number in range(1, len(connection_results) + 1)
        ],
    }
    _write_immutable_json(run_directory / "evidence-manifest.json", manifest)
    latest_path = output_root / "latest.json"
    temporary = output_root / ".latest.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema": f"{SCHEMA}.latest",
                    "run_id": run_id,
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
        default=Path("artifacts/phase3/coinbase-wss-qualification"),
    )
    parser.add_argument("--ws-url", default=COINBASE_EXCHANGE_SANDBOX_WS_URL)
    parser.add_argument("--product-id", default=DEFAULT_PRODUCT_ID)
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_CONNECTION_SECONDS)
    parser.add_argument("--connections", type=int, default=DEFAULT_CONNECTIONS)
    args = parser.parse_args()
    if not args.real:
        parser.error("network reads require explicit --real")
    path, report, evidence_sha256 = run_evidence(
        args.evidence_dir,
        ws_url=args.ws_url,
        product_id=args.product_id,
        duration_seconds=args.duration_seconds,
        connections=args.connections,
    )
    print(
        json.dumps(
            {
                "report": str(path),
                "evidence_sha256": evidence_sha256,
                "network_calls": report["network_calls"],
                "message_type_counts": report["message_type_counts"],
                "sequence_state": report["sequence_state"],
                "passed": report["passed"],
                "gate_state": report["gate_state"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
