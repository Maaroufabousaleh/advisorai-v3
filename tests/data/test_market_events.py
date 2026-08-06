from datetime import UTC, datetime
from decimal import Decimal

import pytest

from advisorai.collectors.sources import NativeVenueCollector, SourceDescriptor
from advisorai.contracts import SourceGrade
from advisorai.execution.events import (
    MarketEvent,
    NativeMarketMessageParser,
    RawEventSpool,
    ReplayEngine,
)


def _parser() -> NativeMarketMessageParser:
    return NativeMarketMessageParser()


def _received() -> datetime:
    return datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


def test_native_parser_normalizes_trade_and_is_deterministic():
    raw = b'{"type":"trade","symbol":"BTC-PERP","timestamp_ms":1785942000000,"sequence":7,"price":"100.5","qty":"0.25"}'
    first = _parser().parse(raw, received_at=_received())
    second = _parser().parse(raw, received_at=_received())

    assert isinstance(first, MarketEvent)
    assert first == second
    assert first.event_type == "trade"
    assert first.instrument_id == "BTC-PERP"
    assert first.price == Decimal("100.5")
    assert first.quantity == Decimal("0.25")
    assert dict(first.payload)["raw_sha256"]


def test_native_parser_handles_envelopes_and_all_v3_core_event_types():
    received = _received()
    events = _parser().parse_many(
        {
            "channel": "market",
            "data": [
                {
                    "type": "book",
                    "symbol": "BTC-PERP",
                    "timestamp": "2026-08-05T14:59:59Z",
                    "sequence": 8,
                    "bid": "100",
                    "ask": "101",
                },
                {
                    "type": "bar",
                    "symbol": "BTC-PERP",
                    "timestamp_ms": 1785941999000,
                    "sequence": 9,
                    "open": "99",
                    "high": "102",
                    "low": "98",
                    "close": "100",
                    "volume": "12",
                },
            ],
        },
        received_at=received,
    )
    funding = _parser().parse(
        {"type": "funding_rate", "s": "BTC-PERP", "ts": 1785941999000, "r": "-0.001", "seq": 10},
        received_at=received,
    )
    oi = _parser().parse(
        {"type": "open_interest", "s": "BTC-PERP", "ts": 1785941999000, "oi": "42", "seq": 11},
        received_at=received,
    )

    assert [event.event_type for event in events] == ["book", "bar"]
    assert events[0].bid == Decimal("100")
    assert events[0].ask == Decimal("101")
    assert events[1].quantity == Decimal("12")
    assert dict(funding.payload)["funding_rate"] == "-0.001"
    assert oi.quantity == Decimal("42")


def test_native_parser_requires_typed_metadata_and_rejects_future_or_invalid_messages():
    with pytest.raises(ValueError, match="event type"):
        _parser().parse({"symbol": "BTC", "price": "1", "qty": "1", "sequence": 1})
    with pytest.raises(ValueError, match="sequence"):
        _parser().parse(
            {"type": "trade", "symbol": "BTC", "timestamp_ms": 1, "price": "1", "qty": "1"},
            received_at=_received(),
        )
    with pytest.raises(ValueError, match="newer"):
        _parser().parse(
            {
                "type": "trade",
                "symbol": "BTC",
                "timestamp": "2026-08-05T15:01:00Z",
                "sequence": 1,
                "price": "1",
                "qty": "1",
            },
            received_at=_received(),
        )
    with pytest.raises(ValueError, match="positive"):
        _parser().parse(
            {
                "type": "trade",
                "symbol": "BTC",
                "timestamp": "2026-08-05T14:59:00Z",
                "sequence": 1,
                "price": "0",
                "qty": "1",
            },
            received_at=_received(),
        )


def test_native_collector_uses_same_market_parser_and_event_replay(tmp_path):
    collector = NativeVenueCollector(
        SourceDescriptor(
            name="native",
            family="market",
            origin="venue",
            grade=SourceGrade.EXECUTION,
            intended_use="market",
            parser_version="native-v1",
        )
    )
    events = collector.parse_market_events(
        b'{"type":"trade","symbol":"ETH-PERP","timestamp_ms":1785941999000,"sequence":1,"price":"10","qty":"2"}',
        received_at=_received(),
    )
    spool = RawEventSpool(tmp_path / "events.jsonl")
    assert spool.append(events[0])
    assert not spool.append(events[0])
    replayed: list[str] = []
    assert (
        ReplayEngine().replay(spool.read(), lambda event: replayed.append(event.instrument_id)) == 1
    )
    assert replayed == ["ETH-PERP"]
