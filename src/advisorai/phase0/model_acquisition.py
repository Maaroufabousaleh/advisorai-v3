"""Secure, exact-revision acquisition for Phase-0 model qualification.

Network access is confined to this staging boundary. Qualification workers
consume only the clean cache produced here and never receive registry
credentials.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, model_validator

from advisorai.phase0.runtime_qualification import (
    ArtifactPin,
    CandidateSpec,
    CheckpointPin,
    RepositoryPin,
    sha256_file,
)


class ModelAcquisitionError(RuntimeError):
    """A staged model acquisition failed without admitting partial output."""


class GatedTermsAcceptanceRequired(ModelAcquisitionError):
    """The upstream requires a human to accept access terms."""


class AcquiredRepository(BaseModel):
    """Observed immutable closure for one acquired repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str
    revision: str
    cache_subdir: str
    runtime_artifacts: tuple[ArtifactPin, ...]
    provenance_artifacts: tuple[ArtifactPin, ...]


class ModelAcquisitionResult(BaseModel):
    """Sanitized acquisition evidence; it never contains credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.model-acquisition.v1"
    candidate: str
    acquired_at: datetime
    cache_path: str
    anonymous: bool
    repositories: tuple[AcquiredRepository, ...]
    manifest_hash: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> ModelAcquisitionResult:
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise ValueError("acquisition timestamp must include a timezone")
        if self.manifest_hash is not None and (
            len(self.manifest_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.manifest_hash)
        ):
            raise ValueError("manifest hash must be SHA-256")
        return self

    def with_manifest_hash(self) -> ModelAcquisitionResult:
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.model_copy(update={"manifest_hash": digest})


DownloadFile = Callable[[str, Path, Mapping[str, str]], None]


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Never forward registry authorization to a different redirect host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


def _default_download(url: str, destination: Path, headers: Mapping[str, str]) -> None:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        opener = build_opener(_SafeRedirectHandler())
        with opener.open(request, timeout=120) as response, destination.open("xb") as output:  # noqa: S310
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise GatedTermsAcceptanceRequired(
                "upstream access is gated or requires user acceptance"
            ) from exc
        raise ModelAcquisitionError(f"upstream artifact request failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise ModelAcquisitionError("upstream artifact request failed") from exc


def _artifact_url(repository: RepositoryPin, relative_path: str) -> str:
    repository_id = quote(repository.repository_id, safe="/")
    revision = quote(repository.revision, safe="")
    artifact_path = "/".join(quote(part, safe="") for part in Path(relative_path).parts)
    return f"https://huggingface.co/{repository_id}/resolve/{revision}/{artifact_path}?download=true"


def _assert_external_root(path: Path, repository_root: Path) -> Path:
    if path.is_symlink():
        raise ModelAcquisitionError("acquisition root must not be a symlink")
    resolved = path.expanduser().resolve(strict=False)
    root = repository_root.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise ModelAcquisitionError("acquisition roots must remain outside the repository")


def _download_repository(
    repository: RepositoryPin,
    *,
    staging_directory: Path,
    token: str | None,
    download_file: DownloadFile,
) -> AcquiredRepository:
    repository_directory = staging_directory / repository.cache_subdir
    repository_directory.mkdir(parents=True, exist_ok=False)
    headers = {"User-Agent": "AdvisorAI-Phase0-Model-Acquisition/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    runtime_paths = {item.relative_path for item in repository.runtime_artifacts or repository.artifacts}
    observed_runtime: list[ArtifactPin] = []
    observed_provenance: list[ArtifactPin] = []
    for artifact in repository.all_artifacts:
        destination = repository_directory / artifact.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ModelAcquisitionError("staging artifact path must not be a symlink")
        download_file(_artifact_url(repository, artifact.relative_path), destination, headers)
        if not destination.is_file() or destination.is_symlink():
            raise ModelAcquisitionError("download did not produce a regular artifact")
        observed = ArtifactPin(
            relative_path=artifact.relative_path,
            sha256=sha256_file(destination),
            size_bytes=destination.stat().st_size,
        )
        if artifact.relative_path in runtime_paths:
            observed_runtime.append(observed)
        else:
            observed_provenance.append(observed)
    observed_paths = {
        str(path.relative_to(repository_directory))
        for path in repository_directory.rglob("*")
        if path.is_file()
    }
    expected_paths = {artifact.relative_path for artifact in repository.all_artifacts}
    if observed_paths != expected_paths:
        raise ModelAcquisitionError("staged repository closure differs from reviewed artifact set")
    return AcquiredRepository(
        repository_id=repository.repository_id,
        revision=repository.revision,
        cache_subdir=repository.cache_subdir,
        runtime_artifacts=tuple(observed_runtime),
        provenance_artifacts=tuple(observed_provenance),
    )


def _identical_cache(staged: Path, existing: Path) -> bool:
    staged_files = {
        str(path.relative_to(staged)): sha256_file(path)
        for path in staged.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    existing_files = {
        str(path.relative_to(existing)): sha256_file(path)
        for path in existing.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    return staged_files == existing_files and all(
        not path.is_symlink() for path in existing.rglob("*")
    )


def acquire_candidate_artifacts(
    candidate: CandidateSpec,
    *,
    staging_root: Path,
    cache_root: Path,
    repository_root: Path,
    token: str | None = None,
    download_file: DownloadFile = _default_download,
) -> tuple[CheckpointPin, ModelAcquisitionResult]:
    """Acquire exactly one candidate's reviewed closure at immutable revisions."""

    checkpoint = candidate.external_checkpoint
    if checkpoint is None:
        raise ModelAcquisitionError("built-in candidates have no external artifacts")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate.name) is None:
        raise ModelAcquisitionError("candidate name is not safe for an external cache path")
    staging_root = _assert_external_root(staging_root, repository_root)
    cache_root = _assert_external_root(cache_root, repository_root)
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    revision_directory = cache_root / candidate.name / checkpoint.revision
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f"{candidate.name}-{checkpoint.revision[:12]}-",
            dir=staging_root,
        )
    )
    try:
        repositories = [
            _download_repository(
                checkpoint.repository,
                staging_directory=temporary,
                token=token,
                download_file=download_file,
            )
        ]
        if checkpoint.tokenizer is not None:
            repositories.append(
                _download_repository(
                    checkpoint.tokenizer,
                    staging_directory=temporary,
                    token=token,
                    download_file=download_file,
                )
            )
        revision_directory.parent.mkdir(parents=True, exist_ok=True)
        if revision_directory.exists():
            if not revision_directory.is_dir() or revision_directory.is_symlink():
                raise ModelAcquisitionError("immutable cache destination is not a regular directory")
            if not _identical_cache(temporary, revision_directory):
                raise ModelAcquisitionError("immutable cache already exists with different content")
        else:
            os.replace(temporary, revision_directory)
        observed_by_subdir = {item.cache_subdir: item for item in repositories}

        def pinned_repository(repository: RepositoryPin) -> RepositoryPin:
            observed = observed_by_subdir[repository.cache_subdir]
            return repository.model_copy(
                update={
                    "artifacts": (),
                    "runtime_artifacts": observed.runtime_artifacts,
                    "provenance_artifacts": observed.provenance_artifacts,
                }
            )

        pinned = checkpoint.model_copy(
            update={
                "repository": pinned_repository(checkpoint.repository),
                "tokenizer": (
                    pinned_repository(checkpoint.tokenizer)
                    if checkpoint.tokenizer is not None
                    else None
                ),
                "cache_path": str(revision_directory),
            }
        )
        result = ModelAcquisitionResult(
            candidate=candidate.name,
            acquired_at=datetime.now(UTC),
            cache_path=str(revision_directory),
            anonymous=not bool(token),
            repositories=tuple(repositories),
        ).with_manifest_hash()
        return CheckpointPin.model_validate(pinned.model_dump()), result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def write_acquisition_manifest(result: ModelAcquisitionResult, path: Path) -> Path:
    """Write immutable sanitized evidence for an acquired closure."""

    payload = (json.dumps(result.model_dump(mode="json"), sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"immutable acquisition evidence differs: {path}")
        return path
    path.write_bytes(payload)
    return path


def checkpoint_pin_payload(checkpoint: CheckpointPin) -> dict[str, Any]:
    """Return a JSON-safe machine admission payload without credentials."""

    return checkpoint.model_dump(mode="json")
