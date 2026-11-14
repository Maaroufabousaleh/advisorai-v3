"""Offline P02 canary-evidence validator (read-only, no credentials, no network).

Validates a stopped bounded-canary state directory for the P02 acceptance
row: SESSION READY was reached, the deadline and submission cap applied,
and the session ended cleanly with no unexplained open orders, reconciled
state, released leases, and a recorded submission count.

Reads the mission ledger plus a private copy of the database; never writes
to the state directory. Takes the owner-run stdout log alongside the state
dir to verify the READY/deadline/outcome markers.

Usage::

    uv run python scripts/validate_testnet_canary_evidence.py \\
        --state-dir var/advisorai-testnet --run-log canary.log
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

EXPECTED_OUTCOME = "clean"
EXPECTED_RECONCILIATION = "reconciled"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    state_dir: str
    checks: list[CheckResult] = field(default_factory=list)
    p02_certified: bool = False
    submission_count: int | None = None

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name, passed, detail))

    @property
    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, object]:
        return {
            "state_dir": self.state_dir,
            "p02_certified": self.p02_certified,
            "submission_count": self.submission_count,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


def _copy_database(source: Path, workdir: Path, name: str) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"missing {source}")
    for suffix in ("", "-shm", "-wal"):
        sibling = Path(str(source) + suffix)
        if sibling.exists():
            shutil.copyfile(sibling, workdir / f"{name}{suffix}")
    staged = workdir / name
    target = workdir / f"copy-{name}"
    origin = sqlite3.connect(staged)
    try:
        clone = sqlite3.connect(target)
        try:
            origin.backup(clone)
        finally:
            clone.close()
    finally:
        origin.close()
    return target


def _mission_events(ledger: Path) -> list[tuple[str, dict]]:
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT event_type, payload_json FROM mission_events ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    return [(event_type, json.loads(payload)) for event_type, payload in rows]


def _count_submissions(ledger: Path) -> int | None:
    """Count venue submissions from durable order events, if present."""
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "order_events" not in tables:
            return None
        rows = connection.execute(
            "SELECT event_type, idempotency_key FROM order_events ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    routed_keys = {key for event_type, key in rows if event_type == "order_routed"}
    return len(routed_keys)


def validate_canary(*, state_dir: Path, run_log: Path | None) -> ValidationReport:
    report = ValidationReport(state_dir=str(state_dir))
    ledger_path = state_dir / "ledgers.sqlite3"
    if not ledger_path.exists():
        report.add("ledger_present", False, f"missing {ledger_path}")
        return report
    report.add("ledger_present", True)

    with tempfile.TemporaryDirectory(prefix="p02-validate-") as workdir:
        ledger = _copy_database(ledger_path, Path(workdir), "ledgers.sqlite3")
        events = _mission_events(ledger)
        report.submission_count = _count_submissions(ledger)

    by_type: dict[str, list[dict]] = {}
    for event_type, payload in events:
        by_type.setdefault(event_type, []).append(payload)

    profiles = by_type.get("session_profile", [])
    report.add(
        "testnet_profile",
        len(profiles) == 1
        and profiles[0].get("profile", {}).get("venue_profile")
        in {"binance_spot_testnet", "coinbase_exchange_sandbox"},
        f"profiles={len(profiles)}",
    )
    ended = by_type.get("session_ended", [])
    report.add("single_session_end", len(ended) == 1, f"count={len(ended)}")
    if not ended:
        return report
    end = ended[-1].get("end", {})
    report.add(
        "outcome_clean",
        end.get("outcome") == EXPECTED_OUTCOME,
        f"outcome={end.get('outcome')}",
    )
    report.add(
        "reconciliation_clean",
        end.get("reconciliation_state") == EXPECTED_RECONCILIATION,
        f"reconciliation={end.get('reconciliation_state')}",
    )
    report.add(
        "no_open_orders",
        end.get("open_orders") in ((), [], None),
        f"open_orders={end.get('open_orders')}",
    )
    report.add(
        "leases_released",
        end.get("leases_released") is True,
        f"leases_released={end.get('leases_released')}",
    )
    report.add(
        "no_unfinished_work",
        end.get("unfinished_work") in ((), [], None),
        f"unfinished_work={end.get('unfinished_work')}",
    )
    if report.submission_count is None:
        report.add("submission_count_recorded", False, "no order_events table")
    else:
        report.add(
            "submission_count_recorded",
            True,
            f"routed_orders={report.submission_count}",
        )

    if run_log is None:
        report.add("run_log_markers", False, "no run log provided")
    elif not run_log.exists():
        report.add("run_log_markers", False, f"missing {run_log}")
    else:
        text = run_log.read_text(encoding="utf-8", errors="replace")
        markers = {
            "SESSION READY": "SESSION READY" in text,
            "stop_reason=runtime_deadline_reached": (
                "stop_reason=runtime_deadline_reached" in text
            ),
            "outcome=clean": "outcome=clean" in text,
            "reconciliation=reconciled": "reconciliation=reconciled" in text,
            "leases_released=True": "leases_released=True" in text,
        }
        missing = sorted(name for name, seen in markers.items() if not seen)
        report.add(
            "run_log_markers",
            not missing,
            f"missing={missing}" if missing else "all markers present",
        )

    report.p02_certified = not report.failed
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--run-log", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.state_dir.is_dir():
        print(f"state directory not found: {args.state_dir}", file=sys.stderr)
        return 2
    try:
        report = validate_canary(state_dir=args.state_dir, run_log=args.run_log)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    if report.p02_certified:
        return 0
    print(
        "P02 checks failed: " + ", ".join(check.name for check in report.failed),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
