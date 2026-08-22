from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.phase4.shutdown_readiness import ProcessInspection, evaluate_shutdown_readiness

TARGET_END = datetime(2026, 8, 22, 19, 35, 6, 869338, tzinfo=UTC)
AFTER_TARGET = TARGET_END + timedelta(seconds=1)
BEFORE_TARGET = TARGET_END - timedelta(seconds=1)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _build_roots(
    tmp_path: Path, *, source_state: str = "target_reached"
) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    resource = tmp_path / "resource"
    candidate = tmp_path / "candidate"
    source.mkdir()
    resource.mkdir()
    candidate.mkdir()

    source_security = {"credentials_loaded": False, "order_writes_attempted": False}
    source_manifest = {
        "target_end_at": TARGET_END.isoformat(),
        **source_security,
    }
    source_status = {
        "state": source_state,
        "target_end_at": TARGET_END.isoformat(),
        "pid": 101,
        "minimum_reached": True,
        **source_security,
    }
    source_summary = {
        "state": source_state,
        "target_end_at": TARGET_END.isoformat(),
        **source_security,
    }
    _write_json(source / "manifest.json", source_manifest)
    _write_json(source / "status.json", source_status)
    _write_json(source / "summary.json", source_summary)
    _write_json(source / "heartbeat.json", {"state": source_state})
    for name in (
        "raw-responses.jsonl",
        "normalized-bars.jsonl",
        "completed-cases.jsonl",
        "case-rejections.jsonl",
        "failures.jsonl",
        "source-health.jsonl",
    ):
        (source / name).touch()
    (source / "collector.lock").touch()
    (source / "collector.pid").write_text("101\n", encoding="utf-8")

    resource_security = {"credentials_loaded": False, "order_writes_attempted": False}
    _write_json(
        resource / "config.json",
        {"until": TARGET_END.isoformat(), **resource_security},
    )
    _write_json(resource / "status.json", {"state": "target_exited", **resource_security})
    _write_json(resource / "summary.json", {"state": "target_exited", **resource_security})
    _write_json(resource / "heartbeat.json", {"state": "target_exited"})
    (resource / "observations.jsonl").touch()
    (resource / "resource-sidecar.pid").write_text("202\n", encoding="utf-8")

    candidate_security = {"credentials_loaded": False, "order_writes_attempted": False}
    _write_json(
        candidate / "manifest.json", {"target_end_at": TARGET_END.isoformat(), **candidate_security}
    )
    _write_json(
        candidate / "status.json",
        {"state": "deadline_reached", "pid": 303, **candidate_security},
    )
    (candidate / "rejections.jsonl").touch()
    return source, resource, candidate


def _dead_process(_pid: int) -> ProcessInspection:
    return ProcessInspection(alive=False)


def test_safe_only_after_deadline_and_terminal_roots(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path)

    before = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=BEFORE_TARGET,
        process_probe=_dead_process,
    )
    assert before.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert any(reason.startswith("clock_before_target_end") for reason in before.reasons)

    after = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=_dead_process,
    )
    assert after.decision == "SAFE_TO_SHUT_DOWN"
    assert after.reasons == ()


def test_running_root_fails_closed_even_after_deadline(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path, source_state="running")
    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=_dead_process,
    )
    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "source_state_not_terminal:'running'" in result.reasons


def test_live_exact_process_fails_closed(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path)

    def live(pid: int) -> ProcessInspection:
        if pid == 303:
            return ProcessInspection(
                alive=True,
                command_line=(
                    "python",
                    "run_phase4_v3core_chronos_predictions.py",
                    str(candidate),
                ),
            )
        return ProcessInspection(alive=False)

    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=live,
    )
    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "candidate_process_active:303" in result.reasons


def test_candidate_status_running_is_unsafe_even_when_pid_is_absent(
    tmp_path: Path,
) -> None:
    source, resource, candidate = _build_roots(tmp_path)
    status = json.loads((candidate / "status.json").read_text(encoding="utf-8"))
    status["state"] = "running"
    _write_json(candidate / "status.json", status)

    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=_dead_process,
    )

    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "candidate_state_not_terminal:'running'" in result.reasons


def test_pid_reuse_or_command_mismatch_fails_closed(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path)

    def reused(pid: int) -> ProcessInspection:
        if pid == 303:
            return ProcessInspection(alive=True, command_line=("unrelated", "process"))
        return ProcessInspection(alive=False)

    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=reused,
    )
    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "candidate_pid_303_reused_or_command_mismatch" in result.reasons


@pytest.mark.parametrize("field", ["credentials_loaded", "order_writes_attempted"])
def test_security_flag_violation_fails_closed(tmp_path: Path, field: str) -> None:
    source, resource, candidate = _build_roots(tmp_path)
    status = json.loads((source / "status.json").read_text(encoding="utf-8"))
    status[field] = True
    _write_json(source / "status.json", status)

    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=_dead_process,
    )
    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert f"source_status.json_{field}_not_false" in result.reasons


def test_missing_lock_or_temporary_file_fails_closed(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path)
    (source / "collector.lock").unlink()
    (source / ".status.json.tmp").touch()

    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=_dead_process,
    )
    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "source_missing_file:collector.lock" in result.reasons
    assert "source_lock_missing" in result.reasons
    assert "source_temporary_file:.status.json.tmp" in result.reasons


def test_nonterminal_resource_sidecar_fails_closed(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path)
    status = json.loads((resource / "status.json").read_text(encoding="utf-8"))
    status["state"] = "running"
    _write_json(resource / "status.json", status)

    result = evaluate_shutdown_readiness(
        source_root=source,
        resource_root=resource,
        candidate_root=candidate,
        now=AFTER_TARGET,
        process_probe=_dead_process,
    )

    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "resource_state_not_terminal:'running'" in result.reasons


def test_active_collector_lock_fails_closed(tmp_path: Path) -> None:
    source, resource, candidate = _build_roots(tmp_path)
    with (source / "collector.lock").open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = evaluate_shutdown_readiness(
                source_root=source,
                resource_root=resource,
                candidate_root=candidate,
                now=AFTER_TARGET,
                process_probe=_dead_process,
            )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert result.decision == "NOT_SAFE_TO_SHUT_DOWN"
    assert "source_lock_owned" in result.reasons
