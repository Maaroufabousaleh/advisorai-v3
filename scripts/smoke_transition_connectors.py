"""Opt-in, read-only network smoke test for the transition connectors.

This intentionally does not place an order and does not call transfer or
withdrawal endpoints. Provider-specific endpoints may be supplied explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

from advisorai.config import SecretSettings, load_env_file
from advisorai.config.secrets import redacted_headers
from advisorai.integrations import HmacVenueSigner, HttpClientConfig, SafeHttpClient


def _host(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError("connector endpoint has no hostname")
    return parsed.hostname.lower().rstrip(".")


def _path(value: str) -> str:
    if not value.startswith("/") or any(
        token in value.lower() for token in ("withdraw", "transfer", "order", "trade")
    ):
        raise ValueError(
            "smoke paths must be read-only and cannot include order/transfer operations"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets", type=Path, default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env"))
    )
    parser.add_argument("--llm-path", type=_path, default="/models")
    parser.add_argument("--venue-path", type=_path, default="/health")
    args = parser.parse_args()
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")
    settings = SecretSettings.from_mapping(load_env_file(args.secrets))
    if not settings.llm_base_url or not settings.venue_base_url:
        raise SystemExit("LLM and venue base URLs are required")
    llm_key = settings.secret_for("ADVISORAI_LLM_API_KEY")
    venue_key = settings.secret_for("ADVISORAI_VENUE_API_KEY")
    venue_secret = settings.secret_for("ADVISORAI_VENUE_API_SECRET")
    if not llm_key or not venue_key or not venue_secret:
        raise SystemExit("direct LLM and paper venue credentials are required")
    llm_client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(_host(settings.llm_base_url),), user_agent="advisorai-v3/smoke"
        ),
        base_url=settings.llm_base_url,
        secret_values={"llm": llm_key},
    )
    venue_client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(_host(settings.venue_base_url),), user_agent="advisorai-v3/smoke"
        ),
        base_url=settings.venue_base_url,
        secret_values={"venue": venue_secret},
    )
    llm_response = llm_client.get(
        f"{settings.llm_base_url.rstrip('/')}/{args.llm_path.lstrip('/')}",
        headers={"Authorization": f"Bearer {llm_key}"},
    )
    signer = HmacVenueSigner(api_key=venue_key, api_secret=SecretStr(venue_secret))
    path = args.venue_path
    headers = signer.sign(method="GET", path=path, timestamp=str(int(time.time() * 1000)), body=b"")
    venue_response = venue_client.request(
        "GET", f"{settings.venue_base_url.rstrip('/')}/{path.lstrip('/')}", headers=headers
    )
    print(
        json.dumps(
            {
                "network_calls": 2,
                "llm_status": llm_response.status_code,
                "venue_status": venue_response.status_code,
                "redacted_headers": redacted_headers(headers),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
