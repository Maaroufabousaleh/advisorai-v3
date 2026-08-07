#!/usr/bin/env python3
"""Small, dependency-free worker protocol for isolated runtime qualification.

The parent AdvisorAI process launches this file with the exact interpreter
recorded in a :class:`RuntimePin`.  It intentionally imports no AdvisorAI
modules and emits one sanitized JSON response.  Candidate-specific workers
can implement the same protocol in their isolated environment; these fixture
kinds let the harness be tested without downloading model weights.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import socket
import statistics
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class _NetworkAttempt(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _environment_fingerprint(
    *,
    sys_executable: str,
    python_version: str,
    package_versions: dict[str, str | None],
    torch_version: str | None,
    cuda_version: str | None,
    runtime_lock_hash: str,
) -> str:
    material = {
        "sys_executable": str(Path(sys_executable).expanduser().resolve(strict=False)),
        "python_version": python_version,
        "package_versions": {
            str(name): value for name, value in sorted(package_versions.items())
        },
        "torch_version": torch_version,
        "cuda_version": cuda_version,
        "runtime_lock_hash": runtime_lock_hash,
    }
    return sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _runner_hash(runner_version: str) -> str:
    script_hash = _sha256_file(Path(__file__).resolve())
    return sha256(f"{runner_version}\n{script_hash}".encode()).hexdigest()


def _package_name(dependency: str) -> str:
    token = dependency.split("==", 1)[0].split("@", 1)[0].strip()
    return token


def _package_versions(dependencies: list[str]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for dependency in dependencies:
        name = _package_name(dependency)
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _torch_identity() -> tuple[str | None, str | None]:
    try:
        torch_version = importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return None, None
    cuda_version: str | None = None
    try:
        import torch

        cuda_version = torch.version.cuda
    except Exception:  # pragma: no cover - optional runtime metadata
        pass
    return torch_version, cuda_version


def _install_network_guard() -> None:
    def blocked(*_args: object, **_kwargs: object) -> object:
        raise _NetworkAttempt("network access attempted")

    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    socket.getaddrinfo = blocked  # type: ignore[assignment]


def _rss_mib() -> float:
    try:
        import resource

        # Linux reports KiB; macOS reports bytes.  WSL/Linux is the supported
        # qualification host, but handling both keeps the fixture portable.
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value / 1024 if sys.platform != "darwin" else value / (1024**2)
    except Exception:  # pragma: no cover - platform fallback
        return 0.0


def _fixture_output(kind: str, payload: Any, counter: list[int], labels: list[str]) -> Any:
    if kind == "fixture_finbert_score":
        if isinstance(payload, list):
            return [{"label": label, "score": 0.8} for label in labels[: len(payload)]]
        return {"label": labels[0] if labels else "neutral", "score": 0.8}
    if kind in {"fixture_stochastic", "fixture_constant_stochastic"}:
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            return [[1.0] for _ in payload]
        counter[0] += 1
        return [1.0] if kind == "fixture_constant_stochastic" else [float(counter[0])]
    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        return [[1.0] for _ in payload]
    return [1.0]


def main() -> int:
    try:
        request = json.loads(sys.stdin.readline())
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        if request.get("trust_remote_code") is not False:
            raise ValueError("remote model code is not admitted")
        if request.get("local_files_only") is not True:
            raise ValueError("worker must use local_files_only=True")
        lock_path = Path(str(request["lock_artifact_path"])).expanduser().resolve(strict=False)
        executable = Path(sys.executable).expanduser().resolve(strict=False)
        lock_hash = _sha256_file(lock_path)
        executable_hash = _sha256_file(executable)
        dependencies = [str(item) for item in request.get("dependencies", [])]
        packages = _package_versions(dependencies)
        torch_version, cuda_version = _torch_identity()
        python_version = platform.python_version()
        runner_version = str(request["runner_version"])
        runner_hash = _runner_hash(runner_version)
        environment_fingerprint = _environment_fingerprint(
            sys_executable=str(executable),
            python_version=python_version,
            package_versions=packages,
            torch_version=torch_version,
            cuda_version=cuda_version,
            runtime_lock_hash=lock_hash,
        )
        identity = {
            "sys_executable": str(executable),
            "model_family": str(request["family"]),
            "python_version": python_version,
            "package_versions": sorted(packages.items()),
            "torch_version": torch_version,
            "cuda_version": cuda_version,
            "runtime_lock_hash": lock_hash,
            "lock_artifact_hash": lock_hash,
            "python_executable_hash": executable_hash,
            "environment_fingerprint": environment_fingerprint,
            "runner_version": runner_version,
            "runner_hash": runner_hash,
        }
        if lock_hash != str(request["lock_hash"]):
            raise ValueError("lock artifact hash mismatch")
        if executable_hash != str(request["python_executable_hash"]):
            raise ValueError("Python executable hash mismatch")
        if runner_hash != str(request["runner_hash"]):
            raise ValueError("worker runner hash mismatch")
        if environment_fingerprint != str(request["environment_fingerprint"]):
            raise ValueError("runtime environment fingerprint mismatch")

        _install_network_guard()
        kind = str(request.get("worker_kind", "qualification"))
        if kind == "network_attempt":
            try:
                socket.create_connection(("example.invalid", 443), timeout=0.1)
            except _NetworkAttempt:
                return _emit(
                    {
                        "protocol_version": 1,
                        "identity": identity,
                        "network_access_attempted": True,
                        "error_class": "NetworkAccessAttemptError",
                    }
                )
        labels = [str(item) for item in request.get("expected_labels", [])]
        sample = request.get("sample_input")
        batch = request.get("batch_input")
        repeats = max(2, int(request.get("repeats", 3)))
        counter = [0]
        outputs: list[Any] = []
        durations: list[float] = []
        cold_started = time.perf_counter()
        # The fixture worker represents a successfully loaded cached runtime.
        # Real candidate workers replace this section with local_files_only
        # model loading and retain the same sanitized response contract.
        _ = object()
        cold_load_ms = (time.perf_counter() - cold_started) * 1000
        for _index in range(repeats):
            started = time.perf_counter()
            output = _fixture_output(kind, sample, counter, labels)
            durations.append((time.perf_counter() - started) * 1000)
            outputs.append(output)
        batch_started = time.perf_counter()
        batch_output = _fixture_output(kind, batch, counter, labels)
        batch_ms = (time.perf_counter() - batch_started) * 1000
        digests = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in outputs]
        numbers = [float(item) for output in outputs for item in output if isinstance(item, (int, float))]
        dispersion = statistics.pstdev(numbers) if len(numbers) > 1 else 0.0
        response = {
            "protocol_version": 1,
            "identity": identity,
            "outputs": outputs,
            "batch_output": batch_output,
            "cold_load_ms": cold_load_ms,
            "warm_durations_ms": durations,
            "batch_inference_ms": batch_ms,
            "batch_size": len(batch) if isinstance(batch, list) else 1,
            "rss_after_load_mib": _rss_mib(),
            "vram_after_load_mib": None,
            "vram_peak_mib": None,
            "vram_after_unload_mib": None,
            "unload_succeeded": True,
            "offline_cached_inference": True,
            "stochastic_repeat_count": repeats,
            "stochastic_unique_output_count": len(set(digests)),
            "stochastic_deterministic_match_rate": sum(item == digests[0] for item in digests) / len(digests),
            "stochastic_seeds": [],
            "stochastic_dispersion": dispersion,
            "stochastic_variation_observed": len(set(digests)) > 1 and dispersion > 0,
        }
        return _emit(response)
    except _NetworkAttempt:
        return 2
    except Exception as exc:  # sanitized: never send provider/file messages to the ledger
        return _emit(
            {
                "protocol_version": 1,
                "identity": locals().get("identity", {
                    "sys_executable": str(Path(sys.executable).resolve()),
                    "model_family": "unknown",
                    "python_version": platform.python_version(),
                    "package_versions": [],
                    "torch_version": None,
                    "cuda_version": None,
                    "runtime_lock_hash": "0" * 64,
                    "lock_artifact_hash": "0" * 64,
                    "python_executable_hash": "0" * 64,
                    "environment_fingerprint": "0" * 64,
                    "runner_version": "unknown",
                    "runner_hash": "0" * 64,
                }),
                "error_class": type(exc).__name__,
            }
        )


def _emit(response: dict[str, Any]) -> int:
    print(json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
