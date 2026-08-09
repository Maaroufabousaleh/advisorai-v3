import json
from datetime import UTC, datetime, timedelta

import pytest

from advisorai.phase0.remote_stability import (
    append_record,
    make_record,
    read_records,
    summarize_records,
)
from scripts.run_remote_route_stability import _attest_config


def _record(
    *,
    run_id: str = "remote-test",
    sequence: int = 0,
    previous: str | None = None,
    config_sha256: str = "a" * 64,
):
    return make_record(
        run_id=run_id,
        sequence=sequence,
        sampled_at=datetime(2026, 8, 9, 16, sequence, tzinfo=UTC),
        identity_key="openrouter:novita:inclusionai/ling-2.6-flash-20260421",
        passed=True,
        probe={"status": "measured", "tool_execution_status": "not_executed"},
        previous_record_hash=previous,
        config_sha256=config_sha256,
    )


def test_remote_records_are_hash_chained_and_append_only(tmp_path):
    path = tmp_path / "cycles.jsonl"
    first = _record()
    append_record(path, first)
    second = _record(sequence=1, previous=first["record_hash"])
    append_record(path, second)

    assert read_records(path) == (first, second)
    with pytest.raises(ValueError, match="sequence|hash chain"):
        append_record(path, first)

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["passed"] = False
    path.write_text("\n".join((json.dumps(tampered), lines[1])) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        read_records(path)


def test_remote_records_reject_configuration_drift(tmp_path):
    path = tmp_path / "cycles.jsonl"
    first = _record()
    second = _record(sequence=1, previous=first["record_hash"], config_sha256="b" * 64)
    append_record(path, first)

    with pytest.raises(ValueError, match="configuration hash"):
        append_record(path, second)


def test_config_attestation_rejects_mutation(tmp_path):
    config_path = tmp_path / "config.json"
    config_hash_path = tmp_path / "config.sha256"
    config_path.write_text('{"run_id":"immutable"}\n', encoding="utf-8")

    original = _attest_config(config_path, config_hash_path)
    assert len(original) == 64

    config_path.write_text('{"run_id":"changed"}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="config hash changed"):
        _attest_config(config_path, config_hash_path)


def test_remote_summary_cannot_pass_before_duration():
    started = datetime(2026, 8, 9, 16, tzinfo=UTC)
    first = _record()
    summary = summarize_records(
        run_id="remote-test",
        started_at=started,
        duration_hours=24,
        records=(first,),
        now=started + timedelta(hours=1),
    )

    assert summary["status"] == "short_smoke_complete"
    assert summary["all_cycles_passed"] is True
    assert summary["duration_gate_passed"] is False


def test_remote_summary_fails_identity_drift():
    started = datetime(2026, 8, 9, 16, tzinfo=UTC)
    first = _record()
    second = make_record(
        run_id="remote-test",
        sequence=1,
        sampled_at=started + timedelta(minutes=5),
        identity_key="openrouter:digitalocean:deepseek/deepseek-v4-flash-20260423",
        passed=True,
        probe={"status": "measured"},
        previous_record_hash=first["record_hash"],
        config_sha256=first["config_sha256"],
    )
    summary = summarize_records(
        run_id="remote-test",
        started_at=started,
        duration_hours=24,
        records=(first, second),
        now=started + timedelta(hours=25),
    )

    assert summary["identity_stable"] is False
    assert summary["status"] == "failed"
