import json
from datetime import UTC, datetime

import pytest

from advisorai.collectors import (
    DataQualityMonitor,
    HttpResponse,
    NativeVenueCollector,
    RawHttpSpool,
    SourceDescriptor,
)
from advisorai.contracts import AssetClass, InstrumentIdentity, SourceGrade
from scripts.qualify_phase3_sources import (
    Operation,
    _endpoint,
    _operation,
    _public_endpoint,
    run_evidence,
)


def _instrument():
    return InstrumentIdentity(
        canonical_id="crypto:BTC-USD:fixture:spot",
        asset_class=AssetClass.CRYPTO,
        venue="fixture",
        venue_symbol="BTC-USD",
        base_asset="BTC",
        quote_asset="USD",
    )


def _collector(tmp_path, response):
    class Transport:
        def get(self, url):
            return response.model_copy(update={"url": url})

    return NativeVenueCollector(
        SourceDescriptor(
            name="fixture-native",
            family="crypto_market",
            origin="fixture",
            grade=SourceGrade.EXECUTION,
            intended_use="test",
            parser_version="fixture-v1",
        ),
        Transport(),
        raw_spool=RawHttpSpool(tmp_path / "raw.jsonl"),
    )


def test_source_operation_replays_raw_bytes_and_rejects_duplicate_append(tmp_path):
    available_at = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
    body = json.dumps(
        {
            "symbol": "BTC-USD",
            "price": "100",
            "time": available_at.isoformat().replace("+00:00", "Z"),
        }
    ).encode()
    collector = _collector(
        tmp_path,
        HttpResponse(
            status_code=200, body=body, fetched_at=available_at, url="https://fixture.test"
        ),
    )
    result, observations = _operation(
        Operation(
            name="fixture_native",
            source="native_venue",
            url="https://fixture.test",
            instrument=_instrument(),
            max_age_seconds=3600,
        ),
        collector,
        monitor=DataQualityMonitor(),
    )
    assert result["passed"] is True
    assert result["replay_match"] is True
    assert result["duplicate_raw_append_rejected"] is True
    assert len(observations) == 1


def test_source_operation_preserves_sanitized_http_failure(tmp_path):
    collector = _collector(
        tmp_path,
        HttpResponse(
            status_code=503,
            body=b"provider unavailable with no credentials",
            fetched_at=datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
            url="https://fixture.test",
        ),
    )
    result, observations = _operation(
        Operation(
            name="fixture_native_failure",
            source="native_venue",
            url="https://fixture.test",
            instrument=_instrument(),
            max_age_seconds=3600,
        ),
        collector,
        monitor=DataQualityMonitor(),
        as_of=datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
    )
    assert result["passed"] is False
    assert result["error"]["error_class"] == "RuntimeError"
    assert result["raw_responses"][0]["status_code"] == 503
    assert "provider unavailable" not in json.dumps(result)
    assert observations == ()


def test_phase3_runner_rejects_coinbase_production_without_network(tmp_path):
    with pytest.raises(ValueError, match="production Coinbase endpoints"):
        run_evidence(tmp_path, native_url="https://api.exchange.coinbase.com")


def test_phase3_endpoint_rejects_a_path_on_a_base_url():
    with pytest.raises(ValueError, match="must not contain a path"):
        _endpoint("https://api-public.sandbox.exchange.coinbase.com/products")


def test_phase3_public_endpoint_rejects_secret_like_query_parameters():
    with pytest.raises(ValueError, match="secret-like query"):
        _public_endpoint("https://public.example.test/feed?api_key=should-not-be-here")
