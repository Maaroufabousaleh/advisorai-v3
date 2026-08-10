import pytest

from advisorai.archive import (
    RcloneArchiveConfig,
    RcloneCommandError,
    RcloneCryptBackend,
)
from advisorai.config import CredentialResolver


def test_rclone_backend_upload_restore_is_explicit_and_key_safe():
    calls = []
    payloads = {}

    class Result:
        returncode = 0

    def runner(args, **kwargs):
        calls.append(args)
        if args[2].startswith("/tmp/"):
            with open(args[2], "rb") as source:
                payloads[args[3]] = source.read()
        else:
            with open(args[3], "wb") as destination:
                destination.write(payloads[args[2]])
        return Result()

    backend = RcloneCryptBackend("remote:archive", runner=runner)
    backend.put("bundle/state.bin", b"state")
    assert backend.get("bundle/state.bin") == b"state"
    assert len(calls) == 2

    try:
        backend.put("../escape", b"bad")
    except ValueError as exc:
        assert "parent traversal" in str(exc)
    else:
        raise AssertionError("rclone backend accepted a traversal key")


def test_rclone_archive_config_supports_two_scoped_provider_pairs(tmp_path):
    resolver = CredentialResolver.from_mapping(
        {
            "RCLONE_CONFIG": str(tmp_path / "rclone.conf"),
            "RCLONE_CONFIG_PASS": "config-secret",
            "RCLONE_REMOTE_A": "raw-a:",
            "RCLONE_CRYPT_REMOTE_A": "crypt-a:",
            "RCLONE_REMOTE_B": "raw-b:",
            "RCLONE_CRYPT_REMOTE_B": "crypt-b:",
            "OPENAI_API_KEY": "must-not-cross-boundary",
        }
    )

    config = RcloneArchiveConfig.from_resolver(resolver)

    assert tuple(provider.name for provider in config.providers) == (
        "provider_a",
        "provider_b",
    )
    assert config.provider("provider_a").raw_remote == "raw-a:"
    assert config.provider("provider_b").crypt_remote == "crypt-b:"
    assert config.credential_references == ("RCLONE_CONFIG", "RCLONE_CONFIG_PASS")
    assert config.process_environment["RCLONE_CONFIG"] == str(tmp_path / "rclone.conf")
    assert "OPENAI_API_KEY" not in config.process_environment


def test_rclone_archive_config_keeps_singular_contract_backward_compatible(tmp_path):
    config = RcloneArchiveConfig.from_resolver(
        CredentialResolver.from_mapping(
            {
                "RCLONE_CONFIG": str(tmp_path / "rclone.conf"),
                "RCLONE_CONFIG_PASS": "config-secret",
                "RCLONE_REMOTE": "raw:",
                "RCLONE_CRYPT_REMOTE": "crypt:",
            }
        )
    )

    assert len(config.providers) == 1
    assert config.providers[0].name == "default"


@pytest.mark.parametrize(
    "values, message",
    [
        (
            {
                "RCLONE_CONFIG": "/tmp/rclone.conf",
                "RCLONE_CONFIG_PASS": "config-secret",
                "RCLONE_REMOTE_A": "raw-a:",
                "RCLONE_CRYPT_REMOTE_A": "crypt-a:",
            },
            "both rclone provider A and B",
        ),
        (
            {
                "RCLONE_CONFIG": "/tmp/rclone.conf",
                "RCLONE_CONFIG_PASS": "config-secret",
                "RCLONE_REMOTE": "raw:",
            },
            "must be set together",
        ),
        (
            {
                "RCLONE_CONFIG": "relative.conf",
                "RCLONE_CONFIG_PASS": "config-secret",
                "RCLONE_REMOTE": "raw:",
                "RCLONE_CRYPT_REMOTE": "crypt:",
            },
            "absolute path",
        ),
        (
            {
                "RCLONE_CONFIG": "/tmp/rclone.conf",
                "RCLONE_CONFIG_PASS": "config-secret",
                "RCLONE_REMOTE_A": "same:",
                "RCLONE_CRYPT_REMOTE_A": "crypt-a:",
                "RCLONE_REMOTE_B": "same:",
                "RCLONE_CRYPT_REMOTE_B": "crypt-b:",
            },
            "independent",
        ),
    ],
)
def test_rclone_archive_config_rejects_ambiguous_or_unsafe_configuration(values, message):
    with pytest.raises(ValueError, match=message):
        RcloneArchiveConfig.from_resolver(CredentialResolver.from_mapping(values))


def test_rclone_command_failure_does_not_include_provider_output():
    class Result:
        returncode = 17
        stderr = b"secret-token-and-provider-account-id"

    def runner(args, **kwargs):
        return Result()

    backend = RcloneCryptBackend("crypt:", runner=runner)
    with pytest.raises(RcloneCommandError) as raised:
        backend.get("missing.bin")

    assert "secret-token" not in str(raised.value)
    assert raised.value.classification == "provider_command_failed"
