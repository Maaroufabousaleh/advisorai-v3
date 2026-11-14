#!/usr/bin/env python3
"""Verify the research replay inside the Hermes sandbox (Phase-8-gated).

Offline research command. Requires an admitted Phase 8 gate in the provided
ledger-backed gate registry; without it the command refuses before spawning
anything. The bounded task recomputes replay accounting from the frozen bars
file and compares obs/trades/net/baseline per exposure against the live replay
run with CLI costs. An optional ``--enable-openbb`` flag runs the FRED gap
evaluation (no network, no install, no credentials). No promotion, no orders,
no admission changes. State defaults under ``var/research-replay/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from advisorai.gates import PhaseGateRegistry  # noqa: E402
from advisorai.ledger import SqliteLedgers  # noqa: E402
from advisorai.phase4.signal_policy import SignalPolicyFamily, SignalPolicySpec  # noqa: E402
from advisorai.research import ResearchRegistry  # noqa: E402
from advisorai.research.hermes_verify import (  # noqa: E402
    HERMES_SCHEMA_VERSION,
    run_hermes_verification,
)
from advisorai.research.openbb_gap import evaluate_fred_gap, gap_manifest  # noqa: E402
from advisorai.research.replay import ExposureSpec, run_replay  # noqa: E402

POLICY_ID = "tsmom-sign-only-v1"
CLI_COSTS = {
    "SPY": (Decimal("1"), Decimal("1"), Decimal("2")),
    "TLT": (Decimal("1"), Decimal("2"), Decimal("2")),
}


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _state_dir(raw: str) -> Path:
    state_dir = Path(raw)
    if not state_dir.is_absolute():
        state_dir = (REPOSITORY_ROOT / state_dir).resolve()
    protected = (REPOSITORY_ROOT / "var" / "advisorai").resolve()
    if state_dir == protected or protected in state_dir.parents:
        raise ValueError("hermes state must stay outside var/advisorai")
    return state_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", default="data/research-replay/snapshots/etf-daily-2015-2025-v1"
    )
    parser.add_argument("--state-dir", default="var/research-replay")
    parser.add_argument("--label", default=None)
    parser.add_argument("--gate-ledger", default=None)
    parser.add_argument("--enable-openbb", action="store_true")
    args = parser.parse_args()

    snapshot_root = (REPOSITORY_ROOT / args.snapshot).resolve()
    state_dir = _state_dir(args.state_dir)
    gate_path = Path(args.gate_ledger) if args.gate_ledger else (state_dir / "phase-gates.sqlite3")
    gates = PhaseGateRegistry(SqliteLedgers(gate_path))
    verification = run_hermes_verification(snapshot_root=snapshot_root, gate_registry=gates)

    registry = ResearchRegistry(state_dir / "research-registry.sqlite3")
    replay = run_replay(
        registry=registry,
        snapshot_root=snapshot_root,
        exposures=tuple(
            ExposureSpec(symbol=symbol, fee_bps=fee, spread_bps=spread, slippage_bps=slip)
            for symbol, (fee, spread, slip) in CLI_COSTS.items()
        ),
        spec=SignalPolicySpec(policy_id=POLICY_ID, family=SignalPolicyFamily.SIGN_ONLY),
    )
    hermes_exposures = (verification.output or {}).get("exposures", {})
    comparisons = []
    for item in replay.exposures:
        other = hermes_exposures.get(item.symbol, {})
        comparisons.append(
            {
                "symbol": item.symbol,
                "observations_match": other.get("observations") == str(item.observations),
                "trades_match": other.get("trades") == str(item.trade_count),
                "net_match": other.get("net_bps") == f"{item.net_bps:.2f}",
                "baseline_match": other.get("baseline_net_bps") == f"{item.baseline_net_bps:.2f}",
                "replay": {
                    "observations": item.observations,
                    "trades": item.trade_count,
                    "net_bps": f"{item.net_bps:.2f}",
                    "baseline_net_bps": f"{item.baseline_net_bps:.2f}",
                },
                "hermes": other,
            }
        )
    all_match = verification.passed and all(
        item["observations_match"]
        and item["trades_match"]
        and item["net_match"]
        and item["baseline_match"]
        for item in comparisons
    )
    gap = gap_manifest(evaluate_fred_gap(enabled=True)) if args.enable_openbb else None
    label = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_root = state_dir / "evidence" / label
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": HERMES_SCHEMA_VERSION,
        "label": label,
        "repository_commit": _git_head(REPOSITORY_ROOT),
        "command": "scripts/run_research_hermes.py",
        "gate_ledger": str(gate_path),
        "phase8_admitted": gates.is_admitted(8),
        "hermes": {
            "task_name": verification.task_name,
            "policy_hash": verification.policy_hash,
            "passed": verification.passed,
            "output_hash": verification.output_hash,
            "elapsed_ms": verification.elapsed_ms,
            "peak_memory_mib": verification.peak_memory_mib,
            "error": verification.error,
            "snapshot_hash": (verification.output or {}).get("snapshot_hash"),
        },
        "replay": {
            "policy_id": replay.policy_id,
            "snapshot_hash": replay.snapshot_hash,
            "failed": list(replay.failed),
        },
        "comparisons": comparisons,
        "all_match": all_match,
        "openbb_gap": gap,
    }
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (evidence_root / "manifest.json").write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    print(f"passed={verification.passed} all_match={all_match} manifest_sha256={digest}")
    for item in comparisons:
        print(
            f"{item['symbol']}: obs={item['observations_match']} trades={item['trades_match']} "
            f"net={item['net_match']} baseline={item['baseline_match']} "
            f"replay_net={item['replay']['net_bps']} hermes_net={item['hermes'].get('net_bps')}"
        )
    if gap is not None:
        print(f"openbb_gap verdict: {gap['verdict']}")
    print(f"evidence: {evidence_root}")
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
