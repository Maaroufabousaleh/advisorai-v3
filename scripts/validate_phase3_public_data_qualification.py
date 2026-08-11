#!/usr/bin/env python3
"""Validate a completed Phase-3 qualification root without network access.

This validator never changes the qualification root and never opens admission.
It produces a separate immutable review record that distinguishes structural
evidence integrity from the source-health outcome observed by the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "advisorai.phase3.public-market-data-validation.v1"
CHAIN_LOGS = (
    "samples.jsonl",
    "observations.jsonl",
    "source-selection.jsonl",
    "disagreement.jsonl",
    "health-transitions.jsonl",
)
FAILURE_DETAIL_FIELDS = ("failure_classes", "failure_layers")
TIMESTAMP_PROJECTION_FIELDS = (
    "last_provider_event_at",
    "last_event_received_at",
    "provider_event_timestamp_count",
)
HEALTH_SNAPSHOT_SCHEMA = "advisorai.phase3.public-market-data-durable.v1.health-snapshot"
HEALTH_SNAPSHOT_FIELDS = frozenset(
    {
        "source_id",
        "symbol",
        "state",
        "last_event_age_seconds",
        "freshness",
        "reconnect_count",
        "sequence_gap_count",
        "disagreement_state",
        "snapshot_recovery_state",
        "failure_classes",
        "failure_layers",
        "actual_provider_identity",
        "fail_closed",
    }
)


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("evidence timestamp is not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _load_chain(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.is_file():
        raise ValueError(f"missing append-only log: {path.name}")
    previous: str | None = None
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path.name}:{line_number} is not an object")
        unsigned = {key: value for key, value in record.items() if key != "record_hash"}
        if record.get("previous_record_hash") != previous:
            raise ValueError(f"{path.name}:{line_number} has an invalid predecessor hash")
        if record.get("record_hash") != _sha256(_canonical(unsigned)):
            raise ValueError(f"{path.name}:{line_number} has an invalid record hash")
        previous = str(record["record_hash"])
        records.append(record)
    if not records:
        raise ValueError(f"empty append-only log: {path.name}")
    return records, previous


def _validate_resource_monitor(path: Path) -> dict[str, Any]:
    rows, last_hash = _load_chain(path / "observations.jsonl")
    status = json.loads((path / "status.json").read_text(encoding="utf-8"))
    summary_path = path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary_sha256 = _sha256(summary_path.read_bytes())
    if status.get("summary_sha256") != summary_sha256:
        raise ValueError("resource monitor summary hash does not match")
    if status.get("last_record_hash") != last_hash:
        raise ValueError("resource monitor status does not name the last observation")
    return {
        "root": str(path),
        "schema": status.get("schema"),
        "state": status.get("state"),
        "sample_count": len(rows),
        "last_record_hash": last_hash,
        "summary_sha256": summary_sha256,
        "max_rss_mib": summary.get("max_rss_mib"),
        "max_vms_mib": summary.get("max_vms_mib"),
        "max_cpu_percent": summary.get("max_cpu_percent"),
        "max_thread_count": summary.get("max_thread_count"),
        "max_file_descriptor_count": summary.get("max_file_descriptor_count"),
        "max_inet_connection_count": summary.get("max_inet_connection_count"),
        "max_target_root_file_count": summary.get("max_target_root_file_count"),
        "max_target_root_bytes": summary.get("max_target_root_bytes"),
        "resource_errors": summary.get("resource_errors", []),
    }


def _validate_failure_details(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate optional sanitized failure labels without rewriting old roots."""

    issues: list[str] = []
    counts = {field: Counter() for field in FAILURE_DETAIL_FIELDS}
    samples_with_details = 0
    samples_without_details = 0
    for index, row in enumerate(samples, 1):
        row_has_details = False
        for field in FAILURE_DETAIL_FIELDS:
            value = row.get(field)
            if value is None:
                continue
            row_has_details = True
            if not isinstance(value, list):
                issues.append(f"sample_{index}_{field}_not_a_list")
                continue
            string_labels = [label for label in value if isinstance(label, str)]
            if len(string_labels) != len(set(string_labels)):
                issues.append(f"sample_{index}_{field}_contains_duplicates")
            for label in value:
                if (
                    not isinstance(label, str)
                    or not label
                    or len(label) > 128
                    or any(not (character.isalnum() or character in "._-") for character in label)
                ):
                    issues.append(f"sample_{index}_{field}_contains_unsafe_label")
                    continue
                counts[field][label] += 1
        if row_has_details:
            samples_with_details += 1
        else:
            samples_without_details += 1
    return {
        "issues": issues,
        "samples_with_details": samples_with_details,
        "samples_without_details": samples_without_details,
        "label_counts": {field: dict(sorted(counter.items())) for field, counter in counts.items()},
    }


def _validate_timestamp_projection(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate provider/receipt timestamp fields while preserving old roots."""

    issues: list[str] = []
    projected_rows = 0
    legacy_rows = 0
    for index, row in enumerate(samples, 1):
        present = [field in row for field in TIMESTAMP_PROJECTION_FIELDS]
        if not any(present):
            legacy_rows += 1
            continue
        projected_rows += 1
        if not all(present):
            issues.append(f"sample_{index}_timestamp_projection_incomplete")
            continue
        provider_at = row.get("last_provider_event_at")
        receipt_at = row.get("last_event_received_at")
        legacy_receipt_at = row.get("last_valid_event_at")
        count = row.get("provider_event_timestamp_count")
        for field, value in (
            ("last_provider_event_at", provider_at),
            ("last_event_received_at", receipt_at),
        ):
            if value is not None:
                try:
                    _timestamp(value)
                except ValueError:
                    issues.append(f"sample_{index}_{field}_invalid")
        if receipt_at != legacy_receipt_at:
            issues.append(f"sample_{index}_receipt_timestamp_mismatch")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            issues.append(f"sample_{index}_provider_timestamp_count_invalid")
        elif (count == 0) != (provider_at is None):
            issues.append(f"sample_{index}_provider_timestamp_count_mismatch")
    if projected_rows and legacy_rows:
        issues.append("timestamp_projection_schema_mixed")
    return {
        "issues": issues,
        "state": "projected"
        if projected_rows and not legacy_rows
        else "legacy_unprojected"
        if not projected_rows
        else "mixed",
        "projected_rows": projected_rows,
        "legacy_rows": legacy_rows,
    }


def _validate_source_selection(selection: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate explicit selection identity and fail-closed semantics.

    A successful selection is intentionally recorded with ``fail_closed`` set
    to ``False``.  The fail-closed invariant applies when no eligible source
    exists; healthy selections must instead bind the selected source and
    provider identity to the actual source.  Treating every successful
    selection as a failure would make the offline validator disagree with the
    source-selection contract and with the admission evaluator.
    """

    issues: list[str] = []
    fail_closed_count = 0
    silent_substitution_count = 0
    for index, row in enumerate(selection, 1):
        fail_closed = row.get("fail_closed")
        if fail_closed is True:
            fail_closed_count += 1
        elif fail_closed is not False:
            issues.append(f"selection_{index}_fail_closed_invalid")

        if row.get("silent_substitution") is True:
            silent_substitution_count += 1
            issues.append(f"selection_{index}_silent_substitution")

        if fail_closed is True:
            if any(
                row.get(field) is not None
                for field in (
                    "selected_source_id",
                    "selected_provider_identity",
                    "actual_source_identity",
                )
            ):
                issues.append(f"selection_{index}_fail_closed_identity_present")
            continue

        if not (
            row.get("selected_source_id") is not None
            and row.get("selected_source_id") == row.get("actual_source_identity")
            and row.get("selected_provider_identity") == row.get("actual_source_identity")
        ):
            issues.append(f"selection_{index}_identity_mismatch")

    return {
        "issues": issues,
        "selection_count": len(selection),
        "selection_fail_closed_count": fail_closed_count,
        "silent_substitution_count": silent_substitution_count,
    }


def _validate_health_snapshot(
    path: Path,
    samples: list[dict[str, Any]],
    *,
    expected_run_id: str,
) -> dict[str, Any]:
    """Validate the sanitized dashboard projection against terminal samples.

    ``latest-health.json`` is intentionally a replaceable operator projection,
    not a second evidence log.  It still must be a truthful projection of the
    latest append-only sample for every source/symbol pair.  Keeping this check
    in the offline validator prevents a stale or widened dashboard payload from
    being mistaken for source-health evidence.
    """

    issues: list[str] = []
    if not path.is_file():
        return {
            "issues": ["health_snapshot_missing"],
            "state": "missing",
            "source_count": 0,
            "sha256": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "issues": ["health_snapshot_invalid_json"],
            "state": "invalid",
            "source_count": 0,
            "sha256": _sha256(path.read_bytes()),
        }
    if not isinstance(payload, dict):
        return {
            "issues": ["health_snapshot_not_an_object"],
            "state": "invalid",
            "source_count": 0,
            "sha256": _sha256(path.read_bytes()),
        }
    if payload.get("schema") != HEALTH_SNAPSHOT_SCHEMA:
        issues.append("health_snapshot_schema_invalid")
    if payload.get("run_id") != expected_run_id:
        issues.append("health_snapshot_run_id_mismatch")
    try:
        _timestamp(payload.get("updated_at"))
    except (TypeError, ValueError):
        issues.append("health_snapshot_updated_at_invalid")
    if payload.get("phase3_admission_opened") is not False:
        issues.append("health_snapshot_admission_invariant_failed")

    latest_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for row in samples:
        source_id = row.get("source_id")
        symbol = row.get("symbol")
        if not isinstance(source_id, str) or not isinstance(symbol, str):
            continue
        try:
            sampled_at = _timestamp(row.get("cycle_ended_at"))
        except (TypeError, ValueError):
            continue
        key = (source_id, symbol)
        previous = latest_by_pair.get(key)
        if previous is None or sampled_at >= _timestamp(previous["cycle_ended_at"]):
            latest_by_pair[key] = row

    records = payload.get("sources")
    if not isinstance(records, list):
        issues.append("health_snapshot_sources_not_a_list")
        records = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            issues.append(f"health_snapshot_source_{index}_not_an_object")
            continue
        missing = HEALTH_SNAPSHOT_FIELDS.difference(record)
        extra = set(record).difference(HEALTH_SNAPSHOT_FIELDS)
        if missing:
            issues.append(f"health_snapshot_source_{index}_missing_fields")
        if extra:
            issues.append(f"health_snapshot_source_{index}_contains_unexpected_fields")
        source_id = record.get("source_id")
        symbol = record.get("symbol")
        if not isinstance(source_id, str) or not source_id.strip():
            issues.append(f"health_snapshot_source_{index}_source_id_invalid")
            continue
        if not isinstance(symbol, str) or not symbol.strip():
            issues.append(f"health_snapshot_source_{index}_symbol_invalid")
            continue
        key = (source_id, symbol)
        if key in seen:
            issues.append(f"health_snapshot_source_{index}_duplicate")
        seen.add(key)
        expected = latest_by_pair.get(key)
        if expected is None:
            issues.append(f"health_snapshot_source_{index}_not_in_samples")
            continue

        projections = {
            "state": expected.get("health_state"),
            "last_event_age_seconds": expected.get("last_valid_event_age_seconds"),
            "reconnect_count": expected.get("reconnects", 0),
            "sequence_gap_count": expected.get("sequence_gap_count", 0),
            "disagreement_state": expected.get("disagreement_state", "unmeasured"),
            "snapshot_recovery_state": expected.get("snapshot_recovery", "unmeasured"),
            "failure_classes": expected.get("failure_classes", []),
            "failure_layers": expected.get("failure_layers", []),
            "actual_provider_identity": expected.get("provider_identity"),
        }
        for field, expected_value in projections.items():
            if record.get(field) != expected_value:
                issues.append(f"health_snapshot_source_{index}_{field}_mismatch")
        state = record.get("state")
        if record.get("freshness") != ("fresh" if state == "HEALTHY" else "fail_closed"):
            issues.append(f"health_snapshot_source_{index}_freshness_invalid")
        expected_fail_closed = state != "HEALTHY"
        if record.get("fail_closed") is not expected_fail_closed:
            issues.append(f"health_snapshot_source_{index}_fail_closed_invalid")
        age = record.get("last_event_age_seconds")
        if age is not None and (
            isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0
        ):
            issues.append(f"health_snapshot_source_{index}_age_invalid")
        for field in ("reconnect_count", "sequence_gap_count"):
            value = record.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                issues.append(f"health_snapshot_source_{index}_{field}_invalid")
        for field in ("failure_classes", "failure_layers"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values
            ):
                issues.append(f"health_snapshot_source_{index}_{field}_invalid")

    expected_pairs = set(latest_by_pair)
    missing_pairs = expected_pairs.difference(seen)
    if missing_pairs:
        issues.append("health_snapshot_missing_sample_pairs")
    if len(records) != len(expected_pairs):
        issues.append("health_snapshot_source_count_mismatch")
    return {
        "issues": issues,
        "state": "validated" if not issues else "invalid",
        "source_count": len(records),
        "sample_pair_count": len(expected_pairs),
        "sha256": _sha256(path.read_bytes()),
    }


def validate(run_directory: Path, *, resource_monitor: Path | None = None) -> dict[str, Any]:
    run_directory = run_directory.resolve()
    config_path = run_directory / "config.json"
    status_path = run_directory / "status.json"
    summary_path = run_directory / "summary.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    logs = {name: _load_chain(run_directory / name)[0] for name in CHAIN_LOGS}
    samples = logs["samples.jsonl"]
    cycles = sorted({int(row["cycle"]) for row in samples})
    pairs = sorted({(row["provider_identity"], row["symbol"]) for row in samples})
    expected_cycles = list(range(1, max(cycles) + 1))
    cycle_counts = Counter(int(row["cycle"]) for row in samples)
    sample_times = [
        _timestamp(row[key]) for row in samples for key in ("cycle_started_at", "cycle_ended_at")
    ]
    target_end = _timestamp(status["target_end_at"])
    last_cycle_end = max(_timestamp(row["cycle_ended_at"]) for row in samples)
    completed_at = _timestamp(status["updated_at"])
    selection = logs["source-selection.jsonl"]
    disagreement = logs["disagreement.jsonl"]
    health = logs["health-transitions.jsonl"]
    raw_spools = list(run_directory.rglob("raw-*.jsonl"))
    raw_hashes = sorted(
        {
            digest
            for row in samples
            for digest in row.get("raw_spool_hashes", [])
            if isinstance(digest, str)
        }
    )
    issues: list[str] = []
    failure_details = _validate_failure_details(samples)
    issues.extend(failure_details["issues"])
    timestamp_projection = _validate_timestamp_projection(samples)
    issues.extend(timestamp_projection["issues"])
    source_selection = _validate_source_selection(selection)
    issues.extend(source_selection["issues"])
    health_snapshot = _validate_health_snapshot(
        run_directory / "latest-health.json",
        samples,
        expected_run_id=str(config.get("run_id", run_directory.name)),
    )
    issues.extend(health_snapshot["issues"])
    if status.get("state") != "multi_hour_window_complete":
        issues.append("qualification_window_not_complete")
    if cycles != expected_cycles:
        issues.append("cycle_sequence_not_contiguous")
    if any(count != len(pairs) for count in cycle_counts.values()):
        issues.append("cycle_pair_count_incomplete")
    if completed_at < target_end:
        issues.append("qualification_completed_before_target")
    if (
        config.get("credentials_loaded") is not False
        or config.get("order_writes_attempted") is not False
    ):
        issues.append("public_runner_credential_or_write_invariant_failed")
    if any(row.get("credentials_loaded") is not False for row in samples):
        issues.append("sample_credential_invariant_failed")
    if any(row.get("order_writes_attempted") is not False for row in samples):
        issues.append("sample_write_invariant_failed")
    if summary.get("state") != status.get("state"):
        issues.append("summary_status_state_mismatch")

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "validated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_directory": str(run_directory),
        "run_config_sha256": _sha256(config_path.read_bytes()),
        "run_summary_sha256": _sha256(summary_path.read_bytes()),
        "run_code_sha256": config.get("code_sha256"),
        "state": "PASS_FOR_REVIEW" if not issues else "FAIL",
        "qualification_state": "evidence_for_review_only",
        "phase3_admission": False,
        "issues": issues,
        "target_window": {
            "started_at": status.get("started_at"),
            "target_end_at": status.get("target_end_at"),
            "last_cycle_end_at": last_cycle_end.isoformat().replace("+00:00", "Z"),
            "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
            "sample_span_seconds": round(
                (max(sample_times) - min(sample_times)).total_seconds(), 3
            ),
            "cycle_count": len(cycles),
            "samples": len(samples),
            "source_symbol_pairs": pairs,
        },
        "append_only_logs": {
            name: {"count": len(rows), "last_record_hash": _load_chain(run_directory / name)[1]}
            for name, rows in logs.items()
        },
        "source_outcomes": {
            "health_states": dict(Counter(row["health_state"] for row in samples)),
            "disagreement_states": dict(Counter(row["state"] for row in disagreement)),
            "health_transition_count": len(health),
            "selection_count": source_selection["selection_count"],
            "selection_fail_closed_count": source_selection["selection_fail_closed_count"],
            "silent_substitution_count": source_selection["silent_substitution_count"],
            "replay_failure_count": sum(not row.get("replay_equivalent", False) for row in samples),
            "sequence_gap_count": sum(int(row.get("sequence_gap_count", 0)) for row in samples),
            "duplicate_count": sum(int(row.get("duplicate_count", 0)) for row in samples),
            "out_of_order_count": sum(int(row.get("out_of_order_count", 0)) for row in samples),
            "raw_spool_file_count": len(raw_spools),
            "raw_spool_bytes": sum(path.stat().st_size for path in raw_spools),
            "raw_spool_hash_count": len(raw_hashes),
            "failure_details": {
                key: value for key, value in failure_details.items() if key != "issues"
            },
            "timestamp_projection": {
                key: value for key, value in timestamp_projection.items() if key != "issues"
            },
            "health_snapshot": {
                key: value for key, value in health_snapshot.items() if key != "issues"
            },
        },
        "fault_drills": json.loads(
            (run_directory / "fault-drills.json").read_text(encoding="utf-8")
        ),
        "resource_monitor": (
            _validate_resource_monitor(resource_monitor.resolve())
            if resource_monitor is not None
            else None
        ),
    }
    return report


def _write_report(output_root: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    report_path = output_root / "phase3-qualification-validation.json"
    encoded = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    report_path.write_bytes(encoded)
    digest_path = output_root / "phase3-qualification-validation.sha256"
    digest_path.write_text(f"{_sha256(encoded)}  {report_path.name}\n", encoding="ascii")
    return report_path, digest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--resource-monitor", type=Path)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    result = validate(args.run_directory, resource_monitor=args.resource_monitor)
    report_path, digest_path = _write_report(args.output_root, result)
    print(
        json.dumps(
            {"state": result["state"], "report": str(report_path), "digest": str(digest_path)}
        )
    )
    raise SystemExit(0 if result["state"] == "PASS_FOR_REVIEW" else 1)
