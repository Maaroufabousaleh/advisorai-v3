#!/usr/bin/env python3
"""Compare a shadow model path against the deterministic baseline.

Offline research command. Runs the roster's deterministic ``linear`` forecaster
through the typed ``Forecast`` evidence path on the same frozen cutoffs,
windows, and costs as the DSL replay, and records shadow experiments. A
``--require-checkpoint`` run proves the quarantine path for weight-bearing
models without a pinned identity. No promotion, no orders, no admission
changes. State defaults under ``var/research-replay/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from advisorai.models.forecasting import (  # noqa: E402
    AbstentionPolicy,
    LinearForecaster,
    NaiveForecaster,
)
from advisorai.research import ResearchRegistry  # noqa: E402
from advisorai.research.shadow import run_shadow  # noqa: E402

MODELS = {"linear": LinearForecaster, "naive": NaiveForecaster}


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _environment_hash(command: str) -> str:
    identity = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "command": command,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", default="data/research-replay/snapshots/synthetic-daily-2015-2025-v1"
    )
    parser.add_argument("--state-dir", default="var/research-replay")
    parser.add_argument("--label", default=None)
    parser.add_argument("--model", default="linear", choices=sorted(MODELS))
    parser.add_argument("--symbols", nargs="*", default=["SPY", "TLT"])
    parser.add_argument(
        "--require-checkpoint",
        action="store_true",
        help="Require a pinned weight identity; expect quarantine for unpinned models.",
    )
    parser.add_argument(
        "--force-abstain",
        action="store_true",
        help="Use an impossible abstention bar to prove the abstention path.",
    )
    args = parser.parse_args()

    snapshot_root = (REPOSITORY_ROOT / args.snapshot).resolve()
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        state_dir = (REPOSITORY_ROOT / state_dir).resolve()
    if (
        state_dir == (REPOSITORY_ROOT / "var" / "advisorai").resolve()
        or (REPOSITORY_ROOT / "var" / "advisorai").resolve() in state_dir.parents
    ):
        raise ValueError("shadow state must stay outside var/advisorai")

    registry = ResearchRegistry(state_dir / "research-registry.sqlite3")
    policy = (
        AbstentionPolicy(max_scale=Decimal("0"), min_history=10**9) if args.force_abstain else None
    )
    report = run_shadow(
        registry=registry,
        snapshot_root=snapshot_root,
        symbols=tuple(args.symbols),
        model=MODELS[args.model](),
        environment_hash=_environment_hash("scripts/run_research_shadow.py"),
        abstention_policy=policy,
        require_checkpoint=args.require_checkpoint,
    )
    label = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_root = state_dir / "evidence" / label
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": report.schema_version,
        "label": label,
        "repository_commit": _git_head(REPOSITORY_ROOT),
        "command": "scripts/run_research_shadow.py",
        "model": args.model,
        "model_identity": report.model_identity,
        "reference_adapter": "naive (untouched)",
        "snapshot_hash": report.snapshot_hash,
        "code_hash": report.code_hash,
        "exposures": [
            {
                "symbol": item.symbol,
                "observations": item.observations,
                "abstentions": item.abstentions,
                "mae_bps": f"{item.mae_bps:.2f}",
                "directional_accuracy": f"{item.directional_accuracy:.4f}",
                "net_utility_bps": f"{item.net_utility_bps:.2f}",
                "baseline_net_bps": f"{item.baseline_net_bps:.2f}",
                "adds_marginal_value": item.adds_marginal_value,
                "latency_p50_ms": item.latency_p50_ms,
                "peak_ram_mib": item.peak_ram_mib,
                "quarantined": item.quarantined,
                "quarantine_reason": item.quarantine_reason,
                "experiment_id": str(item.experiment_id) if item.experiment_id else None,
            }
            for item in report.exposures
        ],
    }
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (evidence_root / "manifest.json").write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    print(
        f"model={args.model} quarantined_any={any(i.quarantined for i in report.exposures)} "
        f"manifest_sha256={digest}"
    )
    for item in report.exposures:
        print(
            f"{item.symbol}: obs={item.observations} abst={item.abstentions} "
            f"net={item.net_utility_bps:.2f}bps baseline={item.baseline_net_bps:.2f}bps "
            f"marginal={item.adds_marginal_value} p50={item.latency_p50_ms}ms "
            f"ram={item.peak_ram_mib}MiB quarantined={item.quarantined}"
        )
    print(f"evidence: {evidence_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
