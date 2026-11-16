"""Read-only P02 baseline diagnostic: preview the accounting-baseline pin.

Calls ``GET /api/v3/account`` once through the reviewed testnet transport
(``PAPER_VENUE`` scope only) and reports the assets the launcher would pin
into the durable baseline: BTC and USDT seed trading state; every other
nonzero asset pins as a frozen, unmarked, exactly-reconciled position.
Reports each pinned asset's symbol plus whether its ``free`` or ``locked``
leg is nonzero. Prints no credentials, no amounts, no prices, no full
account data.

Owner-run only. The coding agent never invokes this with real secrets.
Usage (from the owner shell)::

    uv run python scripts/diagnose_testnet_baseline.py --secrets secrets.env

Exit codes: 0 = launcher-compatible (guard would pass; pin list shown);
1 = launcher would refuse (nonzero canonical position outside BTC/USDT,
off-profile open orders, or a locked BTC leg); 2 = input/transport error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    HttpTransportError,
    VenueTransportError,
    build_binance_spot_testnet_transport,
    build_coinbase_exchange_sandbox_transport,
)


def _nonzero(value: object) -> bool:
    """True for a finite nonzero decimal; malformed values fail closed."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return True
    if not parsed.is_finite():
        return True
    return parsed != 0


def classify_balances(
    balances: object, *, base_asset: str = "BTC", quote_asset: str = "USDT"
) -> list[dict[str, object]]:
    """Return the pin-preview assets: symbol + which leg is nonzero.

    Mirrors the launcher predicate exactly: profile assets are BTC (base)
    and USDT (quote); any other asset with nonzero free+locked pins into
    the baseline as a frozen ``asset:`` position (excluded from equity
    and buying power, exactly reconciled). Pure function over an
    already-fetched ``balances`` sequence; performs no I/O. Accepts the
    sequence the real adapter returns (``list_balances()`` is a tuple)
    as well as plain lists.
    """
    if isinstance(balances, (str, bytes)) or not isinstance(balances, Sequence):
        raise ValueError("balances payload must be a sequence of balance records")
    profile = {base_asset.strip().upper(), quote_asset.strip().upper()}
    flagged: list[dict[str, object]] = []
    for item in balances:
        if not isinstance(item, Mapping):
            raise ValueError("balance record must be an object")
        asset = str(item.get("asset", "")).strip().upper()
        if not asset or asset in profile:
            continue
        free_nonzero = _nonzero(item.get("free", "0"))
        locked_nonzero = _nonzero(item.get("locked", "0"))
        if free_nonzero or locked_nonzero:
            flagged.append(
                {"asset": asset, "free_nonzero": free_nonzero, "locked_nonzero": locked_nonzero}
            )
    return flagged


def classify_coinbase_accounts(
    accounts: object, *, base_asset: str = "BTC", quote_asset: str = "USD"
) -> list[dict[str, object]]:
    """Return the reject-triggering currencies: symbol + which leg is nonzero.

    Coinbase dual of :func:`classify_balances` over ``GET /accounts``
    records (``{currency, balance, available, hold}``). Mirrors the
    launcher predicate exactly: profile assets are BTC (base) and USD
    (quote); any other currency with a nonzero leg trips the
    ``non-zero unmapped asset`` refusal in ``fetch_account_snapshot()``
    before the launcher guard is even reached. Legs reported as
    ``available``/``hold``; ``balance`` is the venue total and is not
    double-counted. Pure function; performs no I/O.
    """
    if isinstance(accounts, (str, bytes)) or not isinstance(accounts, Sequence):
        raise ValueError("accounts payload must be a sequence of account records")
    profile = {base_asset.strip().upper(), quote_asset.strip().upper()}
    flagged: list[dict[str, object]] = []
    for item in accounts:
        if not isinstance(item, Mapping):
            raise ValueError("account record must be an object")
        asset = str(item.get("currency", "")).strip().upper()
        if not asset or asset in profile:
            continue
        available_nonzero = _nonzero(item.get("available", "0"))
        hold_nonzero = _nonzero(item.get("hold", "0"))
        if available_nonzero or hold_nonzero:
            flagged.append(
                {
                    "asset": asset,
                    "available_nonzero": available_nonzero,
                    "hold_nonzero": hold_nonzero,
                }
            )
    return flagged


def diagnose_recovery_fill_open_orders_and_balance(
    transport, *, symbol: str
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    """Read-only recovery distinguisher: fills vs open orders vs balances.

    Verifies BTCUSDT/ETHUSDT product mappings from provider truth first
    (the launcher's own precondition), then returns
    ``(fills, open_orders, balances)`` where fills carry only
    ``venue_fill_id`` + ISO ``occurred_at`` (no qty/price/fee), open orders
    carry only ``venue_order_id`` + symbol, and balances carry only the
    BTC/USDT leg-nonzero flags. Prints nothing; the caller formats.
    """
    from datetime import UTC, datetime

    transport.verify_symbol_mappings(transport.list_products())
    fills: list[dict[str, object]] = []
    for record in transport.list_fills():
        occurred = record.get("occurred_at", record.get("time", ""))
        if isinstance(occurred, (int, float)):
            occurred = datetime.fromtimestamp(occurred / 1000, tz=UTC).isoformat()
        fills.append(
            {
                "venue_fill_id": str(record.get("venue_fill_id", record.get("id", ""))),
                "occurred_at": str(occurred),
            }
        )
    open_orders: list[dict[str, object]] = []
    for record in transport.list_open_orders():
        open_orders.append(
            {
                "venue_order_id": str(record.get("venue_order_id", "")),
                "symbol": str(record.get("symbol", "")),
            }
        )
    balances: dict[str, object] = {}
    for record in transport.list_balances():
        asset = str(record.get("asset", "")).strip().upper()
        if asset in {"BTC", "USDT"}:
            balances[asset] = {
                "free_nonzero": _nonzero(record.get("free", "0")),
                "locked_nonzero": _nonzero(record.get("locked", "0")),
            }
    return fills, open_orders, balances


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", type=Path, default=Path("secrets.env"))
    parser.add_argument(
        "--provider",
        default="binance_spot_testnet",
        choices=("binance_spot_testnet", "coinbase_exchange_sandbox"),
    )
    parser.add_argument("--instrument", default=None)
    parser.add_argument("--quote", default=None)
    parser.add_argument(
        "--recovery-check",
        action="store_true",
        help=(
            "Read-only recovery distinguisher: list venue fills "
            "(id + time only), open orders (id + symbol only), and "
            "BTC/USDT leg flags. No amounts, prices, or fees printed."
        ),
    )
    args = parser.parse_args(argv)

    if args.provider == "binance_spot_testnet":
        instrument = (args.instrument or "BTCUSDT").strip().upper()
        quote = (args.quote or "USDT").strip().upper()
        if instrument != "BTCUSDT" or quote != "USDT":
            print("this path admits exactly BTCUSDT/USDT", file=sys.stderr)
            return 2
        leg_names = ("free", "locked")
        flag_keys = ("free_nonzero", "locked_nonzero")
    else:
        instrument = (args.instrument or "BTC-USD").strip().upper()
        quote = (args.quote or "USD").strip().upper()
        if instrument != "BTC-USD" or quote != "USD":
            print("this path admits exactly BTC-USD/USD", file=sys.stderr)
            return 2
        leg_names = ("available", "hold")
        flag_keys = ("available_nonzero", "hold_nonzero")
    try:
        resolver = CredentialResolver.from_env_file(args.secrets)
        if args.provider == "binance_spot_testnet":
            transport = build_binance_spot_testnet_transport(resolver)
            if args.recovery_check:
                fills, open_orders, balances = diagnose_recovery_fill_open_orders_and_balance(
                    transport, symbol=instrument
                )
                print(f"venue_fills={len(fills)}")
                for fill in fills:
                    print(
                        f"  fill venue_fill_id={fill['venue_fill_id']} "
                        f"occurred_at={fill['occurred_at']}"
                    )
                print(f"venue_open_orders={len(open_orders)}")
                for order in open_orders:
                    print(
                        f"  order venue_order_id={order['venue_order_id']} "
                        f"symbol={order['symbol']}"
                    )
                for asset in ("BTC", "USDT"):
                    legs = balances.get(asset, {})
                    print(
                        f"  balance {asset} "
                        f"free_nonzero={legs.get('free_nonzero')} "
                        f"locked_nonzero={legs.get('locked_nonzero')}"
                    )
                return 0
            flagged = classify_balances(transport.list_balances())
        else:
            if args.recovery_check:
                print("recovery-check supports only binance_spot_testnet", file=sys.stderr)
                return 2
            transport = build_coinbase_exchange_sandbox_transport(resolver)
            flagged = classify_coinbase_accounts(transport.list_balances())
    except (OSError, ValueError, VenueTransportError, HttpTransportError) as exc:
        print(f"diagnostic failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    if args.provider == "binance_spot_testnet":
        print("baseline guard: PASS (launcher pins listed assets as frozen, reconciled)")
        if not flagged:
            print("  pinned=none")
            return 0
        for entry in flagged:
            legs = "/".join(
                leg for leg, key in zip(leg_names, flag_keys, strict=True) if entry[key]
            )
            print(f"  pinned={entry['asset']} nonzero={legs}")
        return 0
    if not flagged:
        print("baseline guard: PASS (no out-of-profile balances)")
        return 0
    print("baseline guard: REJECT (clear these assets first):")
    for entry in flagged:
        legs = "/".join(
            leg for leg, key in zip(leg_names, flag_keys, strict=True) if entry[key]
        )
        print(f"  asset={entry['asset']} nonzero={legs}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
