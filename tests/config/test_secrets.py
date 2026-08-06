from pathlib import Path

import pytest
from pydantic import SecretStr

from advisorai.config import SecretSettings, parse_env_text, redact, redacted_headers


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
