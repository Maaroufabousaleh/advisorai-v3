import json
import os
import subprocess
import sys
from pathlib import Path


def _run_config_check(secrets, *extra):
    return subprocess.run(
        [
            sys.executable,
            "scripts/check_transition_config.py",
            "--secrets",
            str(secrets),
            *extra,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


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


def test_transition_config_requires_an_explicit_reviewed_host_allowlist(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text(
        "\n".join(
            (
                'export ADVISORAI_ENVIRONMENT="paper_testnet"',
                'export ADVISORAI_VENUE_NAME="fixture"',
                'export ADVISORAI_VENUE_ENVIRONMENT="paper_testnet"',
                'export ADVISORAI_VENUE_BASE_URL="https://sandbox.example.test/api"',
                'export ADVISORAI_VENUE_API_KEY="fixture-key"',
                'export ADVISORAI_VENUE_API_SECRET="fixture-secret"',
            )
        )
        + "\n",
        encoding="utf-8",
    )

    missing = _run_config_check(secrets)
    assert "reviewed_venue_hosts=<none>" in missing.stdout
    assert "venue_readiness=configured_requires_reviewed_host_allowlist" in missing.stdout
    assert "fixture-secret" not in missing.stdout

    accepted = _run_config_check(secrets, "--venue-allowed-host", "sandbox.example.test")
    assert "reviewed_venue_hosts=sandbox.example.test" in accepted.stdout
    assert "venue_readiness=configured_reviewed_host_allowlisted" in accepted.stdout


def test_transition_config_rejects_a_non_hostname_allowlist(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text('export ADVISORAI_VENUE_ENVIRONMENT="paper_testnet"\n', encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_transition_config.py",
            "--secrets",
            str(secrets),
            "--venue-allowed-host",
            "https://sandbox.example.test",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert completed.returncode != 0
    assert "bare hostnames" in completed.stderr


def test_venue_smokes_resolve_adapter_hash_from_foreign_cwd(tmp_path):
    """P02 robustness: venue smokes hash the adapter from any cwd.

    Black-box: run both read-only smokes with cwd set to an empty
    dir and synthetic dummy credentials. The runs fail closed on
    the dummy credentials (structured verdict), but must never
    traceback on the adapter source path. Pre-fix both raised
    FileNotFoundError on the cwd-relative ``src/...`` read.
    """
    secrets = tmp_path / "secrets.env"
    secrets.write_text(
        'export ADVISORAI_VENUE_API_KEY="dummy"\n'
        'export ADVISORAI_VENUE_API_SECRET="ZHVtbXk="\n',
        encoding="utf-8",
    )
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    environment = os.environ.copy()
    environment["ADVISORAI_RUN_NETWORK_SMOKE"] = "1"
    root = Path(__file__).resolve().parents[2]
    for script in (
        "scripts/smoke_binance_spot_testnet.py",
        "scripts/smoke_coinbase_exchange_sandbox.py",
    ):
        completed = subprocess.run(
            [
                sys.executable,
                str(root / script),
                "--secrets",
                str(secrets),
                "--configuration-hash",
                "0" * 64,
                "--evidence-dir",
                str(tmp_path / "evidence"),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            cwd=workdir,
            timeout=60,
        )
        assert "integrations/binance_spot.py" not in completed.stderr, script
        assert "integrations/coinbase_exchange.py" not in completed.stderr, script
        assert "Traceback" not in completed.stderr, script
