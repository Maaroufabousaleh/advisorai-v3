"""Offline contracts for the credential-free paper venue comparison."""

from __future__ import annotations

import pytest

from scripts.qualify_paper_venue_candidates import (
    CANDIDATES,
    REQUIRED_SYMBOLS,
    _binance_products,
    _bybit_product,
    _operation,
    select_candidate,
)


def _binance_symbol(symbol: str, base: str):
    return {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": "USDT",
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
            {"filterType": "NOTIONAL", "minNotional": "5"},
        ],
    }


def _bybit_symbol(symbol: str, base: str):
    return {
        "symbol": symbol,
        "baseCoin": base,
        "quoteCoin": "USDT",
        "status": "Trading",
        "priceFilter": {"tickSize": "0.01"},
        "lotSizeFilter": {"basePrecision": "0.0001", "minOrderQty": "0.0001", "minOrderAmt": "5"},
    }


def test_public_product_parsers_require_provider_truth_for_btc_and_eth():
    result = _binance_products(
        {"symbols": [_binance_symbol("BTCUSDT", "BTC"), _binance_symbol("ETHUSDT", "ETH")]}
    )
    assert set(result["required_symbols"]) == set(REQUIRED_SYMBOLS)
    assert result["required_symbols"]["BTCUSDT"]["tick_size"] == "0.01"

    bybit = _bybit_product({"retCode": 0, "result": {"list": [_bybit_symbol("ETHUSDT", "ETH")]}})
    assert bybit["symbol"] == "ETHUSDT"
    assert bybit["product_status"] == "Trading"
    assert bybit["min_notional"] == "5"

    with pytest.raises(ValueError, match="missing ETHUSDT"):
        _binance_products({"symbols": [_binance_symbol("BTCUSDT", "BTC")]})


def test_candidate_selection_prefers_binance_when_public_truth_passes():
    selected = select_candidate(
        {
            "binance_spot_testnet": {"public_status": "passed"},
            "bybit_spot_testnet": {"public_status": "passed"},
        }
    )
    assert selected["venue"] == "binance_spot_testnet"
    assert selected["status"] == "selected_for_authenticated_qualification"


def test_candidate_selection_falls_back_to_bybit_only_if_binance_fails():
    selected = select_candidate(
        {
            "binance_spot_testnet": {"public_status": "failed"},
            "bybit_spot_testnet": {"public_status": "passed"},
        }
    )
    assert selected["venue"] == "bybit_spot_testnet"


def test_candidates_have_nonproduction_hosts_and_no_write_paths():
    assert {candidate.name for candidate in CANDIDATES} == {
        "binance_spot_testnet",
        "bybit_spot_testnet",
    }
    for candidate in CANDIDATES:
        assert candidate.rest_base_url.startswith("https://")
        assert "testnet" in candidate.reviewed_rest_host
        assert candidate.reviewed_rest_host not in candidate.rejected_production_hosts
        assert "withdraw" not in " ".join(candidate.public_paths.values())
        assert "transfer" not in " ".join(candidate.public_paths.values())
        assert candidate.private_capabilities["fake_funds"] is True
        assert candidate.private_capabilities["private_api_requires_credentials"] is True

    assert "stream.binance.com" in CANDIDATES[0].rejected_production_hosts


def test_operation_status_cannot_be_overwritten_by_provider_product_status():
    class Response:
        status_code = 200
        body = b"{}"

    class Client:
        def request(self, *_args, **_kwargs):
            return Response()

    record, _summary = _operation(
        Client(),
        CANDIDATES[1],
        "product_ethusdt",
        "/v5/market/instruments-info",
        lambda _payload: {"status": "Trading"},
    )
    assert record["status"] == "ok"
