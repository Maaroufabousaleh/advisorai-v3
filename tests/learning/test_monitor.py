"""Paper performance monitor: slices, champion/challenger, expiry flags."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from advisorai.learning import PaperDecisionRecord, PaperLearningLoop, PaperOutcome
from advisorai.learning.monitor import (
    compare_champion,
    expiry_flags,
    load_outcomes,
    load_scorecards,
    summarize,
)
from advisorai.ledger import SqliteLedgers


def _seed(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "monitor.sqlite3")
    loop = PaperLearningLoop(ledgers)
    cutoff = datetime(2026, 8, 5, 15, tzinfo=UTC)
    records = []
    for index, (subject, utility) in enumerate(
        [
            ("baseline-v1", Decimal("5")),
            ("baseline-v1", Decimal("-2")),
            ("challenger-v1", Decimal("8")),
            ("challenger-v1", Decimal("7")),
        ]
    ):
        record = loop.record_decision(
            PaperDecisionRecord(
                decision_id=uuid4(),
                mission_id=uuid4(),
                snapshot_id=uuid4(),
                subject=subject,
                subject_version="v1",
                asset="BTC",
                horizon="1h",
                regime="trend",
                cutoff=cutoff + timedelta(hours=index),
                horizon_end=cutoff + timedelta(hours=index + 1),
            )
        )
        loop.record_outcome(
            PaperOutcome(
                decision_id=record.decision_id,
                as_of=record.horizon_end,
                net_utility=utility,
                cost_usd=Decimal("1"),
                fill_quality=Decimal("1"),
                data_reliability=Decimal("1"),
                latency_ms=5,
                failure_rate=Decimal("0"),
            )
        )
        records.append(record)
    return ledgers


def test_summarize_slices_by_asset_regime_horizon(tmp_path):
    ledgers = _seed(tmp_path)
    scorecards = load_scorecards(ledgers)
    assert len(scorecards) == 4
    slices = summarize(scorecards)
    assert len(slices) == 1
    slice_ = slices[0]
    assert (slice_.asset, slice_.regime, slice_.horizon) == ("BTC", "trend", "1h")
    assert slice_.samples == 4
    assert slice_.total_utility == Decimal("18")
    assert slice_.win_rate == Decimal("3") / Decimal("4")
    assert slice_.max_drawdown >= Decimal("0")
    assert slice_.total_cost == Decimal("4")


def test_champion_comparison_uses_shared_slices_only(tmp_path):
    ledgers = _seed(tmp_path)
    scorecards = load_scorecards(ledgers)
    comparison = compare_champion(
        scorecards, baseline_subject="baseline-v1", challenger_subject="challenger-v1"
    )
    assert comparison.baseline_utility == Decimal("3")
    assert comparison.challenger_utility == Decimal("15")
    assert comparison.delta_utility == Decimal("12")
    assert comparison.verdict == "challenger_ahead_on_shared_slices"


def test_expiry_flags_negative_trailing_only(tmp_path):
    ledgers = _seed(tmp_path)
    scorecards = load_scorecards(ledgers)
    flags = expiry_flags(scorecards, trailing_window=2)
    subjects = {flag.subject for flag in flags}
    # Baseline trailing 2: 5, -2 → +3, no flag. Challenger: 8, 7 → no flag.
    assert subjects == set()
    losing = tuple(
        card.model_copy(update={"subject": "loser-v1", "net_utility": Decimal("-5")})
        for card in scorecards[:2]
    )
    flags = expiry_flags(scorecards + losing, trailing_window=4)
    flagged = {flag.subject for flag in flags}
    assert "loser-v1" in flagged
    flag = next(item for item in flags if item.subject == "loser-v1")
    assert flag.action == "review_for_retirement"


def test_load_outcomes_joins_by_decision_id(tmp_path):
    ledgers = _seed(tmp_path)
    pairs = load_outcomes(ledgers)
    assert len(pairs) == 4
    for decision, outcome in pairs:
        assert outcome.decision_id == decision.decision_id


def test_compare_strategies_covers_baseline_and_challengers(tmp_path):
    from advisorai.learning import compare_strategies

    ledgers = _seed(tmp_path)
    from advisorai.learning import load_scorecards

    scorecards = load_scorecards(ledgers)
    comparisons = compare_strategies(scorecards, baseline_subject="baseline-v1")
    subjects = {item.challenger_subject for item in comparisons}
    assert {"baseline-v1", "challenger-v1"} <= subjects
    challenger = next(item for item in comparisons if item.challenger_subject == "challenger-v1")
    assert challenger.delta_utility == Decimal("12")
    assert challenger.shared_slices == 1
    assert challenger.beats_no_trade is True
    assert challenger.expiry_action == "none"
    baseline = next(item for item in comparisons if item.challenger_subject == "baseline-v1")
    assert baseline.delta_utility == Decimal("0")
