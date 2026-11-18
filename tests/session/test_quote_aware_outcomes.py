"""PIT-bound quote conversion and evidence-backed paper outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.contracts import Fill, InstrumentIdentity, Order, Snapshot
from advisorai.execution import RiskMarketState
from advisorai.learning import PaperDecisionRecord, PaperLearningLoop, PaperOutcome
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.outcomes import (
    PaperOutcomeQuality,
    QuoteAwarePaperOutcomeAdapter,
    QuoteToUsdRate,
)

_CUTOFF = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)


def _inputs(*, fee_currency="USDT"):
    snapshot = Snapshot(as_of=_CUTOFF, purpose="quote-outcome-current-pit")
    order_id = uuid4()
    instrument = InstrumentIdentity(
        canonical_id="BTC", asset_class="crypto", venue="paper", venue_symbol="BTCUSDT"
    )
    order = Order(
        artifact_id=order_id,
        parent_intent_id=uuid4(),
        execution_plan_id=uuid4(),
        instrument=instrument,
        side="buy",
        quantity=Decimal("0.01"),
        order_type="market",
        time_in_force="GTC",
        idempotency_key=f"quote-outcome-{order_id}",
    )
    fill = Fill(
        order_id=order_id,
        venue_fill_id=f"fill-{order_id}",
        quantity=Decimal("0.01"),
        price=Decimal("49900"),
        fee=Decimal("0.1"),
        fee_currency=fee_currency,
        occurred_at=_CUTOFF,
    )
    decision = PaperDecisionRecord(
        decision_id=uuid4(),
        mission_id=uuid4(),
        snapshot_id=uuid4(),
        order_ids=(order_id,),
        fill_ids=(fill.artifact_id,),
        subject="decision-pipeline",
        subject_version="provider-profile-v1",
        asset="BTC",
        horizon="1h",
        cutoff=_CUTOFF - timedelta(hours=1),
        horizon_end=_CUTOFF,
    )
    return snapshot, order, fill, decision


def _adapter(
    snapshot,
    *,
    quote_currency="USDT",
    as_of=_CUTOFF,
    snapshot_id=None,
    quality_as_of=_CUTOFF,
    quality_snapshot_id=None,
):
    rate = QuoteToUsdRate(
        quote_currency=quote_currency,
        usd_per_quote=Decimal("0.999"),
        as_of=as_of,
        snapshot_id=snapshot_id or snapshot.artifact_id,
        evidence_id=uuid4(),
        source="fx-observation",
    )
    quality = PaperOutcomeQuality(
        data_reliability=Decimal("0.98"),
        latency_ms=72,
        failure_rate=Decimal("0.01"),
        evidence_id=uuid4(),
        source="owner-quality-measurement",
        snapshot_id=quality_snapshot_id or snapshot.artifact_id,
        as_of=quality_as_of,
        sample_count=100,
        failed_count=1,
        measurement_hash="a" * 64,
    )
    return QuoteAwarePaperOutcomeAdapter(
        quote_currency="USDT",
        fx_rate_provider=lambda _snapshot: rate,
        quality_provider=lambda **_context: quality,
    )


def test_quote_aware_outcome_converts_utility_and_persists_fx_provenance(tmp_path):
    snapshot, order, fill, decision = _inputs()
    adapter = _adapter(snapshot)
    outcome = adapter.build_outcome(
        decision=decision,
        as_of=_CUTOFF,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(fill,),
        orders={order.artifact_id: order},
        initial_marks={"BTC": Decimal("49000")},
    )

    assert outcome.forecast_value is None
    assert outcome.realized_value == Decimal("49950.000")
    assert outcome.net_utility == Decimal("0.8991")
    assert outcome.cost_usd == Decimal("0.0999")
    assert outcome.fx_quote_currency == "USDT"
    assert outcome.fx_usd_per_quote == Decimal("0.999")
    assert outcome.fx_snapshot_id == snapshot.artifact_id
    assert outcome.fx_evidence_id is not None
    assert outcome.quality_evidence_id is not None
    assert outcome.quality_sample_count == 100
    assert outcome.quality_failed_count == 1
    assert outcome.quality_measurement_hash == "a" * 64
    assert outcome.latency_ms == 72
    assert outcome.failure_rate == Decimal("0.01")

    ledgers = SqliteLedgers(tmp_path / "quote-outcome.sqlite3")
    first = PaperLearningLoop(ledgers)
    first.record_decision(decision)
    scorecard = first.record_outcome(outcome)
    restarted = PaperLearningLoop(ledgers)
    assert restarted.record_outcome(outcome).scorecard_id == scorecard.scorecard_id
    recorded = next(
        event
        for event in ledgers.events(LedgerNamespace.MODEL)
        if event.event_type == "paper_scorecard_recorded"
    )
    restored = PaperOutcome.model_validate(recorded.payload["outcome"])
    assert restored.quality_sample_count == 100
    assert restored.quality_failed_count == 1
    assert restored.quality_measurement_hash == "a" * 64


def test_quote_aware_outcome_includes_fills_arriving_after_the_decision_record():
    snapshot, order, fill, decision = _inputs()
    initial_fill = fill.model_copy(update={"quantity": Decimal("0.005")})
    later_fill = Fill(
        order_id=order.artifact_id,
        venue_fill_id=f"late-fill-{order.artifact_id}",
        quantity=Decimal("0.005"),
        price=Decimal("50000"),
        fee=Decimal("0.05"),
        fee_currency="USDT",
        occurred_at=_CUTOFF,
    )
    outcome = _adapter(snapshot).build_outcome(
        decision=decision,
        as_of=_CUTOFF,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(initial_fill, later_fill),
        orders={order.artifact_id: order},
        initial_marks={"BTC": Decimal("49000")},
    )

    assert outcome.net_utility == Decimal("0.34965")
    assert outcome.cost_usd == Decimal("0.14985")


def test_quote_aware_outcome_marks_sell_fills_from_execution_price():
    snapshot, order, fill, decision = _inputs()
    sell_order = order.model_copy(update={"side": "sell"})
    sell_fill = fill.model_copy(update={"price": Decimal("50500")})
    outcome = _adapter(snapshot).build_outcome(
        decision=decision,
        as_of=_CUTOFF,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(sell_fill,),
        orders={sell_order.artifact_id: sell_order},
        initial_marks={"BTC": Decimal("49000")},
    )

    assert outcome.net_utility == Decimal("4.8951")


@pytest.mark.parametrize(
    ("quote_currency", "rate_as_of", "rate_snapshot_id"),
    [
        ("USDC", _CUTOFF, None),
        ("USDT", _CUTOFF - timedelta(minutes=1), None),
        ("USDT", _CUTOFF, uuid4()),
    ],
)
def test_quote_aware_outcome_rejects_mismatched_or_stale_fx(quote_currency, rate_as_of, rate_snapshot_id):
    snapshot, order, fill, decision = _inputs()
    adapter = _adapter(
        snapshot,
        quote_currency=quote_currency,
        as_of=rate_as_of,
        snapshot_id=rate_snapshot_id,
    )

    with pytest.raises(ValueError, match="not current and bound"):
        adapter.build_outcome(
            decision=decision,
            as_of=_CUTOFF,
            snapshot=snapshot,
            market=RiskMarketState(marks={"BTC": Decimal("50000")}),
            fills=(fill,),
            orders={order.artifact_id: order},
            initial_marks={"BTC": Decimal("49000")},
        )


def test_quote_aware_outcome_rejects_unlabeled_fees_and_missing_marks():
    snapshot, order, fill, decision = _inputs(fee_currency="BNB")
    adapter = _adapter(snapshot)
    context = {
        "decision": decision,
        "as_of": _CUTOFF,
        "snapshot": snapshot,
        "market": RiskMarketState(marks={"BTC": Decimal("50000")}),
        "fills": (fill,),
        "orders": {order.artifact_id: order},
        "initial_marks": {"BTC": Decimal("49000")},
    }
    with pytest.raises(ValueError, match="fee currency"):
        adapter.build_outcome(**context)

    fill = fill.model_copy(update={"fee_currency": "USDT"})
    with pytest.raises(ValueError, match="positive current mark"):
        adapter.build_outcome(
            **{**context, "fills": (fill,), "market": RiskMarketState(marks={})}
        )


def test_quote_aware_outcome_rejects_stale_quality_evidence():
    snapshot, order, fill, decision = _inputs()
    adapter = _adapter(snapshot, quality_as_of=_CUTOFF - timedelta(minutes=1))

    with pytest.raises(ValueError, match="quality evidence is not current"):
        adapter.build_outcome(
            decision=decision,
            as_of=_CUTOFF,
            snapshot=snapshot,
            market=RiskMarketState(marks={"BTC": Decimal("50000")}),
            fills=(fill,),
            orders={order.artifact_id: order},
            initial_marks={"BTC": Decimal("49000")},
        )


def test_paper_outcome_rejects_incomplete_fx_or_quality_provenance():
    with pytest.raises(ValueError, match="FX outcome provenance must be complete"):
        PaperOutcome(
            decision_id=uuid4(),
            as_of=_CUTOFF,
            net_utility=Decimal("0"),
            cost_usd=Decimal("0"),
            fill_quality=Decimal("1"),
            data_reliability=Decimal("1"),
            latency_ms=0,
            failure_rate=Decimal("0"),
            fx_quote_currency="USDT",
        )
    with pytest.raises(ValueError, match="quality evidence must be complete"):
        PaperOutcome(
            decision_id=uuid4(),
            as_of=_CUTOFF,
            net_utility=Decimal("0"),
            cost_usd=Decimal("0"),
            fill_quality=Decimal("1"),
            data_reliability=Decimal("1"),
            latency_ms=0,
            failure_rate=Decimal("0"),
            quality_source="fixture",
        )


def test_quote_aware_outcome_refuses_misshapen_construction() -> None:
    """P02/P09 prep: the outcome adapter validates at construction.

    A blank quote currency and uncallable FX/quality providers are
    refused before any outcome is built. Pure unit coverage; no
    network, no credentials.
    """
    snapshot, _order, _fill, _decision = _inputs()
    good = _adapter(snapshot)
    with pytest.raises(ValueError, match="quote currency is required"):
        QuoteAwarePaperOutcomeAdapter(
            quote_currency="  ",
            fx_rate_provider=good.fx_rate_provider,
            quality_provider=good.quality_provider,
        )
    with pytest.raises(ValueError, match="require FX and quality providers"):
        QuoteAwarePaperOutcomeAdapter(
            quote_currency="USDT",
            fx_rate_provider=None,
            quality_provider=good.quality_provider,
        )
    with pytest.raises(ValueError, match="require FX and quality providers"):
        QuoteAwarePaperOutcomeAdapter(
            quote_currency="USDT",
            fx_rate_provider=good.fx_rate_provider,
            quality_provider="not-callable",
        )


def test_quote_aware_outcome_refuses_open_or_misaligned_horizons() -> None:
    """P02/P09 prep: outcomes require a closed, aligned horizon.

    Naive timestamps, an as-of before the decision horizon end, a
    snapshot from another cutoff, and a multi-asset decision record
    are all refused before any FX or mark read.
    """
    snapshot, order, fill, decision = _inputs()
    adapter = _adapter(snapshot)

    def build(**overrides):
        kwargs = {
            "decision": decision,
            "as_of": _CUTOFF,
            "snapshot": snapshot,
            "market": RiskMarketState(marks={"BTC": Decimal("50000")}),
            "fills": (fill,),
            "orders": {order.artifact_id: order},
            "initial_marks": {"BTC": Decimal("49000")},
        }
        kwargs.update(overrides)
        return adapter.build_outcome(**kwargs)

    with pytest.raises(ValueError, match="aware, closed decision horizon"):
        build(as_of=datetime(2026, 11, 12, 16, 0))
    with pytest.raises(ValueError, match="aware, closed decision horizon"):
        build(as_of=_CUTOFF - timedelta(minutes=1))
    other_cutoff = Snapshot(
        as_of=_CUTOFF - timedelta(hours=1), purpose="quote-outcome-stale-pit"
    )
    with pytest.raises(ValueError, match="same current cutoff"):
        build(snapshot=other_cutoff)
    multi_asset = decision.model_copy(update={"asset": "BTC,ETH"})
    with pytest.raises(ValueError, match="exactly one canonical asset"):
        build(decision=multi_asset)


def test_quote_aware_outcome_refuses_untyped_providers() -> None:
    """P02/P09 prep: untyped FX/quality providers are refused.

    A dict-returning FX provider and a dict-returning quality
    provider are refused even when every timestamp aligns. No
    network, no credentials.
    """
    snapshot, order, fill, decision = _inputs()
    fx_rate = QuoteToUsdRate(
        quote_currency="USDT",
        usd_per_quote=Decimal("0.999"),
        as_of=_CUTOFF,
        snapshot_id=snapshot.artifact_id,
        evidence_id=uuid4(),
        source="fx-observation",
    )

    def build(adapter):
        return adapter.build_outcome(
            decision=decision,
            as_of=_CUTOFF,
            snapshot=snapshot,
            market=RiskMarketState(marks={"BTC": Decimal("50000")}),
            fills=(fill,),
            orders={order.artifact_id: order},
            initial_marks={"BTC": Decimal("49000")},
        )

    untyped_fx = QuoteAwarePaperOutcomeAdapter(
        quote_currency="USDT",
        fx_rate_provider=lambda _snapshot: {"usd_per_quote": "0.999"},
        quality_provider=_adapter(snapshot).quality_provider,
    )
    with pytest.raises(ValueError, match="must return a typed QuoteToUsdRate"):
        build(untyped_fx)
    untyped_quality = QuoteAwarePaperOutcomeAdapter(
        quote_currency="USDT",
        fx_rate_provider=lambda _snapshot: fx_rate,
        quality_provider=lambda **_context: {"data_reliability": "0.98"},
    )
    with pytest.raises(ValueError, match="must return typed PaperOutcomeQuality"):
        build(untyped_quality)


def test_quote_aware_outcome_refuses_broken_fill_order_linkage() -> None:
    """P02/P09 prep: broken fill/order linkage is refused.

    Duplicate fill identities, duplicate decision ids, a mismatched
    order-map identity, fills/orders absent from OMS state, fills
    unlinked from decision orders, instrument drift, and fills that
    exceed an order quantity are all refused. No network, no
    credentials.
    """
    snapshot, order, fill, decision = _inputs()
    adapter = _adapter(snapshot)

    def build(**overrides):
        kwargs = {
            "decision": decision,
            "as_of": _CUTOFF,
            "snapshot": snapshot,
            "market": RiskMarketState(marks={"BTC": Decimal("50000")}),
            "fills": (fill,),
            "orders": {order.artifact_id: order},
            "initial_marks": {"BTC": Decimal("49000")},
        }
        kwargs.update(overrides)
        return adapter.build_outcome(**kwargs)

    with pytest.raises(ValueError, match="duplicate fill identities"):
        build(fills=(fill, fill))
    doubled = decision.model_copy(
        update={"order_ids": (order.artifact_id, order.artifact_id)}
    )
    with pytest.raises(ValueError, match="duplicate order or fill identities"):
        build(decision=doubled)
    with pytest.raises(ValueError, match="mismatched local identity"):
        build(orders={uuid4(): order})
    ghost_fill = decision.model_copy(update={"fill_ids": (uuid4(),)})
    with pytest.raises(ValueError, match="fill absent from current OMS"):
        build(decision=ghost_fill)
    ghost_order = decision.model_copy(
        update={"order_ids": (uuid4(),), "fill_ids": ()}
    )
    with pytest.raises(ValueError, match="order absent from current OMS"):
        build(decision=ghost_order)

    # NOTE: the "fill is not linked to one of its decision orders" branch
    # is unreachable: related fills are pre-filtered to decision order ids
    # and missing orders raise earlier, so the loop guard can never fire.
    # It is intentionally left unpinned rather than tested via mocks.


    eth_instrument = order.instrument.model_copy(update={"canonical_id": "ETH"})
    eth_order = order.model_copy(update={"instrument": eth_instrument})
    with pytest.raises(ValueError, match="differs from the decision asset"):
        build(orders={order.artifact_id: eth_order})

    fat_fill = fill.model_copy(update={"quantity": Decimal("99")})
    with pytest.raises(ValueError, match="exceed a decision order quantity"):
        build(fills=(fat_fill,), decision=decision.model_copy(
            update={"fill_ids": (fat_fill.artifact_id,)}
        ))


def test_quote_aware_outcome_refuses_foreign_asset_orders() -> None:
    """P02/P09 prep: decisions spanning assets are refused at the end.

    An order for another asset inside the decision id set is refused
    even when the fill linkage itself is consistent. No network, no
    credentials.
    """
    snapshot, order, fill, decision = _inputs()
    adapter = _adapter(snapshot)
    eth_instrument = order.instrument.model_copy(update={"canonical_id": "ETH"})
    eth_order = order.model_copy(
        update={
            "artifact_id": uuid4(),
            "instrument": eth_instrument,
            "idempotency_key": "quote-outcome-eth-order",
        }
    )
    # No fill names the foreign order, so fill linkage passes and the
    # end-of-build asset sweep is the guard that fires.
    spanning = decision.model_copy(
        update={"order_ids": (order.artifact_id, eth_order.artifact_id)}
    )
    with pytest.raises(ValueError, match="order for another asset"):
        adapter.build_outcome(
            decision=spanning,
            as_of=_CUTOFF,
            snapshot=snapshot,
            market=RiskMarketState(marks={"BTC": Decimal("50000")}),
            fills=(fill,),
            orders={order.artifact_id: order, eth_order.artifact_id: eth_order},
            initial_marks={"BTC": Decimal("49000")},
        )


def test_fx_rate_refuses_misshapen_values() -> None:
    """P02/P09 prep: FX rates validate at construction.

    A blank currency/source, a non-positive or non-Decimal rate, and
    a naive timestamp are all refused. Pure unit coverage; no
    network, no credentials.
    """
    good = {
        "quote_currency": "USDT",
        "usd_per_quote": Decimal("0.999"),
        "as_of": _CUTOFF,
        "snapshot_id": uuid4(),
        "evidence_id": uuid4(),
        "source": "fx-observation",
    }
    with pytest.raises(ValueError, match="requires a currency and source"):
        QuoteToUsdRate(**{**good, "quote_currency": "  "})
    with pytest.raises(ValueError, match="requires a currency and source"):
        QuoteToUsdRate(**{**good, "source": "  "})
    with pytest.raises(ValueError, match="positive finite Decimal"):
        QuoteToUsdRate(**{**good, "usd_per_quote": Decimal("0")})
    with pytest.raises(ValueError, match="positive finite Decimal"):
        QuoteToUsdRate(**{**good, "usd_per_quote": 0.999})
    with pytest.raises(ValueError, match="must include a timezone"):
        QuoteToUsdRate(
            **{**good, "as_of": datetime(2026, 11, 12, 16, 0)}
        )


def test_outcome_quality_refuses_misshapen_measurements() -> None:
    """P02/P09 prep: quality evidence validates at construction.

    Out-of-range rates, non-integer or negative latency, a blank
    source, partial measurement details, invalid counts, a failure
    rate inconsistent with its counts, a non-digest hash, and a
    naive timestamp are all refused. Pure unit coverage; no network,
    no credentials.
    """
    good = {
        "data_reliability": Decimal("0.98"),
        "latency_ms": 72,
        "failure_rate": Decimal("0.01"),
        "evidence_id": uuid4(),
        "source": "owner-quality-measurement",
        "snapshot_id": uuid4(),
        "as_of": _CUTOFF,
        "sample_count": 100,
        "failed_count": 1,
        "measurement_hash": "a" * 64,
    }
    with pytest.raises(ValueError, match=r"finite values in \[0, 1\]"):
        PaperOutcomeQuality(**{**good, "data_reliability": Decimal("1.5")})
    with pytest.raises(ValueError, match=r"finite values in \[0, 1\]"):
        PaperOutcomeQuality(**{**good, "failure_rate": "0.01"})
    with pytest.raises(ValueError, match="integer number of milliseconds"):
        PaperOutcomeQuality(**{**good, "latency_ms": True})
    with pytest.raises(ValueError, match="latency cannot be negative"):
        PaperOutcomeQuality(**{**good, "latency_ms": -1})
    with pytest.raises(ValueError, match="requires an evidence source"):
        PaperOutcomeQuality(**{**good, "source": "  "})
    with pytest.raises(ValueError, match="details must be complete"):
        PaperOutcomeQuality(**{**good, "measurement_hash": None})
    with pytest.raises(ValueError, match="sample counts are invalid"):
        PaperOutcomeQuality(**{**good, "failed_count": 101})
    with pytest.raises(ValueError, match="differs from its measured counts"):
        PaperOutcomeQuality(**{**good, "failure_rate": Decimal("0.02")})
    with pytest.raises(ValueError, match="must be a SHA-256 digest"):
        PaperOutcomeQuality(**{**good, "measurement_hash": "z" * 64})
    with pytest.raises(ValueError, match="must include a timezone"):
        PaperOutcomeQuality(
            **{**good, "as_of": datetime(2026, 11, 12, 16, 0)}
        )
