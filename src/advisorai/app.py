"""Long-lived, supervised, paper-only AdvisorAI owner application.

Usage::

    python -m advisorai.app start --reason "owner session"
    python -m advisorai.app status
    python -m advisorai.app stop --outcome clean
    python -m advisorai.app backup
    python -m advisorai.app restore --source <backup> --destination <new-state-dir>
    python -m advisorai.app rollback --source <prior-backup> --destination <new-state-dir>

The start command holds the durable single-owner lock while a bounded paper
scheduler runs. ``stop`` requests graceful shutdown through a private local
control file; a process crash releases the OS lock and is classified on the
next start. No live venue adapter or credential path is created here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psutil

from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.backups import OwnerStateBackupStore
from advisorai.session.records import SessionOutcome
from advisorai.session.supervisor import SessionSupervisor


def _state_dir() -> Path:
    raw = os.getenv("ADVISORAI_STATE_DIR", "var/advisorai")
    return Path(raw).expanduser().resolve()


def _ledger_path(state_dir: Path) -> Path:
    raw = os.getenv("ADVISORAI_LEDGER_PATH")
    return Path(raw).expanduser().resolve() if raw else state_dir / "ledgers.sqlite3"


def _repo_identity() -> tuple[str, str]:
    """Measure working code/config identities for durable recovery records."""
    import subprocess

    root = Path(__file__).resolve().parents[2]

    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except Exception:
        head = "no-git-head"

    def tree_hash(relative_path: str) -> str:
        digest = hashlib.sha256()
        digest.update(f"head:{head};tree:{relative_path}".encode())
        try:
            tree = subprocess.run(
                ["git", "-C", str(root), "rev-parse", f"HEAD:{relative_path}"],
                capture_output=True,
                timeout=5,
                check=True,
            ).stdout
            diff = subprocess.run(
                ["git", "-C", str(root), "diff", "--binary", "HEAD", "--", relative_path],
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout
            untracked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                    "--",
                    relative_path,
                ],
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout
            digest.update(tree)
            digest.update(diff)
            for raw_path in untracked.split(b"\0"):
                if not raw_path:
                    continue
                path = root / os.fsdecode(raw_path)
                digest.update(raw_path)
                digest.update(path.read_bytes())
        except Exception:
            # When Git is unavailable, measure the entry module itself and
            # retain a non-placeholder identity rather than accepting zeros.
            digest.update(Path(__file__).read_bytes())
        return digest.hexdigest()

    return tree_hash("src/advisorai"), tree_hash("configs")


def cmd_start(args: argparse.Namespace) -> int:
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
    stop_event = __import__("threading").Event()
    try:
        from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication

        app = PaperOwnerApplication(
            ledgers=ledgers,
            state_dir=state_dir,
            lock_identity=args.lock_identity,
            code_hash=code_hash,
            config_hash=config_hash,
            supervisor=supervisor,
            config=OwnerApplicationConfig(
                scenario=args.scenario,
                interval_seconds=args.interval_seconds,
                accelerated_clock=args.accelerated_clock,
                max_rss_mb=args.max_rss_mb,
            ),
            stop_requested=stop_event,
        )
    except BaseException:
        supervisor._release_lock()
        raise
    previous_handlers: dict[int, object] = {}

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        return app.run(reason=args.reason)
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def _read_owner_record(state_dir: Path) -> dict[str, object] | None:
    try:
        payload = json.loads((state_dir / "owner.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        raise ValueError("owner process record is unreadable") from None
    if not isinstance(payload, dict):
        raise ValueError("owner process record has an invalid shape")
    return payload


def _owner_is_live(owner: dict[str, object]) -> bool:
    try:
        process = psutil.Process(int(owner["pid"]))
        expected_start = float(owner["process_create_time"])
        return abs(process.create_time() - expected_start) < 0.01
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def cmd_stop(args: argparse.Namespace) -> int:
    state_dir = _state_dir()
    try:
        owner = _read_owner_record(state_dir)
    except ValueError as exc:
        print(f"STOP REFUSED: {exc}")
        return 2
    if owner is None or not _owner_is_live(owner):
        print("no live supervised application owner")
        return 1
    if str(owner.get("lock_identity")) != args.lock_identity:
        print("STOP REFUSED: lock identity does not match the active owner")
        return 2
    try:
        session_id = UUID(str(owner["session_id"]))
        outcome = SessionOutcome(args.outcome)
    except (KeyError, ValueError):
        print("STOP REFUSED: active owner record is invalid")
        return 2
    request_path = state_dir / f"stop-request-{session_id}.json"
    request = {
        "session_id": str(session_id),
        "outcome": outcome.value,
        "requested_at": datetime.now(UTC).isoformat(),
        "requester_pid": os.getpid(),
    }
    try:
        descriptor = os.open(request_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(request, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    print(f"stop_requested={session_id}", flush=True)

    ledgers = SqliteLedgers(_ledger_path(state_dir))
    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        ended = False
        for event in ledgers.events(LedgerNamespace.MISSION):
            if event.event_type != "session_ended":
                continue
            payload = event.payload.get("end")
            if isinstance(payload, dict) and str(payload.get("session_id")) == str(session_id):
                ended = True
                actual = str(payload.get("outcome", "unclean"))
                print(f"outcome={actual}", flush=True)
                return 0 if actual == "clean" else 2
        if not _owner_is_live(owner) and not ended:
            print("owner exited before a verified session end record", flush=True)
            return 2
        time.sleep(0.1)
    print(f"STOP TIMEOUT: owner {session_id} has not written a session end", flush=True)
    return 2


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
    unmatched = [str(sid) for sid in history["starts"] if sid not in history["ends"]]
    print(f"starts={len(history['starts'])} ends={len(history['ends'])}")
    print(f"unmatched_starts={unmatched}")
    try:
        owner = _read_owner_record(state_dir)
    except ValueError:
        owner = None
        print("owner_record=unreadable")
    if owner is not None and _owner_is_live(owner):
        print(f"active_session={owner.get('session_id')} pid={owner.get('pid')}")
    else:
        print("active_session=none")
    latest = history["latest_end"]
    if latest is not None:
        print(f"latest_end={latest.session_id} outcome={latest.outcome.value}")
    print(f"at={datetime.now(UTC).isoformat()}")
    return 0


def cmd_backup(_args: argparse.Namespace) -> int:
    """Create a verified owner database backup while holding the app lock."""
    state_dir = _state_dir()
    ledger_path = _ledger_path(state_dir)
    scorecards_path = ledger_path.with_name("scorecards.sqlite3")
    if not ledger_path.is_file() or not scorecards_path.is_file():
        print("BACKUP REFUSED: both owner databases must already exist", flush=True)
        return 2
    ledgers = SqliteLedgers(ledger_path)
    code_hash, config_hash = _repo_identity()
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir,
        lock_identity="owner-state-backup",
        code_hash=code_hash,
        config_hash=config_hash,
    )
    try:
        supervisor._acquire_lock()
    except RuntimeError as exc:
        print(f"BACKUP REFUSED: {exc}", flush=True)
        return 2
    try:
        history = supervisor._session_history()
        latest_end = history["latest_end"]
        session_id = latest_end.session_id if latest_end is not None else None
        session_start = history["starts"].get(session_id) if session_id is not None else None
        backup = OwnerStateBackupStore(state_dir / "backups").create(
            ledgers_path=ledger_path,
            scorecards_path=scorecards_path,
            code_hash=session_start.code_hash if session_start is not None else code_hash,
            config_hash=session_start.config_hash if session_start is not None else config_hash,
            session_id=session_id,
        )
        print(f"backup_id={backup.manifest.backup_id}", flush=True)
        print(f"backup_path={backup.path}", flush=True)
        print("backup_verified=true", flush=True)
        return 0
    except Exception as exc:
        print(f"BACKUP FAILED: {type(exc).__name__}", flush=True)
        return 2
    finally:
        supervisor._release_lock()


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore verified databases into a new isolated state directory."""
    try:
        receipt = OwnerStateBackupStore.restore(Path(args.source), Path(args.destination))
    except Exception as exc:
        print(f"RESTORE REFUSED: {type(exc).__name__}", flush=True)
        return 2
    print(f"backup_id={receipt.backup_id}", flush=True)
    print(f"state_dir={Path(args.destination).expanduser().resolve()}", flush=True)
    print("restore_verified=true", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="advisorai.app", description=__doc__)
    parser.add_argument("--lock-identity", default="owner-workstation")
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="run a supervised paper owner session")
    p_start.add_argument("--reason", default="owner_start")
    p_start.add_argument(
        "--scenario",
        choices=("quiet", "qualification"),
        default="quiet",
        help="local paper data profile; qualification uses deterministic evidence and fills",
    )
    p_start.add_argument("--interval-seconds", type=float, default=3600.0)
    p_start.add_argument(
        "--accelerated-clock",
        action="store_true",
        help="advance local paper cutoffs by one hour each cycle for integration qualification",
    )
    p_start.add_argument("--max-rss-mb", type=int, default=2048)
    p_start.set_defaults(func=cmd_start)
    p_stop = sub.add_parser("stop", help="request a graceful stop of the active owner process")
    p_stop.add_argument("--outcome", default="clean", choices=["clean", "emergency", "unclean"])
    p_stop.add_argument("--timeout-seconds", type=float, default=30.0)
    p_stop.set_defaults(func=cmd_stop)
    p_status = sub.add_parser("status", help="show durable session and owner-process state")
    p_status.set_defaults(func=cmd_status)
    p_backup = sub.add_parser("backup", help="verify and back up stopped owner databases")
    p_backup.set_defaults(func=cmd_backup)
    p_restore = sub.add_parser(
        "restore", help="restore a verified backup into a new isolated state directory"
    )
    p_restore.add_argument("--source", required=True, help="backup directory created by backup")
    p_restore.add_argument("--destination", required=True, help="new state directory to create")
    p_restore.set_defaults(func=cmd_restore)
    p_rollback = sub.add_parser(
        "rollback", help="restore a prior backup to a new state root, preserving current state"
    )
    p_rollback.add_argument("--source", required=True, help="prior verified backup directory")
    p_rollback.add_argument("--destination", required=True, help="new rollback state directory")
    p_rollback.set_defaults(func=cmd_restore)
    args = parser.parse_args(argv)
    if args.command == "stop" and not 0.1 <= args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be between 0.1 and 600")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
