import hashlib
import json

from scripts.run_phase8_capability_evidence import run_evidence


def test_phase8_evidence_delivers_hermes_collector_to_active_read(tmp_path):
    report_path, report = run_evidence(tmp_path / "evidence")

    assert report_path.exists()
    assert report["passed"]
    assert report["local_exit_gate_evidence_passed"]
    assert report["phase8_gate_decision"] == "pending"
    assert report["phase8_gate_recorded"] is False
    assert report["phase8_admitted"] is False
    assert report["network_calls"] == 0
    assert report["credentials_used"] is False
    assert report["paper_orders"] == 0
    assert report["live_capital"] is False

    hermes = report["hermes"]
    assert hermes["passed"]
    assert hermes["reproducible_output"]
    assert hermes["secrets_scrubbed"]
    assert hermes["network_access_attempted"] is False
    assert hermes["filesystem_write_attempted"] is False
    assert hermes["sensitive_path_access_attempted"] is False
    assert hermes["process_spawn_attempted"] is False
    assert hermes["first"]["output"]["untrusted_flags"] == [True]
    assert hermes["first"]["output"]["network_calls"] == 0

    capability = report["capability"]
    assert capability["lifecycle"] == "active_read"
    assert capability["restarted_lifecycle"] == "active_read"
    assert capability["allowed_actions"] == ["read_source"]
    assert capability["secrets_required"] == []
    assert capability["network_required"] is False
    assert capability["ledger_event_count"] == 11
    assert report["active_read"]["broker_read_executed"]
    assert report["active_read"]["forbidden_action_rejected"]
    assert report["active_write_rejected_without_human_approval"]


def test_phase8_evidence_report_and_pointer_are_immutable_per_run(tmp_path):
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
