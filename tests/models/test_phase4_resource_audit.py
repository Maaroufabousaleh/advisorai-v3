from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_phase4_v3core_resources import _canonical, _sha256, audit


def _write_sidecar(root: Path, *, state: str = "target_exited") -> None:
    root.mkdir()
    config = {
        "schema": "advisorai.phase3.resource-monitor.v2.config",
        "pid": 123,
        "expected_process_start_ticks": 456,
        "expected_command_sha256": "a" * 64,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "target_root": str(root / "target"),
    }
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    first = {
        "schema": "advisorai.phase3.resource-monitor.v2",
        "sampled_at": "2026-08-18T00:00:00Z",
        "pid": 123,
        "process_status": "running",
        "process_start_ticks": 456,
        "command_sha256": "a" * 64,
        "rss_mib": 10.0,
        "vms_mib": 20.0,
        "cpu_percent": 1.0,
        "thread_count": 1,
        "file_descriptor_count": 4,
        "inet_connection_count": 0,
        "target_root_file_count": 2,
        "target_root_bytes": 100,
        "resource_errors": [],
        "previous_record_hash": None,
    }
    first["record_hash"] = _sha256(_canonical(first))
    second = {
        **first,
        "sampled_at": "2026-08-18T00:00:30Z",
        "rss_mib": 11.0,
        "target_root_bytes": 120,
        "previous_record_hash": first["record_hash"],
    }
    second["record_hash"] = _sha256(
        _canonical({key: value for key, value in second.items() if key != "record_hash"})
    )
    (root / "observations.jsonl").write_text(
        json.dumps(first, separators=(",", ":"))
        + "\n"
        + json.dumps(second, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema": "advisorai.phase3.resource-monitor.v2.summary",
        "state": state,
        "target_root": config["target_root"],
        "sample_count": 2,
        "running_sample_count": 2,
        "first_sampled_at": "2026-08-18T00:00:00+00:00",
        "last_sampled_at": "2026-08-18T00:00:30+00:00",
        "max_rss_mib": 11.0,
        "max_vms_mib": 20.0,
        "max_cpu_percent": 1.0,
        "max_thread_count": 1,
        "max_file_descriptor_count": 4,
        "max_inet_connection_count": 0,
        "max_target_root_file_count": 2,
        "max_target_root_bytes": 120,
        "resource_errors": [],
        "last_record_hash": second["record_hash"],
    }
    summary_path = root / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    status = {
        "schema": "advisorai.phase3.resource-monitor.v2.status",
        "state": state,
        "summary_sha256": _sha256(summary_path.read_bytes()),
        "last_record_hash": second["record_hash"],
    }
    (root / "status.json").write_text(json.dumps(status), encoding="utf-8")


def test_resource_audit_validates_chain_and_reports_growth(tmp_path: Path) -> None:
    root = tmp_path / "resource"
    _write_sidecar(root)
    report = audit(root)
    assert report["state"] == "PASS_FOR_REVIEW"
    assert report["issues"] == []
    assert report["resource"]["rss_mib_growth"] == 1.0
    assert report["resource"]["target_root_bytes_growth"] == 20
    assert report["credentials_loaded"] is False
    assert report["order_writes_attempted"] is False


def test_resource_audit_refuses_running_sidecar(tmp_path: Path) -> None:
    root = tmp_path / "resource"
    _write_sidecar(root, state="running")
    with pytest.raises(ValueError, match="still running"):
        audit(root)


def test_resource_audit_rejects_broken_observation_chain(tmp_path: Path) -> None:
    root = tmp_path / "resource"
    _write_sidecar(root)
    path = root / "observations.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]["previous_record_hash"] = "b" * 64
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="predecessor"):
        audit(root)
