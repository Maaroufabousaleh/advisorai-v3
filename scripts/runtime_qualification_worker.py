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
import random
import re
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


def _launcher_identity_hash(path: Path) -> str:
    if path.is_symlink():
        return sha256(os.readlink(path).encode()).hexdigest()
    return _sha256_file(path)


def _installed_environment_inventory() -> tuple[str, ...]:
    entries = {
        f"{distribution.metadata['Name'] or distribution.name}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name") or distribution.name
    }
    return tuple(sorted(entries, key=str.casefold))


def _installed_environment_hash(entries: tuple[str, ...] | None = None) -> str:
    payload = json.dumps(
        entries or _installed_environment_inventory(),
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return sha256(payload).hexdigest()


def _environment_fingerprint(
    *,
    sys_executable: str,
    python_version: str,
    package_versions: dict[str, str | None],
    torch_version: str | None,
    cuda_version: str | None,
    runtime_lock_hash: str,
    installed_environment_sha256: str,
    sys_prefix: str,
    sys_base_prefix: str,
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
        "installed_environment_sha256": installed_environment_sha256,
        "sys_prefix": str(Path(sys_prefix).expanduser().resolve(strict=False)),
        "sys_base_prefix": str(Path(sys_base_prefix).expanduser().resolve(strict=False)),
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


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = value.strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError("invalid Python version constraint")
    return tuple(int(part) for part in parts)


def _python_constraint_satisfied(constraint: str, version: str) -> bool:
    """Evaluate the small PEP-440 subset used by runtime admission pins.

    The isolated worker deliberately has no dependency on AdvisorAI or the
    core environment.  Runtime pins currently use comma-separated comparison
    clauses (for example ``>=3.12,<3.13``), so evaluating those clauses here
    keeps the Python-version gate before any candidate inference.
    """

    observed = _version_tuple(version)
    for clause in (item.strip() for item in constraint.split(",")):
        match = re.fullmatch(r"(===|==|!=|>=|<=|>|<|~=)\s*(\d+(?:\.\d+)*)", clause)
        if match is None:
            raise ValueError("invalid Python constraint")
        operator, raw_bound = match.groups()
        bound = _version_tuple(raw_bound)
        width = max(len(observed), len(bound))
        left = observed + (0,) * (width - len(observed))
        right = bound + (0,) * (width - len(bound))
        if operator in {"==", "==="} and left != right:
            return False
        if operator == "!=" and left == right:
            return False
        if operator == ">=" and left < right:
            return False
        if operator == "<=" and left > right:
            return False
        if operator == ">" and left <= right:
            return False
        if operator == "<" and left >= right:
            return False
        if operator == "~=":
            if left < right:
                return False
            upper = list(bound)
            if len(upper) == 1:
                upper[0] += 1
            else:
                upper[-2] += 1
                upper[-1] = 0
            upper_tuple = tuple(upper) + (0,) * (width - len(upper))
            if left >= upper_tuple:
                return False
    return True


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


def _apply_seed(seed: int) -> None:
    """Apply a repeat seed to the available local runtime libraries."""

    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:  # pragma: no cover - optional candidate dependency
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:  # pragma: no cover - optional candidate dependency
        pass


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
    if kind == "fixture_network_inference":
        socket.create_connection(("example.invalid", 443), timeout=0.1)
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


def _fallback_identity(request: dict[str, Any]) -> dict[str, Any]:
    zero = "0" * 64
    prefix = str(Path(sys.prefix).expanduser().resolve(strict=False))
    base_prefix = str(Path(sys.base_prefix).expanduser().resolve(strict=False))
    return {
        "sys_executable": str(Path(sys.executable).expanduser()),
        "model_family": str(request.get("family", "unknown")),
        "sys_prefix": prefix,
        "sys_base_prefix": base_prefix,
        "python_launcher_hash": zero,
        "python_launcher_target": None,
        "resolved_python_binary_hash": zero,
        "pyvenv_cfg_hash": None,
        "python_version": platform.python_version(),
        "package_versions": [],
        "torch_version": None,
        "cuda_version": None,
        "runtime_lock_hash": zero,
        "lock_artifact_hash": zero,
        "python_executable_hash": zero,
        "installed_environment_sha256": zero,
        "environment_fingerprint": zero,
        "runner_version": "unknown",
        "runner_hash": zero,
    }


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
        launcher = Path(sys.executable).expanduser()
        executable = launcher.resolve(strict=False)
        lock_hash = _sha256_file(lock_path)
        launcher_hash = _launcher_identity_hash(launcher)
        executable_hash = _sha256_file(executable)
        launcher_target = str(executable) if launcher.is_symlink() else None
        environment_path = Path(sys.prefix).expanduser().resolve(strict=False)
        base_prefix = Path(sys.base_prefix).expanduser().resolve(strict=False)
        cfg_path = environment_path / "pyvenv.cfg"
        cfg_hash = _sha256_file(cfg_path) if cfg_path.is_file() else None
        installed_inventory_hash = _installed_environment_hash()
        manifest_path = Path(str(request["installed_environment_manifest_path"])).expanduser().resolve(strict=False)
        manifest_hash = _sha256_file(manifest_path)
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
            installed_environment_sha256=installed_inventory_hash,
            sys_prefix=str(environment_path),
            sys_base_prefix=str(base_prefix),
        )
        identity = {
            "sys_executable": str(launcher),
            "model_family": str(request["family"]),
            "sys_prefix": str(environment_path),
            "sys_base_prefix": str(base_prefix),
            "python_launcher_hash": launcher_hash,
            "python_launcher_target": launcher_target,
            "resolved_python_binary_hash": executable_hash,
            "pyvenv_cfg_hash": cfg_hash,
            "python_version": python_version,
            "package_versions": sorted(packages.items()),
            "torch_version": torch_version,
            "cuda_version": cuda_version,
            "runtime_lock_hash": lock_hash,
            "lock_artifact_hash": lock_hash,
            "python_executable_hash": executable_hash,
            "installed_environment_sha256": installed_inventory_hash,
            "environment_fingerprint": environment_fingerprint,
            "runner_version": runner_version,
            "runner_hash": runner_hash,
        }
        if lock_hash != str(request["lock_hash"]):
            raise ValueError("lock artifact hash mismatch")
        if launcher_hash != str(request["python_launcher_hash"]):
            raise ValueError("Python launcher hash mismatch")
        if executable_hash != str(request["resolved_python_binary_hash"]):
            raise ValueError("resolved Python binary hash mismatch")
        if launcher_target != request.get("python_launcher_target"):
            raise ValueError("Python launcher target mismatch")
        if cfg_hash != request.get("pyvenv_cfg_hash"):
            raise ValueError("pyvenv.cfg hash mismatch")
        if manifest_hash != str(request["installed_environment_sha256"]):
            raise ValueError("installed-environment manifest hash mismatch")
        if installed_inventory_hash != str(request["installed_environment_sha256"]):
            raise ValueError("installed-environment inventory mismatch")
        if runner_hash != str(request["runner_hash"]):
            raise ValueError("worker runner hash mismatch")
        if environment_fingerprint != str(request["environment_fingerprint"]):
            raise ValueError("runtime environment fingerprint mismatch")
        if not _python_constraint_satisfied(str(request["python_constraint"]), python_version):
            raise ValueError("worker Python version does not satisfy the runtime pin")

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
        applied_seeds: list[int] = []
        repeatability_policy = str(request.get("repeatability_policy", ""))
        requested_seed = request.get("repeatability_seed")
        cold_started = time.perf_counter()
        # The fixture worker represents a successfully loaded cached runtime.
        # Real candidate workers replace this section with local_files_only
        # model loading and retain the same sanitized response contract.
        _ = object()
        cold_load_ms = (time.perf_counter() - cold_started) * 1000
        for _index in range(repeats):
            if repeatability_policy == "seeded_reproducible":
                if not isinstance(requested_seed, int):
                    raise ValueError("seeded worker request is missing its seed")
                _apply_seed(requested_seed)
                applied_seeds.append(requested_seed)
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
            "rss_after_unload_mib": _rss_mib(),
            "vram_after_load_mib": None,
            "vram_peak_mib": None,
            "vram_after_unload_mib": None,
            "unload_succeeded": True,
            "offline_cached_inference": True,
            "stochastic_repeat_count": repeats,
            "stochastic_unique_output_count": len(set(digests)),
            "stochastic_deterministic_match_rate": sum(item == digests[0] for item in digests) / len(digests),
            "stochastic_seeds": [],
            "applied_seeds": applied_seeds,
            "stochastic_dispersion": dispersion,
            "stochastic_variation_observed": len(set(digests)) > 1 and dispersion > 0,
        }
        if kind == "malformed_success":
            response["outputs"] = []
            response["warm_durations_ms"] = []
        return _emit(response)
    except _NetworkAttempt:
        # Network failure is itself sanitized evidence. Never let an
        # unexpected loader/inference connection disappear as a bare exit
        # code that the parent cannot classify.
        request_value = request if isinstance(locals().get("request"), dict) else {}
        identity_value = locals().get("identity") or _fallback_identity(request_value)
        return _emit(
            {
                "protocol_version": 1,
                "identity": identity_value,
                "network_access_attempted": True,
                "error_class": "NetworkAccessAttemptError",
            }
        )
    except Exception as exc:  # sanitized: never send provider/file messages to the ledger
        return _emit(
            {
                "protocol_version": 1,
                "identity": locals().get("identity", {
                    "sys_executable": str(Path(sys.executable).resolve()),
                    "model_family": "unknown",
                    "sys_prefix": str(Path(sys.prefix).resolve()),
                    "sys_base_prefix": str(Path(sys.base_prefix).resolve()),
                    "python_launcher_hash": "0" * 64,
                    "python_launcher_target": None,
                    "resolved_python_binary_hash": "0" * 64,
                    "pyvenv_cfg_hash": None,
                    "python_version": platform.python_version(),
                    "package_versions": [],
                    "torch_version": None,
                    "cuda_version": None,
                    "runtime_lock_hash": "0" * 64,
                    "lock_artifact_hash": "0" * 64,
                    "python_executable_hash": "0" * 64,
                    "installed_environment_sha256": "0" * 64,
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


def _emit_inventory() -> int:
    print(
        json.dumps(_installed_environment_inventory(), separators=(",", ":"), ensure_ascii=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_emit_inventory() if "--inventory" in sys.argv[1:] else main())
