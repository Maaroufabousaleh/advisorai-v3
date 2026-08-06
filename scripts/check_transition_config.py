"""Validate paper-transition configuration without making network calls.

Usage:
    uv run python scripts/check_transition_config.py
    uv run python scripts/check_transition_config.py --secrets /path/to/secrets.env

The command prints connector identities and credential reference names only;
it never prints secret values or sends a request.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from advisorai.config import SecretSettings, load_env_file
from advisorai.integrations import ConnectorCard, ConnectorState


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
        help="safe export-style env file; values are never printed",
    )
    args = parser.parse_args()
    values = load_env_file(args.secrets)
    settings = SecretSettings.from_mapping(values)
    print(f"environment={settings.environment}")
    print(
        f"venue={settings.venue_name or '<unset>'} venue_environment={settings.venue_environment}"
    )
    print(f"venue_endpoint={settings.venue_base_url or '<unset>'}")
    print(
        f"llm_provider={settings.llm_provider or '<unset>'} model={settings.llm_model or '<unset>'}"
    )
    print(f"llm_endpoint={settings.llm_base_url or '<unset>'}")
    print(
        "credential_refs=" + ",".join(settings.credential_references())
        if settings.credential_references()
        else "credential_refs=<none>"
    )
    card = ConnectorCard(
        name="transition-config",
        owner="operator",
        purpose="paper/testnet real API transition",
        endpoint=settings.venue_base_url or "https://unset.invalid",
        allowed_hosts=(
            (settings.venue_base_url or "https://unset.invalid")
            .split("//", 1)[-1]
            .split("/", 1)[0],
        ),
        environment=settings.venue_environment,
        credential_refs=settings.credential_references(),
        source_grade="execution_grade",
        quota_and_cost="operator review required",
        adapter_version="transition-v1",
        rollback_procedure="revoke connector and return to deterministic paper fixture",
        state=ConnectorState.CONFIGURED,
    )
    print(f"config_hash={card.canonical_hash()}")
    print("network_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
