import json
from datetime import timedelta

import pytest

from advisorai.collectors import AlfredCollector, SecEdgarCollector, VintagedReleaseCollector


def test_vintaged_release_collector_preserves_revision_and_first_available(btc_usdt, timestamp):
    body = json.dumps(
        {
            "releases": [
                {
                    "published_at": (timestamp - timedelta(days=1)).isoformat(),
                    "effective_at": timestamp.isoformat(),
                    "first_available_at": (timestamp + timedelta(minutes=5)).isoformat(),
                    "vintage": "2026-08-r2",
                    "value": "42.0",
                }
            ]
        }
    ).encode()
    observations = VintagedReleaseCollector(
        source_family="official_release", origin="official.example", parser_version="v1"
    ).parse(body, instrument=btc_usdt, ingested_at=timestamp + timedelta(minutes=6))
    assert len(observations) == 1
    observation = observations[0]
    assert observation.source_revision == "2026-08-r2"
    assert observation.first_available_at == timestamp + timedelta(minutes=5)
    assert observation.source_family == "official_release"


def test_vintaged_release_collector_rejects_naive_ingestion_time(btc_usdt, timestamp):
    collector = VintagedReleaseCollector(
        source_family="official_release", origin="official.example", parser_version="v1"
    )
    with pytest.raises(ValueError, match="timezone"):
        collector.parse(
            b'{"releases": []}',
            instrument=btc_usdt,
            ingested_at=timestamp.replace(tzinfo=None),
        )


def test_sec_and_alfred_adapters_keep_explicit_source_families(btc_usdt, timestamp):
    body = json.dumps(
        {
            "facts": [
                {
                    "published_at": timestamp.isoformat(),
                    "first_available_at": timestamp.isoformat(),
                }
            ]
        }
    ).encode()
    sec = SecEdgarCollector().parse(body, instrument=btc_usdt, ingested_at=timestamp)
    alfred = AlfredCollector().parse(body, instrument=btc_usdt, ingested_at=timestamp)
    assert sec[0].source_family == "official_sec_edgar"
    assert alfred[0].source_family == "official_alfred"
