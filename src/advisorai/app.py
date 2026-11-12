"""AdvisorAI workstation application: supervised start, operate, stop, recover.

This is the real application entry point for the owner-operated session. It
owns the lifecycle that the dashboard alone cannot provide:

dashboard API + UI  -> inspection and guarded commands (NOT the trading system)
advisorai.app       -> supervised session: start -> verify recovery ->
                       reconcile venue/orders -> run paper cycles -> checkpoint
                       -> clean stop -> restart

Usage:
    python -m advisorai.app start --reason "owner session"
    python -m advisorai.app status
    python -m advisorai.app stop --outcome clean

Environment:
    ADVISORAI_STATE_DIR     session lock + state (default: <cwd>/var/advisorai)
    ADVISORAI_LEDGER_PATH   SQLite WAL ledgers (default: state/ledgers.sqlite3)
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from advisorai.ledger import SqliteLedgers
from advisorai.session import SessionOutcome, SessionSupervisor


def _state_dir() -> Path:
    import os

    raw = os.getenv("ADVISORAI_STATE_DIR", "var/advisorai")
    return Path(raw).expanduser().resolve()


def _ledger_path(state_dir: Path) -> Path:
    import os

    raw = os.getenv("ADVISORAI_LEDGER_PATH")
    return Path(raw).expanduser().resolve() if raw else state_dir / "ledgers.sqlite3"


def _repo_identity() -> tuple[str, str]:
    """Measured code/config identities; verified, never placeholder zeros.

    Code identity is the git HEAD commit hash plus a content hash of the
    live ``src/advisorai`` tree, so uncommitted working-tree changes alter
    the identity instead of silently reusing the committed hash. Config
    identity is the SHA-256 of the canonical active configuration bundle.
    """
    import subprocess

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except Exception:
        head = "no-git-head"
    tree_digest = hashlib.sha256()
    tree_digest.update(f"head:{head}".encode())
    root = Path(__file__).resolve().parent
    for path in sorted(root.rglob("*.py")):
        try:
            tree_digest.update(path.read_bytes())
        except OSError:
            continue
    code_hash = tree_digest.hexdigest()
    config_digest = hashlib.sha256()
    config_root = Path("configs")
    found = False
    if config_root.is_dir():
        for path in sorted(config_root.rglob("*")):
            if path.is_file() and path.suffix in {".yaml", ".yml", ".json"}:
                try:
                    config_digest.update(str(path.relative_to(config_root)).encode())
                    config_digest.update(path.read_bytes())
                    found = True
                except OSError:
                    continue
    config_hash = (
        config_digest.hexdigest() if found else hashlib.sha256(b"no-config-tree").hexdigest()
    )
    return code_hash, config_hash


def cmd_start(args: argparse.Namespace) -> int:
    state_dir = _state_dir()
    ledgers = SqliteLedgers(_ledger_path(state_dir))
    code_hash, config_hash = _repo_identity()
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir,
        lock_identity=args.lock_identity,
        code_hash=code_hash,
        config_hash=config_hash,
    )
    start, recovery = supervisor.start(reason=args.reason)
    print(f"session_id={start.session_id}")
    print(f"classification={start.recovery_classification}")
    print(f"recovery_mode={recovery.recovery_mode.value}")
    for decision in recovery.decisions:
        print(f"decision={decision}")
    if recovery.recovery_mode.value == "abstain":
        print("RECOVERY INCOMPLETE: resolve failed checks before operating.")
        return 2
    if recovery.recovery_mode.value == "recovery":
        print("RECOVERY MODE: reconcile venue/orders before new proposals.")
        return 1
    checkpoint = supervisor.checkpoint(pending_work=("session_active",))
    print(f"checkpoint={checkpoint.checkpoint_hash[:16]}...")
    print("SESSION READY. Stop with: python -m advisorai.app stop")
    # The start command records readiness and leaves the session supervised
    # by the lock file; long-running operation continues under the runtime.
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    state_dir = _state_dir()
    ledgers = SqliteLedgers(_ledger_path(state_dir))
    code_hash, config_hash = _repo_identity()
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir,
        lock_identity=args.lock_identity,
        code_hash=code_hash,
        config_hash=config_hash,
    )
    # Reattach: find the session with an unmatched start.
    history = supervisor._session_history()
    unmatched = [sid for sid in history["starts"] if sid not in history["ends"]]
    if not unmatched:
        print("no active session to stop")
        return 1
    supervisor.session_id = unmatched[-1]
    supervisor._acquire_lock()
    try:
        end = supervisor.stop(
            outcome=SessionOutcome(args.outcome),
            open_orders=tuple(UUID(item) for item in args.open_order),
            unfinished_work=tuple(args.unfinished),
        )
    finally:
        supervisor.session_id = None
        supervisor._release_lock()
    print(f"session_id={end.session_id}")
    print(f"outcome={end.outcome.value}")
    print(f"reconciliation={end.reconciliation_state}")
    print(f"leases_released={end.leases_released}")
    return 0 if end.outcome is SessionOutcome.CLEAN else 2


def cmd_status(_args: argparse.Namespace) -> int:
    state_dir = _state_dir()
    ledgers = SqliteLedgers(_ledger_path(state_dir))
    code_hash, config_hash = _repo_identity()
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir,
        lock_identity="status-probe",
        code_hash=code_hash,
        config_hash=config_hash,
    )
    history = supervisor._session_history()
    print(f"starts={len(history['starts'])} ends={len(history['ends'])}")
    unmatched = [str(sid) for sid in history["starts"] if sid not in history["ends"]]
    print(f"unmatched_starts={unmatched}")
    latest = history["latest_end"]
    if latest is not None:
        print(f"latest_end={latest.session_id} outcome={latest.outcome.value}")
    print(f"at={datetime.now(UTC).isoformat()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="advisorai.app", description=__doc__)
    parser.add_argument("--lock-identity", default="owner-workstation")
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="start a supervised owner session")
    p_start.add_argument("--reason", default="owner_start")
    p_start.set_defaults(func=cmd_start)
    p_stop = sub.add_parser("stop", help="stop the active supervised session")
    p_stop.add_argument("--outcome", default="clean", choices=["clean", "emergency", "unclean"])
    p_stop.add_argument("--open-order", action="append", default=[])
    p_stop.add_argument("--unfinished", action="append", default=[])
    p_stop.set_defaults(func=cmd_stop)
    p_status = sub.add_parser("status", help="show session history without mutating")
    p_status.set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
