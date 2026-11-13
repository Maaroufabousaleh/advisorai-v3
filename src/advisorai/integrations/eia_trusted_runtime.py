"""Owner-operated EIA smoke check; never run from an agent or browser process.

Requires a separately reviewed Infisical machine identity with read-only access
to exactly EIA_API_KEY at the selected project path. No identity or credentials
are created by this module. Disable HTTP auto-instrumentation in the runtime.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC

from advisorai.config.secrets import CredentialScope, InfisicalCredentialResolver
from advisorai.integrations.public_data import EiaApiClient, SmokeTestOutcome


def infisical_reader_from_owner_environment():
    """Build a single-name Infisical reader from owner-injected runtime config.

    Requires ``infisicalsdk`` and Universal Auth credentials injected only
    into the reviewed trusted runtime. This coding environment must not receive
    or execute with those values.
    """
    required = (
        "INFISICAL_HOST",
        "INFISICAL_CLIENT_ID",
        "INFISICAL_CLIENT_SECRET",
        "INFISICAL_PROJECT_ID",
    )
    settings = {name: os.environ.get(name, "") for name in required}
    if any(not value for value in settings.values()):
        raise RuntimeError("trusted Infisical runtime configuration is unavailable")
    try:
        from infisical_sdk import InfisicalSDKClient

        client = InfisicalSDKClient(host=settings["INFISICAL_HOST"], cache_ttl=None)
        client.auth.universal_auth.login(
            client_id=settings["INFISICAL_CLIENT_ID"],
            client_secret=settings["INFISICAL_CLIENT_SECRET"],
        )
    except Exception:
        raise RuntimeError("trusted Infisical authentication failed") from None

    def read_one(name: str, project_id: str, environment: str, secret_path: str) -> str | None:
        if name != "EIA_API_KEY" or project_id != settings["INFISICAL_PROJECT_ID"]:
            raise RuntimeError("secret request is outside the EIA runtime allowlist")
        try:
            result = client.secrets.get_secret_by_name(
                secret_name="EIA_API_KEY",
                project_id=project_id,
                environment_slug=environment,
                secret_path=secret_path,
            )
            return result.secretValue
        except Exception:
            raise RuntimeError("trusted Infisical secret retrieval failed") from None

    return read_one


def run_owner_smoke(
    *,
    secret_reader,
    project_id: str,
    environment: str = "dev",
    secret_path: str = "/",
    client: EiaApiClient | None = None,
) -> SmokeTestOutcome:
    """Fetch one EIA key in memory and return only sanitized status metadata."""
    if environment != "dev":
        raise RuntimeError("EIA smoke check is restricted to the dev environment")
    resolver = InfisicalCredentialResolver(
        secret_reader,
        project_id=project_id,
        environment=environment,
        secret_path=secret_path,
    )
    key = resolver.get(CredentialScope.EIA_PUBLIC_DATA, "EIA_API_KEY")
    if not key:
        raise RuntimeError("EIA credential is unavailable")
    try:
        return (client or EiaApiClient()).smoke_test(key)
    finally:
        key = ""


def trusted_runtime_opted_in(environ: Mapping[str, str] | None = None) -> bool:
    """Guard used by deployment wrappers; no CLI auto-runs on import."""
    env = os.environ if environ is None else environ
    return env.get("ADVISORAI_TRUSTED_EIA_SMOKE") == "1"


def main() -> int:
    """Run only when explicitly launched inside the owner-reviewed runtime."""
    if not trusted_runtime_opted_in():
        raise SystemExit("EIA smoke check requires trusted-runtime opt-in")
    project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
    try:
        outcome = run_owner_smoke(
            secret_reader=infisical_reader_from_owner_environment(),
            project_id=project_id,
            environment="dev",
            secret_path=os.environ.get("INFISICAL_SECRET_PATH", "/"),
        )
    except Exception:
        print("EIA authenticated smoke check failed; details suppressed")
        return 1
    print(
        "EIA authenticated smoke check completed: "
        f"http_status={outcome.status_code} rows={outcome.row_count} "
        f"retrieved_at={outcome.retrieved_at.astimezone(UTC).isoformat()}"
    )
    return 0


__all__ = ["infisical_reader_from_owner_environment", "run_owner_smoke", "trusted_runtime_opted_in"]

if __name__ == "__main__":  # pragma: no cover - trusted owner runtime only
    raise SystemExit(main())
