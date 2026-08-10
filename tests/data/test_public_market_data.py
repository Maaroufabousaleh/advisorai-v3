from __future__ import annotations

import pytest

from advisorai.collectors.public_market_data import (
    PublicMarketDataSource,
    reviewed_public_market_data_sources,
)
from scripts.qualify_phase3_public_market_data import (
    _book_summary,
    _cross_source_comparison,
    _select_primary,
)


def test_reviewed_public_cards_are_credential_free_and_read_only():
    sources = reviewed_public_market_data_sources()

    assert [source.source_id for source in sources] == [
        "binance_spot_public_market_data",
        "coinbase_exchange_public_market_data",
        "deribit_public_context",
    ]
    assert all(source.credentials_required is False for source in sources)
    assert all(source.write_capability is False for source in sources)
    assert all(source.rest_base_url.startswith("https://") for source in sources)
    assert all(source.ws_url.startswith("wss://") for source in sources)


def test_public_card_rejects_credentials_and_order_paths():
    with pytest.raises(ValueError, match="credential-free HTTPS root"):
        PublicMarketDataSource(
            source_id="bad",
            role="primary_candidate",
            rest_base_url="https://api.example.test/order",
            rest_host="api.example.test",
            ws_url="wss://stream.example.test/ws",
            ws_host="stream.example.test",
            symbols=("BTC", "ETH"),
            adapter_version="test",
        )
    with pytest.raises(ValueError, match="credential-free read URL"):
        PublicMarketDataSource(
            source_id="bad",
            role="primary_candidate",
            rest_base_url="https://api.example.test",
            rest_host="api.example.test",
            ws_url="wss://stream.example.test/order",
            ws_host="stream.example.test",
            symbols=("BTC", "ETH"),
            adapter_version="test",
        )


def test_primary_selection_requires_rest_and_wss_pass():
    candidates = [
        {
            "source_id": "binance_spot_public_market_data",
            "role": "primary_candidate",
            "rest": {"required_read_state": "failed"},
            "websocket": {"state": "pass"},
        },
        {
            "source_id": "coinbase_exchange_public_market_data",
            "role": "primary_candidate",
            "rest": {"required_read_state": "pass"},
            "websocket": {
                "state": "pass",
                "reconnect": {"BTCUSDT": {"status": "pass"}},
                "freshness": {"state": "pass"},
            },
        },
    ]

    assert _select_primary(candidates) == "coinbase_exchange_public_market_data"


def test_book_summary_preserves_sanitized_top_levels_for_comparison():
    summary = _book_summary({"bids": [["100", "2"]], "asks": [["101", "3"]]})

    assert summary["top_bid"] == {"price": "100", "size": "2"}
    assert summary["top_ask"] == {"price": "101", "size": "3"}


def test_cross_source_comparison_is_observational_and_never_substitutes():
    candidates = [
        {
            "source_id": "binance_spot_public_market_data",
            "rest": {
                "markets": {
                    "BTCUSDT": {
                        "order_book": _book_summary(
                            {"bids": [["100", "2"]], "asks": [["101", "3"]]}
                        )
                    }
                }
            },
        },
        {
            "source_id": "coinbase_exchange_public_market_data",
            "rest": {
                "markets": {
                    "BTC-USD": {
                        "order_book": _book_summary(
                            {"bids": [["100.5", "2"]], "asks": [["101.5", "3"]]}
                        )
                    }
                }
            },
        },
    ]

    comparison = _cross_source_comparison(candidates)

    assert comparison["status"] == "measured"
    assert comparison["observations"]["BTC"]["silent_substitution"] is False
