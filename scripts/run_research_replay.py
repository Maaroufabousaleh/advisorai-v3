#!/usr/bin/env python3
"""Replay the preregistered time-series-momentum factor on frozen ETF inputs.

Offline research command. Reads one frozen snapshot, replays the versioned
DSL factor against a buy-and-hold baseline separately for each exposure, and
writes a manifest plus registry records. No network, no credentials, no
orders, no admission changes.

State stays separate from the trading checkout: registry and evidence default
under ``var/research-replay/`` (override with ``--state-dir``) and never touch
``var/advisorai/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from advisorai.phase4.signal_policy import (  # noqa: E402
    SignalPolicyFamily,
    SignalPolicySpec,
)
from advisorai.research import (  # noqa: E402
    HypothesisCard,
    PaperCard,
    ResearchRegistry,
    ResearchSourceUse,
)
from advisorai.research.replay import (  # noqa: E402
    ExposureSpec,
    run_replay,
)

PAPER_SOURCE_URL = "https://doi.org/10.1016/j.jfineco.2011.11.003"
HYPOTHESIS_OWNER = "omp-research-replay-v1"

DEFAULT_EXPOSURES = (
    # (symbol, fee_bps, spread_bps, slippage_bps): liquid-ETF conservative costs.
    ("SPY", Decimal("1"), Decimal("1"), Decimal("2")),
    ("TLT", Decimal("1"), Decimal("2"), Decimal("2")),
)


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _environment_hash() -> str:
    import platform

    identity = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "command": "scripts/run_research_replay.py",
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _write_new(path: Path, payload: object) -> str:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
    return hashlib.sha256(encoded).hexdigest()


def _exposures(args: argparse.Namespace) -> tuple[ExposureSpec, ...]:
    wanted = {item.upper() for item in args.exposures} if args.exposures else None
    specs = []
    for symbol, fee, spread, slippage in DEFAULT_EXPOSURES:
        if wanted is not None and symbol not in wanted:
            continue
        specs.append(
            ExposureSpec(symbol=symbol, fee_bps=fee, spread_bps=spread, slippage_bps=slippage)
        )
    if wanted:
        unknown = wanted - {item.symbol for item in specs}
        if unknown:
            raise ValueError(f"unknown exposures requested: {', '.join(sorted(unknown))}")
    return tuple(specs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        default="data/research-replay/snapshots/etf-daily-2015-2025-v1",
        help="Frozen snapshot directory (bars.json + manifest.json).",
    )
    parser.add_argument(
        "--state-dir",
        default="var/research-replay",
        help="Isolated state root (registry + evidence). Must not be var/advisorai.",
    )
    parser.add_argument(
        "--exposures",
        nargs="*",
        default=None,
        help="Subset of exposures to replay (default: all in snapshot spec).",
    )
    parser.add_argument(
        "--policy", default="tsmom-sign-only-v1", help="Signal policy identity for the manifest."
    )
    parser.add_argument(
        "--label", default=None, help="Evidence run label (default: UTC timestamp)."
    )
    args = parser.parse_args()

    repository_root = REPOSITORY_ROOT
    snapshot_root = (repository_root / args.snapshot).resolve()
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        state_dir = (repository_root / state_dir).resolve()
    if (
        state_dir == (repository_root / "var" / "advisorai").resolve()
        or (repository_root / "var" / "advisorai").resolve() in state_dir.parents
    ):
        raise ValueError("research replay state must stay outside var/advisorai")
    if not (snapshot_root / "bars.json").exists():
        raise FileNotFoundError(f"snapshot bars missing: {snapshot_root / 'bars.json'}")

    registry = ResearchRegistry(state_dir / "research-registry.sqlite3")
    paper = registry.store(
        PaperCard(
            source_url=PAPER_SOURCE_URL,
            title="Time series momentum",
            authors=("Tobias J. Moskowitz", "Yao Hua Ooi", "Lasse Heje Pedersen"),
            publication_date="2012-05",
            license="Elsevier TDM; version-of-record CC-BY-NC-ND after embargo",
            claimed_result=(
                "Past 12-month excess return predicts next-month return across "
                "58 equity, bond, currency, and commodity instruments"
            ),
            limitations=(
                "futures-based study; ETF replication differs in costs and trading hours",
                "citation-only intake; method restated from the published abstract and tables",
            ),
            relevance="cross-asset time-series momentum: SPY equity and TLT bond exposures",
            requested_use=ResearchSourceUse.CITATION_ONLY,
        ),
        record_type="paper",
    )
    hypothesis = registry.store(
        HypothesisCard(
            economic_rationale=(
                "Slow diffusion of macro news and risk-capacity constraints produce "
                "continuation in broad equity and bond index returns at a 12-month "
                "formation, 1-month holding horizon"
            ),
            universe="SPY,TLT",
            horizon="252d formation, 21d holding, month-end rebalance",
            point_in_time_inputs=("adj_close-vendor-adjusted-v1",),
            baseline="buy-and-hold on the same month-end windows",
            preregistered_plan=(
                "research replay v1: screen 2015-2019, validation 2020-2022, "
                "holdout 2023-2025; sign-only policy; per-exposure costs"
            ),
            failure_modes=(
                "momentum reversal in stress months",
                "bond trend breakdown under rate shocks",
                "costs exceed edge on low-volatility exposures",
            ),
            owner=HYPOTHESIS_OWNER,
            budget="offline CPU only; no API spend; one trial",
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            paper_card_ids=(paper.artifact_id,),
        ),
        record_type="hypothesis",
    )
    registry.link(paper.artifact_id, hypothesis.artifact_id, "paper_supports_hypothesis")

    spec = SignalPolicySpec(policy_id=args.policy, family=SignalPolicyFamily.SIGN_ONLY)
    report = run_replay(
        registry=registry,
        snapshot_root=snapshot_root,
        exposures=_exposures(args),
        spec=spec,
        hypothesis_id=hypothesis.artifact_id,
        environment_hash=_environment_hash(),
    )
    label = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_root = state_dir / "evidence" / label
    manifest = {
        "schema": report.schema_version,
        "label": label,
        "repository_commit": _git_head(repository_root),
        "command": "scripts/run_research_replay.py",
        "policy_id": report.policy_id,
        "factor_hash": report.factor_hash,
        "snapshot": str(snapshot_root.relative_to(repository_root)),
        "snapshot_hash": report.snapshot_hash,
        "code_hash": report.code_hash,
        "paper_artifact_id": str(paper.artifact_id),
        "hypothesis_artifact_id": str(hypothesis.artifact_id),
        "exposures": [
            {
                "symbol": item.symbol,
                "observations": item.observations,
                "trade_count": item.trade_count,
                "gross_bps": f"{item.gross_bps:.2f}",
                "costs_bps": f"{item.costs_bps:.2f}",
                "net_bps": f"{item.net_bps:.2f}",
                "baseline_net_bps": f"{item.baseline_net_bps:.2f}",
                "directional_accuracy": (
                    f"{item.directional_accuracy:.4f}"
                    if item.directional_accuracy is not None
                    else "n/a"
                ),
                "max_drawdown_bps": f"{item.max_drawdown_bps:.2f}",
                "experiment_artifact_id": str(item.experiment_id),
                "regime_slices": [
                    {"regime": name, "observations": count, "net_bps": value}
                    for name, count, value in item.regime_slices
                ],
            }
            for item in report.exposures
        ],
        "failed": list(report.failed),
        "registry": str((state_dir / "research-registry.sqlite3").relative_to(repository_root))
        if state_dir.is_relative_to(repository_root)
        else str(state_dir / "research-registry.sqlite3"),
    }
    manifest_hash = _write_new(evidence_root / "manifest.json", manifest)
    print(
        f"exposures={len(report.exposures)} failed={len(report.failed)} manifest_sha256={manifest_hash}"
    )
    for item in report.exposures:
        print(
            f"{item.symbol}: obs={item.observations} trades={item.trade_count} "
            f"net={item.net_bps:.2f}bps baseline={item.baseline_net_bps:.2f}bps "
            f"experiment={item.experiment_id}"
        )
    for failure in report.failed:
        print(f"FAILED {failure}")
    if "PYTEST_CURRENT_TEST" not in os.environ:
        print(f"evidence: {evidence_root}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
