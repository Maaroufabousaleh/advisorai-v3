from datetime import timedelta

import pyarrow.parquet as pq
import pytest

from advisorai.contracts import ArtifactTier
from advisorai.lake import DataLake


def test_bronze_rebuild_is_content_addressed_and_deterministic(tmp_path, timestamp):
    lake = DataLake(tmp_path / "lake")
    kwargs = {
        "dataset": "native_market_messages",
        "payload": b'{"price":"100000.00","symbol":"BTCUSDT"}',
        "source_family": "native_venue",
        "origin": "approved-venue",
        "first_available_at": timestamp,
        "ingested_at": timestamp + timedelta(seconds=1),
        "parser_version": "native-v1",
    }
    first = lake.write_bronze(**kwargs)
    retry = lake.write_bronze(**kwargs)
    rebuilt = DataLake(tmp_path / "rebuilt-lake").write_bronze(**kwargs)

    assert rebuilt == first
    assert retry == first
    assert (tmp_path / "lake" / first.uri).exists()
    assert (tmp_path / "lake" / first.manifest_uri).exists()
    assert (tmp_path / "rebuilt-lake" / rebuilt.manifest_uri).read_bytes() == (
        tmp_path / "lake" / first.manifest_uri
    ).read_bytes()
    assert lake.read_rows(first)[0]["payload"] == kwargs["payload"]
    table = pq.read_table(tmp_path / "lake" / first.uri)
    assert table.column("payload").to_pylist() == [kwargs["payload"]]


def test_manifest_read_detects_parquet_corruption(tmp_path, observation):
    lake = DataLake(tmp_path / "lake")
    manifest = lake.write_observations(
        tier=ArtifactTier.SILVER, dataset="market", observations=(observation,)
    )
    (tmp_path / "lake" / manifest.uri).write_bytes(b"corrupt")
    with pytest.raises(Exception, match="immutable artifact|Parquet"):
        lake.read_manifest(manifest)


def test_silver_observations_write_immutable_parquet(tmp_path, observation):
    lake = DataLake(tmp_path / "lake")
    manifest = lake.write_observations(
        tier=ArtifactTier.SILVER,
        dataset="normalized_market",
        observations=(observation,),
    )
    assert manifest.tier is ArtifactTier.SILVER
    assert manifest.row_count == 1
    assert (tmp_path / "lake" / manifest.uri).exists()


def test_bronze_rejects_naive_availability_and_ingestion_times(tmp_path):
    from datetime import datetime

    lake = DataLake(tmp_path / "lake")
    with __import__("pytest").raises(ValueError, match="timezone"):
        lake.write_bronze(
            dataset="market",
            payload=b"raw",
            source_family="native",
            origin="venue",
            first_available_at=datetime(2026, 8, 4),
            ingested_at=datetime(2026, 8, 4),
            parser_version="v1",
        )
