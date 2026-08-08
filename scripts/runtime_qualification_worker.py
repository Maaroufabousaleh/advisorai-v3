#!/usr/bin/env python3
"""Small, core-dependency-free worker for isolated runtime qualification.

The parent AdvisorAI process launches this file with the exact interpreter
recorded in a :class:`RuntimePin`.  It intentionally imports no AdvisorAI
modules and emits one sanitized JSON response.  Candidate-specific workers
implement the same protocol in their isolated environment. Reviewed real
worker kinds live here too, but their third-party imports occur only after the
runtime identity and offline network guard have been established.
"""

from __future__ import annotations

import gc
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
        "package_versions": {str(name): value for name, value in sorted(package_versions.items())},
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

    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, *_args: object, **_kwargs: object) -> None:
            raise _NetworkAttempt("network access attempted")

        def connect_ex(self, *_args: object, **_kwargs: object) -> int:
            raise _NetworkAttempt("network access attempted")

    socket.socket = OfflineSocket
    socket.create_connection = blocked  # type: ignore[assignment]
    socket.getaddrinfo = blocked  # type: ignore[assignment]


def current_rss_mib() -> float:
    """Return current resident memory, never ``ru_maxrss``.

    Linux/WSL exposes the current resident page count through ``statm``.  The
    status-file fallback is useful on restricted proc mounts; psutil is only
    consulted when it is already available in a candidate runtime.  Returning
    zero on an unsupported platform is preferable to silently reporting a
    historical peak as the current post-unload measurement.
    """

    page_size = None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        pass
    if page_size and sys.platform.startswith("linux"):
        try:
            fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
            if len(fields) >= 2:
                return int(fields[1]) * page_size / (1024**2)
        except (OSError, ValueError):
            pass
        try:
            for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024
        except (OSError, ValueError, IndexError):
            pass
    try:
        import psutil

        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024**2)
    except Exception:  # pragma: no cover - optional/platform fallback
        return 0.0


def peak_rss_mib() -> float:
    """Return the process historical peak for diagnostic worker evidence."""

    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value / 1024 if sys.platform != "darwin" else value / (1024**2)
    except Exception:  # pragma: no cover - platform fallback
        return 0.0


def _clear_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pragma: no cover - optional candidate dependency
        pass


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


def _ttm_tensor(payload: Any) -> tuple[Any, bool]:
    """Convert one or more exact admitted TTM contexts to ``[B, 512, 1]``."""

    import torch

    is_batch = isinstance(payload, list) and bool(payload) and isinstance(payload[0], list)
    rows = payload if is_batch else [payload]
    if not isinstance(rows, list) or not rows:
        raise ValueError("TTM input must contain at least one context")
    normalized: list[list[float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 512:
            raise ValueError("TTM requires an exact 512-value context")
        values = [float(value) for value in row]
        if any(not float("-inf") < value < float("inf") for value in values):
            raise ValueError("TTM input must contain finite values")
        normalized.append(values)
    return torch.tensor(normalized, dtype=torch.float32).unsqueeze(-1), is_batch


def _load_real_model(kind: str, request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    admitted_kinds = {
        "chronos-2-small",
        "finbert-minilm",
        "finsentiment-deberta-v3",
        "modern-finbert",
        "kronos-mini",
        "kronos-small",
        "tspulse",
        "ttm-r2",
        "ttm-r3",
    }
    if kind not in admitted_kinds or request.get("family") != kind:
        raise ValueError("unsupported or mismatched admitted real-model worker kind")
    cache_root = Path(str(request.get("cache_path", ""))).expanduser().resolve(strict=True)
    model_root = cache_root / "model" if (cache_root / "model").is_dir() else cache_root
    if model_root.is_symlink() or not model_root.is_dir():
        raise ValueError("model cache root is invalid")

    import torch

    load_kwargs: dict[str, Any] = {}
    if kind in {"chronos-2-small", "kronos-mini", "kronos-small"}:
        if not torch.cuda.is_available():
            raise ValueError("the admitted GPU candidate requires CUDA")
    if kind == "chronos-2-small":
        from chronos import BaseChronosPipeline

        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        pipeline = BaseChronosPipeline.from_pretrained(
            str(model_root),
            local_files_only=True,
            trust_remote_code=False,
            device_map="cuda",
            dtype=torch.bfloat16,
        )
        model = pipeline.model
        loading_info = {}
    elif kind in {"kronos-mini", "kronos-small"}:
        import json as _json

        from model import Kronos, KronosPredictor, KronosTokenizer
        from safetensors.torch import load_model

        tokenizer_root = cache_root / "tokenizer"
        if tokenizer_root.is_symlink() or not tokenizer_root.is_dir():
            raise ValueError("Kronos tokenizer cache root is invalid")
        model_config = _json.loads((model_root / "config.json").read_text(encoding="utf-8"))
        tokenizer_config = _json.loads((tokenizer_root / "config.json").read_text(encoding="utf-8"))
        model = Kronos(**model_config)
        tokenizer = KronosTokenizer(**tokenizer_config)
        model_missing, model_unexpected = load_model(
            model, str(model_root / "model.safetensors"), strict=True, device="cpu"
        )
        tokenizer_missing, tokenizer_unexpected = load_model(
            tokenizer, str(tokenizer_root / "model.safetensors"), strict=True, device="cpu"
        )
        if model_missing or model_unexpected or tokenizer_missing or tokenizer_unexpected:
            raise ValueError("Kronos checkpoint loading identity mismatch")
        model.eval()
        tokenizer.eval()
        predictor = KronosPredictor(model, tokenizer, device="cuda:0", max_context=512)
        loading_info = {}
    elif kind in {"finbert-minilm", "finsentiment-deberta-v3", "modern-finbert"}:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            str(model_root), local_files_only=True, trust_remote_code=False
        )
        model_class = AutoModelForSequenceClassification
        load_kwargs = {"trust_remote_code": False}
        if kind == "modern-finbert":
            load_kwargs["attn_implementation"] = "eager"
    elif kind == "tspulse":
        from tsfm_public.models.tspulse import TSPulseForReconstruction

        model_class = TSPulseForReconstruction
    else:
        from tsfm_public.models.tinytimemixer import (
            TinyTimeMixerForDecomposedPrediction,
            TinyTimeMixerForPrediction,
        )

        model_class = (
            TinyTimeMixerForDecomposedPrediction if kind == "ttm-r3" else TinyTimeMixerForPrediction
        )
    if kind not in {"chronos-2-small", "kronos-mini", "kronos-small"}:
        model, loading_info = model_class.from_pretrained(
            str(model_root),
            local_files_only=True,
            output_loading_info=True,
            **load_kwargs,
        )
    key_groups = {
        "missing": tuple(loading_info.get("missing_keys", ())),
        "unexpected": tuple(loading_info.get("unexpected_keys", ())),
        "mismatched": tuple(loading_info.get("mismatched_keys", ())),
        "errors": tuple(loading_info.get("error_msgs", ())),
    }
    if any(key_groups.values()):
        raise ValueError("TTM checkpoint loading identity mismatch")
    model.eval()
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    expected_parameters = {
        "modern-finbert": 149_607_171,
        "chronos-2-small": 27_934_624,
        "finbert-minilm": 33_361_155,
        "finsentiment-deberta-v3": 184_424_451,
        "tspulse": 1_084_330,
        "ttm-r2": 805_280,
        "ttm-r3": 1_414_514,
        "kronos-mini": 8_066_074,
        "kronos-small": 28_699_418,
    }[kind]
    if kind in {"kronos-mini", "kronos-small"}:
        parameter_count += sum(parameter.numel() for parameter in tokenizer.parameters())
    if parameter_count != expected_parameters:
        raise ValueError("TTM parameter identity mismatch")
    state = {"model": model, "torch": torch}
    if kind == "chronos-2-small":
        state["pipeline"] = pipeline
    if kind in {"kronos-mini", "kronos-small"}:
        state["tokenizer"] = tokenizer
        state["predictor"] = predictor
    if kind in {"finbert-minilm", "finsentiment-deberta-v3", "modern-finbert"}:
        state["tokenizer"] = tokenizer
        state["id2label"] = {int(key): str(value) for key, value in model.config.id2label.items()}
    return (
        state,
        {
            "loaded_model_class": type(model).__name__,
            "loaded_parameter_count": parameter_count,
            "checkpoint_missing_key_count": 0,
            "checkpoint_unexpected_key_count": 0,
            "checkpoint_mismatched_key_count": 0,
        },
    )


def _infer_real_model(kind: str, state: dict[str, Any], payload: Any) -> Any:
    if kind not in {
        "chronos-2-small",
        "finbert-minilm",
        "finsentiment-deberta-v3",
        "modern-finbert",
        "kronos-mini",
        "kronos-small",
        "tspulse",
        "ttm-r2",
        "ttm-r3",
    }:
        raise ValueError("unsupported admitted real-model worker kind")
    if kind == "chronos-2-small":
        is_batch = isinstance(payload, list) and bool(payload) and isinstance(payload[0], list)
        rows = payload if is_batch else [payload]
        if not isinstance(rows, list) or not rows:
            raise ValueError("Chronos input must contain at least one context")
        contexts = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 32 or len(row) > 8192:
                raise ValueError("Chronos requires 32 to 8192 context values")
            values = [float(value) for value in row]
            if any(not float("-inf") < value < float("inf") for value in values):
                raise ValueError("Chronos input must contain finite values")
            contexts.append(state["torch"].tensor(values, dtype=state["torch"].float32))
        quantiles, means = state["pipeline"].predict_quantiles(
            contexts,
            prediction_length=30,
            quantile_levels=[0.1, 0.5, 0.9],
            batch_size=min(32, len(contexts)),
        )
        forecasts = [mean.squeeze(0).detach().cpu().tolist() for mean in means]
        state["last_interval_lower"] = [
            values.squeeze(0)[..., 0].detach().cpu().tolist() for values in quantiles
        ]
        state["last_interval_upper"] = [
            values.squeeze(0)[..., -1].detach().cpu().tolist() for values in quantiles
        ]
        if any(len(row) != 30 for row in forecasts):
            raise ValueError("Chronos returned an unexpected forecast shape")
        if any(
            not float("-inf") < float(value) < float("inf") for row in forecasts for value in row
        ):
            raise ValueError("Chronos returned a non-finite forecast")
        return forecasts if is_batch else forecasts[0]
    if kind in {"kronos-mini", "kronos-small"}:
        import pandas as pd

        is_batch = (
            isinstance(payload, list) and bool(payload) and isinstance(payload[0], (list, dict))
        )
        rows = payload if is_batch else [payload]
        frames = []
        history_times = []
        future_times = []
        for row in rows:
            if isinstance(row, dict):
                ohlcv = row.get("ohlcv")
                timestamps = row.get("timestamps")
                future_values = row.get("future_timestamps")
                if (
                    not isinstance(ohlcv, list)
                    or len(ohlcv) != 512
                    or not isinstance(timestamps, list)
                    or len(timestamps) != 512
                    or not isinstance(future_values, list)
                    or len(future_values) != 30
                ):
                    raise ValueError("Kronos OHLCV request has an invalid shape")
                values = [[float(value) for value in item] for item in ohlcv]
                if any(len(item) != 5 for item in values):
                    raise ValueError("Kronos OHLCV rows require five values")
                frame = pd.DataFrame(values, columns=["open", "high", "low", "close", "volume"])
                frame["amount"] = frame["volume"] * frame[["open", "high", "low", "close"]].mean(
                    axis=1
                )
                history = pd.Series(pd.to_datetime(timestamps, utc=True))
                future = pd.Series(pd.to_datetime(future_values, utc=True))
            else:
                if not isinstance(row, list) or len(row) != 512:
                    raise ValueError("Kronos requires an exact 512-value context")
                close = [float(value) for value in row]
                opened = [close[0], *close[:-1]]
                frame = pd.DataFrame(
                    {
                        "open": opened,
                        "high": [
                            max(left, right) for left, right in zip(opened, close, strict=True)
                        ],
                        "low": [
                            min(left, right) for left, right in zip(opened, close, strict=True)
                        ],
                        "close": close,
                        "volume": [1.0] * len(close),
                        "amount": close,
                    }
                )
                history = pd.Series(pd.date_range("2020-01-01", periods=len(close), freq="D"))
                future = pd.Series(
                    pd.date_range(history.iloc[-1] + pd.Timedelta(days=1), periods=30, freq="D")
                )
            if any(
                not float("-inf") < float(value) < float("inf") for value in frame.to_numpy().flat
            ):
                raise ValueError("Kronos input must contain finite values")
            frames.append(frame)
            history_times.append(history)
            future_times.append(future)
        forecasts = state["predictor"].predict_batch(
            frames,
            history_times,
            future_times,
            pred_len=30,
            T=1.0,
            top_p=0.9,
            sample_count=1,
            verbose=False,
        )
        values = [frame["close"].astype(float).tolist() for frame in forecasts]
        if any(len(row) != 30 for row in values):
            raise ValueError("Kronos returned an unexpected forecast shape")
        if any(not float("-inf") < value < float("inf") for row in values for value in row):
            raise ValueError("Kronos returned a non-finite forecast")
        return values if is_batch else values[0]
    if kind in {"finbert-minilm", "finsentiment-deberta-v3", "modern-finbert"}:
        is_batch = isinstance(payload, list)
        texts = payload if is_batch else [payload]
        if (
            not isinstance(texts, list)
            or not texts
            or any(not isinstance(text, str) for text in texts)
        ):
            raise ValueError("sentiment input must be non-empty text")
        encoded = state["tokenizer"](
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with state["torch"].inference_mode():
            logits = state["model"](**encoded).logits
            probabilities = state["torch"].softmax(logits, dim=-1)
        indices = probabilities.argmax(dim=-1).tolist()
        scores = probabilities.max(dim=-1).values.tolist()
        labels = state["id2label"]
        normalized = {
            "bullish": "positive",
            "bearish": "negative",
            "positive": "positive",
            "negative": "negative",
            "neutral": "neutral",
        }
        results = [
            {"label": normalized[labels[int(index)].lower()], "confidence": float(score)}
            for index, score in zip(indices, scores, strict=True)
        ]
        return results if is_batch else results[0]
    tensor, is_batch = _ttm_tensor(payload)
    with state["torch"].inference_mode():
        output = state["model"](past_values=tensor)
    if kind == "tspulse":
        reconstruction = output.reconstruction_outputs
        hidden = output.backbone_hidden_state
        fft_reconstruction = output.reconstructed_ts_from_fft
        error = reconstruction - tensor
        features = (
            state["torch"]
            .stack(
                (
                    error.abs().mean(dim=(1, 2)),
                    error.square().mean(dim=(1, 2)).sqrt(),
                    error.abs().amax(dim=(1, 2)),
                    hidden.mean(dim=(1, 2)),
                    hidden.std(dim=(1, 2), unbiased=False),
                    (fft_reconstruction - tensor).abs().mean(dim=(1, 2)),
                ),
                dim=1,
            )
            .detach()
            .cpu()
            .tolist()
        )
        if any(
            not float("-inf") < float(value) < float("inf") for row in features for value in row
        ):
            raise ValueError("TSPulse returned non-finite features")
        return features if is_batch else features[0]
    predictions = output.prediction_outputs
    horizon = 30 if kind == "ttm-r3" else 96
    if tuple(predictions.shape[1:]) != (horizon, 1):
        raise ValueError("TTM returned an unexpected forecast shape")
    values = predictions[..., 0].detach().cpu().tolist()
    if any(not float("-inf") < float(value) < float("inf") for row in values for value in row):
        raise ValueError("TTM returned a non-finite forecast")
    return values if is_batch else values[0]


def _gpu_memory_mib() -> tuple[float | None, float | None]:
    try:
        import torch

        if not torch.cuda.is_available():
            return None, None
        return (
            float(torch.cuda.memory_allocated() / (1024**2)),
            float(torch.cuda.max_memory_allocated() / (1024**2)),
        )
    except Exception:  # pragma: no cover - optional GPU runtime
        return None, None


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
        manifest_path = (
            Path(str(request["installed_environment_manifest_path"]))
            .expanduser()
            .resolve(strict=False)
        )
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
        # Runtime libraries needed for identity (notably Torch) are already
        # imported. This is the correct baseline for model unload recovery;
        # the parent monitor still captures the complete process-tree peak.
        rss_before_load = current_rss_mib()
        cold_started = time.perf_counter()
        model_metadata: dict[str, Any] = {}
        real_state: dict[str, Any] | None = None
        if kind in {
            "chronos-2-small",
            "finbert-minilm",
            "finsentiment-deberta-v3",
            "modern-finbert",
            "kronos-mini",
            "kronos-small",
            "tspulse",
            "ttm-r2",
            "ttm-r3",
        }:
            real_state, model_metadata = _load_real_model(kind, request)
            model: object | None = real_state.get("model")
        else:
            model = object()
        cold_load_ms = (time.perf_counter() - cold_started) * 1000
        rss_after_load = current_rss_mib()
        vram_after_load, _ = _gpu_memory_mib()
        for _index in range(repeats):
            if repeatability_policy == "seeded_reproducible":
                if not isinstance(requested_seed, int):
                    raise ValueError("seeded worker request is missing its seed")
                _apply_seed(requested_seed)
                applied_seeds.append(requested_seed)
            started = time.perf_counter()
            output = (
                _infer_real_model(kind, real_state, sample)
                if real_state is not None
                else _fixture_output(kind, sample, counter, labels)
            )
            durations.append((time.perf_counter() - started) * 1000)
            outputs.append(output)
        batch_started = time.perf_counter()
        batch_output = (
            _infer_real_model(kind, real_state, batch)
            if real_state is not None
            else _fixture_output(kind, batch, counter, labels)
        )
        batch_ms = (time.perf_counter() - batch_started) * 1000
        forecast_batch_lower = (
            tuple(
                tuple(float(value) for value in row)
                for row in real_state.get("last_interval_lower", ())
            )
            if real_state is not None
            else ()
        )
        forecast_batch_upper = (
            tuple(
                tuple(float(value) for value in row)
                for row in real_state.get("last_interval_upper", ())
            )
            if real_state is not None
            else ()
        )
        # Candidate workers must release model references and collect Python
        # garbage before recording the post-unload current RSS.  This fixture
        # path deliberately creates a transient high-water allocation so the
        # parent process-tree sampler can distinguish peak from current RSS.
        if kind == "fixture_peak_rss":
            temporary = bytearray(64 * 1024 * 1024)
            for offset in range(0, len(temporary), 4096):
                temporary[offset] = 1
            time.sleep(0.05)
            _ = peak_rss_mib()
            del temporary
        # A real candidate worker uses this same release point after deleting
        # its loaded model/tokenizer references.  Keep the explicit reference
        # deletion in the fixture protocol so post-unload RSS has the same
        # semantics before external runtimes are admitted.
        if real_state is not None:
            real_state["model"] = None
            real_state["tokenizer"] = None
            real_state["pipeline"] = None
            real_state["predictor"] = None
            real_state.clear()
        model = None
        gc.collect()
        _clear_cuda_cache()
        rss_after_unload = current_rss_mib()
        vram_after_unload, vram_peak = _gpu_memory_mib()
        digests = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in outputs]
        numbers = [
            float(item) for output in outputs for item in output if isinstance(item, (int, float))
        ]
        dispersion = statistics.pstdev(numbers) if len(numbers) > 1 else 0.0
        response = {
            "protocol_version": 1,
            "identity": identity,
            "outputs": outputs,
            "batch_output": batch_output,
            "forecast_batch_lower": forecast_batch_lower,
            "forecast_batch_upper": forecast_batch_upper,
            "cold_load_ms": cold_load_ms,
            "warm_durations_ms": durations,
            "batch_inference_ms": batch_ms,
            "batch_size": len(batch) if isinstance(batch, list) else 1,
            "rss_before_load_mib": rss_before_load,
            "rss_after_load_mib": rss_after_load,
            "rss_after_unload_mib": rss_after_unload,
            "vram_after_load_mib": vram_after_load,
            "vram_peak_mib": vram_peak,
            "vram_after_unload_mib": vram_after_unload,
            "unload_succeeded": True,
            "offline_cached_inference": True,
            "stochastic_repeat_count": repeats,
            "stochastic_unique_output_count": len(set(digests)),
            "stochastic_deterministic_match_rate": sum(item == digests[0] for item in digests)
            / len(digests),
            "stochastic_seeds": [],
            "applied_seeds": applied_seeds,
            "stochastic_dispersion": dispersion,
            "stochastic_variation_observed": len(set(digests)) > 1 and dispersion > 0,
            **model_metadata,
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
                "identity": locals().get(
                    "identity",
                    {
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
                    },
                ),
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
