"""Verified owner-database backups and isolated state restoration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.learning import LearningProblem, PaperDecisionRecord, PaperOutcome, ReplayResult
from advisorai.ledger import LedgerEvent, LedgerNamespace
from advisorai.memory.scorecards import Scorecard

from .records import (
    DataGapRecord,
    DataGapResolutionRecord,
    PITSnapshotVerification,
    RecoveryRecord,
    RuntimeLeaseRecord,
    SessionCheckpoint,
    SessionEndRecord,
    SessionStartRecord,
    SourceWatermarkRecord,
)

_FORMAT = "advisorai-owner-state-backup-v1"
_LEDGERS_NAME = "ledgers.sqlite3"
_SCORECARDS_NAME = "scorecards.sqlite3"
_DATABASE_NAMES = (_LEDGERS_NAME, _SCORECARDS_NAME)
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_DATABASE_BYTES = 10 * 1024**3


class BackupFileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1, le=_MAX_DATABASE_BYTES)


class OwnerStateBackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["advisorai-owner-state-backup-v1"] = _FORMAT
    backup_id: UUID
    created_at: datetime
    session_id: UUID | None = None
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, BackupFileRecord]
    ledger_event_counts: dict[str, int]
    scorecard_count: int = Field(ge=0)

    @field_validator("created_at")
    @classmethod
    def created_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("owner backup timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_exact_files_and_namespaces(self) -> OwnerStateBackupManifest:
        if set(self.files) != set(_DATABASE_NAMES):
            raise ValueError("owner backup must contain exactly the ledger and scorecard databases")
        if set(self.ledger_event_counts) != {item.value for item in LedgerNamespace}:
            raise ValueError("owner backup must count every ledger namespace")
        if any(count < 0 for count in self.ledger_event_counts.values()):
            raise ValueError("owner backup ledger counts cannot be negative")
        return self


class OwnerStateRestoreReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["advisorai-owner-state-restore-v1"] = "advisorai-owner-state-restore-v1"
    backup_id: UUID
    restored_at: datetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("restored_at")
    @classmethod
    def restored_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("owner restore timestamp must include a timezone")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class OwnerStateBackup:
    path: Path
    manifest: OwnerStateBackupManifest


class OwnerStateBackupStore:
    """Copy and verify both SQLite databases without replacing active state.

    The caller must hold the owner lock while creating a backup. Restore never
    overwrites an existing state root: it verifies into a private staging
    directory, then publishes the complete directory at a new destination.
    This leaves the source backup and any prior owner state available for
    rollback.
    """

    def __init__(self, root: Path) -> None:
        supplied_root = root.expanduser()
        if supplied_root.is_symlink():
            raise ValueError("owner backup root cannot be a symlink")
        supplied_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = supplied_root.resolve()
        if not self.root.is_dir():
            raise ValueError("owner backup root must be a real directory")
        self.root.chmod(0o700)

    def create(
        self,
        *,
        ledgers_path: Path,
        scorecards_path: Path,
        code_hash: str,
        config_hash: str,
        session_id: UUID | None = None,
    ) -> OwnerStateBackup:
        supplied_sources = {
            _LEDGERS_NAME: ledgers_path.expanduser(),
            _SCORECARDS_NAME: scorecards_path.expanduser(),
        }
        sources: dict[str, Path] = {}
        for name, supplied_source in supplied_sources.items():
            if supplied_source.is_symlink() or not supplied_source.is_file():
                raise ValueError("owner backup requires existing regular database files")
            source = supplied_source.resolve()
            if source.stat().st_size > _MAX_DATABASE_BYTES:
                raise ValueError("owner database exceeds the backup size limit")
            sources[name] = source

        staging = Path(tempfile.mkdtemp(prefix=".owner-backup-", dir=self.root))
        staging.chmod(0o700)
        backup_id = uuid4()
        destination = self.root / str(backup_id)
        published = False
        try:
            for name, source in sources.items():
                target = staging / name
                self._sqlite_backup(source, target)
                target.chmod(0o600)
                self._fsync_file(target)

            ledger_counts = self._validate_ledgers(staging / _LEDGERS_NAME)
            scorecard_count = self._validate_scorecards(staging / _SCORECARDS_NAME)
            files = {
                name: BackupFileRecord(
                    sha256=self._sha256(staging / name),
                    size_bytes=(staging / name).stat().st_size,
                )
                for name in _DATABASE_NAMES
            }
            manifest = OwnerStateBackupManifest(
                backup_id=backup_id,
                created_at=datetime.now(UTC),
                session_id=session_id,
                code_hash=code_hash,
                config_hash=config_hash,
                files=files,
                ledger_event_counts=ledger_counts,
                scorecard_count=scorecard_count,
            )
            self._write_private_json(staging / "manifest.json", manifest.model_dump(mode="json"))
            self._fsync_dir(staging)
            staging.rename(destination)
            published = True
            self._fsync_dir(self.root)
            verified = self.verify(destination)
            return OwnerStateBackup(path=destination, manifest=verified)
        except BaseException:
            shutil.rmtree(destination if published else staging, ignore_errors=True)
            raise

    @classmethod
    def verify(cls, backup_path: Path) -> OwnerStateBackupManifest:
        supplied_root = backup_path.expanduser()
        if supplied_root.is_symlink() or not supplied_root.is_dir():
            raise ValueError("owner backup must be a real directory")
        root = supplied_root.resolve()
        manifest_path = root / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("owner backup manifest is missing or unsafe")
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("owner backup manifest exceeds its size limit")
        try:
            raw_manifest = cls._read_regular_file(manifest_path, _MAX_MANIFEST_BYTES)
            manifest = OwnerStateBackupManifest.model_validate_json(raw_manifest)
        except Exception as exc:
            raise ValueError("owner backup manifest is invalid") from exc
        if manifest.backup_id != UUID(root.name):
            raise ValueError("owner backup directory does not match its manifest identity")
        cls._verify_payload(root, manifest)
        return manifest

    @classmethod
    def restore(cls, backup_path: Path, destination: Path) -> OwnerStateRestoreReceipt:
        supplied_source = backup_path.expanduser()
        supplied_destination = destination.expanduser()
        if supplied_source.is_symlink():
            raise ValueError("owner backup source cannot be a symlink")
        if supplied_destination.is_symlink():
            raise FileExistsError("restore destination cannot be a symlink")
        source = supplied_source.resolve()
        destination = supplied_destination.resolve()
        manifest = cls.verify(source)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("restore destination must be a new isolated state directory")
        supplied_parent = supplied_destination.parent
        if supplied_parent.is_symlink():
            raise ValueError("restore parent cannot be a symlink")
        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("restore parent must be a real directory")
        staging = Path(tempfile.mkdtemp(prefix=".owner-restore-", dir=parent))
        staging.chmod(0o700)
        try:
            for name in _DATABASE_NAMES:
                cls._copy_regular_file(
                    source / name, staging / name, maximum_bytes=_MAX_DATABASE_BYTES
                )
            cls._copy_regular_file(
                source / "manifest.json",
                staging / "manifest.json",
                maximum_bytes=_MAX_MANIFEST_BYTES,
            )
            staged_manifest = OwnerStateBackupManifest.model_validate_json(
                cls._read_regular_file(staging / "manifest.json", _MAX_MANIFEST_BYTES)
            )
            if staged_manifest != manifest:
                raise ValueError("owner backup manifest changed during restore")
            cls._verify_payload(staging, manifest)
            manifest_sha256 = cls._sha256(source / "manifest.json")
            receipt = OwnerStateRestoreReceipt(
                backup_id=manifest.backup_id,
                restored_at=datetime.now(UTC),
                manifest_sha256=manifest_sha256,
                code_hash=manifest.code_hash,
                config_hash=manifest.config_hash,
            )
            cls._write_private_json(
                staging / "restore-receipt.json", receipt.model_dump(mode="json")
            )
            cls._fsync_dir(staging)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(
                    "restore destination appeared during verification; no state was replaced"
                )
            staging.rename(destination)
            cls._fsync_dir(parent)
            return receipt
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @classmethod
    def _verify_payload(cls, root: Path, manifest: OwnerStateBackupManifest) -> None:
        expected_names = {*_DATABASE_NAMES, "manifest.json"}
        actual_names = {item.name for item in root.iterdir()}
        if "restore-receipt.json" in actual_names:
            expected_names.add("restore-receipt.json")
        if actual_names != expected_names:
            raise ValueError("owner backup contains missing or unrecognized files")
        for name, record in manifest.files.items():
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("owner backup database is missing or unsafe")
            if path.stat().st_size != record.size_bytes or cls._sha256(path) != record.sha256:
                raise ValueError(f"owner backup database verification failed: {name}")
        ledger_counts = cls._validate_ledgers(root / _LEDGERS_NAME)
        if ledger_counts != manifest.ledger_event_counts:
            raise ValueError("owner backup ledger counts differ from its manifest")
        scorecard_count = cls._validate_scorecards(root / _SCORECARDS_NAME)
        if scorecard_count != manifest.scorecard_count:
            raise ValueError("owner backup scorecard count differs from its manifest")

    @staticmethod
    def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
        source = sqlite3.connect(
            f"file:{quote(source_path.as_posix(), safe='/')}?mode=ro", uri=True
        )
        target = sqlite3.connect(destination_path)
        try:
            source.backup(target, pages=256, sleep=0.01)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
        finally:
            target.close()
            source.close()

    @classmethod
    def _validate_ledgers(cls, path: Path) -> dict[str, int]:
        connection = cls._readonly_connection(path)
        counts: dict[str, int] = {}
        try:
            cls._integrity_check(connection)
            for namespace in LedgerNamespace:
                rows = connection.execute(
                    f"SELECT event_id, event_type, idempotency_key, occurred_at, payload_json "
                    f"FROM {namespace.value}_events ORDER BY rowid"
                ).fetchall()
                counts[namespace.value] = len(rows)
                for event_id, event_type, idempotency_key, occurred_at, payload_json in rows:
                    event = LedgerEvent.model_validate(
                        {
                            "event_id": event_id,
                            "namespace": namespace.value,
                            "event_type": event_type,
                            "idempotency_key": idempotency_key,
                            "occurred_at": occurred_at,
                            "payload": json.loads(payload_json),
                        }
                    )
                    cls._validate_known_event(event)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("owner ledger database failed typed integrity validation") from exc
        finally:
            connection.close()
        return counts

    @classmethod
    def _validate_known_event(cls, event: LedgerEvent) -> None:
        payload = event.payload
        if event.namespace is LedgerNamespace.MISSION:
            models = {
                "paper_decision_chain": ("decision", PaperDecisionRecord),
                "paper_learning_problem": ("problem", LearningProblem),
                "paper_replay_recorded": ("replay", ReplayResult),
                "session_started": ("start", SessionStartRecord),
                "session_ended": ("end", SessionEndRecord),
                "session_checkpoint": ("checkpoint", SessionCheckpoint),
                "recovery_recorded": ("recovery", RecoveryRecord),
                "data_gap_recorded": ("gap", DataGapRecord),
                "data_gap_resolved": ("resolution", DataGapResolutionRecord),
                "source_watermark_recorded": ("watermark", SourceWatermarkRecord),
                "pit_snapshot_verified": ("verification", PITSnapshotVerification),
                "lease_acquired": ("lease", RuntimeLeaseRecord),
                "lease_released": ("lease", RuntimeLeaseRecord),
            }
            model_entry = models.get(event.event_type)
            if model_entry is not None:
                key, model = model_entry
                raw = payload.get(key)
                if not isinstance(raw, dict):
                    raise ValueError(f"owner backup has an invalid {event.event_type} payload")
                model.model_validate(raw)
            if event.event_type == "paper_replay_recorded":
                problem = payload.get("problem")
                if not isinstance(problem, dict):
                    raise ValueError("owner backup has an invalid paper replay problem payload")
                LearningProblem.model_validate(problem)
        if (
            event.namespace is LedgerNamespace.MODEL
            and event.event_type == "paper_scorecard_recorded"
        ):
            scorecard = payload.get("scorecard")
            outcome = payload.get("outcome")
            if not isinstance(scorecard, dict) or not isinstance(outcome, dict):
                raise ValueError("owner backup has an invalid paper scorecard payload")
            Scorecard.model_validate(scorecard)
            PaperOutcome.model_validate(outcome)

    @classmethod
    def _validate_scorecards(cls, path: Path) -> int:
        connection = cls._readonly_connection(path)
        try:
            cls._integrity_check(connection)
            rows = connection.execute(
                "SELECT payload_json FROM scorecards ORDER BY rowid"
            ).fetchall()
            for (payload,) in rows:
                Scorecard.model_validate_json(payload)
            return len(rows)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("owner scorecard database failed typed integrity validation") from exc
        finally:
            connection.close()

    @staticmethod
    def _readonly_connection(path: Path) -> sqlite3.Connection:
        if path.is_symlink() or not path.is_file():
            raise ValueError("owner database is missing or unsafe")
        connection = sqlite3.connect(f"file:{quote(path.as_posix(), safe='/')}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _integrity_check(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise ValueError("owner database failed SQLite integrity_check")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("owner backup file must be regular")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        finally:
            os.close(descriptor)
        return digest.hexdigest()

    @staticmethod
    def _read_regular_file(path: Path, maximum_bytes: int) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > maximum_bytes:
                raise ValueError("owner backup metadata file is not regular or exceeds its limit")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read(maximum_bytes + 1)
        finally:
            os.close(descriptor)

    @staticmethod
    def _copy_regular_file(source: Path, destination: Path, *, maximum_bytes: int) -> None:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        destination_descriptor: int | None = None
        try:
            source_stat = os.fstat(source_descriptor)
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size > maximum_bytes:
                raise ValueError("owner restore input must be a regular file")
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(source_descriptor, "rb", closefd=False) as source_handle:
                with os.fdopen(destination_descriptor, "wb", closefd=False) as destination_handle:
                    copied = 0
                    while block := source_handle.read(min(1024 * 1024, maximum_bytes - copied + 1)):
                        if copied + len(block) > maximum_bytes:
                            raise ValueError("owner restore input exceeds its size limit")
                        destination_handle.write(block)
                        copied += len(block)
                    if copied != source_stat.st_size:
                        raise ValueError("owner restore input changed while being copied")
                    destination_handle.flush()
                    os.fsync(destination_descriptor)
        finally:
            os.close(source_descriptor)
            if destination_descriptor is not None:
                os.close(destination_descriptor)

    @staticmethod
    def _write_private_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "BackupFileRecord",
    "OwnerStateBackup",
    "OwnerStateBackupManifest",
    "OwnerStateBackupStore",
    "OwnerStateRestoreReceipt",
]
