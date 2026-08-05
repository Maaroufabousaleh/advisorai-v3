from datetime import timedelta
from uuid import uuid4

import pytest

from advisorai.identity import InstrumentRegistry


def test_identity_registry_resolves_only_valid_identity_window(btc_usdt, timestamp):
    registry = InstrumentRegistry()
    registry.register(
        btc_usdt.model_copy(
            update={
                "valid_from": timestamp,
                "valid_to": timestamp + timedelta(hours=1),
            }
        )
    )
    assert registry.resolve(btc_usdt.canonical_id, timestamp).venue_symbol == "BTCUSDT"
    with pytest.raises(KeyError):
        registry.resolve(btc_usdt.canonical_id, timestamp + timedelta(hours=1))


def test_identity_registry_rejects_overlapping_validity_windows(btc_usdt, timestamp):
    registry = InstrumentRegistry()
    first = btc_usdt.model_copy(
        update={
            "valid_from": timestamp,
            "valid_to": timestamp + timedelta(days=2),
        }
    )
    second = first.model_copy(
        update={"artifact_id": uuid4(), "valid_from": timestamp + timedelta(days=1)}
    )
    registry.register(first)
    with pytest.raises(ValueError, match="overlap"):
        registry.register(second)
