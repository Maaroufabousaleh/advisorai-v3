import hashlib
import json

from scripts.run_phase0_component_bakeoff import run_evidence


def test_component_bakeoff_records_local_contracts_without_opening_gates(tmp_path):
    report_path, report = run_evidence(tmp_path / "evidence")

    assert report_path.exists()
    assert report["local_probes_passed"]
    assert report["network_calls"] == 0
    assert report["credentials_used"] is False
    assert report["paper_orders"] == 0
    assert report["live_capital"] is False
    assert report["phase0_gate_decision"] == "pending"
    assert report["phase0_gate_eligible"] is False
    assert report["phase0_gate_recorded"] is False
    assert report["components"]["nautilus-trader"]["probe_output"][
        "admission_guard_rejected_without_gate"
    ]
    assert report["components"]["parquet-manifest"]["probe_output"]["manifest_bytes_equal"]
    assert report["components"]["parquet-manifest"]["probe_output"]["parquet_bytes_equal"]
    assert report["components"]["hermes-sandbox"]["probe_output"]["write_authority"] is False
    assert report["components"]["rclone-crypt-contract"]["probe_output"]["verification"][
        "restore_verified"
    ]


def test_component_bakeoff_report_and_latest_pointer_are_content_addressed(tmp_path):
    root = tmp_path / "evidence"
    report_path, report = run_evidence(root)
    pointer = json.loads((root / "latest.json").read_text(encoding="utf-8"))

    assert pointer["run_id"] == report["run_id"]
    assert pointer["report_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert report["report_sha256"] == pointer["report_sha256"]

    second_path, second = run_evidence(root)
    assert second_path != report_path
    assert second["run_id"] != report["run_id"]
    assert (
        json.loads((root / "latest.json").read_text(encoding="utf-8"))["run_id"] == second["run_id"]
    )
