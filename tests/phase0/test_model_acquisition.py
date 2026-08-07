from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request

import pytest

from advisorai.phase0.model_acquisition import (
    ModelAcquisitionError,
    _SafeRedirectHandler,
    acquire_candidate_artifacts,
    write_acquisition_manifest,
)
from advisorai.phase0.runtime_qualification import (
    ArtifactPin,
    CandidateSpec,
    CheckpointPin,
    ModelFamily,
    ModelTask,
    RepositoryPin,
    verify_checkpoint_artifacts,
)


def _candidate(cache: Path) -> CandidateSpec:
    return CandidateSpec(
        name="fixture-model",
        family=ModelFamily.NAIVE,
        task=ModelTask.FORECAST,
        output_schema="forecast[1]",
        external_checkpoint=CheckpointPin(
            model_family=ModelFamily.NAIVE.value,
            repository=RepositoryPin(
                repository_id="fixture/model",
                revision="a" * 40,
                license="unknown",
                runtime_artifacts=(
                    ArtifactPin(relative_path="config.json"),
                    ArtifactPin(relative_path="model.safetensors"),
                ),
                provenance_artifacts=(ArtifactPin(relative_path="README.md"),),
            ),
            cache_path=str(cache),
        ),
        runtime_pin={
            "project": "fixture",
            "version_or_commit": "fixture-v1",
            "python_constraint": ">=3.12,<3.13",
            "environment_path": "/tmp/advisorai-fixture-runtime",
        },
    )


def _downloader(calls: list[tuple[str, dict[str, str]]]):
    def download(url: str, destination: Path, headers) -> None:
        calls.append((url, dict(headers)))
        filename = unquote(Path(urlsplit(url).path).name)
        destination.write_bytes(f"fixture:{filename}".encode())

    return download


def test_acquisition_uses_exact_revision_and_builds_verified_clean_cache(tmp_path):
    calls: list[tuple[str, dict[str, str]]] = []
    candidate = _candidate(tmp_path / "unused")
    pin, result = acquire_candidate_artifacts(
        candidate,
        staging_root=tmp_path / "staging",
        cache_root=tmp_path / "cache",
        repository_root=tmp_path / "repository",
        download_file=_downloader(calls),
    )
    assert len(calls) == 3
    assert all(f"/{'a' * 40}/" in url for url, _headers in calls)
    assert all("/main/" not in url for url, _headers in calls)
    assert result.anonymous is True
    assert result.manifest_hash
    assert Path(pin.cache_path) == tmp_path / "cache" / candidate.name / ("a" * 40)
    observed = verify_checkpoint_artifacts(pin, cache_root=Path(pin.cache_path))
    assert {item.relative_path for item in observed} == {
        "README.md",
        "config.json",
        "model.safetensors",
    }


def test_cross_host_redirect_drops_registry_authorization():
    request = Request(
        "https://huggingface.co/fixture/model/resolve/revision/model.safetensors",
        headers={"Authorization": "Bearer secret"},
    )
    redirected = _SafeRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://cdn.example/model.safetensors",
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_repository_cache_subdir_rejects_traversal():
    with pytest.raises(ValueError, match="traversal-free"):
        RepositoryPin(
            repository_id="fixture/model",
            revision="a" * 40,
            license="unknown",
            runtime_artifacts=(ArtifactPin(relative_path="model.safetensors"),),
            cache_subdir="../escape",
        )


def test_token_is_scoped_to_download_headers_and_never_persisted(tmp_path):
    calls: list[tuple[str, dict[str, str]]] = []
    secret = "hf_private_fixture_secret"
    _pin, result = acquire_candidate_artifacts(
        _candidate(tmp_path / "unused"),
        staging_root=tmp_path / "staging",
        cache_root=tmp_path / "cache",
        repository_root=tmp_path / "repository",
        token=secret,
        download_file=_downloader(calls),
    )
    assert all(headers["Authorization"] == f"Bearer {secret}" for _url, headers in calls)
    serialized = result.model_dump_json()
    assert secret not in serialized
    assert "Authorization" not in serialized
    assert result.anonymous is False


def test_acquisition_rejects_roots_inside_repository(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    with pytest.raises(ModelAcquisitionError, match="outside"):
        acquire_candidate_artifacts(
            _candidate(tmp_path / "unused"),
            staging_root=repository / "staging",
            cache_root=tmp_path / "cache",
            repository_root=repository,
            download_file=_downloader([]),
        )


def test_acquisition_rejects_candidate_name_path_traversal(tmp_path):
    candidate = _candidate(tmp_path / "unused").model_copy(update={"name": "../escape"})
    with pytest.raises(ModelAcquisitionError, match="not safe"):
        acquire_candidate_artifacts(
            candidate,
            staging_root=tmp_path / "staging",
            cache_root=tmp_path / "cache",
            repository_root=tmp_path / "repository",
            download_file=_downloader([]),
        )


def test_existing_different_immutable_cache_is_not_overwritten(tmp_path):
    candidate = _candidate(tmp_path / "unused")
    destination = tmp_path / "cache" / candidate.name / ("a" * 40) / "model"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text("different", encoding="utf-8")
    with pytest.raises(ModelAcquisitionError, match="different content"):
        acquire_candidate_artifacts(
            candidate,
            staging_root=tmp_path / "staging",
            cache_root=tmp_path / "cache",
            repository_root=tmp_path / "repository",
            download_file=_downloader([]),
        )
    assert (destination / "config.json").read_text(encoding="utf-8") == "different"


def test_acquisition_failure_does_not_promote_partial_cache(tmp_path):
    candidate = _candidate(tmp_path / "unused")
    attempts = 0

    def fail_second(_url: str, destination: Path, _headers) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ModelAcquisitionError("fixture download failed")
        destination.write_bytes(b"partial")

    with pytest.raises(ModelAcquisitionError, match="fixture download failed"):
        acquire_candidate_artifacts(
            candidate,
            staging_root=tmp_path / "staging",
            cache_root=tmp_path / "cache",
            repository_root=tmp_path / "repository",
            download_file=fail_second,
        )
    assert not (tmp_path / "cache" / candidate.name / ("a" * 40)).exists()
    assert list((tmp_path / "staging").iterdir()) == []


def test_manifest_is_immutable_and_contains_no_request_headers(tmp_path):
    _pin, result = acquire_candidate_artifacts(
        _candidate(tmp_path / "unused"),
        staging_root=tmp_path / "staging",
        cache_root=tmp_path / "cache",
        repository_root=tmp_path / "repository",
        download_file=_downloader([]),
    )
    path = write_acquisition_manifest(result, tmp_path / "evidence" / "acquisition.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["manifest_hash"] == result.manifest_hash
    assert write_acquisition_manifest(result, path) == path
    changed = result.model_copy(update={"candidate": "other"})
    with pytest.raises(FileExistsError, match="differs"):
        write_acquisition_manifest(changed, path)
