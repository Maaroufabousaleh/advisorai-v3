#!/usr/bin/env python3
"""Classify Binance Spot Testnet WSS failures without credentials or writes.

This is deliberately separate from the depth qualifier.  It probes the
transport layers in order and records only sanitized classes, status codes,
timings, and public-message digests.  It never sends an order, credentials, or
an intentionally malformed subscription to Binance.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import json
import os
import socket
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

SCHEMA = "advisorai.phase3.binance-wss-diagnostic.v1"
HOST = "stream.testnet.binance.vision"
PORT = 443
DIRECT_URL = f"wss://{HOST}/ws/{{symbol}}@depth@100ms"
SUBSCRIPTION_URL = f"wss://{HOST}/ws"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_ATTEMPTS = 2
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class _Address:
    family: int
    socktype: int
    protocol: int
    sockaddr: object


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


def _write_latest_pointer(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _new_run_directory(base_directory: Path) -> tuple[Path, str]:
    base_directory.mkdir(parents=True, exist_ok=True)
    run_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_base
    suffix = 1
    while (base_directory / run_id).exists():
        suffix += 1
        run_id = f"{run_base}-{suffix}"
    run_directory = base_directory / run_id
    run_directory.mkdir()
    return run_directory, run_id


def _error_record(exc: BaseException, *, failure_layer: str) -> dict[str, object]:
    record: dict[str, object] = {
        "status": "failed",
        "failure_layer": failure_layer,
        "error_class": type(exc).__name__,
    }
    errno = getattr(exc, "errno", None)
    if isinstance(errno, int):
        record["errno"] = errno
    return record


def _address_family_name(family: int) -> str:
    try:
        return socket.AddressFamily(family).name
    except ValueError:
        return f"AF_{family}"


async def _resolve(timeout_seconds: float) -> tuple[dict[str, object], tuple[_Address, ...]]:
    started = time.perf_counter()
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                HOST,
                PORT,
                type=socket.SOCK_STREAM,
            ),
            timeout=timeout_seconds,
        )
    except OSError as exc:
        record = _error_record(exc, failure_layer="dns_resolution")
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return record, ()

    addresses: list[_Address] = []
    seen: set[tuple[int, object]] = set()
    for family, socktype, protocol, _canonname, sockaddr in infos:
        key = (family, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        addresses.append(_Address(family, socktype, protocol, sockaddr))
    record = {
        "status": "pass" if addresses else "failed",
        "failure_layer": None if addresses else "dns_resolution",
        "resolved_address_count": len(addresses),
        "address_families": sorted({_address_family_name(item.family) for item in addresses}),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    return record, tuple(addresses)


def _open_tcp(address: _Address, timeout_seconds: float) -> socket.socket:
    sock = socket.socket(address.family, address.socktype, address.protocol)
    sock.settimeout(timeout_seconds)
    try:
        sock.connect(address.sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


def _tcp_probe(addresses: tuple[_Address, ...], timeout_seconds: float) -> dict[str, object]:
    started = time.perf_counter()
    attempts = 0
    successful = 0
    last_error: BaseException | None = None
    for address in addresses:
        attempts += 1
        try:
            sock = _open_tcp(address, timeout_seconds)
        except (OSError, TimeoutError) as exc:
            last_error = exc
            continue
        sock.close()
        successful += 1
    if successful:
        return {
            "status": "pass",
            "failure_layer": None,
            "address_attempt_count": attempts,
            "successful_address_count": successful,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    record = _error_record(
        last_error or OSError("no resolved address"),
        failure_layer="tcp_connectivity",
    )
    record.update(
        {
            "address_attempt_count": attempts,
            "successful_address_count": successful,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    )
    return record


def _tls_probe(addresses: tuple[_Address, ...], timeout_seconds: float) -> dict[str, object]:
    started = time.perf_counter()
    context = ssl.create_default_context()
    attempts = 0
    last_error: BaseException | None = None
    for address in addresses:
        attempts += 1
        raw: socket.socket | None = None
        try:
            raw = _open_tcp(address, timeout_seconds)
            with context.wrap_socket(raw, server_hostname=HOST) as secure:
                cipher = secure.cipher()
                return {
                    "status": "pass",
                    "failure_layer": None,
                    "address_attempt_count": attempts,
                    "tls_version": secure.version(),
                    "cipher_name": cipher[0] if cipher else None,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                }
        except (OSError, ssl.SSLError, TimeoutError) as exc:
            last_error = exc
            if raw is not None:
                with contextlib.suppress(OSError):
                    raw.close()
    record = _error_record(
        last_error or OSError("no resolved address"),
        failure_layer="tls_negotiation",
    )
    record.update(
        {
            "address_attempt_count": attempts,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    )
    return record


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if value is None:
        value = getattr(exc, "status_code", None)
    return value if isinstance(value, int) else None


def _close_code_class(code: int | None) -> str | None:
    if code is None:
        return None
    if code in {1000, 1001}:
        return "normal_or_endpoint_shutdown"
    if code in {1008, 1011, 4003, 4008, 4009, 4010}:
        return "policy_or_provider_limit_candidate"
    return "provider_close_code_observed"


def _message_metadata(message: object) -> dict[str, object]:
    raw = message.encode() if isinstance(message, str) else bytes(message)
    metadata: dict[str, object] = {
        "message_length": len(raw),
        "message_sha256": _sha256(raw),
    }
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        metadata["message_kind"] = "non_json"
        return metadata
    if not isinstance(payload, Mapping):
        metadata["message_kind"] = "json_non_object"
    elif "code" in payload and isinstance(payload.get("code"), int):
        metadata["message_kind"] = "provider_error"
        metadata["provider_error_code"] = payload["code"]
        metadata["throttling_candidate"] = payload["code"] in {-1003, -1008, 418, 429}
    elif "result" in payload and "id" in payload:
        metadata["message_kind"] = "subscription_ack"
    elif payload.get("e") is not None:
        metadata["message_kind"] = "market_event"
    else:
        metadata["message_kind"] = "json_object"
    return metadata


async def _websocket_probe(
    *,
    url: str,
    symbol: str,
    mode: str,
    timeout_seconds: float,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
    except ImportError as exc:
        record = _error_record(exc, failure_layer="local_runtime_library")
        record.update(
            {
                "mode": mode,
                "url": url,
                "handshake_established": False,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return record

    subscription_sent = False
    try:
        async with connect(
            url,
            open_timeout=timeout_seconds,
            close_timeout=timeout_seconds,
            ping_interval=None,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            if mode == "valid_subscription":
                payload = {
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol.lower()}@depth@100ms"],
                    "id": 1,
                }
                await websocket.send(json.dumps(payload, sort_keys=True, separators=(",", ":")))
                subscription_sent = True
            try:
                first_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
            except TimeoutError as exc:
                return {
                    "status": "failed",
                    "failure_layer": "first_message_timeout",
                    "error_class": type(exc).__name__,
                    "mode": mode,
                    "url": url,
                    "handshake_established": True,
                    "subscription_sent": subscription_sent,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            except ConnectionClosed as exc:
                code = getattr(exc, "code", None)
                return {
                    "status": "failed",
                    "failure_layer": "provider_close_frame",
                    "error_class": type(exc).__name__,
                    "provider_close_code": code if isinstance(code, int) else None,
                    "provider_close_code_class": _close_code_class(
                        code if isinstance(code, int) else None
                    ),
                    "mode": mode,
                    "url": url,
                    "handshake_established": True,
                    "subscription_sent": subscription_sent,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            metadata = _message_metadata(first_message)
            return {
                "status": "pass",
                "failure_layer": None,
                "mode": mode,
                "url": url,
                "handshake_established": True,
                "subscription_sent": subscription_sent,
                "throttling_candidate": metadata.get("throttling_candidate", False),
                "first_message": metadata,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
    except InvalidStatus as exc:
        status = _http_status(exc)
        return {
            "status": "failed",
            "failure_layer": "websocket_handshake",
            "error_class": type(exc).__name__,
            "http_status": status,
            "throttling_candidate": status in {418, 429},
            "mode": mode,
            "url": url,
            "handshake_established": False,
            "subscription_sent": subscription_sent,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except InvalidHandshake as exc:
        return {
            "status": "failed",
            "failure_layer": "websocket_handshake",
            "error_class": type(exc).__name__,
            "mode": mode,
            "url": url,
            "handshake_established": False,
            "subscription_sent": subscription_sent,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except TimeoutError as exc:
        return {
            "status": "failed",
            "failure_layer": "connection_timeout",
            "error_class": type(exc).__name__,
            "mode": mode,
            "url": url,
            "handshake_established": False,
            "subscription_sent": subscription_sent,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except OSError as exc:
        record = _error_record(exc, failure_layer="websocket_connect_runtime")
        record.update(
            {
                "mode": mode,
                "url": url,
                "handshake_established": False,
                "subscription_sent": subscription_sent,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return record
    except Exception as exc:  # diagnostic boundary records class only
        record = _error_record(exc, failure_layer="local_runtime_library")
        record.update(
            {
                "mode": mode,
                "url": url,
                "handshake_established": False,
                "subscription_sent": subscription_sent,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return record


async def _probe_websockets(timeout_seconds: float, attempts: int) -> dict[str, object]:
    direct: list[dict[str, object]] = []
    subscriptions: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        for attempt in range(1, attempts + 1):
            result = await _websocket_probe(
                url=DIRECT_URL.format(symbol=symbol.lower()),
                symbol=symbol,
                mode="direct_stream",
                timeout_seconds=timeout_seconds,
            )
            result["symbol"] = symbol
            result["attempt"] = attempt
            direct.append(result)
        result = await _websocket_probe(
            url=SUBSCRIPTION_URL,
            symbol=symbol,
            mode="valid_subscription",
            timeout_seconds=timeout_seconds,
        )
        result["symbol"] = symbol
        result["attempt"] = 1
        subscriptions.append(result)
    reconnect: dict[str, dict[str, object]] = {}
    for symbol in SYMBOLS:
        samples = [item for item in direct if item["symbol"] == symbol]
        successful = sum(item.get("status") == "pass" for item in samples)
        reconnect[symbol] = {
            "attempt_count": len(samples),
            "handshake_success_count": sum(
                item.get("handshake_established") is True for item in samples
            ),
            "first_message_success_count": successful,
            "status": "pass" if successful == len(samples) and len(samples) >= 2 else "unproven",
            "interpretation": (
                "two sequential direct connections received a first message"
                if successful == len(samples) and len(samples) >= 2
                else "reconnect cannot be admitted from this bounded sample"
            ),
        }
    return {
        "direct_streams": direct,
        "valid_subscriptions": subscriptions,
        "reconnect": reconnect,
        "malformed_subscription": {
            "status": "not_attempted",
            "evidence_type": "safety_boundary",
            "reason": "the diagnostic sends only a provider-valid subscription; it never induces a malformed command",
        },
    }


async def _run_probes(timeout_seconds: float, attempts: int) -> dict[str, object]:
    dns, addresses = await _resolve(timeout_seconds)
    if addresses:
        tcp = await asyncio.to_thread(_tcp_probe, addresses, timeout_seconds)
        tls = await asyncio.to_thread(_tls_probe, addresses, timeout_seconds)
    else:
        tcp = {"status": "not_attempted", "failure_layer": "dns_resolution"}
        tls = {"status": "not_attempted", "failure_layer": "dns_resolution"}
    websocket = await _probe_websockets(timeout_seconds, attempts)
    return {
        "dns_resolution": dns,
        "tcp_connectivity": tcp,
        "tls_negotiation": tls,
        "websocket": websocket,
    }


def _websockets_version() -> str | None:
    try:
        return importlib.metadata.version("websockets")
    except importlib.metadata.PackageNotFoundError:
        return None


def _overall_classification(probes: Mapping[str, object]) -> str:
    websocket = probes.get("websocket")
    if isinstance(websocket, Mapping):
        results = list(websocket.get("direct_streams", ()))
        failures = [
            item for item in results if isinstance(item, Mapping) and item.get("status") != "pass"
        ]
        layers = {item.get("failure_layer") for item in failures if isinstance(item, Mapping)}
        if "local_runtime_library" in layers:
            return "local_runtime_library_missing_or_failed"
        if "websocket_handshake" in layers:
            return "websocket_handshake_failed"
        if "first_message_timeout" in layers:
            return "first_message_timeout"
        if "provider_close_frame" in layers:
            return "provider_close_frame"
        if "connection_timeout" in layers:
            return "websocket_connection_timeout"
        if "websocket_connect_runtime" in layers:
            return "websocket_connect_runtime_failure"
    for key, label in (
        ("dns_resolution", "dns_resolution_failed"),
        ("tcp_connectivity", "tcp_connectivity_failed"),
        ("tls_negotiation", "tls_negotiation_failed"),
    ):
        record = probes.get(key)
        if isinstance(record, Mapping) and record.get("status") == "failed":
            return label
    return "no_failure_observed_in_bounded_diagnostic"


def run_evidence(
    evidence_directory: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
) -> dict[str, object]:
    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds is outside the bounded diagnostic limit")
    if attempts <= 0 or attempts > MAX_ATTEMPTS:
        raise ValueError("attempts is outside the bounded diagnostic limit")
    run_directory, run_id = _new_run_directory(evidence_directory)
    probes = asyncio.run(_run_probes(timeout_seconds, attempts))
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "websocket_transport_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/integrations/websocket.py"
            ).read_bytes()
        ),
        "websockets_version": _websockets_version(),
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "host": HOST,
        "port": PORT,
        "rest_or_execution_calls": 0,
        "order_writes_attempted": False,
        "credentials_loaded": False,
        "public_market_data_only": True,
        "probes": probes,
        "overall_classification": _overall_classification(probes),
    }
    manifest = run_directory / "phase3-binance-wss-diagnostic.json"
    _write_immutable_json(manifest, payload)
    digest = _sha256(manifest.read_bytes())
    _write_immutable_json(
        run_directory / "evidence-manifest.json",
        {
            "schema": f"{SCHEMA}.manifest",
            "run_id": run_id,
            "evidence_path": str(manifest),
            "evidence_sha256": digest,
        },
    )
    _write_latest_pointer(
        evidence_directory / "latest.json",
        {
            "schema": f"{SCHEMA}.latest",
            "run_id": run_id,
            "evidence_sha256": digest,
            "overall_classification": payload["overall_classification"],
        },
    )
    return {
        "status": "diagnostic_complete",
        "evidence": str(manifest),
        "evidence_sha256": digest,
        "overall_classification": payload["overall_classification"],
        "websockets_version": payload["websockets_version"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase3/binance-wss-diagnostic"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument(
        "--real",
        action="store_true",
        help="allow bounded public Binance Spot Testnet DNS/TCP/TLS/WSS reads",
    )
    args = parser.parse_args()
    if not args.real:
        parser.error("public network reads require explicit --real")
    result = run_evidence(
        args.evidence_dir,
        timeout_seconds=args.timeout_seconds,
        attempts=args.attempts,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
