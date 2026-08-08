from pathlib import Path

from scripts.run_phase1_local_rebuild import run_evidence


def test_phase1_local_evidence_rebuilds_config_and_bronze_deterministically(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    report_path, report = run_evidence(
        tmp_path / "phase1-local-rebuild",
        config_root=repository_root,
    )

    assert report_path.exists()
    assert report["passed"] is True
    assert report["network_calls"] == 0
    assert report["configuration_rollback"]["activation_count"] == 3
    assert (
        report["configuration_rollback"]["active_bundle_hash"]
        == report["configuration_rollback"]["bundle_hashes"]["initial"]
    )
    assert (
        report["configuration_rollback"]["active_bundle_hash_after_restart"]
        == report["configuration_rollback"]["bundle_hashes"]["initial"]
    )
    assert report["bronze_rebuild"]["manifest_bytes_equal"] is True
    assert report["bronze_rebuild"]["artifact_bytes_equal"] is True
    assert report["bronze_rebuild"]["rows_equal"] is True


def test_phase1_local_evidence_uses_a_new_immutable_run_directory(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    output_root = tmp_path / "phase1-local-rebuild"

    first_path, first = run_evidence(output_root, config_root=repository_root)
    second_path, second = run_evidence(output_root, config_root=repository_root)

    assert first_path != second_path
    assert first["run_id"] != second["run_id"]
    assert first_path.read_bytes() != b""
    assert second_path.read_bytes() != b""
