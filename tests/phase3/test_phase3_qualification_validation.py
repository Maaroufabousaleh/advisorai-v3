from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.validate_phase3_public_data_qualification import (
    _load_chain,
    _validate_failure_details,
    _validate_health_snapshot,
    _validate_source_selection,
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


def test_source_selection_accepts_bound_healthy_selection_and_fail_closed_rows():
    result = _validate_source_selection(
        [
            {
                "fail_closed": False,
                "silent_substitution": False,
                "selected_source_id": "binance_spot_public_market_data",
                "selected_provider_identity": "binance_spot_public_market_data",
                "actual_source_identity": "binance_spot_public_market_data",
            },
            {
                "fail_closed": True,
                "silent_substitution": False,
                "selected_source_id": None,
                "selected_provider_identity": None,
                "actual_source_identity": None,
            },
        ]
    )

    assert result["issues"] == []
    assert result["selection_fail_closed_count"] == 1
    assert result["silent_substitution_count"] == 0


def test_source_selection_rejects_identity_drift_and_unsafe_fail_closed_row():
    result = _validate_source_selection(
        [
            {
                "fail_closed": False,
                "silent_substitution": False,
                "selected_source_id": "binance_spot_public_market_data",
                "selected_provider_identity": "coinbase_exchange_public_market_data",
                "actual_source_identity": "binance_spot_public_market_data",
            },
            {
                "fail_closed": True,
                "silent_substitution": False,
                "selected_source_id": "coinbase_exchange_public_market_data",
                "selected_provider_identity": "coinbase_exchange_public_market_data",
                "actual_source_identity": "coinbase_exchange_public_market_data",
            },
        ]
    )

    assert "selection_1_identity_mismatch" in result["issues"]
    assert "selection_2_fail_closed_identity_present" in result["issues"]


def _health_sample() -> dict[str, object]:
    return {
        "source_id": "binance_spot_public_market_data",
        "symbol": "BTC",
        "provider_identity": "binance_spot_public_market_data",
        "health_state": "HEALTHY",
        "cycle_ended_at": "2026-08-11T11:00:00Z",
        "last_valid_event_age_seconds": 0.4,
        "reconnects": 2,
        "sequence_gap_count": 0,
        "disagreement_state": "normal",
        "snapshot_recovery": "not_required",
        "failure_classes": [],
        "failure_layers": [],
    }


def _write_health_snapshot(path: Path, sample: dict[str, object]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "advisorai.phase3.public-market-data-durable.v1.health-snapshot",
                "updated_at": "2026-08-11T11:00:01Z",
                "run_id": "run-1",
                "phase3_admission_opened": False,
                "sources": [
                    {
                        "source_id": sample["source_id"],
                        "symbol": sample["symbol"],
                        "state": sample["health_state"],
                        "last_event_age_seconds": sample["last_valid_event_age_seconds"],
                        "freshness": "fresh",
                        "reconnect_count": sample["reconnects"],
                        "sequence_gap_count": sample["sequence_gap_count"],
                        "disagreement_state": sample["disagreement_state"],
                        "snapshot_recovery_state": sample["snapshot_recovery"],
                        "failure_classes": sample["failure_classes"],
                        "failure_layers": sample["failure_layers"],
                        "actual_provider_identity": sample["provider_identity"],
                        "fail_closed": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_health_snapshot_projection_matches_append_only_samples(tmp_path: Path):
    sample = _health_sample()
    path = tmp_path / "latest-health.json"
    _write_health_snapshot(path, sample)

    result = _validate_health_snapshot(path, [sample], expected_run_id="run-1")

    assert result["state"] == "validated"
    assert result["issues"] == []
    assert result["source_count"] == 1
    assert len(result["sha256"]) == 64


def test_health_snapshot_rejects_identity_and_schema_widening(tmp_path: Path):
    sample = _health_sample()
    path = tmp_path / "latest-health.json"
    _write_health_snapshot(path, sample)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sources"][0]["actual_provider_identity"] = "coinbase-public"
    payload["sources"][0]["secret_like_field"] = "must not enter the projection"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = _validate_health_snapshot(path, [sample], expected_run_id="run-1")

    assert result["state"] == "invalid"
    assert "health_snapshot_source_1_actual_provider_identity_mismatch" in result["issues"]
    assert "health_snapshot_source_1_contains_unexpected_fields" in result["issues"]
