import json
import os
import subprocess
import sys


def test_transition_smoke_is_network_free_when_venue_is_not_configured(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text('export ADVISORAI_VENUE_ENVIRONMENT="paper_testnet"\n', encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    environment = os.environ.copy()
    environment["ADVISORAI_RUN_NETWORK_SMOKE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_transition_connectors.py",
            "--secrets",
            str(secrets),
            "--evidence-dir",
            str(evidence_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    evidence = json.loads(completed.stdout)
    assert evidence["status"] == "not_ready"
    assert evidence["network_calls"] == 0
    assert evidence["reason"] == "paper_venue_configuration_missing"
    assert evidence["evidence_sha256"]
    latest = json.loads((evidence_dir / "latest.json").read_text(encoding="utf-8"))
    assert (evidence_dir / latest["run_id"] / "read-only-smoke.json").exists()
