"""Black-box owner application lifecycle through real child processes."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.runtime import RuntimeCycle, RuntimeStage
from advisorai.session.application import PaperOwnerApplication

ROOT = Path(__file__).resolve().parents[2]


def test_private_owner_record_write_does_not_follow_a_symlink(tmp_path):
    target = tmp_path / "owner.json"
    protected = tmp_path / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    predictable_temp = tmp_path / f".{target.name}.{os.getpid()}.tmp"
    predictable_temp.symlink_to(protected)

    PaperOwnerApplication._write_private_json(target, {"session_id": "safe"})

    assert protected.read_text(encoding="utf-8") == "keep"
    assert target.read_text(encoding="utf-8") == '{"session_id": "safe"}\n'
    assert target.stat().st_mode & 0o777 == 0o600


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    state = tmp_path / "application-state"
    environment["ADVISORAI_STATE_DIR"] = str(state)
    environment["ADVISORAI_LEDGER_PATH"] = str(state / "ledgers.sqlite3")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(ROOT / "src"), environment.get("PYTHONPATH", "")) if item
    )
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return environment


def _start(environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "black-box-owner",
            "start",
            "--scenario",
            "qualification",
            "--interval-seconds",
            "0.2",
            "--accelerated-clock",
            "--max-rss-mb",
            "1024",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _read_until(process: subprocess.Popen[str], marker: str, timeout: float = 120) -> str:
    assert process.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            lines.extend(process.stdout.readlines())
            raise AssertionError(
                f"owner process exited {process.returncode} before {marker!r}: {''.join(lines)}"
            )
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if not ready:
            continue
        line = process.stdout.readline()
        if not line:
            continue
        lines.append(line)
        if marker in line:
            return "".join(lines)
    raise AssertionError(f"owner process did not emit {marker!r}: {''.join(lines)}")


def _wait_for(predicate, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for durable paper lifecycle evidence")


def _session_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("session_id="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"application output did not contain a session id: {output}")


def _stop(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "black-box-owner",
            "stop",
            "--timeout-seconds",
            "15",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_subprocess_start_paper_fill_outcome_stop_and_restart(tmp_path):
    environment = _environment(tmp_path)
    state = Path(environment["ADVISORAI_STATE_DIR"])
    ledgers = SqliteLedgers(Path(environment["ADVISORAI_LEDGER_PATH"]))
    first = _start(environment)
    try:
        ready = _read_until(first, "SESSION READY")
        assert "recovery_mode=normal" in ready

        # A second real process cannot acquire the owner lock while the first
        # remains alive; this tests the process boundary rather than objects.
        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "advisorai.app",
                "--lock-identity",
                "black-box-owner",
                "start",
                "--scenario",
                "qualification",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert second.returncode == 2
        assert "SESSION START REFUSED" in second.stdout
        assert first.poll() is None

        concurrent_backup = subprocess.run(
            [sys.executable, "-m", "advisorai.app", "--lock-identity", "black-box-owner", "backup"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert concurrent_backup.returncode == 2
        assert "BACKUP REFUSED" in concurrent_backup.stdout

        def complete_paper_chain() -> bool:
            orders = ledgers.events(LedgerNamespace.ORDER)
            venue = ledgers.events(LedgerNamespace.VENUE)
            model = ledgers.events(LedgerNamespace.MODEL)
            order_events = [event.event_type for event in orders]
            return (
                "order_reconciled" in order_events
                and any(event.event_type == "paper_fill_recorded" for event in venue)
                and any(
                    event.event_type == "reconciliation_recorded"
                    and event.payload.get("artifact", {}).get("reconciled") is True
                    for event in orders
                )
                and any(event.event_type == "paper_scorecard_recorded" for event in model)
            )

        _wait_for(complete_paper_chain)
        venue_before_stop = ledgers.events(LedgerNamespace.VENUE)
        fills_before_stop = [
            event for event in venue_before_stop if event.event_type == "paper_fill_recorded"
        ]
        assert fills_before_stop
        assert len({event.idempotency_key for event in fills_before_stop}) == len(fills_before_stop)

        stop_result = _stop(environment)
        assert stop_result.returncode == 0, stop_result.stdout + stop_result.stderr
        assert "outcome=clean" in stop_result.stdout
        assert first.wait(timeout=10) == 0

        mission = ledgers.events(LedgerNamespace.MISSION)
        first_end = [
            event
            for event in mission
            if event.event_type == "session_ended"
            and event.payload.get("end", {}).get("outcome") == "clean"
        ]
        assert first_end

        backup_result = subprocess.run(
            [sys.executable, "-m", "advisorai.app", "--lock-identity", "black-box-owner", "backup"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert backup_result.returncode == 0, backup_result.stdout + backup_result.stderr
        assert "backup_verified=true" in backup_result.stdout
        backup_path = next(
            Path(line.split("=", 1)[1])
            for line in backup_result.stdout.splitlines()
            if line.startswith("backup_path=")
        )

        restored_state = tmp_path / "restored-application-state"
        restore_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "advisorai.app",
                "--lock-identity",
                "black-box-owner",
                "restore",
                "--source",
                str(backup_path),
                "--destination",
                str(restored_state),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert restore_result.returncode == 0, restore_result.stdout + restore_result.stderr
        assert "restore_verified=true" in restore_result.stdout
        restored_environment = environment.copy()
        restored_environment["ADVISORAI_STATE_DIR"] = str(restored_state)
        restored_environment["ADVISORAI_LEDGER_PATH"] = str(restored_state / "ledgers.sqlite3")
        restored_ledgers = SqliteLedgers(Path(restored_environment["ADVISORAI_LEDGER_PATH"]))
        assert any(
            event.event_type == "paper_fill_recorded"
            for event in restored_ledgers.events(LedgerNamespace.VENUE)
        )

        restarted = _start(restored_environment)
        try:
            restart_output = _read_until(restarted, "SESSION READY")
            assert "classification=clean_start" in restart_output
            assert "recovery_mode=normal" in restart_output
            assert restarted.poll() is None
            stop_again = _stop(restored_environment)
            assert stop_again.returncode == 0, stop_again.stdout + stop_again.stderr
            assert restarted.wait(timeout=10) == 0
        finally:
            if restarted.poll() is None:
                restarted.terminate()
                restarted.wait(timeout=5)

        rollback_state = tmp_path / "rollback-application-state"
        rollback_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "advisorai.app",
                "--lock-identity",
                "black-box-owner",
                "rollback",
                "--source",
                str(backup_path),
                "--destination",
                str(rollback_state),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert rollback_result.returncode == 0, rollback_result.stdout + rollback_result.stderr
        assert (rollback_state / "restore-receipt.json").is_file()
        assert (
            len(
                [
                    event
                    for event in restored_ledgers.events(LedgerNamespace.MISSION)
                    if event.event_type == "session_started"
                ]
            )
            >= 2
        )
        assert backup_path.is_dir()

        # A process crash after completed paper activity must be classified as
        # crash recovery, reconcile the durable venue/account state, and allow
        # the next scheduled cycle to run without replaying old fills.
        crashed = _start(environment)
        try:
            crashed_output = _read_until(crashed, "SESSION READY")
            assert "classification=clean_start" in crashed_output
            crashed_session_id = _session_id(crashed_output)

            def crash_cycle_checkpointed() -> bool:
                events = ledgers.events(LedgerNamespace.MISSION)
                cycle_finished = any(
                    event.event_type == "paper_runtime_cycle"
                    and event.payload.get("cycle", {}).get("owner_session_id") == crashed_session_id
                    and event.payload.get("cycle", {}).get("stage") != "started"
                    for event in events
                )
                source_checkpointed = any(
                    event.event_type == "session_checkpoint"
                    and event.payload.get("checkpoint", {}).get("session_id") == crashed_session_id
                    and "local-paper-fixture/BTC-USD-1h"
                    in event.payload.get("checkpoint", {}).get("watermarks", {})
                    for event in events
                )
                return cycle_finished and source_checkpointed

            _wait_for(crash_cycle_checkpointed)
            crashed.kill()
            crashed.wait(timeout=10)

            # Model a crash after the scheduler journaled its next cycle but
            # before that cycle reached a terminal state. The production
            # runtime uses the same deterministic cycle identity and event.
            latest_checkpoint = next(
                event.payload["checkpoint"]
                for event in reversed(ledgers.events(LedgerNamespace.MISSION))
                if event.event_type == "session_checkpoint"
            )
            latest_watermark = latest_checkpoint["watermarks"]["local-paper-fixture/BTC-USD-1h"]
            interrupted_cutoff = datetime.fromisoformat(latest_watermark).astimezone(
                UTC
            ) + timedelta(hours=1)
            run_id = "advisorai-owner-paper"
            interrupted_cycle_id = uuid5(
                NAMESPACE_URL,
                f"advisorai-v3/runtime-cycle/{run_id}/{interrupted_cutoff.isoformat()}",
            )
            interrupted_cycle = RuntimeCycle(
                cycle_id=interrupted_cycle_id,
                run_id=run_id,
                owner_session_id=UUID(crashed_session_id),
                cutoff=interrupted_cutoff,
                stage=RuntimeStage.STARTED,
                created_at=interrupted_cutoff,
            )
            ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MISSION,
                    event_type="paper_runtime_cycle",
                    idempotency_key=f"runtime:{run_id}:{interrupted_cycle_id}:started",
                    occurred_at=interrupted_cutoff,
                    payload={"cycle": interrupted_cycle.model_dump(mode="json", round_trip=True)},
                )
            )

            recovered = _start(environment)
            try:
                recovery_output = _read_until(recovered, "SESSION READY")
                assert "classification=crash_recovery" in recovery_output
                assert "recovery_mode=recovery" in recovery_output
                assert "decision=reconcile_before_new_proposals" in recovery_output
                recovered_session_id = _session_id(recovery_output)

                def interruption_recorded() -> bool:
                    return any(
                        event.event_type == "paper_runtime_cycle"
                        and event.payload.get("cycle", {}).get("cycle_id")
                        == str(interrupted_cycle_id)
                        and event.payload.get("cycle", {}).get("reasons")
                        == ["interrupted_cycle_requires_recovery"]
                        for event in ledgers.events(LedgerNamespace.MISSION)
                    )

                _wait_for(interruption_recorded)
                _wait_for(
                    lambda: any(
                        event.event_type == "paper_runtime_cycle"
                        and event.payload.get("cycle", {}).get("owner_session_id")
                        == recovered_session_id
                        and event.payload.get("cycle", {}).get("cutoff")
                        > interrupted_cutoff.isoformat()
                        and event.payload.get("cycle", {}).get("stage") != "started"
                        for event in ledgers.events(LedgerNamespace.MISSION)
                    )
                )
                stop_after_recovery = _stop(environment)
                assert stop_after_recovery.returncode == 0, (
                    stop_after_recovery.stdout + stop_after_recovery.stderr
                )
                assert recovered.wait(timeout=10) == 0
            finally:
                if recovered.poll() is None:
                    recovered.terminate()
                    recovered.wait(timeout=5)
        finally:
            if crashed.poll() is None:
                crashed.kill()
                crashed.wait(timeout=5)

        venue_after_restart = ledgers.events(LedgerNamespace.VENUE)
        all_fills = [
            event for event in venue_after_restart if event.event_type == "paper_fill_recorded"
        ]
        assert len({event.idempotency_key for event in all_fills}) == len(all_fills)
        assert len(all_fills) >= len(fills_before_stop)
        assert not (state / "owner.json").exists()
    finally:
        if first.poll() is None:
            try:
                _stop(environment)
            finally:
                if first.poll() is None:
                    first.terminate()
                first.wait(timeout=5)
