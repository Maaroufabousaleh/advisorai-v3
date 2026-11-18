from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256

import pytest

from advisorai.integrations import BinanceSpotSymbolSpec
from scripts.qualify_binance_spot_testnet_lifecycle import (
    LifecycleBlocked,
    _fill_from_record,
    _fill_matches_submitted_order,
    _minimum_practical_quantity,
    _passive_price,
    _validate_read_only_evidence,
)


def _spec() -> BinanceSpotSymbolSpec:
    return BinanceSpotSymbolSpec.from_record(
        {
            "symbol": "BTCUSDT",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "tickSize": "0.01",
                    "minPrice": "0.01",
                    "maxPrice": "1000000",
                },
                {
                    "filterType": "LOT_SIZE",
                    "stepSize": "0.00001",
                    "minQty": "0.00001",
                    "maxQty": "1000",
                },
                {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
            ],
        }
    )


def test_lifecycle_order_helpers_round_up_filters_and_stay_passive():
    spec = _spec()
    price = _passive_price(Decimal("100.00"), Decimal("100.10"), spec.quote_increment, "buy")
    quantity = _minimum_practical_quantity(spec, price)
    assert price == Decimal("100.00")
    assert quantity == Decimal("0.051")
    assert price * quantity >= Decimal("5")


def test_lifecycle_requires_current_immutable_read_only_gate(tmp_path):
    evidence_dir = tmp_path / "read-only"
    run_id = "20260810T000000.000000Z"
    run_dir = evidence_dir / run_id
    run_dir.mkdir(parents=True)
    operations = [
        {"name": name, "status": "ok"}
        for name in (
            "server_time",
            "products",
            "product_mapping_verification",
            "account_state",
            "balances",
            "positions",
            "open_orders",
            "fills",
        )
    ]
    operations[1]["required_symbols"] = ["BTCUSDT", "ETHUSDT"]
    operations[2]["admitted_symbols"] = ["BTCUSDT", "ETHUSDT"]
    record = {
        "schema": "advisorai.phase2.binance-spot-testnet.read-only-smoke.v1",
        "run_id": run_id,
        "result": {
            "status": "passed",
            "writes_attempted": False,
            "config_hash": "a" * 64,
            "endpoint": "https://testnet.binance.vision",
            "credential_refs": [
                "ADVISORAI_VENUE_API_KEY",
                "ADVISORAI_VENUE_API_SECRET",
            ],
            "operations": operations,
        },
    }
    manifest = run_dir / "binance-spot-testnet-read-only-smoke.json"
    encoded = (json.dumps(record, sort_keys=True) + "\n").encode()
    manifest.write_bytes(encoded)
    (evidence_dir / "latest.json").write_text(
        json.dumps({"run_id": run_id, "manifest_sha256": sha256(encoded).hexdigest()}),
        encoding="utf-8",
    )
    validated = _validate_read_only_evidence(evidence_dir, "a" * 64)
    assert validated["manifest_sha256"] == sha256(encoded).hexdigest()
    manifest.write_bytes(encoded + b"tampered")
    with pytest.raises(LifecycleBlocked, match="hash"):
        _validate_read_only_evidence(evidence_dir, "a" * 64)


def _synthetic_transport():
    from advisorai.config import CredentialResolver
    from advisorai.integrations import build_binance_spot_testnet_transport

    resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://testnet.binance.vision",
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
        }
    )
    return build_binance_spot_testnet_transport(resolver)


def _fill_record(**overrides):
    record = {
        "symbol": "BTCUSDT",
        "id": 1,
        "orderId": 7,
        "qty": "0.001",
        "price": "50000",
        "commission": "0.05",
        "commissionAsset": "USDT",
        "time": 1794067200000,
        "isBuyer": True,
    }
    record.update(overrides)
    return record


def test_lifecycle_fill_rejects_non_quote_fee_denomination():
    """P03: a BNB-denominated fee fails closed instead of corrupting USDT cash."""
    from uuid import uuid4

    transport = _synthetic_transport()
    fill, side = _fill_from_record(transport, _fill_record(), uuid4(), quote_asset="USDT")
    assert side == "buy"
    assert fill.fee == Decimal("0.05")
    assert fill.fee_currency == "USDT"
    zero_fill, _ = _fill_from_record(
        transport,
        _fill_record(commission="0E-8", commissionAsset="BNB"),
        uuid4(),
        quote_asset="USDT",
    )
    assert zero_fill.fee == 0
    with pytest.raises(LifecycleBlocked, match="quote asset"):
        _fill_from_record(
            transport,
            _fill_record(id=2, commission="0.0001", commissionAsset="BNB"),
            uuid4(),
            quote_asset="USDT",
        )


def test_lifecycle_fill_rejects_suffix_match_fee_asset():
    """P03: a nonzero T fee on BTCUSDT fails; suffix matching would accept it."""
    from uuid import uuid4

    transport = _synthetic_transport()
    with pytest.raises(LifecycleBlocked, match="quote asset"):
        _fill_from_record(
            transport,
            _fill_record(id=3, commission="0.0001", commissionAsset="T"),
            uuid4(),
            quote_asset="USDT",
        )


def test_lifecycle_fill_match_requires_selected_symbol_and_order():
    """P03: a fill on the other symbol with the same order ID never matches."""
    assert _fill_matches_submitted_order(
        _fill_record(symbol="BTCUSDT", orderId=7), symbol="BTCUSDT", venue_order_id="7"
    )
    assert not _fill_matches_submitted_order(
        _fill_record(symbol="ETHUSDT", orderId=7), symbol="BTCUSDT", venue_order_id="7"
    )
    assert not _fill_matches_submitted_order(
        _fill_record(symbol="BTCUSDT", orderId=8), symbol="BTCUSDT", venue_order_id="7"
    )
    assert not _fill_matches_submitted_order(
        {"not": "a fill"}, symbol="BTCUSDT", venue_order_id="7"
    )
    assert not _fill_matches_submitted_order(None, symbol="BTCUSDT", venue_order_id="7")
