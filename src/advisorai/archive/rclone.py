"""Thin rclone-crypt adapter; remote state never becomes compute truth."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from advisorai.ports import ArchiveObject


class RcloneCryptBackend:
    name = "rclone-crypt"

    def __init__(self, remote: str, *, runner=subprocess.run) -> None:
        if not remote or ":" not in remote:
            raise ValueError("rclone remote must be an explicit configured remote path")
        self.remote = remote.rstrip("/")
        self.runner = runner

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

    def put(self, key: str, payload: bytes) -> ArchiveObject:
        key = self._key(key)
        with tempfile.NamedTemporaryFile(prefix="advisorai-archive-", delete=False) as handle:
            source = Path(handle.name)
            handle.write(payload)
            handle.flush()
        try:
            result = self.runner(
                ["rclone", "copyto", str(source), f"{self.remote}/{key}"],
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                raise RuntimeError("rclone-crypt upload failed")
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
            result = self.runner(
                ["rclone", "copyto", f"{self.remote}/{key}", str(destination)],
                check=False,
                capture_output=True,
            )
            if result.returncode != 0 or not destination.exists():
                raise RuntimeError("rclone-crypt restore failed")
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
