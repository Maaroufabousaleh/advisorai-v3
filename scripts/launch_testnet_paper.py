"""Supervised testnet launch entry point (owner shell only).

Reads the operator inventory through the single ``PAPER_VENUE`` scope,
validates the reviewed sandbox endpoint, snapshots the read-only account
baseline, and starts the bounded paper owner under the same single-owner
lock and clean-stop controls as ``advisorai.app``.

Bounds are enforced, not advisory: ``--max-runtime-seconds`` is a
wall-clock deadline checked in the owner stop loop, and
``--max-submissions`` is a venue-boundary cap that refuses submissions
beyond the cap. Clean stop runs the standard reconciliation stop checks.

Owner-run only. The coding agent never invokes this with real secrets.
Usage (from the owner shell, after reviewing the zero-network check)::

    ADVISORAI_STATE_DIR=var/advisorai-testnet \\
        uv run python scripts/launch_testnet_paper.py \\
        --provider binance_spot_testnet --instrument BTCUSDT --quote USDT \\
        --reason "owner testnet canary" --interval-seconds 10 \\
        --max-runtime-seconds 120 --max-submissions 1

Stop from a second owner shell::

    ADVISORAI_STATE_DIR=var/advisorai-testnet \\
        uv run python -m advisorai.app stop --outcome clean
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

from advisorai.config import CredentialResolver
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig
from advisorai.session.supervisor import SessionSupervisor
from advisorai.session.testnet_launch import (
    TestnetLaunchRequest,
    build_testnet_owner_application,
    read_testnet_baseline,
)


def _state_dir() -> Path:
    return Path(os.getenv("ADVISORAI_STATE_DIR", "var/advisorai-testnet")).expanduser().resolve()


def _ledger_path(state_dir: Path) -> Path:
    raw = os.getenv("ADVISORAI_LEDGER_PATH")
    return Path(raw).expanduser().resolve() if raw else state_dir / "ledgers.sqlite3"


def _repo_identity() -> tuple[str, str]:
    import subprocess

    def tree_hash(root: str) -> str:
        from hashlib import sha256

        digest = sha256()
        entries: list[tuple[str, bytes]] = []
        for path in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            entries.append((path.as_posix(), path.read_bytes()))
        for name, content in entries:
            digest.update(f"{name}\0{len(content)}\0".encode())
            digest.update(content)
        return digest.hexdigest()

    code = tree_hash("src/advisorai")
    config = tree_hash("configs")
    _ = subprocess  # keep import local to this helper
    return code, config


def _positive_decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{label} must be a decimal") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{label} must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--provider",
        default="binance_spot_testnet",
        choices=("binance_spot_testnet", "coinbase_exchange_sandbox"),
    )
    parser.add_argument("--instrument", default="BTCUSDT")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument(
        "--initial-mark",
        type=lambda value: _positive_decimal(value, "--initial-mark"),
        default=Decimal("50000"),
    )
    parser.add_argument("--reason", default="owner testnet canary")
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--max-runtime-seconds", type=float, default=120.0)
    parser.add_argument("--max-submissions", type=int, default=1)
    parser.add_argument("--max-rss-mb", type=int, default=1024)
    parser.add_argument("--lock-identity", default="owner-workstation")
    args = parser.parse_args(argv)

    if args.provider == "binance_spot_testnet" and (
        args.instrument.strip().upper() != "BTCUSDT" or args.quote.strip().upper() != "USDT"
    ):
        parser.error("binance_spot_testnet launch admits exactly BTCUSDT/USDT in this path")
    if args.provider == "coinbase_exchange_sandbox" and (
        args.instrument.strip().upper() != "BTC-USD" or args.quote.strip().upper() != "USD"
    ):
        parser.error("coinbase_exchange_sandbox launch admits exactly BTC-USD/USD in this path")
    if not 1 <= args.max_runtime_seconds <= 86_400:
        parser.error("--max-runtime-seconds must be between 1 and 86400")
    if not 0 <= args.max_submissions <= 100:
        parser.error("--max-submissions must be between 0 and 100")

    resolver = CredentialResolver.from_env_file(args.secrets)
    launch = TestnetLaunchRequest(
        provider=args.provider,
        provider_instrument_id=args.instrument,
        quote_currency=args.quote,
        initial_mark=args.initial_mark,
        max_submissions=args.max_submissions,
    )
    transport, baseline = read_testnet_baseline(resolver=resolver, launch=launch)
    print(
        f"testnet_baseline cash={baseline.cash} positions={dict(baseline.positions)} "
        f"as_of={baseline.as_of.isoformat()}",
        flush=True,
    )
    del transport

    state_dir = _state_dir()
    ledgers = SqliteLedgers(_ledger_path(state_dir))
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir,
        lock_identity=args.lock_identity,
        code_hash="pending-code-identity",
        config_hash="pending-config-identity",
    )
    try:
        supervisor._acquire_lock()
    except RuntimeError as exc:
        print(f"SESSION START REFUSED: {exc}", flush=True)
        return 2
    try:
        code_hash, config_hash = _repo_identity()
        supervisor.code_hash = code_hash
        supervisor.config_hash = config_hash
    except BaseException:
        supervisor._release_lock()
        raise
    try:
        from advisorai.app import _read_owner_record  # noqa: E402
    except ImportError:  # pragma: no cover - import seam for tests
        _read_owner_record = None  # type: ignore[assignment]
    del _read_owner_record
    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    previous: dict[int, object] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        app = build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=state_dir,
            lock_identity=args.lock_identity,
            code_hash=code_hash,
            config_hash=config_hash,
            config=OwnerApplicationConfig(
                scenario="qualification",
                interval_seconds=args.interval_seconds,
                accelerated_clock=True,
                max_rss_mb=args.max_rss_mb,
                max_runtime_seconds=args.max_runtime_seconds,
            ),
            resolver=resolver,
            launch=launch,
            baseline=baseline,
        )
        app.stop_requested = stop_event
        print(
            f"testnet_bounds max_runtime_seconds={args.max_runtime_seconds} "
            f"max_submissions={args.max_submissions}",
            flush=True,
        )
        return app.run(reason=args.reason)
    finally:
        for signum, prev in previous.items():
            signal.signal(signum, prev)  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(main())
