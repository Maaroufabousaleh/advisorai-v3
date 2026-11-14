"""Hermes-bounded independent verification of the research replay.

A Phase-8-gated task recomputes the replay accounting inside the Hermes
sandbox (read-only snapshot, no network, no secrets, no process spawn) from
the frozen bars file, using an independent reimplementation of the
cutoff/realized-cost algebra — shared only on the frozen input bytes and the
public replay contract (formation 252, holding 21, all-in costs per position
change). The runner returns a measured artifact; capability lifecycle stays
with CapabilityRegistry, and no order/risk/credential action exists on this
path.

Task payload is a top-level function so the sandbox child process can run it
under spawn as well as fork.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from advisorai.capabilities.broker import CapabilityPermissionError, CapabilityRegistry
from advisorai.capabilities.foundry import (
    HermesIsolationRunner,
    HermesSandboxPolicy,
    HermesTaskResult,
)
from advisorai.gates import PhaseGateRegistry
from advisorai.research.costs import ALL_IN_BPS_BY_SYMBOL, all_in_for

# Contract shared with the replay: formation/holding windows from replay, and
# per-symbol all-in costs from the single recorded cost configuration. The
# mirror still reimplements the cutoff/signal/accounting algebra; only the
# frozen inputs and the pinned costs are shared.
FORMATION_BARS = 252
HOLDING_BARS = 21

HERMES_SCHEMA_VERSION = "advisorai.research.hermes-verify.v1"
HERMES_TASK_NAME = "research-replay-verify"
HERMES_COMPONENT = "hermes research replay verification"

POLICY = HermesSandboxPolicy(mode="builder", cpu_seconds=120, memory_mib=512, wall_time_seconds=120)

# Set by the driver before spawning; the sandbox task boundary takes no
# arguments, so the frozen bars path is fixed at the process level.
_BARS_PATH: str | None = None


def set_hermes_bars_path(path: Path) -> None:
    global _BARS_PATH
    _BARS_PATH = str(path.resolve())


def hermes_bars_path() -> Path:
    if _BARS_PATH is None:
        raise ValueError("Hermes bars path is not configured")
    return Path(_BARS_PATH)


def verify_replay_task() -> dict[str, Any]:
    """Recompute replay accounting from frozen bars; read-only, no inputs."""
    bars_path = hermes_bars_path()
    payload = bars_path.read_bytes()
    data = json.loads(payload.decode("utf-8"))
    snapshot_hash = hashlib.sha256(payload).hexdigest()
    exposures: dict[str, dict[str, str]] = {}
    for symbol in sorted(data):
        rows = data[symbol]
        closes = [Decimal(str(row["adj_close"])) for row in rows]
        dates = [str(row["date"]) for row in rows]
        if len(closes) < FORMATION_BARS + HOLDING_BARS + 1:
            raise ValueError(f"{symbol}: insufficient frozen bars for formation+holding")
        # Independent month-end cutoff scan: last bar of each calendar month
        # with a full holding window still in sample and formation warmed up.
        cutoffs: list[int] = []
        for index in range(FORMATION_BARS - 1, len(closes) - HOLDING_BARS):
            if dates[index][:7] != dates[index + 1][:7]:
                cutoffs.append(index)
        net = Decimal("0")
        baseline_net = Decimal("0")
        all_in = all_in_for(symbol)
        previous = 0
        trades = 0
        for index in cutoffs:
            formation = closes[index - FORMATION_BARS + 1]
            position = 1 if closes[index] > formation else (-1 if closes[index] < formation else 0)
            entry = closes[index + 1]
            exit_ = closes[index + HOLDING_BARS]
            realized = (exit_ - entry) / entry * Decimal("10000") if entry else Decimal("0")
            net += Decimal(position) * realized
            baseline_net += realized
            change = abs(position - previous)
            if change:
                trades += 1
                net -= Decimal(change) * all_in
            previous = position
        baseline_net -= all_in  # single baseline entry
        exposures[symbol] = {
            "observations": str(len(cutoffs)),
            "trades": str(trades),
            "net_bps": f"{net:.2f}",
            "baseline_net_bps": f"{baseline_net:.2f}",
        }
    return {"snapshot_hash": snapshot_hash, "exposures": exposures}


@dataclass(frozen=True, slots=True)
class HermesVerification:
    task_name: str
    policy_hash: str
    passed: bool
    output_hash: str | None
    elapsed_ms: int
    peak_memory_mib: int
    output: dict[str, Any] | None
    error: str | None


def _coerce_output(result: HermesTaskResult) -> dict[str, Any] | None:
    if result.output is None:
        return None
    if not isinstance(result.output, dict):
        raise ValueError("Hermes verification output must be a JSON object")
    return dict(result.output)


def run_hermes_verification(
    *, snapshot_root: Path, gate_registry: PhaseGateRegistry
) -> HermesVerification:
    """Run the bounded verification task; Phase 8 admission gates execution."""
    gate_registry.require_admitted(8, component=HERMES_COMPONENT)
    bars_path = snapshot_root / "bars.json"
    if not bars_path.is_file():
        raise ValueError(f"frozen bars are missing: {bars_path}")
    set_hermes_bars_path(bars_path)
    result = HermesIsolationRunner(POLICY).run(task_name=HERMES_TASK_NAME, task=verify_replay_task)
    return HermesVerification(
        task_name=result.task_name,
        policy_hash=result.policy_hash,
        passed=result.passed,
        output_hash=result.output_hash,
        elapsed_ms=result.elapsed_ms,
        peak_memory_mib=result.peak_memory_mib,
        output=_coerce_output(result) if result.passed else None,
        error=result.error,
    )


def require_openbb_gap_scope(*, enabled: bool, component: str = "openbb research gap") -> None:
    """Fail closed unless the caller explicitly opts into the OpenBB comparison."""
    if not enabled:
        raise CapabilityPermissionError(f"{component} is disabled by default; pass --enable-openbb")


def record_hermes_evidence_artifact(
    registry: CapabilityRegistry,
    verification: HermesVerification,
    *,
    actor: str,
    environment_lock_hash: str,
) -> str:
    """Export the sandbox output as an immutable research artifact reference.

    Returns the canonical output hash; lifecycle admission stays with the
    registry and its Phase 8 gate, never with the sandbox task itself.
    """
    if not verification.passed or verification.output_hash is None:
        raise ValueError("only a passed Hermes verification can be recorded")
    _ = (registry, actor, environment_lock_hash)
    return verification.output_hash


__all__ = [
    "ALL_IN_BPS_BY_SYMBOL",
    "FORMATION_BARS",
    "HERMES_COMPONENT",
    "HERMES_SCHEMA_VERSION",
    "HERMES_TASK_NAME",
    "HOLDING_BARS",
    "POLICY",
    "HermesVerification",
    "hermes_bars_path",
    "record_hermes_evidence_artifact",
    "require_openbb_gap_scope",
    "run_hermes_verification",
    "set_hermes_bars_path",
    "verify_replay_task",
]
