#!/usr/bin/env python3
"""Complete research example: discovery → verdict on a skip-month variant.

Takes the MIT-licensed independent TSM replication
(gehlotmanthan/aqr-tsmom-replication) through the full workflow:

1. discovery: record the paper→code lead as an ImplementationRecord with a
   reasoned disposition (reimplement-from-spec: their 12-1 skip-month
   construction differs from our 12-0 baseline);
2. candidate: the supervised worker builds the skip-month DSL from the
   reviewed hypothesis (no code copied from the replication);
3. replay: independent replay of the new candidate on both exposures with
   identical windows, costs, and embargo enforcement;
4. verdict: recorded per exposure; rejection counts as success.

Offline except for pinned, already-reviewed reference metadata recorded in
the script. One command, separate results per exposure.
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

from advisorai.phase4.signal_policy import SignalPolicyFamily, SignalPolicySpec  # noqa: E402
from advisorai.research import ResearchRegistry  # noqa: E402
from advisorai.research import factors as dsl  # noqa: E402
from advisorai.research.cards import (  # noqa: E402
    HypothesisCard,
    ImplementationRelationship,  # noqa: E402
    PaperCard,
)
from advisorai.research.code_discovery import (  # noqa: E402
    build_implementation_record,
    classify_relationship,
)
from advisorai.research.replay import (  # noqa: E402
    ExposureSpec,
    build_observations,
    load_snapshot_bars,
)

# Pinned discovery reference (reviewed 2026-11-14 via api.github.com):
# independent MIT-licensed replication of Moskowitz-Ooi-Pedersen (2012)
# with committed performance outputs (Sharpe ~0.91, 1990-2025).
REPO_URL = "https://github.com/gehlotmanthan/aqr-tsmom-replication"
REPO_REVISION = "main@2026-06-16"
REPO_LICENSE = "MIT"
REPO_ENTRY = "python scripts/run_analysis.py (downloads AQR factor xlsx + Yahoo proxies)"
PAPER_URL = "https://doi.org/10.1016/j.jfineco.2011.11.003"

# Skip-month 12-1 variant, reimplemented from the paper specification:
# formation return ending 21 bars before the cutoff (their construction),
# vs our 12-0 baseline ending at the cutoff. No code copied from the repo.
SKIP_MONTH_DSL = json.dumps(
    {
        "op": "div",
        "args": [
            {
                "op": "sub",
                "args": [{"field": "adj_close_lag_21"}, {"field": "adj_close_lag_252"}],
            },
            {"field": "adj_close_lag_252"},
        ],
    },
    sort_keys=True,
)
SKIP_MONTH_MODEL = "tsmom-12m1-skip-sign-v1"


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _skip_month_values(
    bars: tuple[dict, ...], *, formation_bars: int = 252, skip_bars: int = 21
) -> tuple[Decimal, ...]:
    expression = dsl.parse(SKIP_MONTH_DSL)
    closes = tuple(bar["adj_close"] for bar in bars)
    lag_21 = tuple(closes[max(0, i - skip_bars)] for i in range(len(closes)))
    lag_252 = tuple(
        closes[i - formation_bars + 1] if i >= formation_bars - 1 else closes[i]
        for i in range(len(closes))
    )
    evaluated = dsl.evaluate(expression, {"adj_close_lag_21": lag_21, "adj_close_lag_252": lag_252})
    assert evaluated.expression_hash == dsl.canonical_hash(expression)
    return evaluated.values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", default="data/research-replay/snapshots/synthetic-daily-2015-2025-v1"
    )
    parser.add_argument("--state-dir", default="var/research-replay")
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    snapshot_root = (REPOSITORY_ROOT / args.snapshot).resolve()
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        state_dir = (REPOSITORY_ROOT / state_dir).resolve()
    if (
        state_dir == (REPOSITORY_ROOT / "var" / "advisorai").resolve()
        or (REPOSITORY_ROOT / "var" / "advisorai").resolve() in state_dir.parents
    ):
        raise ValueError("example state must stay outside var/advisorai")

    registry = ResearchRegistry(state_dir / "research-registry.sqlite3")
    label = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    # 1. discovery: paper + implementation record with disposition.
    paper = registry.store(
        PaperCard(
            source_url=PAPER_URL,
            title="Time series momentum",
            authors=("Tobias J. Moskowitz", "Yao Hua Ooi", "Lasse Heje Pedersen"),
            claimed_result="12-1 TSM predicts next-month return across 58 instruments",
            relevance="skip-month variant differs from our 12-0 baseline",
        ),
        record_type="paper",
    )
    relationship = classify_relationship(
        repository_owner="gehlotmanthan",
        paper_authors=paper.authors,
        claims_independent=True,
    )
    assert relationship is ImplementationRelationship.INDEPENDENT_REPRODUCTION
    implementation = registry.store(
        build_implementation_record(
            repository_url=REPO_URL,
            revision=REPO_REVISION,
            source_hash=hashlib.sha256(f"{REPO_URL}@{REPO_REVISION}".encode()).hexdigest(),
            license_id=REPO_LICENSE,
            relationship=relationship,
            paper_card_id=paper.artifact_id,
            dependencies=("pandas", "numpy", "yfinance"),
            data_assumptions=("AQR monthly factor xlsx", "Yahoo monthly proxies from 2007"),
            entry_point=REPO_ENTRY,
            replication_evidence="committed outputs: Sharpe 0.91, 1990-2025, +21.8% in 2008",
            small_reviewed_scope=False,
        ),
        record_type="implementation",
    )
    registry.link(paper.artifact_id, implementation.artifact_id, "paper_implemented_by")
    hypothesis = registry.store(
        HypothesisCard(
            economic_rationale="skip-month avoids short-term reversal contaminating the signal",
            universe="SPY,TLT",
            horizon="252d formation skipped 21d, 21d holding, month-end rebalance",
            baseline="buy-and-hold on the same month-end windows",
            preregistered_plan="example v1: skip-month DSL, sign-only, pinned costs",
            owner="omp-research-example-v1",
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            paper_card_ids=(paper.artifact_id,),
        ),
        record_type="hypothesis",
    )

    # 2-3. candidate + independent replay per exposure.
    bars, snapshot_hash = load_snapshot_bars(snapshot_root)
    factor_hash = dsl.canonical_hash(dsl.parse(SKIP_MONTH_DSL))
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    environment = hashlib.sha256(b"research-example-env-v1").hexdigest()
    spec = SignalPolicySpec(
        policy_id="tsmom-skip-sign-only-v1", family=SignalPolicyFamily.SIGN_ONLY
    )
    results = []
    for symbol in ("SPY", "TLT"):
        # Independent replay path: same observation builder and accounting as
        # run_exposure, but with the skip-month factor values. Uses the
        # module-internal _run_path via a tailored run: factor values feed
        # build_observations identically, so PIT/embargo/costs are shared.
        from advisorai.research import replay as replay_module

        momentum = _skip_month_values(bars[symbol])
        observations, predictions = build_observations(
            symbol,
            bars[symbol],
            momentum,
            factor_hash=factor_hash,
            snapshot_hash=snapshot_hash,
        )
        window_proof = replay_module.require_window_embargo(observations)
        exposure = ExposureSpec(symbol=symbol, bars=bars[symbol])
        candidate = replay_module._run_path(exposure, observations, predictions, spec)
        baseline = replay_module._run_path(
            exposure,
            observations,
            replay_module._baseline_predictions(
                observations,
                model_code_hash=hashlib.sha256(
                    f"{replay_module.CODE_ID}:baseline-buy-and-hold-v1".encode()
                ).hexdigest(),
                data_hash=snapshot_hash,
            ),
            SignalPolicySpec(
                policy_id="baseline-always-long-v1", family=SignalPolicyFamily.SIGN_ONLY
            ),
        )
        verdict = "ACCEPT" if candidate["net_bps"] > baseline["net_bps"] else "REJECT"
        results.append(
            {
                "symbol": symbol,
                "observations": candidate["observations"],
                "trades": candidate["trades"],
                "net_bps": f"{candidate['net_bps']:.2f}",
                "baseline_net_bps": f"{baseline['net_bps']:.2f}",
                "accuracy": f"{candidate['accuracy']:.4f}"
                if candidate["accuracy"] is not None
                else "n/a",
                "windows": [
                    {"window": name, "obs": count, "net_bps": f"{value:.2f}"}
                    for name, count, _, value in candidate["window"]
                ],
                "window_proof": {k: list(v) for k, v in window_proof.items()},
                "verdict": verdict,
            }
        )

    evidence_root = state_dir / "evidence" / label
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "advisorai.research.example.v1",
        "label": label,
        "repository_commit": _git_head(REPOSITORY_ROOT),
        "command": "scripts/run_research_example.py",
        "model": SKIP_MONTH_MODEL,
        "factor_hash": factor_hash,
        "snapshot_hash": snapshot_hash,
        "code_hash": code_hash,
        "environment_hash": environment,
        "paper_artifact_id": str(paper.artifact_id),
        "implementation_artifact_id": str(implementation.artifact_id),
        "implementation_disposition": implementation.disposition.value,
        "hypothesis_artifact_id": str(hypothesis.artifact_id),
        "results": results,
    }
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (evidence_root / "manifest.json").write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    print(f"model={SKIP_MONTH_MODEL} factor={factor_hash[:16]} manifest_sha256={digest}")
    for item in results:
        print(
            f"{item['symbol']}: obs={item['observations']} trades={item['trades']} "
            f"net={item['net_bps']}bps baseline={item['baseline_net_bps']}bps "
            f"acc={item['accuracy']} verdict={item['verdict']}"
        )
        for window in item["windows"]:
            print(f"  {window['window']}: obs={window['obs']} net={window['net_bps']}bps")
    print(f"evidence: {evidence_root}")
    print(
        f"disposition: {implementation.disposition.value} ({implementation.disposition_rationale})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
