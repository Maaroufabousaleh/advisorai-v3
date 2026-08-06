"""Small, dependency-light security primitives for the operator console.

The dashboard is intentionally an untrusted client.  These helpers keep the
authentication boundary on the API side while leaving the trading authority in
the existing deterministic services.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field


class AuthConfiguration(BaseModel):
    """Runtime security settings; production defaults fail closed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    auth_required: bool = True
    session_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    idle_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls) -> AuthConfiguration:
        development = os.getenv("ADVISORAI_DASHBOARD_DEV_MODE", "0") == "1"
        raw_origins = os.getenv("ADVISORAI_DASHBOARD_ALLOWED_ORIGINS", "")
        origins = tuple(item.strip() for item in raw_origins.split(",") if item.strip())
        return cls(
            auth_required=not development,
            session_ttl_seconds=int(os.getenv("ADVISORAI_DASHBOARD_SESSION_TTL", "900")),
            idle_ttl_seconds=int(os.getenv("ADVISORAI_DASHBOARD_IDLE_TTL", "900")),
            allowed_origins=origins,
        )


class PasswordService:
    """Argon2id password hashing with an explicit fail-closed dependency check."""

    def __init__(self) -> None:
        try:
            from argon2 import PasswordHasher
        except ImportError as exc:  # pragma: no cover - exercised in deployments
            raise RuntimeError(
                "argon2-cffi is required for dashboard authentication; install the dashboard extra"
            ) from exc
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        if len(password) < 12:
            raise ValueError("dashboard passwords must contain at least 12 characters")
        return self._hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        try:
            return bool(self._hasher.verify(encoded, password))
        except Exception:
            # Password verification must not disclose whether the encoded value
            # was malformed or merely incorrect.
            return False


class TotpService:
    """RFC 6238-compatible TOTP verification without a second runtime package."""

    @staticmethod
    def new_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    @staticmethod
    def verify(secret: str, code: str, *, at: datetime | None = None) -> bool:
        normalized = "".join(character for character in code if character.isdigit())
        if len(normalized) != 6:
            return False
        try:
            padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
            key = base64.b32decode(padded, casefold=True)
        except (ValueError, base64.binascii.Error):
            return False
        timestamp = (at or datetime.now(UTC)).timestamp()
        counter = int(timestamp // 30)
        expected_codes = (
            TotpService._code(key, counter - 1),
            TotpService._code(key, counter),
            TotpService._code(key, counter + 1),
        )
        return any(hmac.compare_digest(normalized, expected) for expected in expected_codes)

    @staticmethod
    def _code(key: bytes, counter: int) -> str:
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{value % 1_000_000:06d}"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1)
    scopes: tuple[str, ...] = ("dashboard:read", "dashboard:control")
    authenticated_at: datetime


@dataclass(slots=True)
class _Session:
    principal: Principal
    csrf_token: str
    expires_at: datetime
    last_seen_at: datetime


class SessionStore:
    """In-memory sessions for the single-owner local console.

    A deployment that needs multiple replicas should replace this store with a
    durable encrypted session backend; the API contract stays unchanged.
    """

    def __init__(self, config: AuthConfiguration | None = None) -> None:
        self.config = config or AuthConfiguration.from_environment()
        self._sessions: dict[str, _Session] = {}

    def create(self, subject: str, *, now: datetime | None = None) -> tuple[str, str]:
        current = now or datetime.now(UTC)
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        principal = Principal(subject=subject, authenticated_at=current)
        self._sessions[session_id] = _Session(
            principal=principal,
            csrf_token=csrf_token,
            expires_at=current + timedelta(seconds=self.config.session_ttl_seconds),
            last_seen_at=current,
        )
        return session_id, csrf_token

    def get(self, session_id: str | None, *, now: datetime | None = None) -> _Session | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        current = now or datetime.now(UTC)
        if current >= session.expires_at or (
            current - session.last_seen_at
        ).total_seconds() >= self.config.idle_ttl_seconds:
            self._sessions.pop(session_id, None)
            return None
        session.last_seen_at = current
        return session

    def revoke(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def csrf_matches(self, session_id: str | None, token: str | None) -> bool:
        session = self._sessions.get(session_id or "")
        return session is not None and bool(token) and hmac.compare_digest(session.csrf_token, token)


def configured_password_hash() -> str | None:
    """Read the hash without ever accepting a plaintext password from config."""

    value = os.getenv("ADVISORAI_DASHBOARD_PASSWORD_HASH")
    return value.strip() if value and value.strip() else None


def configured_totp_secret() -> str | None:
    value = os.getenv("ADVISORAI_DASHBOARD_TOTP_SECRET")
    return value.strip() if value and value.strip() else None
