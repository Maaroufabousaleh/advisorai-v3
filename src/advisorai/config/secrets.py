"""Typed, fail-closed loading of local AdvisorAI connector secrets.

The repository's ``secrets.env`` file is an operator input, not executable
configuration.  This module deliberately parses the small ``export NAME=...``
format without invoking a shell and keeps credential values out of model
representations, logs, and persisted configuration bundles.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator

_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")

# This is intentionally the union of names in the checked-in operator
# template.  A connector still chooses the subset it is allowed to receive.
KNOWN_ENV_NAMES = frozenset(
    {
        "ADVISORAI_API_AUTH_TOKEN",
        "ADVISORAI_ARTIFACT_ENCRYPTION_KEY",
        "ADVISORAI_ARTIFACT_SIGNING_KEY",
        "ADVISORAI_CONFIG_DIR",
        "ADVISORAI_CONFIG_SIGNING_KEY",
        "ADVISORAI_ENVIRONMENT",
        "ADVISORAI_LEDGER_ENCRYPTION_KEY",
        "ADVISORAI_LLM_API_KEY",
        "ADVISORAI_LLM_BASE_URL",
        "ADVISORAI_LLM_MODEL",
        "ADVISORAI_LLM_PROVIDER",
        "ADVISORAI_SESSION_SECRET",
        "ADVISORAI_VENUE_ACCOUNT_ID",
        "ADVISORAI_VENUE_API_KEY",
        "ADVISORAI_VENUE_API_SECRET",
        "ADVISORAI_VENUE_BASE_URL",
        "ADVISORAI_VENUE_ENVIRONMENT",
        "ADVISORAI_VENUE_NAME",
        "ADVISORAI_VENUE_PASSPHRASE",
        "ADVISORAI_VENUE_SUBACCOUNT",
        "ADVISORAI_VENUE_WS_URL",
        "ADVISORAI_WEBHOOK_SIGNING_SECRET",
        "ADVISORAI_DASHBOARD_PASSWORD_HASH",
        "ADVISORAI_DASHBOARD_TOTP_SECRET",
        "ADVISORAI_DASHBOARD_SUBJECT",
        "ADVISORAI_DASHBOARD_COOKIE_SECURE",
        "ALFRED_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_STORAGE_ACCOUNT",
        "AZURE_STORAGE_KEY",
        "AZURE_STORAGE_SAS_TOKEN",
        "BEA_API_KEY",
        "BLS_API_KEY",
        "CCXT_API_KEY",
        "CCXT_API_SECRET",
        "CCXT_EXCHANGE_ID",
        "CCXT_PASSWORD",
        "CENTRAL_BANK_API_KEY",
        "COHERE_API_KEY",
        "DATA_VENDOR_API_KEY",
        "DATA_VENDOR_API_SECRET",
        "DERIBIT_API_KEY",
        "DERIBIT_API_SECRET",
        "DERIBIT_CLIENT_ID",
        "DERIBIT_CLIENT_SECRET",
        "DERIBIT_TESTNET",
        "FRED_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GROQ_API_KEY",
        "HF_TOKEN",
        "HUGGINGFACE_HUB_TOKEN",
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY_PATH",
        "LITELLM_BASE_URL",
        "LITELLM_MASTER_KEY",
        "LITELLM_SALT_KEY",
        "LSE_API_KEY",
        "LSE_API_SECRET",
        "MISTRAL_API_KEY",
        "MLFLOW_TRACKING_TOKEN",
        "NATS_CREDS_FILE",
        "NATS_NKEY",
        "NATS_PASSWORD",
        "NATS_URL",
        "NATS_USER",
        "OMNICLOUD_API_KEY",
        "OMNICLOUD_API_SECRET",
        "OMNICLOUD_BASE_URL",
        "OMNIROUTE_API_KEY",
        "OMNIROUTE_BASE_URL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_PASSPHRASE",
        "POLYMARKET_API_SECRET",
        "PREFECT_API_KEY",
        "PREFECT_API_URL",
        "RCLONE_CONFIG",
        "RCLONE_CONFIG_PASS",
        "RCLONE_CRYPT_REMOTE",
        "RCLONE_REMOTE",
        "RCLONE_S3_ACCESS_KEY_ID",
        "RCLONE_S3_ENDPOINT",
        "RCLONE_S3_REGION",
        "RCLONE_S3_SECRET_ACCESS_KEY",
        "RCLONE_S3_SESSION_TOKEN",
        "SEC_USER_AGENT",
        "TOGETHER_API_KEY",
        "TREASURY_API_KEY",
        "WANDB_API_KEY",
    }
)

SECRET_ENV_NAMES = frozenset(
    name
    for name in KNOWN_ENV_NAMES
    if any(
        token in name
        for token in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSPHRASE", "AUTH", "NKEY")
    )
)


def _unquote(value: str, *, line_number: int) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        # shlex only parses a value; it never evaluates substitutions or
        # commands.  Reject multiple shell words so malformed values fail
        # closed rather than being silently concatenated.
        words = shlex.split(value, comments=True, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid secrets.env quoting at line {line_number}") from exc
    if len(words) != 1:
        raise ValueError(f"invalid secrets.env value at line {line_number}")
    return words[0]


def parse_env_text(text: str, *, strict: bool = True) -> dict[str, str]:
    """Parse the safe subset used by the project's ignored secrets template."""

    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match is None:
            raise ValueError(f"invalid secrets.env assignment at line {line_number}")
        name, raw_value = match.groups()
        if not _NAME.fullmatch(name):
            raise ValueError(f"invalid environment variable name at line {line_number}")
        if strict and name not in KNOWN_ENV_NAMES:
            raise ValueError(f"unknown AdvisorAI environment variable: {name}")
        if name in parsed:
            raise ValueError(f"duplicate AdvisorAI environment variable: {name}")
        parsed[name] = _unquote(raw_value, line_number=line_number)
    return parsed


def load_env_file(path: Path, *, strict: bool = True) -> dict[str, str]:
    """Load a template without executing it or mutating ``os.environ``."""

    if not path.is_file():
        raise FileNotFoundError(path)
    return parse_env_text(path.read_text(encoding="utf-8"), strict=strict)


class SecretSettings(BaseModel):
    """Adapter-scoped secrets with masked representations and paper-only guards."""

    model_config = ConfigDict(extra="forbid", frozen=True, repr=False)

    environment: str = "paper_testnet"
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    venue_name: str | None = None
    venue_environment: str = "paper_testnet"
    venue_base_url: str | None = None
    venue_ws_url: str | None = None
    venue_account_id: str | None = None
    venue_subaccount: str | None = None
    venue_api_key: SecretStr | None = None
    venue_api_secret: SecretStr | None = None
    venue_passphrase: SecretStr | None = None
    dashboard_password_hash: SecretStr | None = None
    dashboard_totp_secret: SecretStr | None = None
    artifact_encryption_key: SecretStr | None = None
    artifact_signing_key: SecretStr | None = None
    config_signing_key: SecretStr | None = None
    ledger_encryption_key: SecretStr | None = None
    session_secret: SecretStr | None = None
    webhook_signing_secret: SecretStr | None = None

    @field_validator("environment", "venue_environment")
    @classmethod
    def paper_only_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError(
                "AdvisorAI transition connectors accept only paper/testnet environments"
            )
        return normalized

    @field_validator(
        "llm_provider",
        "llm_model",
        "venue_name",
        "venue_account_id",
        "venue_subaccount",
    )
    @classmethod
    def clean_optional_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("llm_base_url", "venue_base_url")
    @classmethod
    def safe_https_endpoint(cls, value: str | None) -> str | None:
        return cls._safe_endpoint(value, scheme="https")

    @field_validator("venue_ws_url")
    @classmethod
    def safe_wss_endpoint(cls, value: str | None) -> str | None:
        return cls._safe_endpoint(value, scheme="wss")

    @staticmethod
    def _safe_endpoint(value: str | None, *, scheme: str) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        if parsed.scheme != scheme or not parsed.netloc:
            raise ValueError(f"connector endpoints must be absolute {scheme.upper()} URLs")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "connector endpoints cannot contain credentials, query, or fragment data"
            )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    @model_validator(mode="after")
    def reject_live_venue_identity(self) -> SecretSettings:
        if self.venue_environment in {"paper", "testnet", "paper_testnet"}:
            for endpoint in (self.venue_base_url, self.venue_ws_url):
                if endpoint and any(
                    token in endpoint.lower() for token in ("/live", "/prod", "production")
                ):
                    raise ValueError("paper/testnet configuration cannot use a production endpoint")
        return self

    @classmethod
    def from_mapping(cls, values: Mapping[str, str] | None = None) -> SecretSettings:
        source = dict(os.environ if values is None else values)
        selected = {name: value for name, value in source.items() if name in KNOWN_ENV_NAMES}
        aliases = {
            "environment": "ADVISORAI_ENVIRONMENT",
            "llm_provider": "ADVISORAI_LLM_PROVIDER",
            "llm_model": "ADVISORAI_LLM_MODEL",
            "llm_base_url": "ADVISORAI_LLM_BASE_URL",
            "llm_api_key": "ADVISORAI_LLM_API_KEY",
            "venue_name": "ADVISORAI_VENUE_NAME",
            "venue_environment": "ADVISORAI_VENUE_ENVIRONMENT",
            "venue_base_url": "ADVISORAI_VENUE_BASE_URL",
            "venue_ws_url": "ADVISORAI_VENUE_WS_URL",
            "venue_account_id": "ADVISORAI_VENUE_ACCOUNT_ID",
            "venue_subaccount": "ADVISORAI_VENUE_SUBACCOUNT",
            "venue_api_key": "ADVISORAI_VENUE_API_KEY",
            "venue_api_secret": "ADVISORAI_VENUE_API_SECRET",
            "venue_passphrase": "ADVISORAI_VENUE_PASSPHRASE",
            "dashboard_password_hash": "ADVISORAI_DASHBOARD_PASSWORD_HASH",
            "dashboard_totp_secret": "ADVISORAI_DASHBOARD_TOTP_SECRET",
            "artifact_encryption_key": "ADVISORAI_ARTIFACT_ENCRYPTION_KEY",
            "artifact_signing_key": "ADVISORAI_ARTIFACT_SIGNING_KEY",
            "config_signing_key": "ADVISORAI_CONFIG_SIGNING_KEY",
            "ledger_encryption_key": "ADVISORAI_LEDGER_ENCRYPTION_KEY",
            "session_secret": "ADVISORAI_SESSION_SECRET",
            "webhook_signing_secret": "ADVISORAI_WEBHOOK_SIGNING_SECRET",
        }
        payload: dict[str, str] = {}
        for field, name in aliases.items():
            value = selected.get(name)
            if value is not None and value.strip():
                payload[field] = value
        return cls.model_validate(payload)

    @classmethod
    def from_env_file(
        cls, path: Path, *, environ: Mapping[str, str] | None = None
    ) -> SecretSettings:
        values = dict(environ or {})
        values.update(load_env_file(path))
        return cls.from_mapping(values)

    def secret_for(self, name: str) -> str | None:
        """Return one value only to the owning adapter; unknown names fail closed."""

        if name not in SECRET_ENV_NAMES:
            raise KeyError(f"secret is not allowlisted: {name}")
        field = {
            "ADVISORAI_LLM_API_KEY": "llm_api_key",
            "ADVISORAI_VENUE_API_KEY": "venue_api_key",
            "ADVISORAI_VENUE_API_SECRET": "venue_api_secret",
            "ADVISORAI_VENUE_PASSPHRASE": "venue_passphrase",
            "ADVISORAI_DASHBOARD_PASSWORD_HASH": "dashboard_password_hash",
            "ADVISORAI_DASHBOARD_TOTP_SECRET": "dashboard_totp_secret",
            "ADVISORAI_ARTIFACT_ENCRYPTION_KEY": "artifact_encryption_key",
            "ADVISORAI_ARTIFACT_SIGNING_KEY": "artifact_signing_key",
            "ADVISORAI_CONFIG_SIGNING_KEY": "config_signing_key",
            "ADVISORAI_LEDGER_ENCRYPTION_KEY": "ledger_encryption_key",
            "ADVISORAI_SESSION_SECRET": "session_secret",
            "ADVISORAI_WEBHOOK_SIGNING_SECRET": "webhook_signing_secret",
        }.get(name)
        if field is None:
            return None
        value = getattr(self, field)
        return value.get_secret_value() if isinstance(value, SecretStr) else None

    def credential_references(self) -> tuple[str, ...]:
        """Names only, suitable for connector cards and configuration bundles."""

        return tuple(
            name
            for name in (
                "ADVISORAI_LLM_API_KEY",
                "ADVISORAI_VENUE_API_KEY",
                "ADVISORAI_VENUE_API_SECRET",
                "ADVISORAI_VENUE_PASSPHRASE",
            )
            if self.secret_for(name)
        )


_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|passphrase|secret|token|private[_-]?key|nkey)",
    re.I,
)


def redact(value: Any, *, secrets: Mapping[str, str] | None = None) -> Any:
    """Recursively redact mappings/sequences and known secret substrings."""

    secret_values = tuple(item for item in (secrets or {}).values() if item)
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SENSITIVE_KEY.search(str(key))
            else redact(item, secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return type(value)(redact(item, secrets=secrets) for item in value)
    if isinstance(value, bytes):
        return b"[REDACTED]" if any(secret.encode() in value for secret in secret_values) else value
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def redacted_headers(
    headers: Mapping[str, str], *, secrets: Mapping[str, str] | None = None
) -> dict[str, str]:
    return {
        key: "[REDACTED]" if _SENSITIVE_KEY.search(key) else redact(value, secrets=secrets)
        for key, value in headers.items()
    }


__all__ = [
    "KNOWN_ENV_NAMES",
    "SECRET_ENV_NAMES",
    "SecretSettings",
    "load_env_file",
    "parse_env_text",
    "redact",
    "redacted_headers",
]
