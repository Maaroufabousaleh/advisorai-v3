"""Archive automation only after two-provider restore verification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import PurePosixPath

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.ports import ArchiveBackend


@dataclass(frozen=True, slots=True)
class ArchiveVerification:
    key: str
    content_hash: str
    providers: tuple[str, ...]
    upload_verified: bool
    restore_verified: bool
    passed: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("archive verification key cannot be blank")
        if len(self.content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_hash
        ):
            raise ValueError("archive verification content_hash must be a lowercase SHA-256 digest")
        if any(not provider.strip() for provider in self.providers):
            raise ValueError("archive verification provider names cannot be blank")
        normalized_providers = tuple(provider.strip().lower() for provider in self.providers)
        if self.passed and len(normalized_providers) != len(set(normalized_providers)):
            raise ValueError("archive verification providers must be distinct")
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("archive verification reasons cannot be blank")
        if self.passed and self.reasons:
            raise ValueError("a passed archive verification cannot contain reasons")
        if self.passed and (
            not self.upload_verified or not self.restore_verified or len(self.providers) < 2
        ):
            raise ValueError("a passed archive verification requires two verified providers")


class ArchiveAutomation:
    def __init__(
        self, backends: tuple[ArchiveBackend, ...], *, ledgers: SqliteLedgers | None = None
    ) -> None:
        self.backends = backends
        self.ledgers = ledgers

    def archive(self, *, key: str, payload: bytes) -> ArchiveVerification:
        path = PurePosixPath(key)
        if (
            not key
            or "\\" in key
            or path.is_absolute()
            or ".." in path.parts
            or any(not part or part == "." for part in path.parts)
        ):
            raise ValueError("archive key must be a relative path without parent traversal")
        content_hash = sha256(payload).hexdigest()
        reasons: list[str] = []
        uploaded: list[bool] = []
        for backend in self.backends:
            try:
                obj = backend.put(key, payload)
                verified = (
                    obj.key == key and backend.verify(obj) and obj.content_hash == content_hash
                )
                if obj.size_bytes != len(payload):
                    verified = False
                if not obj.encrypted:
                    verified = False
            except Exception:
                verified = False
            uploaded.append(verified)
            if not verified:
                reasons.append(f"upload_verification_failed:{backend.name}")
        restored = []
        if len(self.backends) < 2:
            reasons.append("two_providers_required")
        if len({backend.name for backend in self.backends}) < 2:
            reasons.append("two_distinct_providers_required")
        for backend in self.backends:
            try:
                restored.append(sha256(backend.get(key)).hexdigest() == content_hash)
            except Exception:
                restored.append(False)
        if restored and not all(restored):
            reasons.append("restore_verification_failed")
        verification = ArchiveVerification(
            key=key,
            content_hash=content_hash,
            providers=tuple(backend.name for backend in self.backends),
            upload_verified=bool(uploaded) and all(uploaded),
            restore_verified=bool(restored) and all(restored),
            passed=not reasons,
            reasons=tuple(reasons),
        )
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.INCIDENT,
                    event_type="archive_verification_recorded",
                    idempotency_key=f"archive:{verification.key}:{verification.content_hash}",
                    payload={"verification": asdict(verification)},
                )
            )
        return verification
