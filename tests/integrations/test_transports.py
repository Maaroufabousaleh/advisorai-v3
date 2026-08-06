import asyncio
import base64
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from advisorai.config import SecretSettings
from advisorai.integrations import (
    ConnectorCard,
    ConnectorRegistry,
    ConnectorState,
    HmacVenueSigner,
    HttpClientConfig,
    HttpTransportError,
    OpenAICompatibleGatewayAdapter,
    PaperTestnetVenueTransport,
    RawHttpSpool,
    RawMessageSpool,
    SafeHttpClient,
    SourceEndpoint,
    build_direct_gateway,
    build_paper_venue_transport,
    build_v3_core_collectors,
)
from advisorai.integrations.websocket import RawWebSocketFeed
from advisorai.ledger import SqliteLedgers
from advisorai.ports import GatewayMessage, GatewayRequest, GatewayRoute, GatewayTool


def client_for(responses, *, retries=0, threshold=5):
    calls = []

    def requester(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), body))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("sandbox.example.test",),
            max_retries=retries,
            circuit_failure_threshold=threshold,
            requests_per_second=100,
        ),
        base_url="https://sandbox.example.test/api",
        requester=requester,
        sleeper=lambda _: None,
    )
    return client, calls


def test_http_client_enforces_https_host_and_retries_5xx():
    response = (200, b'{"ok":true}', (("Content-Type", "application/json"),))
    client, calls = client_for([(500, b"temporary", ()), response], retries=1)
    assert client.get("https://sandbox.example.test/status").status_code == 200
    assert len(calls) == 2
    with pytest.raises(HttpTransportError, match="reviewed"):
        client.get("https://evil.example.test/status")
    with pytest.raises(HttpTransportError, match="HTTPS"):
        client.get("http://sandbox.example.test/status")


def test_http_client_opens_circuit_after_repeated_failures():
    client, _ = client_for([(500, b"bad", ())], threshold=1)
    with pytest.raises(HttpTransportError, match="HTTP 500"):
        client.get("https://sandbox.example.test/status")
    with pytest.raises(HttpTransportError, match="circuit"):
        client.get("https://sandbox.example.test/status")


def test_http_client_spools_failed_response_before_raising(tmp_path):
    spool = RawHttpSpool(tmp_path / "failed-http.jsonl")

    def requester(method, url, headers, body, timeout):
        return 503, b"provider unavailable", (("Retry-After", "1"),)

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("sandbox.example.test",),
            max_retries=0,
            requests_per_second=100,
        ),
        base_url="https://sandbox.example.test/api",
        requester=requester,
        failed_response_sink=spool.append,
        sleeper=lambda _: None,
    )
    with pytest.raises(HttpTransportError, match="HTTP 503"):
        client.get("https://sandbox.example.test/status")

    records = spool.read()
    assert len(records) == 1
    assert records[0].status_code == 503
    assert base64.b64decode(records[0].payload_b64) == b"provider unavailable"


def test_direct_gateway_maps_openai_shape_and_rejects_non_typed_output():
    body = json.dumps(
        {
            "id": "req-provider-1",
            "provider": "example",
            "model": "model-v1",
            "provider_variant": "example-endpoint",
            "choices": [{"message": {"content": '{"decision":"abstain"}'}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 3},
        }
    ).encode()
    client, calls = client_for([(200, body, ())])
    route = GatewayRoute(
        provider="example", model="model-v1", gateway="direct", endpoint_variant="example-endpoint", schema_mode="typed_json"
    )
    adapter = OpenAICompatibleGatewayAdapter(route, client, api_key="secret-key")
    request = GatewayRequest(
        route=route,
        messages=(GatewayMessage(role="user", content="return typed JSON"),),
        prompt_version="p1",
    )
    response = adapter.complete(request)
    assert response.typed_payload == {"decision": "abstain"}
    assert response.input_tokens == 4
    assert calls[0][2]["Authorization"] == "Bearer secret-key"

    bad_client, _ = client_for(
        [
            (
                200,
                json.dumps(
                    {
                        "provider": "example",
                        "model": "model-v1",
                        "provider_variant": "example-endpoint",
                        "choices": [{"message": {"content": "not json"}}],
                    }
                ).encode(),
                (),
            )
        ]
    )
    bad_adapter = OpenAICompatibleGatewayAdapter(route, bad_client, api_key="secret-key")
    with pytest.raises(Exception, match="malformed typed JSON"):
        bad_adapter.complete(request)


def test_direct_gateway_accepts_tool_calls_with_null_content():
    body = json.dumps(
        {
            "provider": "example",
            "model": "model-v1",
            "provider_variant": "example-endpoint",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "read_orderbook",
                                    "arguments": '{"instrument":"BTC"}',
                                }
                            }
                        ],
                    }
                }
            ]
        }
    ).encode()
    client, calls = client_for([(200, body, ())])
    route = GatewayRoute(
        provider="example", model="model-v1", gateway="direct", endpoint_variant="example-endpoint", schema_mode="text"
    )
    request = GatewayRequest(
        route=route,
        messages=(GatewayMessage(role="user", content="inspect evidence"),),
        tools=(
            GatewayTool(
                name="read_orderbook",
                input_schema_version="v1",
                output_schema_version="v1",
                input_schema={
                    "type": "object",
                    "properties": {"instrument": {"type": "string"}},
                    "required": ["instrument"],
                    "additionalProperties": False,
                },
            ),
        ),
        prompt_version="p1",
    )

    response = OpenAICompatibleGatewayAdapter(route, client, api_key="secret-key").complete(request)

    assert response.content == ""
    assert response.tool_calls[0]["name"] == "read_orderbook"
    sent = json.loads(calls[0][3])
    assert sent["tools"][0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"instrument": {"type": "string"}},
        "required": ["instrument"],
        "additionalProperties": False,
    }


def test_direct_gateway_preserves_actual_provider_identity_and_pricing_parameters():
    body = json.dumps(
        {
            "id": "req-provider-identity",
            "model": "actual-reviewed-model",
            "provider": "reviewed-provider",
            "provider_variant": "reviewed/fp8",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode()
    client, _ = client_for([(200, body, ())])
    route = GatewayRoute(
        provider="reviewed-provider",
        model="requested-model",
        gateway="direct",
        endpoint_variant="reviewed/fp8",
    )
    adapter = OpenAICompatibleGatewayAdapter(
        route,
        client,
        api_key="secret-key",
        input_price_per_million=0.1,
        output_price_per_million=0.3,
    )
    response = adapter.complete(
        GatewayRequest(
            route=route,
            messages=(GatewayMessage(role="user", content="typed"),),
            prompt_version="p1",
        )
    )

    assert response.requested_route == route
    assert response.route.model == "actual-reviewed-model"
    assert response.route.provider == "reviewed-provider"
    assert response.actual_model == "actual-reviewed-model"
    assert response.endpoint_variant == "reviewed/fp8"
    assert response.input_price_per_million == 0.1


def test_hmac_signer_and_paper_venue_transport_keep_endpoint_scope():
    settings = SecretSettings.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "example",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://sandbox.example.test/api",
        }
    )
    response = json.dumps({"result": {"accepted": True, "venue_order_id": "v-1"}}).encode()
    client, calls = client_for([(200, response, ())])
    signer = HmacVenueSigner("key", __import__("pydantic").SecretStr("secret"))
    transport = PaperTestnetVenueTransport(client, settings, signer=signer)
    result = transport.submit_order({"client_order_id": "local-1", "symbol": "BTC-PERP"})
    assert result["venue_order_id"] == "v-1"
    assert calls[0][0] == "POST"
    assert calls[0][2]["X-API-KEY"] == "key"
    with pytest.raises(Exception, match="prohibited"):
        transport._request("POST", "/withdraw", {})


def test_paper_venue_open_order_reconciliation_is_signed():
    settings = SecretSettings.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "example",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://sandbox.example.test/api",
        }
    )
    client, calls = client_for([(200, json.dumps({"result": []}).encode(), ())])
    signer = HmacVenueSigner("key", __import__("pydantic").SecretStr("secret"))
    transport = PaperTestnetVenueTransport(client, settings, signer=signer)

    assert transport.list_open_orders() == ()
    assert calls[0][2]["X-API-KEY"] == "key"
    assert calls[0][2]["X-API-SIGNATURE"]


def test_paper_venue_transport_maps_read_only_account_and_collections():
    settings = SecretSettings.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "example",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://sandbox.example.test/api",
        }
    )
    account = json.dumps(
        {
            "result": {
                "as_of": "2026-08-05T15:00:00Z",
                "cash": "1000",
                "positions": {"BTC-PERP": "0.5"},
                "margin_used": "10",
                "margin_available": "990",
            }
        }
    ).encode()
    fills = json.dumps({"result": {"fills": [{"id": "fill-1"}]}}).encode()
    client, _ = client_for([(200, account, ()), (200, fills, ())])
    transport = PaperTestnetVenueTransport(client, settings)
    snapshot = transport.fetch_account_snapshot()
    assert snapshot.cash == Decimal("1000")
    assert snapshot.positions["BTC-PERP"] == Decimal("0.5")
    assert snapshot.margin_available == Decimal("990")
    assert transport.list_fills() == ({"id": "fill-1"},)
    with pytest.raises(Exception, match="not admitted"):
        transport.account_state(path="/prod/account")


def test_raw_websocket_spool_is_idempotent_and_replayable(tmp_path):
    from datetime import UTC, datetime

    spool = RawMessageSpool(tmp_path / "raw-ws.jsonl")
    at = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    first = spool.append(b"trade-1", received_at=at, sequence=1)
    assert spool.append(b"trade-1", received_at=at, sequence=1) == first
    assert spool.read() == ((1, b"trade-1"),)
    records = spool.read_records()
    assert records[0][0] == 1
    assert records[0][1] == at

    replay_spool = RawMessageSpool(tmp_path / "replay-ws.jsonl")
    replay_spool.append(
        b'{"type":"trade","symbol":"BTC-PERP","price":"100","qty":"1"}',
        received_at=at,
        sequence=1,
    )
    replay_feed = RawWebSocketFeed(
        "wss://sandbox.example.test/stream",
        allowed_hosts=("sandbox.example.test",),
        spool=replay_spool,
    )
    replayed = replay_feed.replay_market_events(instrument_id="BTC-PERP")
    assert replayed == replay_feed.replay_market_events(instrument_id="BTC-PERP")


def test_raw_websocket_spool_rejects_payload_hash_tampering(tmp_path):
    path = tmp_path / "tampered-ws.jsonl"
    path.write_text(
        json.dumps(
            {
                "message_id": "0" * 64,
                "sequence": 1,
                "received_at": "2026-08-05T15:00:00+00:00",
                "payload_b64": base64.b64encode(b"trusted-looking payload").decode("ascii"),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match payload digest"):
        RawMessageSpool(path)


def test_raw_websocket_feed_parses_only_after_raw_spool(monkeypatch, tmp_path):
    spool = RawMessageSpool(tmp_path / "raw-ws.jsonl")
    feed = RawWebSocketFeed(
        "wss://sandbox.example.test/stream",
        allowed_hosts=("sandbox.example.test",),
        spool=spool,
    )

    async def recorded_messages(*, subscription=None):
        del subscription
        raw = b'{"type":"trade","symbol":"BTC-PERP","price":"100","qty":"1"}'
        spool.append(raw, received_at=datetime.now(UTC), sequence=1)
        yield raw

    monkeypatch.setattr(feed, "messages", recorded_messages)
    events = asyncio.run(_collect_events(feed))
    assert events[0].event_type == "trade"
    assert spool.read()[0][1].startswith(b'{"type":"trade"')


async def _collect_events(feed):
    return [event async for event in feed.market_events()]


def test_v3_core_collector_factory_keeps_fixed_four_source_scope():
    settings = SecretSettings.from_mapping({"ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet"})
    endpoint = SourceEndpoint(
        url="https://sandbox.example.test/api", allowed_host="sandbox.example.test"
    )
    collectors = build_v3_core_collectors(
        settings=settings, native=endpoint, deribit=endpoint, rss=endpoint, gdelt=endpoint
    )
    assert collectors.native.descriptor.grade.value == "execution_grade"
    assert collectors.deribit.descriptor.name == "deribit"


def test_source_endpoint_cannot_pair_a_reviewed_host_with_another_url():
    with pytest.raises(ValueError, match="hostname must match"):
        SourceEndpoint(
            url="https://another.example.test/api",
            allowed_host="sandbox.example.test",
        )


def test_connector_reregistration_validates_from_durable_state(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "connectors.sqlite3")
    registry = ConnectorRegistry(ledgers)
    card = ConnectorCard(
        name="venue",
        owner="ops",
        purpose="paper market data",
        endpoint="https://sandbox.example.test/api",
        allowed_hosts=("sandbox.example.test",),
        source_grade="execution",
        quota_and_cost="reviewed",
        adapter_version="v1",
        rollback_procedure="revoke",
    )
    configured = registry.transition(
        registry.register(card, reason="register").name,
        ConnectorState.CONFIGURED,
        reason="configure",
    )

    with pytest.raises(ValueError, match="invalid connector lifecycle transition"):
        registry.register(
            configured.model_copy(update={"state": ConnectorState.ACTIVE_READ}), reason="skip smoke"
        )


def test_factories_bind_credentials_only_to_the_named_adapter():
    settings = SecretSettings.from_mapping(
        {
            "ADVISORAI_LLM_BASE_URL": "https://sandbox.example.test/api",
            "ADVISORAI_LLM_API_KEY": "llm-secret",
            "ADVISORAI_VENUE_NAME": "example",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://sandbox.example.test/api",
            "ADVISORAI_VENUE_API_KEY": "venue-key",
            "ADVISORAI_VENUE_API_SECRET": "venue-secret",
        }
    )
    route = GatewayRoute(provider="example", model="model", gateway="direct", schema_mode="text")
    gateway = build_direct_gateway(settings, route)
    venue = build_paper_venue_transport(settings)
    assert gateway.name == "direct_provider"
    assert venue.settings.venue_name == "example"
