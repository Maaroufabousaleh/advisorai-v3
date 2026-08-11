from __future__ import annotations

import json
from pathlib import Path

from advisorai.config import SecretSettings
from scripts.qualify_binance_spot_testnet_recovery import (
    CHILD_SCHEMA,
    _child_environment,
    _configuration_bundle_content,
    _configuration_rollback,
    _read_only_probe,
    _restart_command,
)


class _FakeClient:
    request_count = 0


class _FakeTransport:
    client = _FakeClient()

    def __init__(self) -> None:
        self.write_calls: list[str] = []

    def server_time(self):
        return {"serverTime": 1}

    def list_products(self):
        return [
            {"symbol": "BTCUSDT"},
            {"symbol": "ETHUSDT"},
        ]

    def verify_symbol_mappings(self, products):
        class _Spec:
            def __init__(self, symbol):
                self.symbol = symbol
                self.base_asset = symbol[:3]
                self.quote_asset = "USDT"

        return (_Spec("BTCUSDT"), _Spec("ETHUSDT"))

    def account_state(self):
        return {"accountType": "SPOT", "canTrade": True}

    def list_balances(self):
        return ()

    def list_positions(self):
        return ()

    def list_open_orders(self):
        return ()

    def list_fills(self):
        return ()

    def submit_order(self, _payload):
        self.write_calls.append("submit")
        raise AssertionError("read-only recovery must not submit")

    def cancel_order(self, *, client_order_id):
        self.write_calls.append(f"cancel:{client_order_id}")
        raise AssertionError("read-only recovery must not cancel")


def _settings() -> SecretSettings:
    return SecretSettings.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://testnet.binance.vision",
            "ADVISORAI_VENUE_WS_URL": "wss://stream.testnet.binance.vision/ws",
        }
    )


def test_read_only_probe_never_calls_transport_write_methods():
    transport = _FakeTransport()

    result = _read_only_probe(transport)  # type: ignore[arg-type]

    assert result["status"] == "passed"
    assert result["writes_attempted"] is False
    assert transport.write_calls == []
    assert [item["name"] for item in result["operations"]] == [
        "server_time",
        "products",
        "product_mapping_verification",
        "account_state",
        "balances",
        "positions",
        "open_orders",
        "fills",
    ]


def test_read_only_probe_fails_closed_on_a_private_read_error():
    transport = _FakeTransport()

    def fail_fills():
        raise RuntimeError("fixture failure")

    transport.list_fills = fail_fills  # type: ignore[method-assign]

    result = _read_only_probe(transport)  # type: ignore[arg-type]

    assert result["status"] == "failed"
    assert result["reason"] == "read_only_operation_failed"
    assert result["writes_attempted"] is False
    assert result["operations"][-1]["name"] == "fills"
    assert result["operations"][-1]["status"] == "failed"
    assert transport.write_calls == []


def test_configuration_rollback_persists_non_secret_bundle_and_reopens(tmp_path):
    initial = _configuration_bundle_content(
        _settings(),
        ("ADVISORAI_VENUE_API_KEY", "ADVISORAI_VENUE_API_SECRET"),
        "a" * 64,
        "b" * 64,
        "initial",
    )
    encoded_initial = json.dumps(initial, sort_keys=True)
    assert "fixture-api-key-value" not in encoded_initial
    assert "fixture-api-secret-value" not in encoded_initial
    result = _configuration_rollback(
        tmp_path / "deployed-state",
        _settings(),
        ("ADVISORAI_VENUE_API_KEY", "ADVISORAI_VENUE_API_SECRET"),
        "a" * 64,
        "b" * 64,
    )

    assert result["status"] == "passed"
    assert result["active_bundle_hash"] == result["bundle_hashes"]["initial"]
    assert result["active_bundle_hash_after_reopen"] == result["bundle_hashes"]["initial"]
    assert result["writes_attempted"] is False
    assert len(list((tmp_path / "deployed-state" / "config" / "bundles").glob("*.json"))) == 2


def test_restart_child_environment_contains_no_credential_values(tmp_path):
    environment = _child_environment(tmp_path)
    assert environment == {
        "ADVISORAI_RUN_NETWORK_SMOKE": "1",
        "PYTHONPATH": tmp_path.as_posix(),
    }
    command = _restart_command(
        secrets=Path("/mnt/c/projects/advisorai-v3/secrets.env"),
        state_root=tmp_path / "state",
        child_report=tmp_path / "child.json",
        expected_bundle_hash="a" * 64,
        configuration_hash="b" * 64,
    )
    assert "--secrets" in command
    assert "--child" in command
    assert "ADVISORAI_VENUE_API_SECRET" not in " ".join(command)


def test_child_schema_is_explicitly_distinct_from_parent():
    assert CHILD_SCHEMA.endswith(".child")
