"""Kraken spot, Deribit perpetual, and BLS collector contract tests.

All fixtures are synthetic representative payloads shaped like the real public
APIs; no test performs network I/O and no credential is required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from advisorai.collectors import (
    BeaCollector,
    BlsCollector,
    DeribitCollector,
    EiaCollector,
    KrakenSpotCollector,
    SourceDescriptor,
    reviewed_public_market_data_sources,
)
from advisorai.collectors.official import VintagedReleaseCollector
from advisorai.contracts import AssetClass, InstrumentIdentity, SourceGrade

BTC = InstrumentIdentity(
    canonical_id="crypto:XBTUSD:kraken:spot",
    asset_class=AssetClass.CRYPTO,
    venue="kraken",
    venue_symbol="XBTUSD",
    base_asset="XBT",
    quote_asset="USD",
)

BTC_PERP = InstrumentIdentity(
    canonical_id="crypto:BTC-PERPETUAL:deribit:perpetual",
    asset_class=AssetClass.CRYPTO,
    venue="deribit",
    venue_symbol="BTC-PERPETUAL",
    base_asset="BTC",
    quote_asset="USD",
)

CPI = InstrumentIdentity(
    canonical_id="macro:CUUR0000SA0:bls:cpi",
    asset_class=AssetClass.OTHER,
    venue="bls",
    venue_symbol="CUUR0000SA0",
    base_asset="CPI",
    quote_asset="INDEX",
)

AVAILABLE_AT = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _descriptor(name: str, family: str, origin: str, version: str) -> SourceDescriptor:
    return SourceDescriptor(
        name=name,
        family=family,
        origin=origin,
        grade=SourceGrade.CONTEXT,
        intended_use="context_only",
        parser_version=version,
    )


def test_kraken_spot_parses_ticker_result_to_point_in_time_observation() -> None:
    collector = KrakenSpotCollector(_descriptor("kraken-spot", "spot", "kraken", "kraken-spot-v1"))
    body = json.dumps(
        {
            "error": [],
            "result": {
                "XXBTZUSD": {
                    "a": ["70456.10000", "2", "2.000"],
                    "b": ["70456.00000", "1", "1.000"],
                    "c": ["70456.10000", "0.00007097"],
                    "o": "71370.70000",
                }
            },
        }
    ).encode()

    observations = collector.parse(body, instrument=BTC, available_at=AVAILABLE_AT)

    assert len(observations) == 1
    (observation,) = observations
    assert observation.instrument == BTC
    assert observation.first_available_at == AVAILABLE_AT
    assert observation.ingested_at == AVAILABLE_AT
    assert observation.parser_version == "kraken-spot-v1"
    assert observation.source_family == "spot"
    assert observation.origin == "kraken"
    payload = json.loads(observation.value)
    assert payload["kraken_pair"] == "XXBTZUSD"
    assert len(observation.raw_artifact_hash) == 64


def test_kraken_spot_fails_closed_on_api_errors() -> None:
    collector = KrakenSpotCollector(_descriptor("kraken-spot", "spot", "kraken", "kraken-spot-v1"))
    body = json.dumps({"error": ["EQuery:Unknown asset pair"], "result": {}}).encode()

    with pytest.raises(RuntimeError, match="kraken spot returned errors"):
        collector.parse(body, instrument=BTC, available_at=AVAILABLE_AT)


def test_kraken_spot_rejects_empty_result() -> None:
    collector = KrakenSpotCollector(_descriptor("kraken-spot", "spot", "kraken", "kraken-spot-v1"))
    with pytest.raises(ValueError, match="non-empty result"):
        collector.parse(b'{"error": [], "result": {}}', instrument=BTC, available_at=AVAILABLE_AT)


def test_deribit_ticker_result_parses_to_point_in_time_observation() -> None:
    collector = DeribitCollector(_descriptor("deribit", "derivatives", "deribit", "deribit-v1"))
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "result": {
                "instrument_name": "BTC-PERPETUAL",
                "timestamp": 1762958400000,
                "last_price": 70456.0,
                "state": "open",
            },
        }
    ).encode()

    observations = collector.parse(body, instrument=BTC_PERP, available_at=AVAILABLE_AT)

    assert len(observations) == 1
    (observation,) = observations
    assert observation.instrument == BTC_PERP
    assert observation.first_available_at == AVAILABLE_AT
    assert observation.parser_version == "deribit-v1"
    assert json.loads(observation.value)["instrument_name"] == "BTC-PERPETUAL"


def test_bls_parses_monthly_vintages_without_leakage() -> None:
    collector = BlsCollector()
    body = json.dumps(
        {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [
                            {
                                "year": "2025",
                                "period": "M12",
                                "periodName": "December",
                                "value": "324.054",
                                "footnotes": [{}],
                            },
                            {
                                "year": "2025",
                                "period": "M11",
                                "periodName": "November",
                                "value": "324.122",
                                "footnotes": [{}],
                            },
                            {
                                "year": "2025",
                                "period": "M10",
                                "periodName": "October",
                                "value": "-",
                                "footnotes": [{"code": "X", "text": "missing"}],
                            },
                        ],
                    }
                ]
            },
        }
    ).encode()

    observations = collector.parse(body, instrument=CPI, ingested_at=AVAILABLE_AT)

    assert len(observations) == 2
    assert [item.source_published_at for item in observations] == [
        datetime(2025, 12, 1, tzinfo=UTC),
        datetime(2025, 11, 1, tzinfo=UTC),
    ]
    assert all(item.first_available_at == item.source_published_at for item in observations)
    assert all(item.parser_version == "bls-v1" for item in observations)
    assert all(item.source_family == "official_bls" for item in observations)
    first = json.loads(observations[0].value)
    assert first["series_id"] == "CUUR0000SA0"
    assert first["value"] == "324.054"


def test_bls_rejects_missing_results_and_unusable_periods() -> None:
    collector = BlsCollector()
    with pytest.raises(ValueError, match="Results object"):
        collector.parse(
            b'{"status": "REQUEST_SUCCEEDED"}', instrument=CPI, ingested_at=AVAILABLE_AT
        )
    only_bad_period = json.dumps(
        {
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [{"year": "2025", "period": "Q01", "value": "1.0"}],
                    }
                ]
            }
        }
    ).encode()
    with pytest.raises(ValueError, match="no usable observations"):
        collector.parse(only_bad_period, instrument=CPI, ingested_at=AVAILABLE_AT)
    mixed_period = json.dumps(
        {
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [
                            {"year": "2025", "period": "M13", "value": "321.943"},
                            {
                                "year": "2025",
                                "period": "M12",
                                "periodName": "December",
                                "value": "324.054",
                                "footnotes": [],
                            },
                        ],
                    }
                ]
            }
        }
    ).encode()
    (kept,) = collector.parse(mixed_period, instrument=CPI, ingested_at=AVAILABLE_AT)
    assert json.loads(kept.value)["period"] == "M12"


def test_reviewed_public_sources_include_kraken_card() -> None:
    sources = {item.source_id: item for item in reviewed_public_market_data_sources()}

    kraken = sources["kraken_spot_public_market_data"]
    assert kraken.role == "primary_candidate"
    assert kraken.rest_host == "api.kraken.com"
    assert kraken.credentials_required is False
    assert kraken.write_capability is False
    assert "XBTUSD" in kraken.symbols
    # Kraken remains a market-data context candidate, not execution authority.
    assert "deribit_public_context" in sources


def test_bls_collector_carries_vintaged_release_contract() -> None:
    assert isinstance(BlsCollector(), VintagedReleaseCollector)


GDP = InstrumentIdentity(
    canonical_id="macro:GDP:bea:quarterly",
    asset_class=AssetClass.OTHER,
    venue="bea",
    venue_symbol="GDP",
    base_asset="GDP",
    quote_asset="USD",
)

BRENT = InstrumentIdentity(
    canonical_id="macro:PET.WTTOTRR: eia:weekly".replace(" ", ""),
    asset_class=AssetClass.OTHER,
    venue="eia",
    venue_symbol="PET.WTTOTRR",
    base_asset="WTI",
    quote_asset="USD",
)


def test_bea_parses_quarterly_vintages_without_backfill_leakage() -> None:
    collector = BeaCollector()
    body = json.dumps(
        {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {
                            "SeriesCode": "GDP",
                            "TimePeriod": "2025Q3",
                            "ReleaseDate": "2025-09-26T08:30:00",
                            "DataValue": "23500.1",
                            "UNIT_MULT": "6",
                        },
                        {
                            "SeriesCode": "GDP",
                            "TimePeriod": "2025Q2",
                            "ReleaseDate": "2025-06-27",
                            "DataValue": "23300.4",
                            "UNIT_MULT": "6",
                        },
                    ]
                }
            }
        }
    ).encode()

    observations = collector.parse(body, instrument=GDP, ingested_at=AVAILABLE_AT)

    assert len(observations) == 2
    assert observations[0].source_published_at == datetime(2025, 9, 26, 8, 30, tzinfo=UTC)
    assert observations[1].source_published_at == datetime(2025, 6, 27, tzinfo=UTC)
    assert all(item.first_available_at == item.source_published_at for item in observations)
    assert all(item.parser_version == "bea-v1" for item in observations)
    assert all(item.source_family == "official_bea" for item in observations)


def test_bea_skips_rows_without_usable_vintage() -> None:
    collector = BeaCollector()
    body = json.dumps(
        {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {"SeriesCode": "GDP", "TimePeriod": "n/a", "DataValue": "1.0"},
                        {
                            "SeriesCode": "GDP",
                            "TimePeriod": "2025",
                            "DataValue": "2.0",
                        },
                    ]
                }
            }
        }
    ).encode()

    (kept,) = collector.parse(body, instrument=GDP, ingested_at=AVAILABLE_AT)
    assert json.loads(kept.value)["time_period"] == "2025"


def test_eia_parses_period_vintages_without_backfill_leakage() -> None:
    collector = EiaCollector()
    body = json.dumps(
        {
            "response": {
                "data": [
                    {
                        "period": "2025-11-07",
                        "value": "61.5",
                        "series-description": "Spot Price",
                        "units": "$/bbl",
                    },
                    {
                        "period": "2025-10",
                        "value": "62.1",
                        "series-description": "Spot Price",
                        "units": "$/bbl",
                    },
                ]
            }
        }
    ).encode()

    observations = collector.parse(body, instrument=BRENT, ingested_at=AVAILABLE_AT)

    assert len(observations) == 2
    assert observations[0].source_published_at == datetime(2025, 11, 7, tzinfo=UTC)
    assert observations[1].source_published_at == datetime(2025, 10, 1, tzinfo=UTC)
    assert all(item.first_available_at == item.source_published_at for item in observations)
    assert all(item.parser_version == "eia-v1" for item in observations)
    assert all(item.source_family == "official_eia" for item in observations)


def test_eia_rejects_unusable_periods() -> None:
    collector = EiaCollector()
    body = json.dumps({"response": {"data": [{"period": "soon", "value": "1.0"}]}}).encode()
    with pytest.raises(ValueError, match="no usable observations"):
        collector.parse(body, instrument=BRENT, ingested_at=AVAILABLE_AT)
