"""Record-day script: fixture chain, missed day, restart, live refusal."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from advisorai.research.btc_shadow_log import bar_close_ms

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_shadow_record_day.py"
HOUR_MS = 3_600_000


def _load():
    spec = importlib.util.spec_from_file_location("run_shadow_record_day", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["run_shadow_record_day.py", *argv])
    module.main()


def _report(state: Path) -> dict:
    return json.loads((state / "report.json").read_text())


def test_fixture_view_is_deterministic(tmp_path):
    module = _load()
    _, _, first = module.build_rehearsal_view(tmp_path / "a")
    _, _, second = module.build_rehearsal_view(tmp_path / "b")
    assert first == second


def test_two_run_chain_records_missed_day_and_links_prior(tmp_path, monkeypatch):
    module = _load()
    first = tmp_path / "run1"
    _run(
        module,
        monkeypatch,
        "--mode",
        "rehearsal",
        "--dates",
        "2024-03-05",
        "--recorded-at-ms",
        str(bar_close_ms("2024-03-05") + 2 * HOUR_MS),
        "--state-dir",
        str(first),
    )
    report1 = _report(first)
    assert report1["processed"] == ["2024-03-05"]
    assert report1["quarantined"] == []
    assert "prior_report_sha256" not in report1

    # No run records March 6: the next run catches it up past its cutoff.
    second = tmp_path / "run2"
    _run(
        module,
        monkeypatch,
        "--mode",
        "rehearsal",
        "--dates",
        "2024-03-06,2024-03-07",
        "--recorded-at-ms",
        str(bar_close_ms("2024-03-07") + 2 * HOUR_MS),
        "--state-dir",
        str(second),
        "--prior-state-dir",
        str(first),
    )
    report2 = _report(second)
    assert report2["processed"] == ["2024-03-06", "2024-03-07"]
    assert report2["quarantined"] == ["2024-03-06"]
    assert "prior_report_sha256" in report2
    decisions = {
        json.loads(line)["date"]: json.loads(line)
        for line in (second / "decisions.jsonl").read_text().splitlines()
    }
    assert decisions["2024-03-06"]["missed"] is True
    assert decisions["2024-03-07"]["missed"] is False
    assert decisions["2024-03-07"]["signal"] == 1
    # Prior bytes preserved exactly (append-only across the hosted handoff).
    assert (
        (second / "decisions.jsonl")
        .read_bytes()
        .startswith((first / "decisions.jsonl").read_bytes())
    )


def test_rerun_with_same_prior_is_idempotent(tmp_path, monkeypatch):
    module = _load()
    first = tmp_path / "run1"
    _run(
        module,
        monkeypatch,
        "--mode",
        "rehearsal",
        "--dates",
        "2024-03-05",
        "--recorded-at-ms",
        str(bar_close_ms("2024-03-05") + 2 * HOUR_MS),
        "--state-dir",
        str(first),
    )
    args = [
        "--mode",
        "rehearsal",
        "--dates",
        "2024-03-06,2024-03-07",
        "--recorded-at-ms",
        str(bar_close_ms("2024-03-07") + 2 * HOUR_MS),
    ]
    second = tmp_path / "run2"
    _run(module, monkeypatch, *args, "--state-dir", str(second), "--prior-state-dir", str(first))
    rerun = tmp_path / "rerun"
    _run(module, monkeypatch, *args, "--state-dir", str(rerun), "--prior-state-dir", str(first))
    for name in ("decisions.jsonl", "fills.jsonl", "shadow_state.json"):
        assert (rerun / name).read_bytes() == (second / name).read_bytes()
    assert _report(rerun)["processed"] == ["2024-03-06", "2024-03-07"]


def test_tampered_prior_fails_closed(tmp_path, monkeypatch):
    module = _load()
    first = tmp_path / "run1"
    _run(
        module,
        monkeypatch,
        "--mode",
        "rehearsal",
        "--dates",
        "2024-03-05",
        "--recorded-at-ms",
        str(bar_close_ms("2024-03-05") + 2 * HOUR_MS),
        "--state-dir",
        str(first),
    )
    with (first / "decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"forged": true}\n')
    with pytest.raises(ValueError, match="hash mismatch"):
        _run(
            module,
            monkeypatch,
            "--mode",
            "rehearsal",
            "--dates",
            "2024-03-06",
            "--recorded-at-ms",
            str(bar_close_ms("2024-03-06") + 2 * HOUR_MS),
            "--state-dir",
            str(tmp_path / "run2"),
            "--prior-state-dir",
            str(first),
        )


def test_live_mode_refuses_without_flag_and_arm(tmp_path, monkeypatch):
    module = _load()
    base = [
        "--mode",
        "live",
        "--dates",
        "2024-03-05",
        "--recorded-at-ms",
        str(bar_close_ms("2024-03-05") + 2 * HOUR_MS),
        "--state-dir",
        str(tmp_path / "live"),
        "--snapshot-dir",
        str(tmp_path / "snap"),
        "--forward-dir",
        str(tmp_path / "fwd"),
        "--receipts",
        str(tmp_path / "receipts.json"),
    ]
    monkeypatch.delenv("BTC_SHADOW_LIVE_ARMED", raising=False)
    with pytest.raises(ValueError, match="not armed"):
        _run(module, monkeypatch, *base)
    with pytest.raises(ValueError, match="not armed"):
        _run(module, monkeypatch, *base, "--allow-live")
    monkeypatch.setenv("BTC_SHADOW_LIVE_ARMED", "true")
    with pytest.raises(ValueError, match="not armed"):
        _run(module, monkeypatch, *base)
    with pytest.raises(ValueError, match="rehearsal mode takes no --allow-live"):
        _run(
            module,
            monkeypatch,
            "--mode",
            "rehearsal",
            "--dates",
            "2024-03-05",
            "--recorded-at-ms",
            str(bar_close_ms("2024-03-05") + 2 * HOUR_MS),
            "--state-dir",
            str(tmp_path / "rehearsal"),
            "--allow-live",
        )
