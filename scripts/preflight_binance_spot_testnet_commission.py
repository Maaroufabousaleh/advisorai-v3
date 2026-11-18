"""Read-only Binance Spot Testnet commission preflight for the FOK probe.

Establishes, before any submission, the symbol-specific commission for a
SELL taker order via the documented ``GET /api/v3/account/commission``
endpoint. Read-only: product catalogue plus one commission read, no
writes, no retries of anything but idempotent reads.

Decision rule (fail-closed, shared with the probe via
:func:`evaluate_commission`). The applicable legs for a SELL taker fill
are the taker and seller legs of each block:

- all six applicable legs (standard/special/tax x taker/seller) verified
  present, parseable, non-negative, and exactly zero → fee known zero →
  BNB payment cannot apply → exit 0.
- otherwise, the ``discount`` object must rule out BNB payment with an
  explicit ``enabledForAccount: false`` or ``enabledForSymbol: false``;
  the fee then lands in the received asset (USDT for a SELL), which the
  ledger supports → exit 0. The exact post-fill fee check still verifies
  the actual fill.
- missing legs, malformed or negative values, a missing discount object
  when fees are positive, or BNB payment not ruled out → exit 1.

Stdout carries only the verdict (status, symbol, ``bnb_payment_can_apply``,
fixed reason code). Balances, credentials, rates, and response bodies are
never printed or persisted.
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
REASON_USDT_NO_BNB = "positive_usdt_fee_bnb_ruled_out"
REASON_BNB_MAY_APPLY = "bnb_payment_may_apply"
REASON_UNESTABLISHABLE = "commission_unestablishable"

_COMMISSION_BLOCKS = ("standardCommission", "specialCommission", "taxCommission")
_SELL_TAKER_LEGS = ("taker", "seller")


def _applicable_legs(record: Mapping[str, object]) -> list[Decimal] | None:
    """Return the six SELL-taker legs, or None when any is unestablishable."""
    legs: list[Decimal] = []
    for block in _COMMISSION_BLOCKS:
        rates = record.get(block)
        if not isinstance(rates, Mapping):
            return None
        for leg in _SELL_TAKER_LEGS:
            raw = rates.get(leg)
            if raw is None:
                return None
            try:
                value = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                return None
            if value < 0 or not value.is_finite():
                return None
            legs.append(value)
    return legs


def _bnb_ruled_out(record: Mapping[str, object]) -> bool:
    """True only when the discount object explicitly disables BNB payment."""
    discount = record.get("discount")
    if not isinstance(discount, Mapping):
        return False
    account = discount.get("enabledForAccount")
    symbol = discount.get("enabledForSymbol")
    if not isinstance(account, bool) or not isinstance(symbol, bool):
        return False
    return account is False or symbol is False


def evaluate_commission(transport, symbol: str) -> dict[str, object]:
    """Decide whether a SELL FOK on ``symbol`` may proceed fee-wise.

    Reads the symbol-specific commission record and applies the admission
    rule. Returns a verdict dict with no balances, no credentials, and no
    numeric values.
    """
    products = transport.list_products()
    transport.verify_symbol_mappings(products)
    if symbol.strip().upper() not in transport.verified_symbol_ids:
        raise VenueTransportError("commission preflight symbol is not admitted")
    record = transport.symbol_commission(symbol.strip().upper())
    if not isinstance(record, Mapping):
        raise VenueTransportError("Binance commission response must be an object")
    legs = _applicable_legs(record)
    if legs is None:
        return {
            "status": "failed",
            "symbol": symbol.strip().upper(),
            "bnb_payment_can_apply": True,
            "reason": REASON_UNESTABLISHABLE,
        }
    if all(leg == 0 for leg in legs):
        return {
            "status": "passed",
            "symbol": symbol.strip().upper(),
            "bnb_payment_can_apply": False,
            "reason": REASON_FEE_KNOWN_ZERO,
        }
    if _bnb_ruled_out(record):
        return {
            "status": "passed",
            "symbol": symbol.strip().upper(),
            "bnb_payment_can_apply": False,
            "reason": REASON_USDT_NO_BNB,
        }
    return {
        "status": "failed",
        "symbol": symbol.strip().upper(),
        "bnb_payment_can_apply": True,
        "reason": REASON_BNB_MAY_APPLY,
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
