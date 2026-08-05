"""Content-addressed immutable configuration bundles with auditable activation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ConfigBundle:
    content_hash: str
    uri: str
    content: dict[str, object]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise ValueError("configuration hash must be a lowercase SHA-256 digest")
        if not self.uri.strip():
            raise ValueError("configuration bundle URI cannot be blank")
        if not isinstance(self.content, dict):
            raise TypeError("configuration bundle content must be an object")
        actual_hash = hashlib.sha256(_canonical_json(self.content)).hexdigest()
        if actual_hash != self.content_hash:
            raise ValueError("configuration bundle hash does not match its content")


class ConfigBundleStore:
    """Configuration content is immutable; the active pointer is an audited projection."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bundle_dir = root / "bundles"
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        self.activation_log = root / "activations.jsonl"
        self.active_pointer = root / "active.json"

    def create(self, content: dict[str, object]) -> ConfigBundle:
        frozen_content = deepcopy(content)
        payload = _canonical_json(frozen_content)
        content_hash = hashlib.sha256(payload).hexdigest()
        path = self.bundle_dir / f"{content_hash}.json"
        try:
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            pass
        return ConfigBundle(
            content_hash=content_hash,
            uri=path.as_posix(),
            content=frozen_content,
        )

    def get(self, content_hash: str) -> ConfigBundle:
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise ValueError("configuration hash must be a lowercase SHA-256 digest")
        path = self.bundle_dir / f"{content_hash}.json"
        with path.open("rb") as handle:
            content = json.load(handle)
        actual_hash = hashlib.sha256(_canonical_json(content)).hexdigest()
        if actual_hash != content_hash:
            raise RuntimeError("configuration bundle hash mismatch")
        return ConfigBundle(content_hash=content_hash, uri=path.as_posix(), content=content)

    def activate(self, content_hash: str, *, actor: str, reason: str) -> ConfigBundle:
        bundle = self.get(content_hash)
        actor = actor.strip()
        reason = reason.strip()
        if not actor or not reason:
            raise ValueError("configuration activation requires actor and reason")
        event = {
            "at": datetime.now(UTC).isoformat(),
            "actor": actor,
            "reason": reason,
            "content_hash": content_hash,
        }
        with self.activation_log.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(event).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary = self.active_pointer.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            handle.write(_canonical_json(event))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.active_pointer)
        return bundle

    def rollback(self, content_hash: str, *, actor: str, reason: str) -> ConfigBundle:
        """Rollback is an explicit activation of a known immutable prior bundle."""

        return self.activate(content_hash, actor=actor, reason=f"rollback: {reason}")

    def active(self) -> ConfigBundle | None:
        if not self.active_pointer.exists():
            return None
        with self.active_pointer.open("rb") as handle:
            content_hash = json.load(handle)["content_hash"]
        return self.get(content_hash)
