"""Contract tests for the Binance Spot Testnet adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from pydantic import SecretStr

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    BinanceSpotSigner,
    BinanceSpotTestnetTransport,
    VenueTransportError,
    build_binance_spot_testnet_transport,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient


def _client_for(responses):
    calls = []

    def requester(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), body))
        return responses.pop(0)

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,),
            max_retries=0,
            requests_per_second=100,
        ),
        base_url=BINANCE_SPOT_TESTNET_BASE_URL,
        requester=requester,
        sleeper=lambda _: None,
    )
    return client, calls


def _transport(responses):
    client, calls = _client_for(responses)
    transport = BinanceSpotTestnetTransport(
        client,
        BinanceSpotSigner(api_key="fixture-key", api_secret=SecretStr("fixture-secret")),
        timestamp_provider=lambda: 1700000000000,
    )
    return transport, calls


def _symbol(symbol, base, quote):
    return {
        "symbol": symbol,
        "status": "TRADING",
        "baseAsset": base,
        "quoteAsset": quote,
        "filters": [
            {
                "filterType": "PRICE_FILTER",
                "tickSize": "0.10",
                "minPrice": "0.10",
                "maxPrice": "1000000",
            },
            {
                "filterType": "LOT_SIZE",
                "stepSize": "0.001",
                "minQty": "0.001",
                "maxQty": "1000",
            },
            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
        ],
    }


def _symbols():
    return [_symbol("BTCUSDT", "BTC", "USDT"), _symbol("ETHUSDT", "ETH", "USDT")]


def _account():
    return {
        "accountType": "SPOT",
        "canTrade": True,
        "balances": [
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "BTC", "free": "0.010", "locked": "0"},
            {"asset": "ETH", "free": "0.0", "locked": "0"},
        ],
    }


def test_binance_signer_canonicalizes_query_and_uses_raw_hmac_secret():
    signer = BinanceSpotSigner(api_key="key", api_secret=SecretStr("secret"))
    query = signer.signed_query({"timestamp": 1700000000000, "symbol": "BTCUSDT"})
    unsigned = "symbol=BTCUSDT&timestamp=1700000000000"
    expected = hmac.new(b"secret", unsigned.encode(), hashlib.sha256).hexdigest()
    assert query == f"{unsigned}&signature={expected}"
    assert signer.headers() == {"X-MBX-APIKEY": "key"}


def test_binance_adapter_rejects_production_and_non_spot_paths():
    transport, _calls = _transport([])
    for path in (
        "/api/v3/sapi/account",
        "/api/v3/transfer",
        "/api/v3/withdraw",
        "/fapi/v1/order",
    ):
        with pytest.raises(VenueTransportError, match="prohibited"):
            transport._path_url(path)
    with pytest.raises(ValueError, match="Spot Testnet base URL"):
        BinanceSpotTestnetTransport(
            SafeHttpClient(
                HttpClientConfig(allowed_hosts=("api.binance.com",)),
                base_url="https://api.binance.com",
                requester=lambda *_: (200, b"{}", ()),
            ),
            BinanceSpotSigner(api_key="key", api_secret=SecretStr("secret")),
        )


def test_binance_product_truth_requires_btc_and_eth():
    transport, _calls = _transport([])
    admitted = transport.verify_symbol_mappings(_symbols())
    assert tuple(item.symbol for item in admitted) == ("BTCUSDT", "ETHUSDT")
    assert transport.verified_symbol_ids == ("BTCUSDT", "ETHUSDT")
    with pytest.raises(VenueTransportError, match="does not contain ETHUSDT"):
        transport.verify_symbol_mappings(_symbols()[:1])


def test_binance_read_schema_maps_account_balances_positions_and_snapshot():
    responses = [
        (200, json.dumps({"serverTime": 1700000000000}).encode(), ()),
        (200, json.dumps({"symbols": _symbols()}).encode(), ()),
        (200, json.dumps(_account()).encode(), ()),
        (200, json.dumps(_account()).encode(), ()),
        (200, json.dumps(_account()).encode(), ()),
    ]
    transport, calls = _transport(responses)
    transport.server_time()
    products = transport.list_products()
    transport.verify_symbol_mappings(products)
    assert tuple(item["symbol"] for item in products) == ("BTCUSDT", "ETHUSDT")
    assert len(transport.list_balances()) == 3
    positions = transport.list_positions()
    assert positions[0]["symbol"] == "BTCUSDT"
    snapshot = transport.fetch_account_snapshot()
    assert snapshot.cash == Decimal("1000")
    assert snapshot.positions == {"crypto:BTCUSDT:binance_spot_testnet:spot": Decimal("0.010")}
    assert len(calls) == 5
    assert "X-MBX-APIKEY" not in calls[0][2]
    assert all("X-MBX-APIKEY" in calls[index][2] for index in (2, 3, 4))


def test_binance_account_read_fails_closed_when_trading_permission_is_missing():
    transport, _calls = _transport(
        [(200, json.dumps({**_account(), "canTrade": False}).encode(), ())]
    )
    with pytest.raises(VenueTransportError, match="not trade-enabled"):
        transport.account_state()


def test_binance_order_submission_is_signed_once_and_cancel_is_idempotency_bound():
    responses = [
        (
            200,
            json.dumps(
                {
                    "symbol": "BTCUSDT",
                    "orderId": 42,
                    "clientOrderId": "local-order-1",
                    "status": "NEW",
                }
            ).encode(),
            (),
        ),
        (
            200,
            json.dumps(
                {
                    "symbol": "BTCUSDT",
                    "orderId": 42,
                    "clientOrderId": "local-order-1",
                    "status": "CANCELED",
                }
            ).encode(),
            (),
        ),
    ]
    transport, calls = _transport(responses)
    transport.verify_symbol_mappings(_symbols())
    acknowledgement = transport.submit_order(
        {
            "client_order_id": "local-order-1",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": "0.001",
            "order_type": "passive_limit",
            "price": "10000.00",
            "time_in_force": "gtc",
        }
    )
    assert acknowledgement["accepted"] is True
    assert acknowledgement["venue_order_id"] == "42"
    cancellation = transport.cancel_order(client_order_id="local-order-1")
    assert cancellation == {
        "client_order_id": "local-order-1",
        "venue_order_id": "42",
        "cancelled": True,
    }
    assert len(calls) == 2
    assert all("signature=" in call[1] for call in calls)
    assert all("X-MBX-APIKEY" in call[2] for call in calls)


def test_binance_write_rejects_bad_filters_before_network():
    transport, calls = _transport([])
    transport.verify_symbol_mappings(_symbols())
    with pytest.raises(VenueTransportError, match="step size"):
        transport.submit_order(
            {
                "client_order_id": "local-order-1",
                "symbol": "BTCUSDT",
                "side": "buy",
                "quantity": "0.0001",
                "order_type": "limit",
                "price": "100.00",
            }
        )
    assert calls == []


def test_binance_terminal_order_projection_is_not_an_open_acknowledgement():
    transport, _calls = _transport([])
    normalized = transport._normalize_order(
        {
            "symbol": "BTCUSDT",
            "orderId": 42,
            "clientOrderId": "local-order-1",
            "status": "CANCELED",
        }
    )
    assert normalized["accepted"] is False


def test_binance_restart_query_searches_admitted_symbols_without_resubmitting():
    responses = [
        (400, b'{"code":-2013,"msg":"Order does not exist."}', ()),
        (
            200,
            json.dumps(
                {
                    "symbol": "ETHUSDT",
                    "orderId": 99,
                    "clientOrderId": "local-order-1",
                    "status": "FILLED",
                }
            ).encode(),
            (),
        ),
    ]
    transport, calls = _transport(responses)
    transport.verify_symbol_mappings(_symbols())
    result = transport.query_order(client_order_id="local-order-1")
    assert result is not None
    assert result["venue_order_id"] == "99"
    assert result["accepted"] is False
    assert "symbol=BTCUSDT" in calls[0][1]
    assert "symbol=ETHUSDT" in calls[1][1]


def test_binance_write_rejects_non_provider_admissible_client_id():
    transport, calls = _transport([])
    transport.verify_symbol_mappings(_symbols())
    with pytest.raises(VenueTransportError, match="client ID"):
        transport.submit_order(
            {
                "client_order_id": "local order with spaces",
                "symbol": "BTCUSDT",
                "side": "buy",
                "quantity": "0.001",
                "order_type": "passive_limit",
                "price": "10000.00",
            }
        )
    assert calls == []


def test_binance_builder_is_scoped_and_rejects_coinbase_identity():
    resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://api-public.sandbox.exchange.coinbase.com",
            "ADVISORAI_VENUE_API_KEY": "key",
            "ADVISORAI_VENUE_API_SECRET": "secret",
            "ADVISORAI_VENUE_PASSPHRASE": "passphrase",
        }
    )
    with pytest.raises(ValueError, match="binance_spot_testnet"):
        build_binance_spot_testnet_transport(resolver)
