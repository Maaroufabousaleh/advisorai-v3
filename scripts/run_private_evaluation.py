#!/usr/bin/env python3
"""Independent evaluation on the private snapshot (read-only).

One command. Reads the private bars read-only, evaluates momentum +
LightGBM + naive + linear + buy-and-hold on identical cutoffs and pinned
costs, proves LightGBM label observability, and records per-cutoff model
hashes. Writes evidence only under the given state dir; never writes to
the snapshot.

Short/financing disclosure and the already-viewed 2023-2025 caveat are
recorded in the manifest. A rejection is a valid outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from advisorai.research.evaluate import (  # noqa: E402
    EVAL_SCHEMA_VERSION,
    SHORT_DISCLOSURE,
    evaluate_exposure,
    prove_labels_observable,
)
from advisorai.research.lightgbm_runner import lightgbm_dependency_version  # noqa: E402
from advisorai.research.replay import load_snapshot_bars  # noqa: E402


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _metrics(m) -> dict[str, str]:
    return {
        "observations": str(m.observations),
        "trades": str(m.trades),
        "gross_bps": f"{m.gross_bps:.2f}",
        "costs_bps": f"{m.costs_bps:.2f}",
        "net_bps": f"{m.net_bps:.2f}",
        "drawdown_bps": f"{m.drawdown_bps:.2f}",
        "accuracy": f"{m.accuracy:.4f}" if m.accuracy is not None else "n/a",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Private snapshot directory (read-only).")
    parser.add_argument("--state-dir", required=True, help="Local evidence dir (outside the repo).")
    parser.add_argument("--label", default=None)
    parser.add_argument("--symbols", nargs="*", default=["SPY", "TLT"])
    parser.add_argument(
        "--cost-multipliers", nargs="*", default=["1", "2"], help="Cost sensitivity grid."
    )
    args = parser.parse_args()

    snapshot_root = Path(args.snapshot).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser()
    state_dir = state_dir if state_dir.is_absolute() else (REPOSITORY_ROOT / state_dir).resolve()
    if (
        state_dir == (REPOSITORY_ROOT / "var" / "advisorai").resolve()
        or (REPOSITORY_ROOT / "var" / "advisorai").resolve() in state_dir.parents
    ):
        raise ValueError("evaluation state must stay outside var/advisorai")
    if not (snapshot_root / "bars.json").is_file():
        raise FileNotFoundError(f"snapshot bars missing: {snapshot_root / 'bars.json'}")
    bars, snapshot_hash = load_snapshot_bars(snapshot_root)
    try:
        lightgbm_version = lightgbm_dependency_version()
    except Exception as exc:  # noqa: BLE001 - recorded, LightGBM abstains cleanly
        lightgbm_version = f"unavailable: {exc}"

    from datetime import UTC, datetime

    label = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    multipliers = tuple(Decimal(value) for value in args.cost_multipliers)
    exposures = []
    for symbol in args.symbols:
        if symbol not in bars:
            raise ValueError(f"snapshot lacks exposure {symbol}")
        proof = prove_labels_observable(symbol, bars[symbol])
        evaluation = evaluate_exposure(
            symbol, bars[symbol], lightgbm_factory=True, cost_multipliers=multipliers
        )
        model_hashes = sorted(
            {item.lightgbm_model_hash for item in evaluation.forecasts if item.lightgbm_model_hash}
        )
        abstentions = sum(1 for item in evaluation.forecasts if item.lightgbm_abstained)
        holdout = {path: _metrics(metrics) for path, metrics in evaluation.holdout.items()}
        verdict = (
            "ACCEPT"
            if Decimal(holdout["momentum"]["net_bps"]) > Decimal(holdout["buy_and_hold"]["net_bps"])
            and Decimal(holdout["lightgbm"]["net_bps"])
            > Decimal(holdout["buy_and_hold"]["net_bps"])
            else "REJECT"
        )
        exposures.append(
            {
                "symbol": symbol,
                "all_in_bps": str(evaluation.all_in_bps),
                "label_proof": proof,
                "lightgbm_model_hashes": len(model_hashes),
                "lightgbm_abstentions": abstentions,
                "lightgbm_model_map": [
                    {
                        "cutoff": item.cutoff_date,
                        "model_hash": (item.lightgbm_model_hash or "abstained")[:16],
                    }
                    for item in evaluation.forecasts
                ],
                "holdout_viewed_caveat": (
                    "2023-2025 results were already viewed during development; "
                    "historical replay, not prospective evidence; no tuning performed"
                ),
                "holdout": holdout,
                "full": {path: _metrics(metrics) for path, metrics in evaluation.full.items()},
                "cost_sensitivity": evaluation.cost_sensitivity,
                "verdict": verdict,
            }
        )
    evidence_root = state_dir / "evidence" / label
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": EVAL_SCHEMA_VERSION,
        "label": label,
        "repository_commit": _git_head(REPOSITORY_ROOT),
        "command": "scripts/run_private_evaluation.py",
        "snapshot_hash": snapshot_hash,
        "lightgbm_dependency": lightgbm_version,
        "short_disclosure": SHORT_DISCLOSURE,
        "exposures": exposures,
    }
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (evidence_root / "manifest.json").write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    print(f"input={snapshot_hash[:16]} manifest_sha256={digest}")
    for item in exposures:
        holdout = item["holdout"]
        print(f"== {item['symbol']} verdict={item['verdict']} ==")
        for path in ("momentum", "lightgbm", "naive", "linear", "buy_and_hold"):
            metrics = holdout[path]
            print(
                f"  {path}: n={metrics['observations']} trades={metrics['trades']} "
                f"net={metrics['net_bps']}bps dd={metrics['drawdown_bps']}bps "
                f"acc={metrics['accuracy']}"
            )
        print(f"  cost_sensitivity(momentum): {item['cost_sensitivity']['momentum']}")
        print(
            f"  lightgbm: {item['lightgbm_model_hashes']} models, "
            f"{item['lightgbm_abstentions']} abstentions"
        )
    print(f"evidence: {evidence_root}")
    print("caveat: 2023-2025 holdout already viewed; historical replay, not prospective.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
