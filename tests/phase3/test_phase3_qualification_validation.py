from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.validate_phase3_public_data_qualification import (
    _load_chain,
    _validate_failure_details,
    _validate_timestamp_projection,
)


def _write_chain(path: Path) -> str:
    previous = None
    records = []
    for value in ("one", "two"):
        unsigned = {"previous_record_hash": previous, "value": value}
        record = {
            **unsigned,
            "record_hash": hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        records.append(record)
        previous = record["record_hash"]
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return previous


def test_load_chain_validates_hashes_and_predecessors(tmp_path: Path):
    path = tmp_path / "records.jsonl"
    last_hash = _write_chain(path)

    records, actual_last_hash = _load_chain(path)

    assert len(records) == 2
    assert actual_last_hash == last_hash


def test_load_chain_rejects_tampering(tmp_path: Path):
    path = tmp_path / "records.jsonl"
    _write_chain(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["value"] = "changed"
    lines[1] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid record hash"):
        _load_chain(path)


def test_failure_detail_validation_is_backward_compatible_with_old_roots():
    result = _validate_failure_details([{"source_id": "old-root"}])

    assert result["issues"] == []
    assert result["samples_with_details"] == 0
    assert result["samples_without_details"] == 1


def test_failure_detail_validation_rejects_unsafe_or_duplicate_labels():
    result = _validate_failure_details(
        [
            {
                "failure_classes": ["TimeoutError", "TimeoutError", "secret value"],
                "failure_layers": ["first_message_timeout"],
            }
        ]
    )

    assert "sample_1_failure_classes_contains_duplicates" in result["issues"]
    assert "sample_1_failure_classes_contains_unsafe_label" in result["issues"]
    assert result["label_counts"]["failure_classes"] == {"TimeoutError": 2}


def test_timestamp_projection_validates_new_fields_and_preserves_old_roots():
    result = _validate_timestamp_projection(
        [
            {"source_id": "old-root"},
            {
                "last_provider_event_at": "2026-08-11T11:00:00Z",
                "last_event_received_at": "2026-08-11T11:00:01Z",
                "last_valid_event_at": "2026-08-11T11:00:01Z",
                "provider_event_timestamp_count": 1,
            },
        ]
    )

    assert result["state"] == "mixed"
    assert result["projected_rows"] == 1
    assert result["legacy_rows"] == 1
    assert result["issues"] == ["timestamp_projection_schema_mixed"]


def test_timestamp_projection_rejects_inconsistent_count_and_receipt():
    result = _validate_timestamp_projection(
        [
            {
                "last_provider_event_at": None,
                "last_event_received_at": "2026-08-11T11:00:01Z",
                "last_valid_event_at": "2026-08-11T11:00:02Z",
                "provider_event_timestamp_count": 1,
            }
        ]
    )

    assert "sample_1_receipt_timestamp_mismatch" in result["issues"]
    assert "sample_1_provider_timestamp_count_mismatch" in result["issues"]
