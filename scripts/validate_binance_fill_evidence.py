"""Offline P03 fill-evidence validator (read-only, no credentials, no network).

Validates a paper-lifecycle run directory for the P03 acceptance row:
a post-fix Binance Testnet fill matching venue cash, positions, fees
and order state, with restart that does not duplicate the order.

Reads the manifest plus a private copy of oms.sqlite; never writes to
the evidence directory. A cancellation-only pass is reported as such
and can never certify P03.

Usage::

    uv run python scripts/validate_binance_fill_evidence.py \\
        --evidence-dir artifacts/phase2/binance-spot-testnet/paper-lifecycle/<run_id>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

LIFECYCLE_SCHEMA = "advisorai.phase2.binance-spot-testnet.paper-lifecycle.v1"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    run_id: str
    manifest_sha256: str
    checks: list[CheckResult] = field(default_factory=list)
    fill_pass: bool = False
    p03_certified: bool = False

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name, passed, detail))

    @property
    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "manifest_sha256": self.manifest_sha256,
            "fill_pass": self.fill_pass,
            "p03_certified": self.p03_certified,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


def _load_manifest(run_dir: Path) -> tuple[dict, str, Path]:
    manifest = run_dir / "binance-spot-testnet-paper-lifecycle.json"
    encoded = manifest.read_bytes()
    return json.loads(encoded), sha256(encoded).hexdigest(), manifest


def _copy_ledger(run_dir: Path, workdir: Path) -> Path:
    source = run_dir / "oms.sqlite"
    if not source.exists():
        raise FileNotFoundError(f"missing oms.sqlite in {run_dir}")
    # Copy the main file plus any WAL sidecars into the workdir first, so
    # opening the database never creates sidecars inside the evidence dir.
    for suffix in ("", "-shm", "-wal"):
        sibling = Path(str(source) + suffix)
        if sibling.exists():
            shutil.copyfile(sibling, workdir / f"oms.sqlite{suffix}")
    staged = workdir / "oms.sqlite"
    target = workdir / "oms-copy.sqlite"
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


def _order_events(ledger: Path) -> list[tuple[str, dict]]:
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT event_type, payload_json FROM order_events ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    return [(event_type, json.loads(payload)) for event_type, payload in rows]


def _account_events(ledger: Path, event_type: str) -> list[dict]:
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT payload_json FROM account_events WHERE event_type = ? ORDER BY rowid",
            (event_type,),
        ).fetchall()
    finally:
        connection.close()
    return [json.loads(payload) for (payload,) in rows]


def validate_run(run_dir: Path) -> ValidationReport:
    record, manifest_sha256, _ = _load_manifest(run_dir)
    report = ValidationReport(
        run_id=str(record.get("run_id", run_dir.name)),
        manifest_sha256=manifest_sha256,
    )
    report.add(
        "manifest_schema",
        record.get("schema") == LIFECYCLE_SCHEMA,
        f"schema={record.get('schema')}",
    )
    result = record.get("result")
    if not isinstance(result, dict):
        report.add("manifest_result_mapping", False, "result is not an object")
        return report
    report.add("manifest_result_mapping", True)
    report.add(
        "lifecycle_passed",
        result.get("status") == "passed",
        f"status={result.get('status')} reason={result.get('reason')}",
    )
    report.add(
        "single_submission",
        result.get("signed_submission_count") == 1,
        f"signed_submission_count={result.get('signed_submission_count')}",
    )
    fill_ingestion = result.get("fill_ingestion")
    ledger_fill_count: int | None = None
    if isinstance(fill_ingestion, dict):
        report.add("fill_ingestion_present", True)
        real_fills = fill_ingestion.get("real_fill_count", 0)
        oms_fills = fill_ingestion.get("oms_fill_count", 0)
        report.fill_pass = (
            fill_ingestion.get("status") != "no_fill_observed"
            and isinstance(real_fills, int)
            and real_fills >= 1
        )
        report.add(
            "fill_observed",
            report.fill_pass,
            f"source=manifest status={fill_ingestion.get('status')} "
            f"real={real_fills} oms={oms_fills}",
        )
        report.add(
            "fill_counts_agree",
            real_fills == oms_fills,
            f"real={real_fills} oms={oms_fills}",
        )
    else:
        # Runs that raised before writing the full payload (e.g. the
        # pre-fix fill run) have no ingestion block. Classify from the
        # ledger instead of misreporting them as cancellation-only.
        report.add(
            "fill_ingestion_present",
            False,
            "missing fill_ingestion block; classifying from ledger",
        )
        with tempfile.TemporaryDirectory(prefix="p03-classify-") as workdir:
            ledger = _copy_ledger(run_dir, Path(workdir))
            ledger_fill_count = len(
                [event for event, _ in _order_events(ledger) if event == "fill_recorded"]
            )
        oms_fills = ledger_fill_count
        real_fills = ledger_fill_count
        report.fill_pass = ledger_fill_count >= 1
        report.add(
            "fill_observed",
            report.fill_pass,
            f"source=ledger fill_recorded={ledger_fill_count}",
        )
    if not report.fill_pass:
        report.add(
            "p03_requires_fill",
            False,
            "cancellation-only pass cannot certify P03",
        )
        return report

    with tempfile.TemporaryDirectory(prefix="p03-validate-") as workdir:
        ledger = _copy_ledger(run_dir, Path(workdir))
        events = _order_events(ledger)
        applied_events = _account_events(ledger, "fill_applied")
    by_type: dict[str, list[dict]] = {}
    for event_type, payload in events:
        by_type.setdefault(event_type, []).append(payload)

    created = by_type.get("order_created", [])
    recorded = by_type.get("fill_recorded", [])
    report.add("single_order_created", len(created) == 1, f"count={len(created)}")
    report.add(
        "fill_count_matches_ingestion",
        len(recorded) == oms_fills,
        f"fill_recorded={len(recorded)} oms_fill_count={oms_fills}",
    )
    if created and recorded:
        order = created[0].get("artifact", {})
        order_id = str(order.get("artifact_id", ""))
        expected_qty = Decimal(str(order.get("quantity", "0")))
        linked = [
            fill.get("artifact", {})
            for fill in recorded
            if str(fill.get("artifact", {}).get("order_id", "")) == order_id
        ]
        report.add(
            "fills_link_to_order",
            len(linked) == len(recorded),
            f"linked={len(linked)} recorded={len(recorded)}",
        )
        total = sum((Decimal(str(fill.get("quantity", "0"))) for fill in linked), Decimal("0"))
        report.add(
            "fill_quantity_matches_order",
            total == expected_qty,
            f"filled={total} order={expected_qty}",
        )
        venue_ids = [str(fill.get("venue_fill_id", "")) for fill in linked]
        report.add(
            "venue_fill_ids_present",
            all(venue_ids) and len(set(venue_ids)) == len(venue_ids),
            f"distinct_venue_ids={len(set(venue_ids))}",
        )
        applied_by_venue = {str(item.get("venue_fill_id", "")): item for item in applied_events}
        mismatches = []
        for fill in linked:
            venue_id = str(fill.get("venue_fill_id", ""))
            item = applied_by_venue.get(venue_id)
            if item is None:
                mismatches.append(f"{venue_id}:missing_from_account_ledger")
                continue
            for key in ("quantity", "price", "fee"):
                if str(item.get(key)) != str(fill.get(key)):
                    mismatches.append(f"{venue_id}:{key}_mismatch")
        report.add(
            "account_ledger_matches_fills",
            not mismatches,
            ";".join(mismatches) if mismatches else f"matched={len(linked)}",
        )
    states = [
        str(item.get("artifact", {}).get("state", ""))
        for key in ("order_filled", "order_reconciled")
        for item in by_type.get(key, [])
    ]
    report.add(
        "terminal_fill_chain",
        states == ["filled", "reconciled"],
        f"states={states}",
    )
    reconciled_records = by_type.get("reconciliation_recorded", [])
    if reconciled_records:
        artifact = reconciled_records[-1].get("artifact", {})
        report.add(
            "reconciliation_clean",
            artifact.get("reconciled") is True
            and not artifact.get("discrepancies"),
            f"reconciled={artifact.get('reconciled')} "
            f"discrepancies={artifact.get('discrepancies')}",
        )
    else:
        report.add("reconciliation_clean", False, "no reconciliation_recorded event")
    manifest_recon = result.get("reconciliation")
    if isinstance(manifest_recon, dict):
        report.add(
            "manifest_reconciliation_clean",
            manifest_recon.get("reconciled") is True
            and manifest_recon.get("discrepancy_count") == 0,
            f"reconciled={manifest_recon.get('reconciled')} "
            f"count={manifest_recon.get('discrepancy_count')}",
        )
    else:
        report.add("manifest_reconciliation_clean", False, "missing block")

    restart = result.get("restart_recovery")
    if isinstance(restart, dict):
        report.add(
            "restart_no_duplicate",
            restart.get("status") == "passed"
            and restart.get("signed_submissions_after_restart") == 0
            and restart.get("duplicate_submission") is False,
            f"status={restart.get('status')} "
            f"resubmits={restart.get('signed_submissions_after_restart')} "
            f"duplicate={restart.get('duplicate_submission')}",
        )
    else:
        report.add("restart_no_duplicate", False, "missing block")

    sequence = result.get("oms_event_sequence")
    if isinstance(sequence, list):
        counts = Counter(sequence)
        report.add(
            "event_sequence_consistent",
            counts.get("fill_recorded", 0) == oms_fills
            and "order_filled" in counts
            and "order_reconciled" in counts,
            f"sequence={list(sequence)}",
        )
    else:
        report.add("event_sequence_consistent", False, "missing sequence")

    report.p03_certified = report.fill_pass and not report.failed
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run_dir = args.evidence_dir
    if not run_dir.is_dir():
        print(f"evidence directory not found: {run_dir}", file=sys.stderr)
        return 2
    try:
        report = validate_run(run_dir)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    if report.p03_certified:
        return 0
    if not report.fill_pass:
        print("cancellation-only pass: P03 not certified", file=sys.stderr)
        return 3
    print(
        "P03 checks failed: " + ", ".join(check.name for check in report.failed),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
