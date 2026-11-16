"""Read-only P02 baseline diagnostic: which assets trigger the out-of-profile guard.

Calls ``GET /api/v3/account`` once through the reviewed testnet transport
(``PAPER_VENUE`` scope only) and reports ONLY the asset symbols that would
trip ``read_testnet_baseline()``'s rejection predicate, plus whether their
``free`` or ``locked`` leg is nonzero. Prints no credentials, no amounts,
no prices, no full account data.

Owner-run only. The coding agent never invokes this with real secrets.
Usage (from the owner shell)::

    uv run python scripts/diagnose_testnet_baseline.py --secrets secrets.env

Exit codes: 0 = profile-compatible (launcher would proceed past the guard);
1 = out-of-profile balances would be rejected; 2 = input/transport error.
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
)


def _nonzero(value: object) -> bool:
    """True only for a finite nonzero decimal; malformed values fail closed."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return True
    return parsed.is_finite() and parsed != 0


def classify_balances(
    balances: object, *, base_asset: str = "BTC", quote_asset: str = "USDT"
) -> list[dict[str, object]]:
    """Return the reject-triggering assets: symbol + which leg is nonzero.

    Mirrors the launcher predicate exactly: profile assets are BTC (base)
    and USDT (quote); any other asset with nonzero free+locked trips the
    ``asset:`` guard in ``read_testnet_baseline()``. Pure function over an
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", type=Path, default=Path("secrets.env"))
    parser.add_argument("--instrument", default="BTCUSDT")
    parser.add_argument("--quote", default="USDT")
    args = parser.parse_args(argv)

    if args.instrument.strip().upper() != "BTCUSDT" or args.quote.strip().upper() != "USDT":
        print("this path admits exactly BTCUSDT/USDT", file=sys.stderr)
        return 2
    try:
        resolver = CredentialResolver.from_env_file(args.secrets)
        transport = build_binance_spot_testnet_transport(resolver)
        balances = transport.list_balances()
        flagged = classify_balances(balances)
    except (OSError, ValueError, VenueTransportError, HttpTransportError) as exc:
        print(f"diagnostic failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not flagged:
        print("baseline guard: PASS (no out-of-profile balances)")
        return 0
    print("baseline guard: REJECT (clear these assets first):")
    for entry in flagged:
        legs = "/".join(
            leg
            for leg, on in (("free", entry["free_nonzero"]), ("locked", entry["locked_nonzero"]))
            if on
        )
        print(f"  asset={entry['asset']} nonzero={legs}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
