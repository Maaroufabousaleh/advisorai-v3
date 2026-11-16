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


def test_subprocess_testnet_start_without_cap_refuses_before_secrets(tmp_path):
    """P02 owner-flow: testnet start without --max-submissions refuses fast.

    Black-box: a real ``advisorai.app start --venue-profile
    binance_spot_testnet`` child with no secrets file and no cap must
    exit 2 naming the missing cap — before reading secrets or touching
    the venue (no baseline print, no session start). The refusal also
    releases the owner lock, so an immediate retry is not blocked.
    """
    environment = _environment(tmp_path)
    assert not Path("secrets.env").exists() or True  # no reliance on repo secrets
    environment["ADVISORAI_SECRETS_FILE"] = str(tmp_path / "no-such-secrets.env")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "cap-refusal-probe",
            "start",
            "--venue-profile",
            "binance_spot_testnet",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert "--max-submissions is required" in run.stdout
    assert "testnet_baseline" not in run.stdout
    assert "SESSION READY" not in run.stdout
    assert "session_id=" not in run.stdout
    # Lock released: an immediate local-simulator start reaches READY.
    environment["ADVISORAI_STATE_DIR"] = str(tmp_path / "application-state")
    retry = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "cap-refusal-probe",
            "start",
            "--scenario",
            "quiet",
            "--interval-seconds",
            "3600",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _read_until(retry, "SESSION READY", timeout=60)
    finally:
        if retry.poll() is None:
            retry.terminate()
        retry.wait(timeout=10)


def test_subprocess_testnet_start_with_out_of_bounds_cap_refuses_before_secrets(
    tmp_path,
) -> None:
    """P02 owner-flow: out-of-bounds --max-submissions refuses fast.

    Black-box: a real child with ``--max-submissions -1`` or ``101``
    must exit 2 naming the bounds — before reading secrets or
    touching the venue. Mirrors the canary launcher's 0–100 bound
    so both owner entry points enforce the same contract.
    """
    environment = _environment(tmp_path)
    environment["ADVISORAI_SECRETS_FILE"] = str(tmp_path / "no-such-secrets.env")
    for cap in ("-1", "101"):
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "advisorai.app",
                "--lock-identity",
                "cap-bounds-probe",
                "start",
                "--venue-profile",
                "binance_spot_testnet",
                "--max-submissions",
                cap,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert run.returncode == 2, cap
        assert "--max-submissions must be between 0 and 100" in run.stdout, cap
        assert "testnet_baseline" not in run.stdout, cap
        assert "SESSION READY" not in run.stdout, cap
        assert "Traceback" not in run.stdout + run.stderr, cap


def test_subprocess_start_with_out_of_bounds_flags_refuses_cleanly(tmp_path) -> None:
    """P02 owner-flow: out-of-bounds start flags refuse at argparse.

    Black-box: a real child with an invalid --interval-seconds,
    --max-rss-mb, or --max-runtime-seconds must exit 2 naming the
    bound — before lock acquisition, so no SESSION START REFUSED
    and no traceback. Mirrors the canary launcher's exact bounds.
    """
    environment = _environment(tmp_path)
    cases = (
        (["--interval-seconds", "0.05"], "--interval-seconds must be between 0.1 and 86400"),
        (["--interval-seconds", "86401"], "--interval-seconds must be between 0.1 and 86400"),
        (["--max-rss-mb", "255"], "--max-rss-mb must be between 256 and 8192"),
        (["--max-rss-mb", "8193"], "--max-rss-mb must be between 256 and 8192"),
        (["--max-runtime-seconds", "0"], "--max-runtime-seconds must be between 1 and 86400"),
        (["--max-runtime-seconds", "86401"], "--max-runtime-seconds must be between 1 and 86400"),
    )
    for extra, message in cases:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "advisorai.app",
                "--lock-identity",
                "flag-bounds-probe",
                "start",
                "--scenario",
                "quiet",
                "--interval-seconds",
                "3600",
                *extra,
            ]
            if extra[0] != "--interval-seconds"
            else [
                sys.executable,
                "-m",
                "advisorai.app",
                "--lock-identity",
                "flag-bounds-probe",
                "start",
                "--scenario",
                "quiet",
                *extra,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert run.returncode == 2, extra
        assert message in run.stderr, extra
        assert "SESSION READY" not in run.stdout, extra
        assert "Traceback" not in run.stdout + run.stderr, extra


def test_subprocess_testnet_start_with_missing_secrets_refuses_cleanly(
    tmp_path,
) -> None:
    """P02 owner-flow: unreadable secrets refuse with SESSION START REFUSED.

    Black-box: a real child with valid bounds but a missing secrets
    file must exit 2 naming the failure — no traceback — and release
    the owner lock so an immediate retry is not blocked.
    """
    environment = _environment(tmp_path)
    environment["ADVISORAI_SECRETS_FILE"] = str(tmp_path / "no-such-secrets.env")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "secrets-refusal-probe",
            "start",
            "--venue-profile",
            "binance_spot_testnet",
            "--max-submissions",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert "SESSION START REFUSED: cannot read secrets file" in run.stdout
    assert "testnet_baseline" not in run.stdout
    assert "SESSION READY" not in run.stdout
    assert "Traceback" not in run.stdout + run.stderr
    # Lock released: an immediate local-simulator start reaches READY.
    environment["ADVISORAI_STATE_DIR"] = str(tmp_path / "application-state")
    retry = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "secrets-refusal-probe",
            "start",
            "--scenario",
            "quiet",
            "--interval-seconds",
            "3600",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _read_until(retry, "SESSION READY", timeout=60)
    finally:
        if retry.poll() is None:
            retry.terminate()
        retry.wait(timeout=10)


def test_subprocess_status_on_corrupt_ledger_refuses_cleanly(tmp_path) -> None:
    """P02 owner-flow: status on a damaged ledger refuses without traceback.

    Black-box: a real ``status`` child against a state dir whose
    ledger file is not a database must exit 2 with STATUS
    UNAVAILABLE — no traceback. Status is the first diagnostic an
    owner runs when something is wrong, so it must fail clean.
    """
    state_dir = tmp_path / "corrupt-state"
    state_dir.mkdir()
    (state_dir / "ledgers.sqlite3").write_bytes(b"not-a-sqlite-file")
    environment = _environment(tmp_path)
    environment["ADVISORAI_STATE_DIR"] = str(state_dir)
    environment["ADVISORAI_LEDGER_PATH"] = str(state_dir / "ledgers.sqlite3")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "status-corruption-probe",
            "status",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert "STATUS UNAVAILABLE" in run.stdout
    assert "Traceback" not in run.stdout + run.stderr


def test_subprocess_stop_on_corrupt_ledger_is_unverified_not_traceback(tmp_path) -> None:
    """P02 owner-flow: stop with a live owner but damaged ledger stays clean.

    Black-box: the owner record points at this live test process,
    so stop writes its request file, then finds the ledger
    unreadable and must exit 2 with STOP UNVERIFIED — no
    traceback. The request file must still exist (the live owner
    can act on it even though confirmation is impossible).
    """
    import json

    import psutil

    state_dir = tmp_path / "stop-corrupt-state"
    state_dir.mkdir()
    (state_dir / "ledgers.sqlite3").write_bytes(b"not-a-sqlite-file")
    session_id = str(uuid5(NAMESPACE_URL, "stop-corruption-probe"))
    me = psutil.Process()
    owner = {
        "session_id": session_id,
        "pid": me.pid,
        "process_create_time": me.create_time(),
        "lock_identity": "stop-corruption-probe",
    }
    (state_dir / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
    environment = _environment(tmp_path)
    environment["ADVISORAI_STATE_DIR"] = str(state_dir)
    environment["ADVISORAI_LEDGER_PATH"] = str(state_dir / "ledgers.sqlite3")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "stop-corruption-probe",
            "stop",
            "--outcome",
            "clean",
            "--timeout-seconds",
            "2",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert f"stop_requested={session_id}" in run.stdout
    assert "STOP UNVERIFIED" in run.stdout
    assert "Traceback" not in run.stdout + run.stderr
    assert (state_dir / f"stop-request-{session_id}.json").exists()


def test_subprocess_start_on_corrupt_ledger_refuses_cleanly(tmp_path) -> None:
    """P02 owner-flow: start on a damaged ledger refuses without traceback.

    Black-box: a real ``start`` child against a state dir whose
    ledger file is not a database must exit 2 with SESSION START
    REFUSED — no traceback, and no owner record written (the
    ledger open sits before lock acquisition, so nothing to
    release).
    """
    state_dir = tmp_path / "start-corrupt-state"
    state_dir.mkdir()
    (state_dir / "ledgers.sqlite3").write_bytes(b"not-a-sqlite-file")
    environment = _environment(tmp_path)
    environment["ADVISORAI_STATE_DIR"] = str(state_dir)
    environment["ADVISORAI_LEDGER_PATH"] = str(state_dir / "ledgers.sqlite3")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "start-corruption-probe",
            "start",
            "--reason",
            "corruption-probe",
            "--venue-profile",
            "local-simulator",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert "SESSION START REFUSED" in run.stdout
    assert "Traceback" not in run.stdout + run.stderr
    assert not (state_dir / "owner.json").exists()


def test_subprocess_backup_on_corrupt_ledger_refuses_cleanly(tmp_path) -> None:
    """P02 owner-flow: backup on a damaged ledger refuses without traceback.

    Black-box: both database files exist (so the missing-file
    refusal does not trigger) but the ledger is not a database.
    Backup must exit 2 with BACKUP REFUSED — no traceback, and
    no backup directory created.
    """
    state_dir = tmp_path / "backup-corrupt-state"
    state_dir.mkdir()
    (state_dir / "ledgers.sqlite3").write_bytes(b"not-a-sqlite-file")
    (state_dir / "scorecards.sqlite3").write_bytes(b"also-garbage")
    environment = _environment(tmp_path)
    environment["ADVISORAI_STATE_DIR"] = str(state_dir)
    environment["ADVISORAI_LEDGER_PATH"] = str(state_dir / "ledgers.sqlite3")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "backup-corruption-probe",
            "backup",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert "BACKUP REFUSED" in run.stdout
    assert "Traceback" not in run.stdout + run.stderr
    assert not (state_dir / "backups").exists()


def test_subprocess_testnet_start_with_wrong_instrument_refuses_cleanly(
    tmp_path,
) -> None:
    """P02 owner-flow: out-of-profile instrument refuses before secrets.

    Black-box: a real child with a wrong ``--venue-instrument`` must
    exit 2 with SESSION START REFUSED — no traceback — without
    touching the secrets file or the venue. The secrets path points
    at a missing file to prove the bounds check runs first; the
    refusal must name the instrument bound, not the missing file.
    """
    environment = _environment(tmp_path)
    environment["ADVISORAI_SECRETS_FILE"] = str(tmp_path / "no-such-secrets.env")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "advisorai.app",
            "--lock-identity",
            "instrument-refusal-probe",
            "start",
            "--venue-profile",
            "binance_spot_testnet",
            "--venue-instrument",
            "ETHUSDT",
            "--max-submissions",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert (
        "SESSION START REFUSED: binance_spot_testnet admits exactly BTCUSDT/USDT"
        in run.stdout
    )
    assert "cannot read secrets file" not in run.stdout
    assert "testnet_baseline" not in run.stdout
    assert "SESSION READY" not in run.stdout
    assert "Traceback" not in run.stdout + run.stderr


def test_app_start_refuses_out_of_profile_baseline_without_traceback(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 owner-flow: baseline-guard rejection refuses cleanly in-process.

    In-process (the black-box children cannot reach the baseline
    read without venue network): valid synthetic secrets, then the
    baseline read raises the exact guard ValueError. ``cmd_start``
    must exit 2 with SESSION START REFUSED surfacing the guard
    message — no traceback — and release the owner lock.
    """
    import argparse

    import advisorai.app as app_module

    secrets = tmp_path / "synthetic.env"
    secrets.write_text(
        "ADVISORAI_VENUE_API_KEY=synthetic-testnet-key\n"
        "ADVISORAI_VENUE_API_SECRET=synthetic-testnet-secret\n"
        "ADVISORAI_VENUE_NAME=binance_spot_testnet\n"
        "ADVISORAI_VENUE_ENVIRONMENT=testnet\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "guard-state"
    monkeypatch.setenv("ADVISORAI_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADVISORAI_LEDGER_PATH", str(state_dir / "ledgers.sqlite3"))

    def _refuse(*args, **kwargs):
        raise ValueError(
            "testnet account holds out-of-profile balances; clear them first"
        )

    import advisorai.session.testnet_launch as launch_module

    monkeypatch.setattr(launch_module, "read_testnet_baseline", _refuse)
    argv = argparse.Namespace(
        lock_identity="guard-refusal-probe",
        scenario="quiet",
        interval_seconds=10.0,
        accelerated_clock=False,
        max_rss_mb=1024,
        max_runtime_seconds=120.0,
        venue_profile="binance_spot_testnet",
        max_submissions=0,
        secrets=secrets,
        venue_instrument=None,
        venue_quote=None,
        print_baseline=False,
        reason="guard-probe",
    )
    assert app_module.cmd_start(argv) == 2
    captured = capsys.readouterr()
    assert "SESSION START REFUSED: testnet baseline refused" in captured.out
    assert "clear them first" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert not (state_dir / "owner.json").exists()
    # Lock released: the supervisor lock path is not held.
    from advisorai.session.supervisor import SessionSupervisor

    probe = SessionSupervisor(
        app_module.SqliteLedgers(app_module._ledger_path(state_dir)),
        state_dir=state_dir,
        lock_identity="guard-release-check",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    probe._acquire_lock()
    probe._release_lock()
