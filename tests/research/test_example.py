"""Complete research example: skip-month variant discovery to verdict.

Proves the skip-month DSL parses and evaluates past-only (reads closes
i-21 and i-251 only), differs from the 12-0 baseline, replays through the
shared observation/accounting path with enforced windows, and records a
per-exposure verdict. The example script is the reproduction command.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from advisorai.research import factors as dsl
from advisorai.research.replay import build_observations, load_snapshot_bars

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research-replay"
    / "snapshots"
    / "synthetic-daily-2015-2025-v1"
)

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


def _skip_month_values(bars: tuple[dict, ...]) -> tuple[Decimal, ...]:
    expression = dsl.parse(SKIP_MONTH_DSL)
    closes = tuple(bar["adj_close"] for bar in bars)
    lag_21 = tuple(closes[max(0, i - 21)] for i in range(len(closes)))
    lag_252 = tuple(closes[i - 251] if i >= 251 else closes[i] for i in range(len(closes)))
    return dsl.evaluate(
        expression, {"adj_close_lag_21": lag_21, "adj_close_lag_252": lag_252}
    ).values


def _bars(count: int = 300) -> tuple[dict, ...]:
    from datetime import date, timedelta

    base = date(2020, 1, 2)
    rows = []
    current = base
    added = 0
    while added < count:
        if current.weekday() < 5:
            price = Decimal(100 + added)
            rows.append({"date": current.isoformat(), "adj_close": price})
            added += 1
        current += timedelta(days=1)
    return tuple(rows)


def test_skip_month_dsl_is_versioned_and_past_only():
    bars = _bars(300)
    first = _skip_month_values(bars)
    assert len(first) == 300
    mutated = list(bars)
    bumped = dict(mutated[250])
    bumped["adj_close"] = bumped["adj_close"] * Decimal("2")
    mutated[250] = bumped
    second = _skip_month_values(tuple(mutated))
    # A bump at bar 250 enters its own warmup value and the skip leg at 271;
    # strictly earlier values are immune (past-only).
    assert second[:250] == first[:250]
    assert second[250] != first[250]
    assert second[271] != first[271]
    assert dsl.canonical_hash(dsl.parse(SKIP_MONTH_DSL)) == dsl.canonical_hash(
        dsl.parse(SKIP_MONTH_DSL)
    )


def test_skip_month_differs_from_baseline_and_replays(tmp_path):

    from advisorai.research.synthetic import materialize as _materialize

    _materialize(FIXTURE)
    from advisorai.research.replay import evaluate_momentum, require_window_embargo

    bars, snapshot_hash = load_snapshot_bars(FIXTURE)
    skip = _skip_month_values(bars["SPY"])
    baseline = evaluate_momentum(bars["SPY"])
    assert skip != baseline
    observations, predictions = build_observations(
        "SPY", bars["SPY"], skip, factor_hash="f" * 64, snapshot_hash=snapshot_hash
    )
    assert len(observations) == 120
    proof = require_window_embargo(observations)
    assert sorted(proof) == ["holdout", "screen", "validation"]
    assert {p.observation_id for p in predictions} == {o.observation_id for o in observations}


def test_example_script_reproduces_verdicts(tmp_path):
    root = Path(__file__).resolve().parents[2]
    state_dir = tmp_path / "example"
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "run_research_example.py"),
            "--label",
            "example-test",
            "--state-dir",
            str(state_dir),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    manifest = json.loads((state_dir / "evidence" / "example-test" / "manifest.json").read_text())
    assert manifest["model"] == "tsmom-12m1-skip-sign-v1"
    assert [item["symbol"] for item in manifest["results"]] == ["SPY", "TLT"]
    assert all(item["verdict"] in {"ACCEPT", "REJECT"} for item in manifest["results"])
    assert manifest["implementation_disposition"] == "adapt_in_isolation"
