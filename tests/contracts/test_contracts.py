from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from advisorai.contracts import (
    AgentRun,
    ArtifactReference,
    ArtifactTier,
    CapabilityCard,
    Evidence,
    Forecast,
    PointInTimeObservation,
    Reconciliation,
    RiskDecision,
    RiskOutcome,
    Snapshot,
)


def test_snapshot_rejects_future_artifact(timestamp):
    future_reference = ArtifactReference(
        tier=ArtifactTier.BRONZE,
        uri="bronze/market/date=2026-08-04/future.parquet",
        content_hash="b" * 64,
        dataset="market",
        first_available_at=timestamp + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="unavailable as_of"):
        Snapshot(as_of=timestamp, purpose="test", artifact_references=(future_reference,))


def test_forecast_rejects_future_training_data(btc_usdt, timestamp):
    with pytest.raises(ValidationError, match="training_cutoff"):
        Forecast(
            instrument=btc_usdt,
            snapshot_id="e6704f91-01c6-4a6e-b825-f49b4ee816c4",
            cutoff=timestamp,
            horizon_seconds=3600,
            target="one_hour_return",
            point_forecast=Decimal("0.01"),
            confidence=Decimal("0.5"),
            model_version="baseline-v1",
            data_hash="c" * 64,
            feature_hash="d" * 64,
            code_hash="e" * 64,
            calibration_version="cal-v1",
            training_cutoff=timestamp + timedelta(seconds=1),
            latency_ms=1,
            peak_ram_mib=1,
            peak_vram_mib=0,
        )


def test_abstained_forecast_cannot_carry_a_point_payload(btc_usdt, timestamp):
    with pytest.raises(ValidationError, match="forecast payload"):
        Forecast(
            instrument=btc_usdt,
            snapshot_id=uuid4(),
            cutoff=timestamp,
            horizon_seconds=3600,
            target="one_hour_return",
            point_forecast=Decimal("0.01"),
            confidence=Decimal("0"),
            abstained=True,
            abstention_reason="insufficient history",
            model_version="baseline-v1",
            data_hash="c" * 64,
            feature_hash="d" * 64,
            code_hash="e" * 64,
            calibration_version="cal-v1",
            training_cutoff=timestamp,
            latency_ms=1,
            peak_ram_mib=1,
            peak_vram_mib=0,
        )


def test_distribution_forecast_is_a_valid_non_point_payload(btc_usdt, timestamp):
    forecast = Forecast(
        instrument=btc_usdt,
        snapshot_id=uuid4(),
        cutoff=timestamp,
        horizon_seconds=3600,
        target="one_hour_return",
        distribution="normal(mu=0,sigma=0.01)",
        confidence=Decimal("0.5"),
        model_version="distribution-v1",
        data_hash="c" * 64,
        feature_hash="d" * 64,
        code_hash="e" * 64,
        calibration_version="cal-v1",
        training_cutoff=timestamp,
        latency_ms=1,
        peak_ram_mib=1,
        peak_vram_mib=0,
    )
    assert forecast.distribution is not None


def test_evidence_is_immutable_and_requires_expiry(timestamp):
    evidence = Evidence(
        claim="The feed is fresh.",
        source_family="native_venue",
        origin="approved-venue",
        observed_at=timestamp,
        first_available_at=timestamp,
        uncertainty=Decimal("0.1"),
        expires_at=timestamp + timedelta(minutes=5),
    )
    with pytest.raises(ValidationError):
        evidence.claim = "mutated"  # type: ignore[misc]


def test_capability_card_cannot_gain_trading_authority():
    with pytest.raises(ValidationError, match="trading authority"):
        CapabilityCard(
            name="unsafe",
            capability_version="v1",
            inputs=("input",),
            outputs=("output",),
            allowed_actions=("submit_order",),
            resource_envelope="small",
            latency_class="fast",
            deterministic=True,
        )


@pytest.mark.parametrize(
    "action",
    (
        "place_order",
        "relax_risk_limit",
        "set_risk_limit",
        "orders.create",
        "submitOrder",
        "execute_trade",
        "set_position_limit",
        "increaseLeverageLimit",
        "order",
    ),
)
def test_capability_card_rejects_trading_authority_aliases(action):
    with pytest.raises(ValidationError, match="trading authority"):
        CapabilityCard(
            name="unsafe",
            capability_version="v1",
            inputs=("input",),
            outputs=("output",),
            allowed_actions=(action,),
            resource_envelope="small",
            latency_class="fast",
            deterministic=True,
        )


def test_capability_card_allows_read_only_order_status_actions():
    card = CapabilityCard(
        name="order-reader",
        capability_version="v1",
        inputs=("query",),
        outputs=("status",),
        allowed_actions=("read_orderbook", "trade_history", "order_status"),
        resource_envelope="small",
        latency_class="fast",
        deterministic=True,
    )
    assert card.allowed_actions == ("read_orderbook", "trade_history", "order_status")


def test_reconciliation_requires_full_account_state_digest(timestamp):
    with pytest.raises(ValidationError, match="SHA-256 digest"):
        Reconciliation(
            as_of=timestamp,
            account_state_hash="z" * 64,
            reconciled=True,
        )


def test_observation_rejects_effective_time_after_ingestion(observation, timestamp):
    payload = observation.model_dump(mode="python")
    payload["effective_time"] = timestamp + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="effective_time"):
        PointInTimeObservation.model_validate(payload)


def test_rejected_risk_decision_requires_an_explanation():
    with pytest.raises(ValidationError, match="rejected risk decisions require reasons"):
        RiskDecision(
            target_portfolio_id=uuid4(),
            risk_policy_id=uuid4(),
            outcome=RiskOutcome.REJECTED,
            authoritative_state_hash="a" * 64,
        )


def test_agent_run_metadata_is_paired_and_artifact_ids_are_unique():
    with pytest.raises(ValidationError, match="provider and model route"):
        AgentRun(
            mission_id=uuid4(),
            role="technical_flow",
            mode="standard",
            snapshot_id=uuid4(),
            provider="direct",
            latency_ms=1,
        )
    artifact = uuid4()
    with pytest.raises(ValidationError, match="output artifact IDs"):
        AgentRun(
            mission_id=uuid4(),
            role="technical_flow",
            mode="standard",
            snapshot_id=uuid4(),
            input_artifact_ids=(artifact,),
            output_artifact_ids=(artifact, artifact),
            latency_ms=1,
        )
