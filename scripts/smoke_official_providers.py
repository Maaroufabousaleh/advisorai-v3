"""Opt-in, read-only BLS/BEA/EIA smoke checks using the local operator inventory.

The owner runs this with the real ``secrets.env``. The agent/test path uses
synthetic credentials only. Each provider resolves exactly one scope, so one
key is never visible to another provider's client:

- BLS (``BLS_PUBLIC_DATA``): ``BLS_API_KEY`` is optional upstream (it raises
  rate limits). Keyed mode sends it only in the POST body; public mode runs
  the same CPI read unauthenticated. Neither mode may fall back silently:
  the emitted line always names ``keyed`` or ``public``.
- BEA (``BEA_PUBLIC_DATA``): replacement ``BEA_API_KEY`` UserID is required;
  the call fails closed when the scope is empty.
- EIA (``EIA_PUBLIC_DATA``): ``EIA_API_KEY`` is supported as a local-file
  credential source through ``CredentialResolver``. The Infisical route in
  ``advisorai.integrations.eia_trusted_runtime`` remains the documented
  alternative; this script never assumes that route is configured.

Every mode returns exactly one sanitized line: provider, mode, HTTP status,
row count, and retrieval timestamp. It never prints a key, URL, request body,
or response body. Empty assignments in ``secrets.env`` count as unset.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from advisorai.config import CredentialResolver, CredentialScope
from advisorai.integrations.public_data import (  # noqa: E402
    BEA_DATA_URL,
    BLS_V2_URL,
    EIA_BASE_URL,
    BeaDataClient,
    BlsV2Client,
    EiaApiClient,
)


@dataclass(frozen=True, slots=True)
class ProviderSmokeOutcome:
    """Sanitized smoke metadata only: provider, HTTP status, row count, time."""

    provider: str
    status_code: int
    row_count: int
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if self.provider not in {"bls", "bea", "eia"}:
            raise ValueError("unknown smoke-test provider")
        if self.status_code != 200 or self.row_count < 1:
            raise ValueError("smoke-test outcome must contain successful rows")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("smoke-test timestamp must include a timezone")

BLS_HOST = urlsplit(BLS_V2_URL).hostname or ""
BEA_HOST = urlsplit(BEA_DATA_URL).hostname or ""
EIA_HOST = urlsplit(EIA_BASE_URL).hostname or ""

_PROVIDER_HOSTS = {
    "bls": BLS_HOST,
    "bea": BEA_HOST,
    "eia": EIA_HOST,
}


def _reviewed_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or " " in host or "/" in host or host.startswith("-"):
        raise ValueError("reviewed host must be a bare hostname")
    return host


def _emit(payload: dict[str, object]) -> int:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload.get("status") in {"passed", "not_ready"} else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=("bls", "bea", "eia"),
    )
    parser.add_argument(
        "--reviewed-host",
        action="append",
        default=[],
        help="reviewed provider hostname; repeat for an explicit allowlist",
    )
    return parser.parse_args(argv)


def smoke_bls(
    *, resolver: CredentialResolver, requester: object = None
) -> tuple[dict[str, object], ProviderSmokeOutcome]:
    """One keyed-or-public CPI read; returns scope names and sanitized outcome."""
    scoped_names = resolver.available_names(CredentialScope.BLS_PUBLIC_DATA)
    key = resolver.get(CredentialScope.BLS_PUBLIC_DATA, "BLS_API_KEY")
    mode = "keyed" if key else "public"
    rows = BlsV2Client(requester=requester).fetch_series(
        "CUUR0000SA0",
        start_year=2024,
        end_year=2024,
        registration_key=key,
    )
    if not rows:
        raise RuntimeError("BLS smoke-test returned no rows")
    retrieved_at = rows[0].first_available_at
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=UTC)
    outcome = ProviderSmokeOutcome("bls", 200, len(rows), retrieved_at.astimezone(UTC))
    return {"credential_refs": scoped_names, "mode": mode}, outcome


def smoke_bea(
    *, resolver: CredentialResolver, requester: object = None
) -> tuple[dict[str, object], ProviderSmokeOutcome]:
    """One keyed NIPA read; the key is required upstream and never optional."""
    scoped_names = resolver.available_names(CredentialScope.BEA_PUBLIC_DATA)
    key = resolver.get(CredentialScope.BEA_PUBLIC_DATA, "BEA_API_KEY")
    if not key:
        raise RuntimeError("BEA credential is unavailable")
    rows = BeaDataClient(requester=requester).fetch_data(
        "NIPA",
        series_id="T20100-Q",
        parameters={
            "TableName": "T20100",
            "Frequency": "Q",
            "Year": "2023,2024",
        },
        user_id=key,
    )
    if not rows:
        raise RuntimeError("BEA smoke-test returned no rows")
    retrieved_at = rows[0].first_available_at
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=UTC)
    outcome = ProviderSmokeOutcome("bea", 200, len(rows), retrieved_at.astimezone(UTC))
    return {"credential_refs": scoped_names}, outcome


def smoke_eia(
    *, resolver: CredentialResolver, requester: object = None
) -> tuple[dict[str, object], ProviderSmokeOutcome]:
    """One keyed EIA retail-sales read via the local ``secrets.env`` file.

    ``EIA_API_KEY`` is supported as a local-file credential source: this path
    resolves the ``EIA_PUBLIC_DATA`` scope through ``CredentialResolver`` and
    holds the value only inside the client call. The Infisical route in
    ``advisorai.integrations.eia_trusted_runtime`` remains the documented
    alternative; this script never assumes that route is configured.
    """
    scoped_names = resolver.available_names(CredentialScope.EIA_PUBLIC_DATA)
    key = resolver.get(CredentialScope.EIA_PUBLIC_DATA, "EIA_API_KEY")
    if not key:
        raise RuntimeError("EIA credential is unavailable")
    raw = EiaApiClient(requester=requester).smoke_test(key)
    outcome = ProviderSmokeOutcome("eia", raw.status_code, raw.row_count, raw.retrieved_at)
    return {"credential_refs": scoped_names}, outcome


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")
    try:
        reviewed_hosts = tuple(_reviewed_host(value) for value in args.reviewed_host)
    except ValueError as exc:
        raise SystemExit(f"refusing smoke check: {exc}") from None
    expected = _PROVIDER_HOSTS[args.provider]
    if expected not in reviewed_hosts:
        raise SystemExit(
            f"refusing smoke check: reviewed host allowlist must contain {expected}"
        )
    try:
        resolver = CredentialResolver.from_env_file(args.secrets)
    except Exception as exc:
        return _emit(
            {
                "provider": args.provider,
                "status": "failed",
                "reason": "credential_inventory_unreadable",
                "error_class": type(exc).__name__,
            }
        )
    try:
        if args.provider == "bls":
            meta, outcome = smoke_bls(resolver=resolver)
        elif args.provider == "bea":
            meta, outcome = smoke_bea(resolver=resolver)
        else:
            meta, outcome = smoke_eia(resolver=resolver)
    except Exception as exc:
        return _emit(
            {
                "provider": args.provider,
                "status": "failed",
                "reason": "provider_read_failed",
                "error_class": type(exc).__name__,
            }
        )
    return _emit(
        {
            "provider": args.provider,
            "status": "passed",
            "mode": meta.get("mode", "keyed"),
            "http_status": outcome.status_code,
            "rows": outcome.row_count,
            "retrieved_at": outcome.retrieved_at.astimezone(UTC).isoformat(),
            "credential_refs": list(meta.get("credential_refs", ())),
            "network_calls": 1,
            "writes_attempted": False,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
