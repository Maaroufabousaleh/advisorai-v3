from datetime import UTC, datetime, timedelta

import pytest

from advisorai.contracts import AssetClass, InstrumentIdentity, PointInTimeObservation, SourceGrade


@pytest.fixture
def timestamp() -> datetime:
    return datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def btc_usdt() -> InstrumentIdentity:
    return InstrumentIdentity(
        canonical_id="crypto:BTC-USDT:approved-venue:spot",
        asset_class=AssetClass.CRYPTO,
        venue="approved-venue",
        venue_symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
    )


@pytest.fixture
def observation(btc_usdt: InstrumentIdentity, timestamp: datetime) -> PointInTimeObservation:
    return PointInTimeObservation(
        instrument=btc_usdt,
        event_time=timestamp - timedelta(minutes=5),
        source_published_at=timestamp - timedelta(minutes=4),
        first_available_at=timestamp - timedelta(minutes=3),
        ingested_at=timestamp - timedelta(minutes=2),
        raw_artifact_hash="a" * 64,
        parser_version="native-v1",
        source_family="native_venue",
        origin="approved-venue",
        quality_grade=SourceGrade.EXECUTION,
        intended_use="paper_research",
        value="100000.00",
    )
