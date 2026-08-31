from __future__ import annotations

import fcntl
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

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


def test_launch_entrypoint_rejects_only_at_or_after_the_frozen_start() -> None:
    module = _load_script(
        "phase4_longrun_launch_entrypoint_test",
        "launch_phase4_v3core_longrun.py",
    )
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    module._require_future_launch_window(start - timedelta(microseconds=1), start)
    with pytest.raises(ValueError, match="start window was missed"):
        module._require_future_launch_window(start, start)
    with pytest.raises(ValueError, match="start window was missed"):
        module._require_future_launch_window(start + timedelta(seconds=1), start)


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
