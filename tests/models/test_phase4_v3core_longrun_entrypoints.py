from __future__ import annotations

import fcntl
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]


def _load_script(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_watchdog_recheck_recognizes_its_own_persisted_identity(monkeypatch) -> None:
    module = _load_script(
        "phase4_longrun_watchdog_entrypoint_test",
        "watch_phase4_v3core_longrun.py",
    )
    pid = 12345
    command = ["/python", "watch_phase4_v3core_longrun.py", "--once"]
    monkeypatch.setattr(module.os, "getpid", lambda: pid)
    monkeypatch.setattr(module, "process_create_time", lambda value: 17.25)
    monkeypatch.setattr(module.sys, "executable", command[0])
    monkeypatch.setattr(module.sys, "argv", command[1:])
    status = {
        "watchdog_pid": pid,
        "process_create_time": 17.25,
        "command": command,
        "command_identity": module.process_command_identity(command),
    }
    assert module._same_watchdog_process(status) is True
    status["command_identity"] = "a" * 64
    assert module._same_watchdog_process(status) is False


def test_watchdog_identity_helper_rejects_a_takeover(monkeypatch) -> None:
    module = _load_script(
        "phase4_longrun_watchdog_takeover_test",
        "watch_phase4_v3core_longrun.py",
    )
    monkeypatch.setattr(module.os, "getpid", lambda: 12345)
    monkeypatch.setattr(module, "process_create_time", lambda value: 17.25)
    status = {
        "watchdog_pid": 12346,
        "process_create_time": 17.25,
        "command": ["/python", "watch_phase4_v3core_longrun.py"],
        "command_identity": "a" * 64,
    }
    assert module._same_watchdog_process(status) is False


def test_watchdog_public_runner_rejects_a_competing_instance(monkeypatch, tmp_path) -> None:
    module = _load_script(
        "phase4_longrun_watchdog_lock_test",
        "watch_phase4_v3core_longrun.py",
    )
    sentinel = {"decision": "LONG_RUN_HEALTHY"}
    monkeypatch.setattr(module, "_run_unlocked", lambda **_kwargs: sentinel)
    watchdog_root = tmp_path / "watchdog"
    watchdog_root.mkdir()
    lock_path = watchdog_root / "watchdog.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="another long-run watchdog"):
            module.run(watchdog_root=watchdog_root)
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert module.run(watchdog_root=watchdog_root) == sentinel


def test_launch_entrypoint_accepts_only_the_frozen_post_start_window() -> None:
    module = _load_script(
        "phase4_longrun_launch_entrypoint_test",
        "launch_phase4_v3core_longrun.py",
    )
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    end = start + timedelta(seconds=60)
    with pytest.raises(ValueError, match="has not opened"):
        module._require_launch_window(
            start - timedelta(microseconds=1),
            launch_not_before_at=start,
            launch_not_after_at=end,
        )
    module._require_launch_window(
        start,
        launch_not_before_at=start,
        launch_not_after_at=end,
    )
    module._require_launch_window(
        end,
        launch_not_before_at=start,
        launch_not_after_at=end,
    )
    with pytest.raises(ValueError, match="start window was missed"):
        module._require_launch_window(
            end + timedelta(microseconds=1),
            launch_not_before_at=start,
            launch_not_after_at=end,
        )


def test_launch_entrypoint_refuses_legacy_preregistration_without_window() -> None:
    module = _load_script(
        "phase4_longrun_legacy_launch_entrypoint_test",
        "launch_phase4_v3core_longrun.py",
    )
    with pytest.raises(ValueError, match="no post-start launch window"):
        module._require_launch_window(
            datetime(2026, 9, 1, 12, tzinfo=UTC),
            launch_not_before_at=None,
            launch_not_after_at=None,
        )


def test_launch_entrypoint_emits_structured_exact_refusal(monkeypatch, capsys) -> None:
    module = _load_script(
        "phase4_longrun_structured_refusal_test",
        "launch_phase4_v3core_longrun.py",
    )
    monkeypatch.setattr(
        module,
        "launch",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("checkpoint identity mismatch")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "launch_phase4_v3core_longrun.py",
            "--launch",
            "--preregistration",
            "prereg.json",
            "--preregistration-sha256",
            "a" * 64,
            "--readiness-report",
            "readiness.json",
            "--evidence-root",
            "evidence",
            "--admission",
            "admission.json",
            "--qualification-evidence",
            "qualification.json",
            "--requirements-lock",
            "requirements.lock",
            "--checkpoint",
            "checkpoint",
            "--phase3-gate",
            "phase3.json",
            "--model-runtime-qualification",
            "runtime.json",
        ],
    )
    assert module.main() == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["state"] == "LONGRUN_NOT_LAUNCHED_PREFLIGHT_FAILED"
    assert refusal["error_type"] == "ValueError"
    assert refusal["reason"] == "checkpoint identity mismatch"


def test_long_run_launch_wires_the_scheduler_to_the_outcome_root(tmp_path) -> None:
    module = _load_script(
        "phase4_longrun_launch_command_construction_test",
        "launch_phase4_v3core_longrun.py",
    )
    outcome_root = tmp_path / "outcomes"
    commands = module._component_commands(
        repository_root=tmp_path / "repo",
        preregistration_path=tmp_path / "prereg.json",
        preregistration_sha256="a" * 64,
        source_root=tmp_path / "source",
        candidate_root=tmp_path / "candidate",
        outcome_root=outcome_root,
        coordinator_root=tmp_path / "coordinator",
        watchdog_root=tmp_path / "watchdog",
        scheduler_root=tmp_path / "scheduler",
        audit_root=tmp_path / "audit",
        admission_path=tmp_path / "admission.json",
        qualification_evidence_path=tmp_path / "qualification.json",
    )
    scheduler = commands["scheduler"]
    assert scheduler[scheduler.index("--outcome-root") + 1] == str(outcome_root)
    assert "--outcome-root" not in commands["collector"]


def test_long_run_preregistration_builder_does_not_duplicate_rule_hash_arguments(
    monkeypatch, tmp_path
) -> None:
    module = _load_script(
        "phase4_longrun_preregistration_builder_test",
        "preregister_phase4_v3core_longrun.py",
    )
    component_names = (
        "finality_rule_sha256",
        "context_rule_sha256",
        "preprocessing_sha256",
        "long_run_contract_code_sha256",
        "forward_contract_code_sha256",
        "cadence_contract_code_sha256",
        "collector_code_sha256",
        "candidate_worker_code_sha256",
        "outcome_linker_code_sha256",
        "watchdog_code_sha256",
        "auditor_code_sha256",
        "scheduler_code_sha256",
        "coordinator_code_sha256",
        "launcher_code_sha256",
        "launch_gate_code_sha256",
    )
    component_files = {}
    for name in component_names:
        path = tmp_path / name
        path.write_bytes(name.encode())
        component_files[name] = path
    monkeypatch.setattr(module, "long_run_component_files", lambda _root: component_files)
    phase3 = tmp_path / "phase3.json"
    runtime = tmp_path / "runtime.json"
    phase3.write_text("{}", encoding="utf-8")
    runtime.write_text("{}", encoding="utf-8")
    real_sha256_file = module.sha256_file

    def qualified_sha256_file(path: Path) -> str:
        if path == phase3:
            return "4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232"
        if path == runtime:
            return "e04dc75df9bebd79f623ea32a8e815f7f15ad92cb0e343edf098ad577c896289"
        return real_sha256_file(path)

    monkeypatch.setattr(module, "sha256_file", qualified_sha256_file)
    start = datetime(2030, 1, 1, tzinfo=UTC)
    contract = module.build_preregistration(
        repository_root=ROOT,
        generation_id="builder-regression",
        branch_or_tag="test",
        start_at=start,
        created_at=start - timedelta(hours=1),
        terminal_deadline=datetime(2030, 1, 4, 14, tzinfo=UTC),
        terminal_check_at=datetime(2030, 1, 4, 14, 5, tzinfo=UTC),
        model_runtime_qualification_path=runtime,
        phase3_gate_path=phase3,
    )
    assert contract.finality_rule_sha256 == sha256(b"finality_rule_sha256").hexdigest()
    assert contract.context_rule_sha256 == sha256(b"context_rule_sha256").hexdigest()
    assert contract.launch_not_before_at == start
    assert contract.launch_not_after_at == start + timedelta(seconds=60)
    assert contract.launch_gate_code_sha256 == sha256(b"launch_gate_code_sha256").hexdigest()


def _gate_preregistration(start: datetime):
    return SimpleNamespace(
        generation_id="synthetic-gate",
        repository_commit="a" * 40,
        start_at=start,
        launch_not_before_at=start,
        launch_not_after_at=start + timedelta(seconds=60),
        launch_gate_code_sha256="b" * 64,
    )


def _arm_gate(
    module,
    monkeypatch,
    tmp_path,
    *,
    now_values: list[datetime],
    launch_stdout: str = '{"state":"RUNNING"}',
    launch_returncode: int = 0,
):
    start = datetime(2030, 1, 1, tzinfo=UTC)
    preregistration = _gate_preregistration(start)
    readiness = SimpleNamespace(
        decision="LONG_RUN_READY",
        long_run_ready=True,
        preregistration_sha256="c" * 64,
        checks=(SimpleNamespace(passed=True),),
    )
    monkeypatch.setattr(
        module, "load_long_run_preregistration", lambda *_args, **_kwargs: preregistration
    )
    monkeypatch.setattr(module, "sha256_file", lambda _path: "b" * 64)
    monkeypatch.setattr(module, "read_json_stable", lambda _path: {})
    monkeypatch.setattr(
        module,
        "LongRunLaunchReadinessReport",
        SimpleNamespace(model_validate=lambda _value: readiness),
    )
    monkeypatch.setattr(module, "_current_command", lambda: ["python", "gate.py", "--arm"])
    monkeypatch.setattr(module, "process_create_time", lambda _pid: 17.0)
    actions: list[object] = []
    observed = iter(now_values)

    def now() -> datetime:
        value = next(observed)
        actions.append(("now", value))
        return value

    def sleep(seconds: float) -> None:
        actions.append(("sleep", seconds))

    def run(command, **_kwargs):
        actions.append(("run", tuple(command)))
        return SimpleNamespace(returncode=launch_returncode, stdout=launch_stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    result = module.arm(
        repository_root=tmp_path / "repo",
        preregistration_path=tmp_path / "prereg.json",
        preregistration_sha256="c" * 64,
        readiness_report_path=tmp_path / "readiness.json",
        evidence_root=tmp_path / "evidence",
        gate_root=tmp_path / "gate",
        admission_path=tmp_path / "admission.json",
        qualification_evidence_path=tmp_path / "qualification.json",
        requirements_lock_path=tmp_path / "requirements.lock",
        checkpoint_path=tmp_path / "checkpoint",
        phase3_gate_path=tmp_path / "phase3.json",
        model_runtime_qualification_path=tmp_path / "runtime.json",
        arm_requested=True,
        now=now,
        sleep=sleep,
    )
    return result, actions, tmp_path / "gate"


def test_detached_gate_never_invokes_launcher_before_frozen_start(monkeypatch, tmp_path) -> None:
    module = _load_script("phase4_longrun_gate_wait_test", "arm_phase4_v3core_longrun.py")
    start = datetime(2030, 1, 1, tzinfo=UTC)
    result, actions, gate_root = _arm_gate(
        module,
        monkeypatch,
        tmp_path,
        now_values=[start - timedelta(seconds=10), start - timedelta(seconds=10), start, start],
    )
    assert result["state"] == module.LAUNCHED_STATE
    assert [action[0] for action in actions].index("sleep") < [
        action[0] for action in actions
    ].index("run")
    events = [json.loads(line) for line in (gate_root / "events.jsonl").read_text().splitlines()]
    assert [event["state"] for event in events] == [
        module.PRESTART_STATE,
        "FINAL_LAUNCH_ATTESTATION",
        module.LAUNCHED_STATE,
    ]
    assert events[0]["observed_at"] < events[1]["observed_at"]


def test_detached_gate_records_missed_window_without_invoking_launcher(
    monkeypatch, tmp_path
) -> None:
    module = _load_script("phase4_longrun_gate_missed_test", "arm_phase4_v3core_longrun.py")
    start = datetime(2030, 1, 1, tzinfo=UTC)
    result, actions, _gate_root = _arm_gate(
        module,
        monkeypatch,
        tmp_path,
        now_values=[
            start - timedelta(seconds=10),
            start - timedelta(seconds=10),
            start + timedelta(seconds=61),
        ],
    )
    assert result["state"] == module.MISSED_WINDOW_STATE
    assert not any(action[0] == "run" for action in actions)


def test_detached_gate_malformed_launcher_output_fails_closed(monkeypatch, tmp_path) -> None:
    module = _load_script("phase4_longrun_gate_output_test", "arm_phase4_v3core_longrun.py")
    start = datetime(2030, 1, 1, tzinfo=UTC)
    result, _actions, gate_root = _arm_gate(
        module,
        monkeypatch,
        tmp_path,
        now_values=[start, start, start],
        launch_stdout="not-json",
    )
    assert result["state"] == module.PREFLIGHT_FAILED_STATE
    assert "JSONDecodeError" in result["detail"]
    assert (gate_root / "launch.stdout.log").read_text() == "not-json"


def test_detached_gate_preserves_exact_structured_launcher_refusal(monkeypatch, tmp_path) -> None:
    module = _load_script("phase4_longrun_gate_refusal_test", "arm_phase4_v3core_longrun.py")
    start = datetime(2030, 1, 1, tzinfo=UTC)
    refusal = {
        "state": module.PREFLIGHT_FAILED_STATE,
        "reason": "actual checkpoint identity differs from preregistration",
    }
    result, _actions, _gate_root = _arm_gate(
        module,
        monkeypatch,
        tmp_path,
        now_values=[start, start, start],
        launch_stdout=json.dumps(refusal),
        launch_returncode=2,
    )
    assert result["state"] == module.PREFLIGHT_FAILED_STATE
    assert refusal["reason"] in result["detail"]


def test_watchdog_rejects_an_ordinary_terminal_status_before_the_deadline() -> None:
    module = _load_script(
        "phase4_longrun_watchdog_terminal_timing_test",
        "watch_phase4_v3core_longrun.py",
    )
    deadline = datetime(2026, 9, 1, 16, tzinfo=UTC)
    reasons = module._check_process(
        {"state": "DEADLINE_REACHED"},
        component="candidate",
        now=deadline - timedelta(seconds=1),
        deadline=deadline,
    )
    assert reasons == ["candidate_terminated_before_deadline"]
    assert (
        module._check_process(
            {"state": "GENERATION_FATAL"},
            component="candidate",
            now=deadline - timedelta(seconds=1),
            deadline=deadline,
        )
        == []
    )
