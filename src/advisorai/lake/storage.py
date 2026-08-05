"""Manifest-managed immutable Parquet storage for Bronze, Silver, and Gold.

Parquet files contain payloads plus retrieval-critical columns; the paired
manifest holds the complete, content-addressed provenance. Existing artifacts are
verified and returned, never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pyarrow as pa
import pyarrow.parquet as pq

from advisorai.contracts import ArtifactReference, ArtifactTier, PointInTimeObservation


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _json_safe(value: object) -> object:
    if isinstance(value, bytes):
        return {"__binary_hex__": value.hex()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_artifact_id(content_hash: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"advisorai-v3/artifact/{content_hash}")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Immutable metadata for one content-addressed Parquet artifact."""

    artifact_id: UUID
    tier: ArtifactTier
    dataset: str
    content_hash: str
    uri: str
    manifest_uri: str
    first_available_at: datetime
    row_count: int
    schema_version: str
    parser_version: str | None

    def __post_init__(self) -> None:
        if not self.dataset or "/" in self.dataset or "\\" in self.dataset:
            raise ValueError("dataset must be a non-empty path segment")
        if len(self.content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_hash
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        if self.first_available_at.tzinfo is None or self.first_available_at.utcoffset() is None:
            raise ValueError("first_available_at must include a timezone")
        if self.row_count < 0 or not self.schema_version.strip():
            raise ValueError("manifest row count and schema version are required")

    def to_reference(self) -> ArtifactReference:
        return ArtifactReference(
            artifact_id=self.artifact_id,
            tier=self.tier,
            uri=self.uri,
            content_hash=self.content_hash,
            dataset=self.dataset,
            first_available_at=self.first_available_at,
            parser_version=self.parser_version,
        )


class DataLake:
    """Small local lake with immutable artifact paths and atomic materialization."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for tier in ArtifactTier:
            (self.root / tier.value).mkdir(parents=True, exist_ok=True)

    def write_bronze(
        self,
        *,
        dataset: str,
        payload: bytes,
        source_family: str,
        origin: str,
        first_available_at: datetime,
        ingested_at: datetime,
        parser_version: str,
    ) -> DatasetManifest:
        """Persist an exact raw payload as one immutable Bronze Parquet row."""

        for name, value in (
            ("first_available_at", first_available_at),
            ("ingested_at", ingested_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone")
        if ingested_at < first_available_at:
            raise ValueError("ingested_at cannot precede first_available_at")
        if not dataset.strip() or not source_family.strip() or not origin.strip():
            raise ValueError("Bronze records require dataset, source family, and origin")
        if not parser_version.strip():
            raise ValueError("Bronze records require a parser version")

        row = {
            "payload": payload,
            "source_family": source_family,
            "origin": origin,
            "first_available_at": first_available_at.astimezone(UTC).isoformat(),
            "ingested_at": ingested_at.astimezone(UTC).isoformat(),
            "parser_version": parser_version,
        }
        return self._write_rows(
            tier=ArtifactTier.BRONZE,
            dataset=dataset,
            rows=(row,),
            first_available_at=first_available_at,
            parser_version=parser_version,
        )

    def write_observations(
        self,
        *,
        tier: ArtifactTier,
        dataset: str,
        observations: tuple[PointInTimeObservation, ...],
        schema_version: str = "v1",
    ) -> DatasetManifest:
        """Persist normalized observations in Silver or frozen data in Gold."""

        if tier not in {ArtifactTier.SILVER, ArtifactTier.GOLD}:
            raise ValueError(
                "write_observations is for Silver or Gold; raw payloads use write_bronze"
            )
        if not observations:
            raise ValueError("an immutable dataset artifact requires at least one observation")
        observation_ids = [observation.artifact_id for observation in observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("immutable dataset artifacts cannot contain duplicate observations")
        rows = tuple(
            {
                "observation_id": str(observation.artifact_id),
                "instrument_id": observation.instrument.canonical_id,
                "event_time": observation.event_time.isoformat()
                if observation.event_time
                else None,
                "first_available_at": observation.first_available_at.isoformat(),
                "origin": observation.origin,
                "source_family": observation.source_family,
                "payload_json": _canonical_json(
                    observation.model_dump(mode="json", round_trip=True)
                ).decode("utf-8"),
            }
            for observation in observations
        )
        return self._write_rows(
            tier=tier,
            dataset=dataset,
            rows=rows,
            first_available_at=min(item.first_available_at for item in observations),
            parser_version=f"normalized-{schema_version}",
            schema_version=schema_version,
        )

    def read_manifest(self, manifest: DatasetManifest) -> DatasetManifest:
        """Verify a manifest and its artifact before it is admitted to a read."""

        root = self.root.resolve()
        manifest_path = (root / manifest.manifest_uri).resolve()
        artifact_path = (root / manifest.uri).resolve()
        if root not in manifest_path.parents or root not in artifact_path.parents:
            raise PermissionError("immutable artifacts must remain inside the local lake root")
        self._verify_existing(artifact_path, manifest_path, manifest)
        with manifest_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        expected = {
            "artifact_id": str(manifest.artifact_id),
            "tier": manifest.tier.value,
            "dataset": manifest.dataset,
            "content_hash": manifest.content_hash,
            "uri": manifest.uri,
            "first_available_at": manifest.first_available_at.isoformat(),
            "row_count": manifest.row_count,
            "schema_version": manifest.schema_version,
            "parser_version": manifest.parser_version,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise RuntimeError("immutable manifest metadata changed")
        return manifest

    def read_rows(self, manifest: DatasetManifest) -> tuple[dict[str, object], ...]:
        """Read only after verifying the immutable manifest and content hash."""

        self.read_manifest(manifest)
        table = pq.read_table(self.root / manifest.uri, partitioning=None)
        rows = tuple(table.to_pylist())
        if len(rows) != manifest.row_count:
            raise RuntimeError("immutable artifact row count changed")
        if _sha256(_canonical_json(rows)) != manifest.content_hash:
            raise RuntimeError("immutable artifact content hash mismatch")
        return rows

    def _write_rows(
        self,
        *,
        tier: ArtifactTier,
        dataset: str,
        rows: tuple[dict[str, object], ...],
        first_available_at: datetime,
        parser_version: str | None,
        schema_version: str = "v1",
    ) -> DatasetManifest:
        if first_available_at.tzinfo is None or first_available_at.utcoffset() is None:
            raise ValueError("first_available_at must include a timezone")
        if not dataset or "/" in dataset or "\\" in dataset:
            raise ValueError("dataset must be a non-empty path segment")

        canonical_rows = _canonical_json(rows)
        content_hash = _sha256(canonical_rows)
        partition = first_available_at.astimezone(UTC).date().isoformat()
        relative_uri = Path(tier.value) / dataset / f"date={partition}" / f"{content_hash}.parquet"
        relative_manifest_uri = relative_uri.with_suffix(".manifest.json")
        artifact_path = self.root / relative_uri
        manifest_path = self.root / relative_manifest_uri
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        manifest = DatasetManifest(
            artifact_id=_stable_artifact_id(content_hash),
            tier=tier,
            dataset=dataset,
            content_hash=content_hash,
            uri=relative_uri.as_posix(),
            manifest_uri=relative_manifest_uri.as_posix(),
            first_available_at=first_available_at.astimezone(UTC),
            row_count=len(rows),
            schema_version=schema_version,
            parser_version=parser_version,
        )
        if artifact_path.exists() or manifest_path.exists():
            self._verify_existing(artifact_path, manifest_path, manifest)
            return manifest

        table = pa.Table.from_pylist(list(rows))
        self._write_parquet_exclusive(table, artifact_path)
        manifest_payload = {
            "artifact_id": str(manifest.artifact_id),
            "tier": manifest.tier.value,
            "dataset": manifest.dataset,
            "content_hash": manifest.content_hash,
            "uri": manifest.uri,
            "first_available_at": manifest.first_available_at.isoformat(),
            "row_count": manifest.row_count,
            "schema_version": manifest.schema_version,
            "parser_version": manifest.parser_version,
        }
        self._write_bytes_exclusive(_canonical_json(manifest_payload), manifest_path)
        return manifest

    @staticmethod
    def _write_parquet_exclusive(table: pa.Table, destination: Path) -> None:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".parquet", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            pq.write_table(table, temporary, compression="zstd")
            os.link(temporary, destination)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_bytes_exclusive(payload: bytes, destination: Path) -> None:
        try:
            with destination.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return

    @staticmethod
    def _verify_existing(
        artifact_path: Path, manifest_path: Path, expected: DatasetManifest
    ) -> None:
        if not artifact_path.exists() or not manifest_path.exists():
            raise RuntimeError("partial immutable artifact exists; investigate before retrying")
        with manifest_path.open("rb") as handle:
            actual = json.load(handle)
        expected_metadata = {
            "artifact_id": str(expected.artifact_id),
            "tier": expected.tier.value,
            "dataset": expected.dataset,
            "content_hash": expected.content_hash,
            "uri": expected.uri,
            "first_available_at": expected.first_available_at.isoformat(),
            "row_count": expected.row_count,
            "schema_version": expected.schema_version,
            "parser_version": expected.parser_version,
        }
        if any(actual.get(key) != value for key, value in expected_metadata.items()):
            raise RuntimeError(
                "existing artifact path does not match the requested immutable artifact"
            )
        table = pq.read_table(artifact_path, partitioning=None)
        rows = tuple(table.to_pylist())
        if (
            len(rows) != expected.row_count
            or _sha256(_canonical_json(rows)) != expected.content_hash
        ):
            raise RuntimeError("immutable artifact content hash mismatch")
