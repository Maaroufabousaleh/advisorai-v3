"""Three-way BTCUSDT replay agreement; offline contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from advisorai.research.btc_replay import (
    BTCUSDT_ALL_IN_BPS,
    agree,
    mirror_accounting,
    plain_loop,
    replay_sign_only,
)
from advisorai.research.btc_spot import check_continuity, validate_manifest_values

COST = Decimal("0.998")


def _bars(n: int, close: str = "100.0") -> tuple[dict, ...]:
    base = datetime(2024, 1, 1, tzinfo=UTC).date()
    rows = []
    for i in range(n):
        day = (base + timedelta(days=i)).isoformat()
        rows.append(
            {
                "date": day,
                "open": "100.0",
                "high": "101.0",
                "low": "99.0",
                "adj_close": close,
                "volume": "10.0",
            }
        )
    return tuple(rows)


def test_cost_pin_is_twenty_bps_all_in():
    assert BTCUSDT_ALL_IN_BPS == Decimal("20")


def test_flat_market_three_way_agreement_is_exact():
    bars = _bars(66)
    first = replay_sign_only(bars)
    assert first["trades"] == 0
    assert first["obs"] == 2
    assert Decimal(str(first["net_bps"])) == Decimal("0")
    assert Decimal(str(first["baseline_bps"])) == (COST - 1) * Decimal("10000")
    assert mirror_accounting(bars) == first
    assert plain_loop(bars) == first
    assert agree(bars) == first


def test_rising_market_three_way_agreement():
    base = datetime(2024, 1, 1, tzinfo=UTC).date()
    rows = []
    for i in range(70):
        day = (base + timedelta(days=i)).isoformat()
        price = str(100 + i)
        rows.append(
            {
                "date": day,
                "open": str(99 + i),
                "high": str(101 + i),
                "low": str(98 + i),
                "adj_close": price,
                "volume": "10.0",
            }
        )
    bars = tuple(rows)
    result = agree(bars)
    assert result["obs"] == 70 - 1 - 63
    assert result["trades"] == 1
    expected_value = Decimal("169") / Decimal("163") * COST
    assert Decimal(str(result["baseline_bps"])) == (expected_value - 1) * Decimal("10000")
    quantum = Decimal("0.01")
    assert Decimal(str(result["net_bps"])).quantize(quantum) == Decimal(
        str(result["baseline_bps"])
    ).quantize(quantum)


def test_two_consecutive_long_days_with_gap_move():
    """Entry fill skips the overnight gap; value compounds from the fill."""
    base = datetime(2024, 1, 1, tzinfo=UTC).date()
    rows = []
    for i in range(66):
        day = (base + timedelta(days=i)).isoformat()
        close = "200.0" if i >= 64 else "100.0"
        rows.append(
            {
                "date": day,
                "open": "205.0" if i == 65 else "100.0",
                "high": "205.0" if i == 65 else "200.0" if i == 64 else "100.0",
                "low": "200.0" if i >= 64 else "100.0",
                "adj_close": close,
                "volume": "10.0",
            }
        )
    bars = tuple(rows)
    result = agree(bars)
    assert result["obs"] == 2
    assert result["trades"] == 1
    expected_value = COST * (Decimal("200.0") / Decimal("205.0"))
    assert Decimal(str(result["net_bps"])) == (expected_value - 1) * Decimal("10000")
    expected_base = Decimal("200.0") / Decimal("100.0") * COST
    assert Decimal(str(result["baseline_bps"])) == (expected_base - 1) * Decimal("10000")


def test_exit_attributes_gap_through_the_fill():
    """Closing prints at the exit fill: the overnight gap belongs to the exit."""
    base = datetime(2024, 1, 1, tzinfo=UTC).date()
    closes = ["100.0"] * 64 + ["200.0", "50.0", "50.0"]
    opens = ["100.0"] * 64 + ["100.0", "205.0", "40.0"]
    rows = []
    for i in range(67):
        day = (base + timedelta(days=i)).isoformat()
        rows.append(
            {
                "date": day,
                "open": opens[i],
                "high": max(opens[i], closes[i]),
                "low": min(opens[i], closes[i]),
                "adj_close": closes[i],
                "volume": "10.0",
            }
        )
    bars = tuple(rows)
    result = agree(bars)
    assert result["obs"] == 3
    assert result["trades"] == 2
    value = Decimal("1") * COST * (Decimal("50.0") / Decimal("205.0"))
    value = value * COST * (Decimal("40.0") / Decimal("50.0"))
    assert Decimal(str(result["net_bps"])) == (value - 1) * Decimal("10000")
    expected_base = Decimal("50.0") / Decimal("100.0") * COST
    assert Decimal(str(result["baseline_bps"])) == (expected_base - 1) * Decimal("10000")


def test_manifest_values_reject_mismatch():
    good = {
        "source": "binance-public-spot-archive",
        "symbol": "BTCUSDT",
        "interval": "1d",
        "timestamp_units": "ms-utc",
    }
    validate_manifest_values(
        good,
        source="binance-public-spot-archive",
        symbol="BTCUSDT",
        interval="1d",
        timestamp_units="ms-utc",
    )
    for key, bad in (
        ("source", "other"),
        ("symbol", "ETHUSDT"),
        ("interval", "1h"),
        ("timestamp_units", "s"),
    ):
        mutated = dict(good)
        mutated[key] = bad
        try:
            validate_manifest_values(
                mutated,
                source="binance-public-spot-archive",
                symbol="BTCUSDT",
                interval="1d",
                timestamp_units="ms-utc",
            )
        except ValueError:
            continue
        raise AssertionError(f"manifest mismatch not rejected: {key}={bad}")


def test_handoff_continuity_is_gapless_or_rejected():
    check_continuity("2026-09-30", "2026-10-01")
    for bad in ("2026-09-30", "2026-10-02", "2026-09-29"):
        try:
            check_continuity("2026-09-30", bad)
        except ValueError:
            continue
        raise AssertionError(f"handoff gap not rejected: {bad}")
