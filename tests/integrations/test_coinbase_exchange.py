"""Contract tests for the Coinbase Exchange Sandbox adapter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_URL,
    CoinbaseExchangeSandboxTransport,
    CoinbaseExchangeSigner,
    VenueTransportError,
    build_coinbase_exchange_sandbox_transport,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient

SECRET = base64.b64encode(b"coinbase-fixture-secret").decode("ascii")


def _client_for(responses):
    calls = []

    def requester(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), body))
        return responses.pop(0)

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(COINBASE_EXCHANGE_SANDBOX_HOST,),
            max_retries=0,
            requests_per_second=100,
        ),
        base_url=COINBASE_EXCHANGE_SANDBOX_BASE_URL,
        requester=requester,
        sleeper=lambda _: None,
    )
    return client, calls


def _transport(responses):
    client, calls = _client_for(responses)
    transport = CoinbaseExchangeSandboxTransport(
        client,
        CoinbaseExchangeSigner(
            api_key="fixture-key",
            api_secret=SecretStr(SECRET),
            passphrase=SecretStr("fixture-passphrase"),
        ),
        timestamp_provider=lambda: "1700000000.123",
    )
    return transport, calls


def _products():
    return [
        {
            "id": "BTC-USD",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "base_min_size": "0.00001",
            "base_max_size": "1000",
            "min_market_funds": "1",
            "status": "online",
        },
        {
            "id": "ETH-USD",
            "base_currency": "ETH",
            "quote_currency": "USD",
            "base_increment": "0.000001",
            "quote_increment": "0.01",
            "base_min_size": "0.001",
            "base_max_size": "10000",
            "min_market_funds": "1",
            "status": "online",
        },
    ]


def _accounts():
    return [
        {
            "id": "usd-account",
            "currency": "USD",
            "balance": "1000",
            "available": "1000",
            "hold": "0",
            "profile_id": "profile-fixture",
            "trading_enabled": True,
        },
        {
            "id": "btc-account",
            "currency": "BTC",
            "balance": "0",
            "available": "0",
            "hold": "0",
            "profile_id": "profile-fixture",
            "trading_enabled": True,
        },
    ]


def test_coinbase_signer_uses_base64_decoded_secret_seconds_and_exact_body():
    signer = CoinbaseExchangeSigner(
        api_key="key",
        api_secret=SecretStr(SECRET),
        passphrase=SecretStr("passphrase"),
    )
    body = b'{"price":"1.0"}'
    headers = signer.sign(
        method="post",
        request_path="/orders?ignored=from-url",
        timestamp="1700000000.123",
        body=body,
    )
    expected = hmac.new(
        base64.b64decode(SECRET),
        b"1700000000.123POST/orders" + body,
        hashlib.sha256,
    ).digest()
    assert headers["CB-ACCESS-SIGN"] == base64.b64encode(expected).decode("ascii")
    assert headers["CB-ACCESS-TIMESTAMP"] == "1700000000.123"
    assert set(headers) == {
        "CB-ACCESS-KEY",
        "CB-ACCESS-SIGN",
        "CB-ACCESS-TIMESTAMP",
        "CB-ACCESS-PASSPHRASE",
    }


def test_coinbase_signer_rejects_non_base64_secret_and_absolute_url():
    with pytest.raises(ValueError, match="valid base64"):
        CoinbaseExchangeSigner(
            api_key="key",
            api_secret=SecretStr("not base64 !!!"),
            passphrase=SecretStr("passphrase"),
        )
    signer = CoinbaseExchangeSigner(
        api_key="key",
        api_secret=SecretStr(SECRET),
        passphrase=SecretStr("passphrase"),
    )
    with pytest.raises(ValueError, match="absolute path"):
        signer.sign(method="GET", request_path="https://production.example/orders", timestamp="1")


def test_coinbase_transport_rejects_transfer_withdrawal_and_production_path_segments():
    transport, _calls = _transport([])
    for path in ("/transfers", "/withdrawals", "/prod/orders", "/production/orders"):
        with pytest.raises(VenueTransportError, match="prohibited"):
            transport._path_url(path)


def test_coinbase_product_truth_admits_btc_and_eth_and_rejects_missing_mapping():
    transport, _calls = _transport([])
    admitted = transport.verify_product_mappings(_products())
    assert tuple(item.product_id for item in admitted) == ("BTC-USD", "ETH-USD")
    assert transport.verified_product_ids == ("BTC-USD", "ETH-USD")
    with pytest.raises(VenueTransportError, match="does not contain ETH-USD"):
        transport.verify_product_mappings(_products()[:1])


def test_coinbase_product_verification_ignores_unrelated_catalogue_schema_variants():
    transport, _calls = _transport([])
    unrelated = {
        "id": "UNRELATED-USD",
        "base_currency": "UNRELATED",
        "quote_currency": "USD",
        "status": "offline",
    }
    admitted = transport.verify_product_mappings([*_products(), unrelated])
    assert tuple(item.product_id for item in admitted) == ("BTC-USD", "ETH-USD")


def test_coinbase_read_schema_maps_accounts_to_balances_positions_and_snapshot():
    responses = [
        (200, json.dumps({"iso": "2026-08-09T18:00:00Z", "epoch": 1786298400}).encode(), ()),
        (200, json.dumps(_products()).encode(), ()),
        (200, json.dumps(_accounts()).encode(), ()),
        (200, json.dumps(_accounts()).encode(), ()),
        (200, json.dumps(_accounts()).encode(), ()),
        (200, json.dumps(_accounts()).encode(), ()),
    ]
    transport, calls = _transport(responses)
    server = transport.server_time()
    products = transport.list_products()
    transport.verify_product_mappings(products)
    assert server["epoch"] == 1786298400
    assert len(transport.account_state()["accounts"]) == 2
    assert len(transport.list_balances()) == 2
    assert transport.list_positions() == ()
    snapshot = transport.fetch_account_snapshot()
    assert snapshot.cash == Decimal("1000")
    assert snapshot.positions == {}
    assert len(calls) == 6
    assert all("CB-ACCESS-KEY" not in calls[index][2] for index in (0, 1))
    assert all("CB-ACCESS-KEY" in calls[index][2] for index in (2, 3, 4))


def test_coinbase_fills_use_product_filter_and_signature_path_excludes_query():
    responses = [
        (200, json.dumps([{"trade_id": 2, "product_id": "BTC-USD"}]).encode(), ()),
        (200, json.dumps([{"trade_id": 1, "product_id": "ETH-USD"}]).encode(), ()),
    ]
    transport, calls = _transport(responses)
    transport.verify_product_mappings(_products())
    fills = transport.list_fills()
    assert tuple(item["trade_id"] for item in fills) == (1, 2)
    assert calls[0][1].endswith("/fills?product_id=BTC-USD")
    assert calls[1][1].endswith("/fills?product_id=ETH-USD")
    expected = hmac.new(
        base64.b64decode(SECRET),
        b"1700000000.123GET/fills",
        hashlib.sha256,
    ).digest()
    assert calls[0][2]["CB-ACCESS-SIGN"] == base64.b64encode(expected).decode("ascii")


def test_coinbase_order_submission_maps_native_payload_and_cancel_is_idempotency_bound():
    responses = [
        (
            200,
            json.dumps(
                {
                    "id": "venue-order-1",
                    "client_oid": "local-order-1",
                    "product_id": "BTC-USD",
                    "status": "open",
                }
            ).encode(),
            (),
        ),
        (200, json.dumps(["venue-order-1"]).encode(), ()),
    ]
    transport, calls = _transport(responses)
    transport.verify_product_mappings(_products())
    acknowledgement = transport.submit_order(
        {
            "client_order_id": "local-order-1",
            "symbol": "BTC-USD",
            "side": "buy",
            "quantity": "0.00001",
            "order_type": "passive_limit",
            "price": "100",
            "time_in_force": "gtc",
        }
    )
    assert acknowledgement["accepted"] is True
    assert acknowledgement["venue_order_id"] == "venue-order-1"
    body = json.loads(calls[0][3])
    assert body == {
        "client_oid": "local-order-1",
        "post_only": True,
        "price": "100",
        "product_id": "BTC-USD",
        "side": "buy",
        "size": "0.00001",
        "time_in_force": "GTC",
        "type": "limit",
    }
    cancelled = transport.cancel_order(client_order_id="local-order-1")
    assert cancelled == {
        "client_order_id": "local-order-1",
        "venue_order_id": "venue-order-1",
        "cancelled": True,
    }
    assert calls[1][1].endswith("/orders/venue-order-1")


def test_coinbase_factory_uses_only_paper_venue_scope_and_rejects_production():
    resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_LLM_API_KEY": "must-not-be-bound",
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": COINBASE_EXCHANGE_SANDBOX_BASE_URL,
            "ADVISORAI_VENUE_WS_URL": COINBASE_EXCHANGE_SANDBOX_WS_URL,
            "ADVISORAI_VENUE_API_KEY": "fixture-key",
            "ADVISORAI_VENUE_API_SECRET": SECRET,
            "ADVISORAI_VENUE_PASSPHRASE": "fixture-passphrase",
        }
    )
    transport = build_coinbase_exchange_sandbox_transport(resolver)
    assert transport.client.config.allowed_hosts == (COINBASE_EXCHANGE_SANDBOX_HOST,)
    assert transport.venue_name == "coinbase_exchange_sandbox"

    production_resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://api.exchange.coinbase.com",
            "ADVISORAI_VENUE_API_KEY": "fixture-key",
            "ADVISORAI_VENUE_API_SECRET": SECRET,
            "ADVISORAI_VENUE_PASSPHRASE": "fixture-passphrase",
        }
    )
    with pytest.raises(ValueError, match="non-sandbox or production"):
        build_coinbase_exchange_sandbox_transport(production_resolver)

    ws_resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": COINBASE_EXCHANGE_SANDBOX_BASE_URL,
            "ADVISORAI_VENUE_WS_URL": "wss://ws-feed-public.exchange.coinbase.com",
            "ADVISORAI_VENUE_API_KEY": "fixture-key",
            "ADVISORAI_VENUE_API_SECRET": SECRET,
            "ADVISORAI_VENUE_PASSPHRASE": "fixture-passphrase",
        }
    )
    with pytest.raises(ValueError, match="WebSocket endpoints"):
        build_coinbase_exchange_sandbox_transport(ws_resolver)


def test_coinbase_snapshot_fails_closed_on_unmapped_nonzero_asset():
    accounts = _accounts()
    accounts.append(
        {
            "id": "sol-account",
            "currency": "SOL",
            "balance": "1",
            "available": "1",
            "hold": "0",
            "profile_id": "profile-fixture",
            "trading_enabled": True,
        }
    )
    transport, _calls = _transport([(200, json.dumps(accounts).encode(), ())])
    transport.verify_product_mappings(_products())
    with pytest.raises(VenueTransportError, match="unmapped asset SOL"):
        transport.fetch_account_snapshot()


def test_coinbase_snapshot_timestamp_is_timezone_aware():
    transport, _calls = _transport(
        [
            (200, json.dumps(_accounts()).encode(), ()),
            (200, json.dumps({"iso": "2026-08-09T18:00:00Z", "epoch": 1786298400}).encode(), ()),
        ]
    )
    transport.verify_product_mappings(_products())
    snapshot = transport.fetch_account_snapshot()
    assert snapshot.as_of.tzinfo is UTC
    assert snapshot.as_of == datetime(2026, 8, 9, 18, 0, tzinfo=UTC)
