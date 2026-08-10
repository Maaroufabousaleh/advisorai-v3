"""Typed rclone-crypt boundary; remote state never becomes compute truth.

The archive connector deliberately receives a process-local environment built
from the ``ARCHIVE_RCLONE`` credential scope.  It never relies on the caller's
ambient environment, and the provider pair is explicit so a two-provider
restore cannot accidentally use the same remote twice.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from advisorai.config.secrets import CredentialResolver, CredentialScope
from advisorai.ports import ArchiveObject


def _remote_path(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a text rclone remote path")
    normalized = value.strip().rstrip("/")
    if (
        not normalized
        or ":" not in normalized
        or any(character in normalized for character in "\r\n\t")
        or normalized.startswith("/")
    ):
        raise ValueError(f"{field_name} must be an explicit configured rclone remote path")
    return normalized


@dataclass(frozen=True, slots=True)
class RcloneProviderConfig:
    """One independently configured raw/crypt rclone provider pair."""

    name: str
    raw_remote: str
    crypt_remote: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("rclone provider name must be text")
        normalized_name = self.name.strip().lower()
        if not normalized_name or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in normalized_name
        ):
            raise ValueError("rclone provider name must be a simple lowercase identity")
        raw_remote = _remote_path(self.raw_remote, field_name="rclone raw remote")
        crypt_remote = _remote_path(self.crypt_remote, field_name="rclone crypt remote")
        if raw_remote == crypt_remote:
            raise ValueError("rclone raw and crypt remotes must be distinct")
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "raw_remote", raw_remote)
        object.__setattr__(self, "crypt_remote", crypt_remote)


class RcloneCommandError(RuntimeError):
    """Sanitized rclone failure without provider output or credential material."""

    def __init__(self, operation: str, classification: str, returncode: int | None = None):
        self.operation = operation
        self.classification = classification
        self.returncode = returncode
        suffix = f" ({returncode})" if returncode is not None else ""
        super().__init__(f"rclone {operation} failed: {classification}{suffix}")


@dataclass(frozen=True, slots=True, repr=False)
class RcloneArchiveConfig:
    """Scoped rclone credentials plus one or more explicit provider pairs.

    ``RCLONE_REMOTE``/``RCLONE_CRYPT_REMOTE`` remain valid for the historical
    one-provider adapter.  A real two-provider qualification uses the
    suffixed ``_A`` and ``_B`` names and requires both complete pairs.
    """

    config_path: Path
    config_password: str
    providers: tuple[RcloneProviderConfig, ...]

    def __post_init__(self) -> None:
        config_path = Path(self.config_path).expanduser()
        if not config_path.is_absolute():
            raise ValueError("RCLONE_CONFIG must be an absolute path")
        if not isinstance(self.config_password, str) or not self.config_password.strip():
            raise ValueError("RCLONE_CONFIG_PASS is required")
        if not self.providers:
            raise ValueError("at least one rclone provider is required")
        names = tuple(provider.name for provider in self.providers)
        if len(names) != len(set(names)):
            raise ValueError("rclone provider identities must be distinct")
        remotes = tuple(
            remote
            for provider in self.providers
            for remote in (provider.raw_remote, provider.crypt_remote)
        )
        if len(remotes) != len(set(remotes)):
            raise ValueError("rclone provider remotes must be independent")
        object.__setattr__(self, "config_path", config_path)

    @classmethod
    def from_resolver(cls, resolver: CredentialResolver) -> RcloneArchiveConfig:
        """Build the archive config from exactly the ``ARCHIVE_RCLONE`` scope."""

        values = resolver.resolve(CredentialScope.ARCHIVE_RCLONE)

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required for rclone archive access")
            return value

        config_path = Path(required("RCLONE_CONFIG"))
        config_password = required("RCLONE_CONFIG_PASS")
        singular = (
            values.get("RCLONE_REMOTE", "").strip(),
            values.get("RCLONE_CRYPT_REMOTE", "").strip(),
        )
        suffixed = {
            "a": (
                values.get("RCLONE_REMOTE_A", "").strip(),
                values.get("RCLONE_CRYPT_REMOTE_A", "").strip(),
            ),
            "b": (
                values.get("RCLONE_REMOTE_B", "").strip(),
                values.get("RCLONE_CRYPT_REMOTE_B", "").strip(),
            ),
        }
        has_suffixed = any(any(pair) for pair in suffixed.values())
        if has_suffixed:
            if any(singular):
                raise ValueError("singular and suffixed rclone provider settings cannot be mixed")
            if any(not value for pair in suffixed.values() for value in pair):
                raise ValueError("both rclone provider A and B remote pairs are required")
            providers = tuple(
                RcloneProviderConfig(
                    name=f"provider_{provider_id}",
                    raw_remote=pair[0],
                    crypt_remote=pair[1],
                )
                for provider_id, pair in suffixed.items()
            )
        else:
            if any(singular) and not all(singular):
                raise ValueError("RCLONE_REMOTE and RCLONE_CRYPT_REMOTE must be set together")
            providers = (
                (
                    RcloneProviderConfig(
                        name="default",
                        raw_remote=singular[0],
                        crypt_remote=singular[1],
                    ),
                )
                if all(singular)
                else ()
            )
        return cls(
            config_path=config_path,
            config_password=config_password,
            providers=providers,
        )

    @property
    def credential_references(self) -> tuple[str, ...]:
        """Credential names only; suitable for sanitized evidence."""

        return ("RCLONE_CONFIG", "RCLONE_CONFIG_PASS")

    @property
    def process_environment(self) -> Mapping[str, str]:
        """Return the minimal process environment needed by rclone."""

        return MappingProxyType(
            {
                "PATH": os.environ.get("PATH", os.defpath),
                "LANG": "C",
                "RCLONE_CONFIG": str(self.config_path),
                "RCLONE_CONFIG_PASS": self.config_password,
            }
        )

    def provider(self, name: str) -> RcloneProviderConfig:
        normalized = name.strip().lower()
        for provider in self.providers:
            if provider.name == normalized:
                return provider
        raise KeyError(f"unknown rclone provider: {normalized}")

    def backend(
        self,
        provider: str | RcloneProviderConfig,
        *,
        runner=subprocess.run,
        timeout_seconds: float | None = None,
    ) -> RcloneCryptBackend:
        selected = self.provider(provider) if isinstance(provider, str) else provider
        if selected not in self.providers:
            raise ValueError("rclone provider is not part of this archive configuration")
        return RcloneCryptBackend(
            selected.crypt_remote,
            provider_name=selected.name,
            runner=runner,
            environment=self.process_environment,
            timeout_seconds=timeout_seconds,
        )


class RcloneCryptBackend:
    name = "rclone-crypt"

    def __init__(
        self,
        remote: str,
        *,
        provider_name: str | None = None,
        runner=subprocess.run,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.remote = _remote_path(remote, field_name="rclone remote")
        if provider_name is not None:
            normalized_name = provider_name.strip().lower()
            if not normalized_name:
                raise ValueError("rclone provider name cannot be blank")
            self.name = normalized_name
        self.runner = runner
        self.environment = MappingProxyType(dict(environment)) if environment is not None else None
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _key(key: str) -> str:
        path = PurePosixPath(key)
        if (
            not key.strip()
            or "\\" in key
            or path.is_absolute()
            or ".." in path.parts
            or any(not part or part == "." for part in path.parts)
        ):
            raise ValueError("archive key must be a safe relative path without parent traversal")
        return key.strip()

    def _run(self, args: list[str], *, operation: str):
        kwargs: dict[str, object] = {"check": False, "capture_output": True}
        if self.environment is not None:
            kwargs["env"] = dict(self.environment)
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        try:
            result = self.runner(args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise RcloneCommandError(operation, "timeout") from exc
        except FileNotFoundError as exc:
            raise RcloneCommandError(operation, "rclone_not_installed") from exc
        except OSError as exc:
            raise RcloneCommandError(operation, "process_error") from exc
        returncode = getattr(result, "returncode", None)
        if returncode != 0:
            raise RcloneCommandError(operation, "provider_command_failed", returncode)
        return result

    def put(self, key: str, payload: bytes) -> ArchiveObject:
        key = self._key(key)
        with tempfile.NamedTemporaryFile(prefix="advisorai-archive-", delete=False) as handle:
            source = Path(handle.name)
            handle.write(payload)
            handle.flush()
        try:
            self._run(
                ["rclone", "copyto", str(source), f"{self.remote}/{key}"],
                operation="upload",
            )
        finally:
            source.unlink(missing_ok=True)
        return ArchiveObject(
            key=key,
            content_hash=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            encrypted=True,
        )

    def get(self, key: str) -> bytes:
        key = self._key(key)
        with tempfile.TemporaryDirectory(prefix="advisorai-restore-") as directory:
            destination = Path(directory) / "payload"
            try:
                self._run(
                    ["rclone", "copyto", f"{self.remote}/{key}", str(destination)],
                    operation="restore",
                )
            except RcloneCommandError:
                raise
            if not destination.exists():
                raise RcloneCommandError("restore", "destination_missing")
            return destination.read_bytes()

    def verify(self, obj: ArchiveObject) -> bool:
        if not obj.encrypted or len(obj.content_hash) != 64:
            return False
        try:
            payload = self.get(obj.key)
        except Exception:
            return False
        return (
            len(payload) == obj.size_bytes
            and hashlib.sha256(payload).hexdigest() == obj.content_hash
        )
