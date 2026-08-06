from pathlib import Path

import pytest
from pydantic import SecretStr

from advisorai.config import (
    CREDENTIAL_SCOPES,
    KNOWN_ENV_NAMES,
    CredentialAlias,
    CredentialResolver,
    CredentialScope,
    CredentialScopeError,
    SecretSettings,
    parse_env_text,
    redact,
    redacted_headers,
)


def test_env_parser_never_executes_and_rejects_unknown_names():
    parsed = parse_env_text('export ADVISORAI_ENVIRONMENT="paper_testnet"\n')
    assert parsed == {"ADVISORAI_ENVIRONMENT": "paper_testnet"}
    with pytest.raises(ValueError, match="unknown"):
        parse_env_text("DO_NOT_EXECUTE=$(touch /tmp/nope)\n")


def test_env_parser_rejects_duplicate_or_malformed_values():
    with pytest.raises(ValueError, match="duplicate"):
        parse_env_text("ADVISORAI_ENVIRONMENT=paper\nADVISORAI_ENVIRONMENT=testnet\n")
    with pytest.raises(ValueError, match="invalid secrets.env"):
        parse_env_text('ADVISORAI_ENVIRONMENT="unterminated\n')


def test_settings_are_paper_only_and_endpoints_are_safe():
    settings = SecretSettings.from_mapping(
        {
            "ADVISORAI_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": "https://sandbox.example.test/api",
            "ADVISORAI_VENUE_API_SECRET": "not-for-output",
        }
    )
    assert settings.environment == "testnet"
    assert settings.venue_base_url == "https://sandbox.example.test/api"
    assert settings.venue_api_secret == SecretStr("not-for-output")
    with pytest.raises(ValueError, match="paper/testnet"):
        SecretSettings.from_mapping({"ADVISORAI_ENVIRONMENT": "live"})
    with pytest.raises(ValueError, match="production endpoint"):
        SecretSettings.from_mapping(
            {
                "ADVISORAI_VENUE_BASE_URL": "https://production.example.test/api",
                "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            }
        )


def test_redaction_masks_secret_values_and_sensitive_headers():
    value = {"Authorization": "Bearer abc", "nested": ["abc", {"api_key": "abc"}]}
    redacted = redact(value, secrets={"key": "abc"})
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["nested"][0] == "[REDACTED]"
    assert redacted["nested"][1]["api_key"] == "[REDACTED]"
    assert redacted_headers({"X-API-KEY": "abc", "Accept": "abc"}, secrets={"key": "abc"}) == {
        "X-API-KEY": "[REDACTED]",
        "Accept": "[REDACTED]",
    }


def test_file_parser_does_not_require_a_real_secret_file(tmp_path: Path):
    path = tmp_path / "secrets.env"
    path.write_text("ADVISORAI_ENVIRONMENT=paper_testnet\n", encoding="utf-8")
    assert SecretSettings.from_env_file(path).environment == "paper_testnet"


def test_credential_scopes_are_explicit_and_return_only_the_requested_subset():
    assert all(names <= KNOWN_ENV_NAMES for names in CREDENTIAL_SCOPES.values())
    values = {
        "ADVISORAI_LLM_API_KEY": "direct-secret",
        "OPENAI_API_KEY": "worker-secret",
        "ADVISORAI_VENUE_API_SECRET": "venue-secret",
        "NATS_PASSWORD": "event-secret",
        "AWS_SECRET_ACCESS_KEY": "archive-secret",
    }
    resolver = CredentialResolver.from_mapping(values)

    assert resolver.resolve(CredentialScope.DIRECT_LLM) == {
        "ADVISORAI_LLM_API_KEY": "direct-secret"
    }
    assert resolver.resolve(CredentialScope.LITELLM) == {"OPENAI_API_KEY": "worker-secret"}
    assert resolver.resolve(CredentialScope.PAPER_VENUE) == {
        "ADVISORAI_VENUE_API_SECRET": "venue-secret"
    }
    assert resolver.resolve(CredentialScope.EVENT_BUS) == {"NATS_PASSWORD": "event-secret"}
    assert resolver.resolve(CredentialScope.ARCHIVE_RCLONE) == {
        "AWS_SECRET_ACCESS_KEY": "archive-secret"
    }
    assert "direct-secret" not in repr(resolver)


def test_credential_alias_is_process_local_and_supports_direct_to_litellm_mapping():
    values = {"ADVISORAI_LLM_API_KEY": "direct-secret", "OPENROUTER_API_KEY": ""}
    resolver = CredentialResolver.from_mapping(values)
    alias = CredentialAlias(target="OPENROUTER_API_KEY", source="ADVISORAI_LLM_API_KEY")

    resolved = resolver.resolve_for_process(CredentialScope.LITELLM, aliases=(alias,))

    assert resolved == {"OPENROUTER_API_KEY": "direct-secret"}
    assert values == {"ADVISORAI_LLM_API_KEY": "direct-secret", "OPENROUTER_API_KEY": ""}
    assert resolver.available_names(CredentialScope.LITELLM) == ()


def test_credential_resolver_rejects_unscoped_requests_and_cross_scope_leaks():
    resolver = CredentialResolver.from_mapping({"OPENAI_API_KEY": "worker-secret"})
    with pytest.raises(CredentialScopeError, match="single credential scope"):
        resolver.resolve(None)
    with pytest.raises(CredentialScopeError, match="single credential scope"):
        resolver.resolve({})  # type: ignore[arg-type]
    with pytest.raises(CredentialScopeError, match="unknown credential scope"):
        resolver.resolve("all")
    with pytest.raises(CredentialScopeError, match="not allowlisted"):
        resolver.get(CredentialScope.PAPER_VENUE, "OPENAI_API_KEY")
    with pytest.raises(CredentialScopeError, match="unknown environment"):
        CredentialResolver.from_mapping({"UNSCOPED_SECRET": "do-not-return"})


def test_credential_aliases_require_an_allowed_target_and_detect_conflicts():
    resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_LLM_API_KEY": "direct-secret",
            "OPENROUTER_API_KEY": "different-secret",
        }
    )
    with pytest.raises(CredentialScopeError, match="conflicting"):
        resolver.resolve(
            CredentialScope.LITELLM,
            aliases=(
                CredentialAlias(
                    target="OPENROUTER_API_KEY", source="ADVISORAI_LLM_API_KEY"
                ),
            ),
        )
    with pytest.raises(CredentialScopeError, match="outside"):
        resolver.resolve(
            CredentialScope.DIRECT_LLM,
            aliases=(
                CredentialAlias(target="OPENROUTER_API_KEY", source="ADVISORAI_LLM_API_KEY"),
            ),
        )
    with pytest.raises(CredentialScopeError, match="not allowlisted"):
        resolver.resolve(
            CredentialScope.LITELLM,
            aliases=(CredentialAlias(target="OPENROUTER_API_KEY", source="ADVISORAI_LLM_PROVIDER"),),
        )
