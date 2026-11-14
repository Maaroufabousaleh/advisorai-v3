"""Hermes-bounded verification and the OpenBB gap evaluation.

Proves the Phase 8 gate refuses ungated runs, the sandbox recomputation
matches replay numerics exactly, isolation holds (no network/write/spawn,
no credential inheritance), OpenBB stays disabled by default, and the FRED
gap evaluation keeps vintages on the direct path with no new dependency.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.capabilities.broker import CapabilityPermissionError
from advisorai.capabilities.foundry import HermesIsolationRunner
from advisorai.gates import (
    GateDecision,
    GateEvidence,
    GateEvidenceKind,
    PhaseGateRecord,
    PhaseGateRegistry,
)
from advisorai.ledger import SqliteLedgers
from advisorai.phase4.signal_policy import SignalPolicyFamily, SignalPolicySpec
from advisorai.research import ResearchRegistry
from advisorai.research.costs import ALL_IN_BPS_BY_SYMBOL
from advisorai.research.hermes_verify import (
    POLICY,
    hermes_bars_path,
    require_openbb_gap_scope,
    run_hermes_verification,
    set_hermes_bars_path,
    verify_replay_task,
)
from advisorai.research.openbb_gap import (
    VINTAGED_FIXTURE,
    evaluate_fred_gap,
    gap_manifest,
)
from advisorai.research.replay import ExposureSpec, load_snapshot_bars, run_replay

SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research-replay"
    / "snapshots"
    / "synthetic-daily-2015-2025-v1"
)


def _bars(count: int = 300) -> tuple[dict, ...]:
    from datetime import date, timedelta

    base = date(2020, 1, 2)
    rows = []
    current = base
    added = 0
    while added < count:
        if current.weekday() < 5:
            price = Decimal(100 + added)
            rows.append(
                {
                    "date": current.isoformat(),
                    "open": price,
                    "high": price + Decimal("1"),
                    "low": price - Decimal("1"),
                    "adj_close": price,
                    "volume": 1000,
                }
            )
            added += 1
        current += timedelta(days=1)
    return tuple(rows)


def _admitted_gates(tmp_path: Path) -> PhaseGateRegistry:
    gates = PhaseGateRegistry(SqliteLedgers(tmp_path / "gates.sqlite3"))
    now = datetime.now(UTC)
    for phase in range(9):
        kind = GateEvidenceKind.EXTERNAL_TIMED if phase in (0, 7) else GateEvidenceKind.LOCAL_TEST
        evidence = GateEvidence(
            name=f"evidence-{phase}",
            kind=kind,
            passed=True,
            artifact_hash="ab" * 32,
            source=f"command-{phase}",
            verified_by="slice4-test",
            observed_at=now,
        )
        gates.record(
            PhaseGateRecord(
                phase=phase,
                name=f"phase-{phase}",
                decision=GateDecision.PASSED,
                required_evidence=(f"evidence-{phase}",),
                evidence=(evidence,),
                recorded_by="slice4-test",
            )
        )
    assert gates.is_admitted(8)
    return gates


def _write_snapshot(tmp_path: Path, symbols: tuple[str, ...] = ("SPY", "TLT")) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir(exist_ok=True)
    payload = {
        symbol: [
            {
                "date": row["date"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "adj_close": float(row["adj_close"]),
                "volume": row["volume"],
            }
            for row in _bars()
        ]
        for symbol in symbols
    }
    (root / "bars.json").write_text(json.dumps(payload))
    return root


def test_hermes_policy_is_read_only_without_network_or_secrets():
    assert POLICY.mode == "builder"
    assert POLICY.read_only_snapshot is True
    assert POLICY.allowed_network_hosts == ()
    assert POLICY.allowed_secrets == ()
    assert {"submit_order", "change_risk_limit"}.issubset(set(POLICY.forbidden_actions))


def test_verification_refuses_without_phase8_admission(tmp_path):
    gates = PhaseGateRegistry(SqliteLedgers(tmp_path / "gates.sqlite3"))
    with pytest.raises(PermissionError, match="admitted Phase 8"):
        run_hermes_verification(snapshot_root=_write_snapshot(tmp_path), gate_registry=gates)


def test_verification_recomputes_fixture_replay_exactly(tmp_path):
    from advisorai.research.synthetic import materialize as _materialize

    _materialize(SNAPSHOT)
    gates = _admitted_gates(tmp_path)
    verification = run_hermes_verification(snapshot_root=SNAPSHOT, gate_registry=gates)
    assert verification.passed
    assert verification.error is None
    assert verification.output_hash is not None
    replay = run_replay(
        registry=ResearchRegistry(tmp_path / "r.sqlite3"),
        snapshot_root=SNAPSHOT,
        exposures=(ExposureSpec(symbol="SPY"), ExposureSpec(symbol="TLT")),
        spec=SignalPolicySpec(policy_id="slice4", family=SignalPolicyFamily.SIGN_ONLY),
    )
    assert verification.output is not None
    assert verification.output["snapshot_hash"] == replay.snapshot_hash
    for item in replay.exposures:
        other = verification.output["exposures"][item.symbol]
        assert other["observations"] == str(item.observations)
        assert other["trades"] == str(item.trade_count)
        assert other["net_bps"] == f"{item.net_bps:.2f}"
        assert other["baseline_net_bps"] == f"{item.baseline_net_bps:.2f}"
    assert ALL_IN_BPS_BY_SYMBOL == {"SPY": Decimal("4"), "TLT": Decimal("5")}


def test_verification_cost_contract_guards_unknown_symbols(tmp_path):
    root = _write_snapshot(tmp_path, symbols=("SPY", "QQQ"))
    set_hermes_bars_path(root / "bars.json")
    with pytest.raises(ValueError, match="no pinned all-in cost"):
        verify_replay_task()


def test_bars_path_must_be_configured(monkeypatch):
    import advisorai.research.hermes_verify as module

    monkeypatch.setattr(module, "_BARS_PATH", None)
    with pytest.raises(ValueError, match="not configured"):
        hermes_bars_path()


def test_setter_rejects_non_snapshot_files_and_missing_paths(tmp_path):
    other = tmp_path / "other.json"
    other.write_text("{}")
    with pytest.raises(ValueError, match="snapshot bars.json"):
        set_hermes_bars_path(other)
    with pytest.raises((ValueError, OSError), match="bars.json|No such file"):
        set_hermes_bars_path(tmp_path / "missing-bars.json")


def test_sandbox_blocks_network_write_spawn_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "must-not-cross-boundary")
    runner = HermesIsolationRunner(POLICY)

    def attempt_network():
        import socket

        try:
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        except Exception as exc:
            return {"caught": type(exc).__name__}
        return {"caught": None}

    networked = runner.run(task_name="network-attempt", task=attempt_network)
    assert not networked.passed
    assert networked.network_access_attempted
    assert networked.output is None

    def attempt_write():
        Path("hermes-write-probe.txt").write_text("nope")
        return {"wrote": True}

    written = runner.run(task_name="write-attempt", task=attempt_write)
    assert not written.passed
    assert written.filesystem_write_attempted or written.error is not None

    def attempt_spawn():
        import subprocess

        try:
            subprocess.run(["true"], check=False)
        except Exception as exc:
            return {"caught": type(exc).__name__}
        return {"caught": None}

    spawned = runner.run(task_name="spawn-attempt", task=attempt_spawn)
    assert not spawned.passed
    assert spawned.process_spawn_attempted

    result = runner.run(
        task_name="secret-visibility",
        task=lambda: {"broker_key_visible": os.getenv("BROKER_API_KEY")},
    )
    assert result.passed
    assert result.output == {"broker_key_visible": None}


def test_openbb_gap_is_disabled_by_default():
    with pytest.raises(CapabilityPermissionError, match="disabled by default"):
        require_openbb_gap_scope(enabled=False)
    with pytest.raises(CapabilityPermissionError, match="disabled by default"):
        evaluate_fred_gap(enabled=False)


def test_fred_gap_keeps_vintages_without_new_dependency():
    report = evaluate_fred_gap(enabled=True)
    assert report.direct_observations == 2
    assert report.direct_vintages_kept is True
    assert report.direct_keyless_metadata is True
    assert report.openbb_credentialed is True
    assert report.openbb_independent_publisher is False
    assert "direct ALFRED" in report.verdict
    manifest = gap_manifest(report)
    assert manifest["direct"]["origin"] == "alfred"
    assert manifest["openbb"]["route"] == "obb.fred.economy.fred_series"
    assert VINTAGED_FIXTURE.count(b'"vintage"') == 2


def test_snapshot_loader_hash_covers_bytes(tmp_path):
    root = _write_snapshot(tmp_path)
    _, digest = load_snapshot_bars(root)
    assert len(digest) == 64
