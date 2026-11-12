"""Paper performance monitoring: slices, champion/challenger, expiry flags.

Read-only projections over recorded decisions, scorecards, and outcomes.
No promotion, no training, no authority. A candidate with negative trailing
utility is flagged for review (expire/retire), never auto-promoted; a strong
candidate is reported as eligible for *human-reviewed* paper shadow only.

Slices: per (asset, regime, horizon) — count, total/mean net utility, win
rate, max drawdown on the cumulative utility curve, total costs.
Champion/challenger: baseline subject vs candidate subject comparison with
identical slice keys; the comparison is evidence, not admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from advisorai.learning.paper import PaperDecisionRecord, PaperOutcome
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.memory.scorecards import Scorecard


@dataclass(frozen=True, slots=True)
class PerformanceSlice:
    asset: str
    regime: str
    horizon: str
    samples: int
    total_utility: Decimal
    mean_utility: Decimal
    win_rate: Decimal
    max_drawdown: Decimal
    total_cost: Decimal


class ChampionComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_subject: str = Field(min_length=1)
    challenger_subject: str = Field(min_length=1)
    baseline_utility: Decimal
    challenger_utility: Decimal
    delta_utility: Decimal
    baseline_samples: int = Field(ge=0)
    challenger_samples: int = Field(ge=0)
    verdict: str = Field(min_length=1)


class ExpiryFlag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1)
    subject_version: str = Field(min_length=1)
    trailing_utility: Decimal
    samples: int = Field(ge=0)
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def _drawdown(utilities: tuple[Decimal, ...]) -> Decimal:
    peak = Decimal("0")
    running = Decimal("0")
    worst = Decimal("0")
    for utility in utilities:
        running += utility
        if running > peak:
            peak = running
        drawdown = peak - running
        if drawdown > worst:
            worst = drawdown
    return worst


def summarize(
    scorecards: tuple[Scorecard, ...],
) -> tuple[PerformanceSlice, ...]:
    """Aggregate scorecards into per-(asset, regime, horizon) slices."""
    groups: dict[tuple[str, str, str], list[Scorecard]] = {}
    for scorecard in scorecards:
        key = (scorecard.asset, scorecard.regime, scorecard.horizon)
        groups.setdefault(key, []).append(scorecard)
    slices: list[PerformanceSlice] = []
    for (asset, regime, horizon), members in sorted(groups.items()):
        ordered = sorted(members, key=lambda item: item.recorded_at)
        utilities = tuple(item.net_utility for item in ordered)
        wins = sum(1 for utility in utilities if utility > 0)
        total = sum(utilities, Decimal("0"))
        slices.append(
            PerformanceSlice(
                asset=asset,
                regime=regime,
                horizon=horizon,
                samples=len(ordered),
                total_utility=total,
                mean_utility=(total / len(ordered)) if ordered else Decimal("0"),
                win_rate=Decimal(wins) / Decimal(len(ordered)) if ordered else Decimal("0"),
                max_drawdown=_drawdown(utilities),
                total_cost=sum((item.api_cost_usd for item in ordered), Decimal("0")),
            )
        )
    return tuple(slices)


def compare_champion(
    scorecards: tuple[Scorecard, ...],
    *,
    baseline_subject: str,
    challenger_subject: str,
) -> ChampionComparison:
    """Compare total net utility on identical slice keys only.

    Slices present for one subject but not the other are excluded, so a
    challenger cannot win by trading different regimes. The verdict is
    evidence for human review, never admission.
    """
    if not baseline_subject.strip() or not challenger_subject.strip():
        raise ValueError("champion comparison requires both subjects")
    base = {item for item in scorecards if item.subject == baseline_subject}
    chal = {item for item in scorecards if item.subject == challenger_subject}
    base_keys = {(item.asset, item.regime, item.horizon) for item in base}
    chal_keys = {(item.asset, item.regime, item.horizon) for item in chal}
    shared = base_keys & chal_keys
    base_utility = sum(
        (item.net_utility for item in base if (item.asset, item.regime, item.horizon) in shared),
        Decimal("0"),
    )
    chal_utility = sum(
        (item.net_utility for item in chal if (item.asset, item.regime, item.horizon) in shared),
        Decimal("0"),
    )
    delta = chal_utility - base_utility
    verdict = (
        "challenger_ahead_on_shared_slices"
        if delta > 0
        else "baseline_ahead_or_tied_on_shared_slices"
    )
    return ChampionComparison(
        baseline_subject=baseline_subject,
        challenger_subject=challenger_subject,
        baseline_utility=base_utility,
        challenger_utility=chal_utility,
        delta_utility=delta,
        baseline_samples=sum(1 for _ in base),
        challenger_samples=sum(1 for _ in chal),
        verdict=verdict,
    )


def expiry_flags(
    scorecards: tuple[Scorecard, ...],
    *,
    trailing_window: int = 20,
) -> tuple[ExpiryFlag, ...]:
    """Flag subjects with negative trailing utility for human review.

    Flags are retirement *candidates*, not retirements: the owner reviews
    and retires explicitly. A subject with no samples is never flagged.
    """
    if trailing_window < 1:
        raise ValueError("trailing window must be positive")
    by_subject: dict[tuple[str, str], list[Scorecard]] = {}
    for scorecard in scorecards:
        by_subject.setdefault((scorecard.subject, scorecard.subject_version), []).append(scorecard)
    flags: list[ExpiryFlag] = []
    for (subject, version), members in sorted(by_subject.items()):
        ordered = sorted(members, key=lambda item: item.recorded_at)[-trailing_window:]
        trailing = sum((item.net_utility for item in ordered), Decimal("0"))
        if trailing < 0:
            flags.append(
                ExpiryFlag(
                    subject=subject,
                    subject_version=version,
                    trailing_utility=trailing,
                    samples=len(ordered),
                    action="review_for_retirement",
                    reason=f"trailing {len(ordered)}-sample utility is negative ({trailing})",
                )
            )
    return tuple(flags)


def load_scorecards(ledgers: SqliteLedgers) -> tuple[Scorecard, ...]:
    """Read all recorded scorecards from the MODEL ledger in commit order."""
    scorecards: list[Scorecard] = []
    for event in ledgers.events(LedgerNamespace.MODEL):
        if event.event_type != "paper_scorecard_recorded":
            continue
        raw = event.payload.get("scorecard")
        if isinstance(raw, dict):
            scorecards.append(Scorecard.model_validate(raw))
    return tuple(scorecards)


def load_outcomes(ledgers: SqliteLedgers) -> tuple[tuple[PaperDecisionRecord, PaperOutcome], ...]:
    """Join decisions with their realized outcomes by exact decision ID."""
    from advisorai.learning import PaperLearningLoop

    loop = PaperLearningLoop(ledgers)
    pairs: list[tuple[PaperDecisionRecord, PaperOutcome]] = []
    for event in ledgers.events(LedgerNamespace.MODEL):
        if event.event_type != "paper_scorecard_recorded":
            continue
        raw_outcome = event.payload.get("outcome")
        decision_id = event.payload.get("decision_id")
        if not isinstance(raw_outcome, dict) or not isinstance(decision_id, str):
            continue
        outcome = PaperOutcome.model_validate(raw_outcome)
        decision = loop.decision(outcome.decision_id)
        if decision is not None:
            pairs.append((decision, outcome))
    return tuple(pairs)


__all__ = [
    "ChampionComparison",
    "ExpiryFlag",
    "PerformanceSlice",
    "compare_champion",
    "expiry_flags",
    "load_outcomes",
    "load_scorecards",
    "summarize",
]
