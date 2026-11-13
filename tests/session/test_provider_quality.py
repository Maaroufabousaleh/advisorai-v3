"""Measured provider transport quality is bounded to current outcome snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.contracts import Snapshot
from advisorai.execution import RiskMarketState
from advisorai.integrations.http import HttpClientConfig, HttpTransportError, SafeHttpClient
from advisorai.learning import PaperDecisionRecord, PaperLearningLoop
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.outcomes import QuoteAwarePaperOutcomeAdapter, QuoteToUsdRate
from advisorai.session.provider_quality import ProviderOutcomeQualityProvider


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _decision(snapshot: Snapshot) -> PaperDecisionRecord:
    cutoff = snapshot.as_of
    return PaperDecisionRecord(
        decision_id=uuid4(),
        mission_id=uuid4(),
        snapshot_id=snapshot.artifact_id,
        order_ids=(),
        fill_ids=(),
        subject="provider-quality-test",
        subject_version="provider-quality-v1",
        asset="BTC",
        horizon="1h",
        cutoff=cutoff,
        horizon_end=cutoff + timedelta(hours=1),
    )


def test_provider_quality_uses_http_attempt_latency_failures_and_current_snapshot():
    clock = _Clock()
    responses = [(200, b"{}", ()), (503, b"unavailable", ()), (200, b"{}", ())]
    durations = [0.012, 0.004, 0.001]

    def requester(_method, _url, _headers, _body, _timeout):
        clock.now += durations.pop(0)
        return responses.pop(0)

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("sandbox.example.test",),
            max_retries=0,
            requests_per_second=100,
        ),
        base_url="https://sandbox.example.test",
        requester=requester,
        clock=clock,
        sleeper=lambda _seconds: None,
    )
    quality_provider = ProviderOutcomeQualityProvider("binance_spot_testnet", client)
    cutoff = datetime(2026, 11, 12, 16, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="provider-quality-current", data_quality_state="gold")

    assert client.get("https://sandbox.example.test/health").status_code == 200
    with pytest.raises(HttpTransportError, match="HTTP 503"):
        client.get("https://sandbox.example.test/health")

    measured = quality_provider(
        decision=_decision(snapshot),
        as_of=cutoff,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(),
    )
    assert measured.sample_count == 2
    assert measured.failed_count == 1
    assert measured.failure_rate == Decimal("0.5")
    assert measured.latency_ms == 8
    assert measured.data_reliability == Decimal("1")
    assert measured.snapshot_id == snapshot.artifact_id
    assert measured.as_of == cutoff
    assert len(measured.measurement_hash) == 64
    assert quality_provider(
        decision=_decision(snapshot),
        as_of=cutoff,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(),
    ) == measured

    next_snapshot = Snapshot(
        as_of=cutoff + timedelta(hours=1),
        purpose="provider-quality-next-current-pit",
        data_quality_state="gold",
    )
    assert client.get("https://sandbox.example.test/health").status_code == 200
    next_measured = quality_provider(
        decision=_decision(next_snapshot),
        as_of=next_snapshot.as_of,
        snapshot=next_snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(),
    )
    assert next_measured.sample_count == 1
    assert next_measured.failed_count == 0
    assert next_measured.snapshot_id == next_snapshot.artifact_id
    assert next_measured.measurement_hash != measured.measurement_hash


def test_provider_quality_requires_samples_and_rejects_snapshot_cutoff_mismatch():
    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("sandbox.example.test",)),
        base_url="https://sandbox.example.test",
        requester=lambda *_args: (200, b"{}", ()),
    )
    provider = ProviderOutcomeQualityProvider("coinbase_exchange_sandbox", client)
    cutoff = datetime(2026, 11, 12, 16, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="provider-quality-no-samples")
    context = {
        "decision": _decision(snapshot),
        "as_of": cutoff,
        "snapshot": snapshot,
        "market": RiskMarketState(marks={"BTC": Decimal("50000")}),
        "fills": (),
    }
    with pytest.raises(ValueError, match="requires current measured request samples"):
        provider(**context)
    with pytest.raises(ValueError, match="differs from the outcome cutoff"):
        provider(**{**context, "as_of": datetime(2026, 11, 12, 17, tzinfo=UTC)})


def test_provider_quality_reduces_unvalidated_data_reliability_to_zero():
    clock = _Clock()
    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("sandbox.example.test",), requests_per_second=100),
        base_url="https://sandbox.example.test",
        requester=lambda *_args: (200, b"{}", ()),
        clock=clock,
    )
    provider = ProviderOutcomeQualityProvider("coinbase_exchange_sandbox", client)
    client.get("https://sandbox.example.test/health")
    cutoff = datetime(2026, 11, 12, 16, tzinfo=UTC)
    snapshot = Snapshot(
        as_of=cutoff,
        purpose="provider-quality-unvalidated",
        data_quality_state="unvalidated",
    )
    measured = provider(
        decision=_decision(snapshot),
        as_of=cutoff,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(),
    )
    assert measured.data_reliability == Decimal("0")


def test_measured_provider_quality_flows_into_durable_outcome(tmp_path):
    clock = _Clock()

    def requester(_method, _url, _headers, _body, _timeout):
        clock.now += 0.013
        return 200, b"{}", ()

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("sandbox.example.test",),
            max_retries=0,
            requests_per_second=100,
        ),
        base_url="https://sandbox.example.test",
        requester=requester,
        clock=clock,
        sleeper=lambda _seconds: None,
    )
    quality_provider = ProviderOutcomeQualityProvider("coinbase_exchange_sandbox", client)
    cutoff = datetime(2026, 11, 12, 16, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="provider-quality-outcome", data_quality_state="gold")
    decision = PaperDecisionRecord(
        mission_id=uuid4(),
        snapshot_id=snapshot.artifact_id,
        subject="provider-quality-integration",
        subject_version="provider-quality-v1",
        asset="BTC",
        horizon="1h",
        cutoff=cutoff - timedelta(hours=1),
        horizon_end=cutoff,
    )
    client.get("https://sandbox.example.test/account")

    adapter = QuoteAwarePaperOutcomeAdapter(
        quote_currency="USD",
        fx_rate_provider=lambda current: QuoteToUsdRate(
            quote_currency="USD",
            usd_per_quote=Decimal("1"),
            as_of=current.as_of,
            snapshot_id=current.artifact_id,
            evidence_id=uuid4(),
            source="currency-identity/USD",
        ),
        quality_provider=quality_provider,
    )
    outcome = adapter.build_outcome(
        decision=decision,
        as_of=cutoff,
        snapshot=snapshot,
        market=RiskMarketState(marks={"BTC": Decimal("50000")}),
        fills=(),
        orders={},
        initial_marks={},
    )
    assert outcome.quality_sample_count == 1
    assert outcome.quality_failed_count == 0
    assert outcome.failure_rate == Decimal("0")
    assert outcome.latency_ms == 13
    assert outcome.quality_measurement_hash is not None

    ledgers = SqliteLedgers(tmp_path / "provider-quality.sqlite3")
    first = PaperLearningLoop(ledgers)
    first.record_decision(decision)
    first.record_outcome(outcome)
    restarted = PaperLearningLoop(ledgers)
    restored = next(
        event
        for event in ledgers.events(LedgerNamespace.MODEL)
        if event.event_type == "paper_scorecard_recorded"
    )
    restored_outcome = restored.payload["outcome"]
    assert restored_outcome["quality_sample_count"] == 1
    assert restored_outcome["quality_failed_count"] == 0
    assert restored_outcome["quality_measurement_hash"] == outcome.quality_measurement_hash
    assert restarted.record_outcome(outcome).scorecard_id == first.record_outcome(outcome).scorecard_id
