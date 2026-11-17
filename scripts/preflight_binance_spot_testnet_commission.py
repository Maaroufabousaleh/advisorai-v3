"""Read-only Binance Spot Testnet commission preflight for the FOK probe.

Establishes, before any submission, whether a taker fee can apply on the
probe symbol and therefore whether BNB payment can apply. Read-only: three
GETs at most (product catalogue, account state), no writes, no retries of
anything but idempotent reads.

Decision rule (fail-closed, shared with the probe via
:func:`evaluate_commission`):

- taker rate parses to exactly zero (``commissionRates.taker``, falling
  back to legacy ``takerCommission``) → fee known zero → BNB payment
  cannot apply → exit 0.
- taker rate positive → BNB burn status is not observable through the
  admitted ``/api/v3`` surface (the adapter rejects ``/sapi`` by policy),
  so BNB payment must be assumed possible → exit 1.
- rates missing, malformed, or negative → cannot establish → exit 1.

Stdout carries only the verdict (status, symbol, ``bnb_payment_can_apply``,
fixed reason code). Balances, credentials, and response bodies are never
printed or persisted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisorai.config import CredentialResolver  # noqa: E402
from advisorai.integrations import (  # noqa: E402
    VenueTransportError,
    build_binance_spot_testnet_transport,
)

COMMISSION_SCHEMA = "advisorai.phase2.binance-spot-testnet.commission-preflight.v1"
REASON_FEE_KNOWN_ZERO = "fee_known_zero"
REASON_TAKER_POSITIVE = "taker_rate_positive_burn_unobservable"
REASON_UNESTABLISHABLE = "commission_rates_unestablishable"


def _taker_rate(account: Mapping[str, object]) -> Decimal | None:
    """Return the spot taker rate, or None when it cannot be established."""
    candidates: list[object] = []
    rates = account.get("commissionRates")
    if isinstance(rates, Mapping):
        candidates.append(rates.get("taker"))
    else:
        candidates.append(account.get("takerCommission"))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return Decimal(str(candidate))
        except (InvalidOperation, ValueError):
            return None
    return None


def evaluate_commission(transport, symbol: str) -> dict[str, object]:
    """Decide whether BNB payment can apply for a taker order on ``symbol``.

    Verifies the symbol against live product truth, then reads the
    authenticated account's commission rates. Returns a verdict dict with
    no balances, no credentials, and no numeric account values.
    """
    products = transport.list_products()
    transport.verify_symbol_mappings(products)
    if symbol.strip().upper() not in transport.verified_symbol_ids:
        raise VenueTransportError("commission preflight symbol is not admitted")
    account = transport.account_state()
    if not isinstance(account, Mapping):
        raise VenueTransportError("Binance account response must be an object")
    rate = _taker_rate(account)
    if rate is None or rate < 0:
        return {
            "status": "failed",
            "symbol": symbol.strip().upper(),
            "bnb_payment_can_apply": True,
            "reason": REASON_UNESTABLISHABLE,
        }
    if rate > 0:
        return {
            "status": "failed",
            "symbol": symbol.strip().upper(),
            "bnb_payment_can_apply": True,
            "reason": REASON_TAKER_POSITIVE,
        }
    return {
        "status": "passed",
        "symbol": symbol.strip().upper(),
        "bnb_payment_can_apply": False,
        "reason": REASON_FEE_KNOWN_ZERO,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args(argv)
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        print(
            "refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly",
            file=sys.stderr,
        )
        return 2
    try:
        resolver = CredentialResolver.from_env_file(args.secrets)
    except (OSError, ValueError):
        print("cannot read secrets file", file=sys.stderr)
        return 2
    try:
        transport = build_binance_spot_testnet_transport(resolver)
        verdict = evaluate_commission(transport, args.symbol)
    except Exception as exc:  # fail closed; never leak response detail
        verdict = {
            "status": "failed",
            "symbol": str(args.symbol).strip().upper(),
            "bnb_payment_can_apply": True,
            "reason": REASON_UNESTABLISHABLE,
            "error_class": type(exc).__name__,
        }
    print(
        json.dumps(
            {"schema": COMMISSION_SCHEMA, **verdict},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if verdict.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
