from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER = REPOSITORY_ROOT / "scripts/schedule_phase4_v3core_canary.sh"


def _fake_python(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env bash
set -u
script=$(basename "$1")
shift
output_root=
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--output-root" ]; then
        output_root=$2
        shift 2
    else
        shift
    fi
done
mkdir -p "$output_root"
if [ "$script" = "watch_phase4_v3core_canary.py" ]; then
    case "${FAKE_MODE:-pass}" in
        pass)
            printf '%s\\n' '{"decision":"CANARY_COMPLETE_PENDING_AUDIT","source_state":"deadline_reached","candidate_state":"deadline_reached"}' > "$output_root/status.json"
            ;;
        fail)
            printf '%s\\n' '{"decision":"CANARY_FAILED","source_state":"running","candidate_state":"CANARY_FAILED"}' > "$output_root/status.json"
            ;;
        missing)
            ;;
        malformed)
            printf '%s\\n' 'not-json' > "$output_root/status.json"
            ;;
    esac
    exit 0
fi
if [ "$script" = "audit_phase4_v3core_canary.py" ]; then
    printf '%s\\n' '{}' > "$output_root/canary-terminal-audit.json"
    exit 0
fi
exit 99
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run_scheduler(tmp_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("jq") is None:
        pytest.skip("jq is required by the canonical scheduler")
    fake_python = _fake_python(tmp_path / "fake-python")
    monitor = tmp_path / "monitor"
    result = subprocess.run(
        [
            "bash",
            str(SCHEDULER),
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--run-root",
            str(tmp_path / "run"),
            "--monitor-root",
            str(monitor),
            "--preregistration",
            str(tmp_path / "prereg.json"),
            "--preregistration-sha256",
            "a" * 64,
            "--phase3-gate-sha256",
            "b" * 64,
            "--python",
            str(fake_python),
            "--checkpoint-at",
            "2000-01-01T00:00:00Z",
            "--terminal-check-at",
            "2000-01-01T00:00:00Z",
            "--recheck-sleep-seconds",
            "0.01",
        ],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "FAKE_MODE": mode},
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result


def test_scheduler_uses_wall_clock_guard_and_authoritative_status_file() -> None:
    source = SCHEDULER.read_text(encoding="utf-8")
    assert "wait_until_utc()" in source
    assert 'if [ "$now_ns" -ge "$target_ns" ]' in source
    assert 'sleep "$RECHECK_SLEEP_SECONDS"' in source
    assert '"$output_root/status.json"' in source
    assert '--history-root "$WATCHDOG_ROOT"' in source
    assert "status_value()" in source
    assert "CHECKPOINT_DECISION=$checkpoint_decision" in source


def test_scheduler_parses_pass_status_and_runs_terminal_audit(tmp_path: Path) -> None:
    result = _run_scheduler(tmp_path, "pass")
    assert result.returncode == 0, result.stderr
    events = (tmp_path / "monitor" / "scheduler-events.log").read_text(encoding="utf-8")
    assert "CHECKPOINT_DECISION=CANARY_COMPLETE_PENDING_AUDIT" in events
    assert "TERMINAL_STATE_CONFIRMED" in events
    assert (tmp_path / "monitor" / "terminal-audit" / "canary-terminal-audit.json").is_file()


def test_scheduler_preserves_fail_decision_without_treating_it_as_healthy(
    tmp_path: Path,
) -> None:
    result = _run_scheduler(tmp_path, "fail")
    assert result.returncode == 0, result.stderr
    events = (tmp_path / "monitor" / "scheduler-events.log").read_text(encoding="utf-8")
    assert "CHECKPOINT_DECISION=CANARY_FAILED" in events
    assert "CANARY_NOT_TERMINAL" in events


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_scheduler_fails_closed_for_missing_or_malformed_status(tmp_path: Path, mode: str) -> None:
    result = _run_scheduler(tmp_path, mode)
    assert result.returncode != 0
    checkpoint = tmp_path / "monitor" / "checkpoint-1"
    assert (checkpoint / "status-error").is_file()
    assert not (tmp_path / "monitor" / "terminal-audit" / "canary-terminal-audit.json").exists()


def test_scheduler_script_is_shell_valid() -> None:
    result = subprocess.run(["bash", "-n", str(SCHEDULER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
