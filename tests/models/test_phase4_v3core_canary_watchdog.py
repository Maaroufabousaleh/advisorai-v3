from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import watch_phase4_v3core_canary as watchdog


def _status(*, state: str = "running", pid: int = 999999) -> dict[str, object]:
    return {
        "state": state,
        "pid": pid,
        "evidence_class": "PROSPECTIVE_CANARY_ONLY",
        "admission_eligible": False,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "finality": {"post_admission_revision_count": 0},
        "rejection_count": 0,
        "raw_response_count": 1,
        "admitted_final_bar_count": 1,
        "prediction_counts": {"BTCUSDT": 0, "ETHUSDT": 0},
        "warmup_state": "WARMUP_NOT_ELIGIBLE",
        "last_eligibility_status": "WARMUP_NOT_ELIGIBLE",
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _prepare_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    history = tmp_path / "watchdog"
    _write_json(source / "status.json", _status())
    _write_json(candidate / "status.json", _status())
    _write_json(source / "manifest.json", {"manifest": "source"})
    _write_json(candidate / "manifest.json", {"manifest": "candidate"})
    history.mkdir(parents=True)
    return source, candidate, history


def _patch_prereg(monkeypatch: pytest.MonkeyPatch, target_end: datetime) -> None:
    monkeypatch.setattr(
        watchdog,
        "load_canary_preregistration",
        lambda *_args, **_kwargs: SimpleNamespace(
            canary_id="test-canary", target_end_at=target_end
        ),
    )


def test_atomic_read_race_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "status.json"
    path.write_text('{"state":"running"}\n', encoding="utf-8")
    original = Path.read_bytes
    calls = 0

    def flaky_read(candidate: Path) -> bytes:
        nonlocal calls
        if candidate == path and calls == 0:
            calls += 1
            raise FileNotFoundError(path)
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", flaky_read)
    assert json.loads(watchdog._read_stable_bytes(path, retry_seconds=0)) == {"state": "running"}
    assert calls == 1


def test_genuine_missing_input_fails_with_exact_path(tmp_path: Path) -> None:
    missing = tmp_path / "source" / "status.json"
    with pytest.raises(watchdog.WatchdogInputError, match="status.json") as error:
        watchdog._read_stable_bytes(missing, attempts=2, retry_seconds=0)
    assert error.value.path == missing.resolve()
    assert error.value.detail == "missing"


def test_fatal_history_latches_and_blocks_later_healthy_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, candidate, history = _prepare_roots(tmp_path)
    _write_json(
        history / "status.json",
        {
            "decision": "CANARY_FAILED",
            "observed_at": "2026-08-24T00:00:00Z",
            "reasons": ["watchdog_input_error:read:/source/status.json:missing"],
        },
    )
    (history / "events.jsonl").write_text(
        json.dumps(
            {
                "decision": "CANARY_FAILED",
                "observed_at": "2026-08-24T00:00:00Z",
                "reasons": ["watchdog_input_error:read:/source/status.json:missing"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _patch_prereg(monkeypatch, datetime(2099, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(watchdog, "_pid_exists", lambda _pid: True)
    report = watchdog.evaluate_once(
        preregistration=tmp_path / "preregistration.json",
        preregistration_sha256="a" * 64,
        source_root=source,
        candidate_root=candidate,
        history_root=history,
    )
    assert report["decision"] == "CANARY_FAILED"
    assert report["fatal_latched"] is True
    assert any("historical_watchdog_failure" in reason for reason in report["reasons"])


def test_process_death_before_deadline_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, candidate, history = _prepare_roots(tmp_path)
    _patch_prereg(monkeypatch, datetime(2099, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(watchdog, "_pid_exists", lambda _pid: False)
    report = watchdog.evaluate_once(
        preregistration=tmp_path / "preregistration.json",
        preregistration_sha256="a" * 64,
        source_root=source,
        candidate_root=candidate,
        history_root=history,
    )
    assert report["decision"] == "CANARY_FAILED"
    assert "source_process_missing_before_deadline" in report["reasons"]
    assert "candidate_process_missing_before_deadline" in report["reasons"]
