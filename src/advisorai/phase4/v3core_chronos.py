"""Prospective Chronos-2-small prediction boundary for V3-Core.

This boundary is a candidate-only, credential-free consumer of the forward
normalized bar spool.  It binds the exact locally admitted Chronos runtime,
keeps predictions in the shared append-only ledger, and never reads outcomes,
opens a network client, or exposes an execution operation.

The qualified Chronos runtime accepts 32--8192 values and emits 30 forecast
values.  V3-Core therefore has a real 48-bar -> 30-output compatibility path;
the worker scores the preregistered 12th output (one hour) and retains native
quantile bounds when the runtime supplies them.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.phase0.runtime_qualification import (
    LocalCandidateAdmission,
    apply_local_candidate_admission,
    default_runtime_candidates,
    verify_checkpoint_artifacts,
    verify_runtime_pin,
)
from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_SYMBOLS,
    V3CoreBar,
)
from advisorai.phase4.v3core_forward import (
    FORWARD_INTERVAL_SECONDS,
    ForwardNormalizedBarSpool,
    ForwardPredictionRecord,
    ForwardRejectionSpool,
)
from advisorai.phase4.v3core_prediction_ledger import ForwardPredictionLedger

CHRONOS_MODEL = "chronos-2-small"
CHRONOS_CONTEXT_BARS = 48
CHRONOS_HORIZON_BARS = 12
CHRONOS_OUTPUT_BARS = 30
CHRONOS_NATIVE_CONTEXT_MIN = 32
CHRONOS_NATIVE_CONTEXT_MAX = 8192
CHRONOS_PREPROCESSING_IDENTITY = "v3core-raw-close-48-direct-chronos-v1"
CHRONOS_RUN_SCHEMA = "advisorai.phase4.v3-core-forward.chronos-2-small-predictions.v1"
CHRONOS_PREDICTION_IDENTITY_SCHEMA = "advisorai.phase4.v3-core-forward.chronos-2-small-identity.v1"
CHRONOS_POLL_SECONDS = 5.0
CHRONOS_WORKER_TIMEOUT_SECONDS = 120.0

CHRONOS_RESUME_IDENTITY_FIELDS = (
    "schema",
    "source_root",
    "source_manifest_sha256",
    "source_snapshot_hash",
    "source_provider_identity",
    "admission_path",
    "admission_sha256",
    "qualification_evidence_path",
    "qualification_evidence_sha256",
    "preregistration_sha256",
    "phase3_gate_record_sha256",
    "repository_commit",
    "prediction_code_sha256",
    "prediction_script_sha256",
    "runner_script_hash",
    "runner_hash",
    "checkpoint_hash",
    "preprocessing_identity",
    "preprocessing_hash",
    "model_identity_hash",
    "model_identity",
    "device",
    "native_context_min",
    "native_context_max",
    "output_bars",
    "context_bars",
    "horizon_bars",
    "cadence",
    "target_end_at",
)


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _finite_decimal(value: object, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


class ChronosInferenceFailure(RuntimeError):
    """A sanitized failure from the isolated Chronos worker."""

    def __init__(self, error_class: str) -> None:
        safe = "".join(
            character for character in str(error_class) if character.isalnum() or character in "_-"
        )
        self.error_class = safe[:120] or "UnknownWorkerError"
        super().__init__(self.error_class)


class ChronosRuntimeIdentity(BaseModel):
    """Exact checkpoint, runtime, and role identity for Chronos-2-small."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_name: str
    checkpoint_repository: str
    checkpoint_revision: str
    cache_path: str
    cache_subdir: str
    config_hash: str
    checkpoint_hash: str
    dependencies: tuple[str, ...]
    environment_fingerprint: str
    installed_environment_manifest_path: str
    installed_environment_sha256: str
    lock_artifact_path: str
    lock_hash: str
    python_constraint: str
    python_launcher: str
    python_launcher_hash: str
    python_launcher_target: str | None = None
    pyvenv_cfg_hash: str | None = None
    resolved_python_binary_hash: str
    runner_version: str
    runner_hash: str
    runner_script: str
    runner_script_hash: str
    admission_path: str
    admission_sha256: str
    qualification_evidence_path: str
    qualification_evidence_sha256: str
    device: str
    native_context_min: int = CHRONOS_NATIVE_CONTEXT_MIN
    native_context_max: int = CHRONOS_NATIVE_CONTEXT_MAX
    output_bars: int = CHRONOS_OUTPUT_BARS

    @field_validator(
        "config_hash",
        "checkpoint_hash",
        "environment_fingerprint",
        "installed_environment_sha256",
        "lock_hash",
        "python_launcher_hash",
        "pyvenv_cfg_hash",
        "resolved_python_binary_hash",
        "runner_hash",
        "runner_script_hash",
        "admission_sha256",
        "qualification_evidence_sha256",
    )
    @classmethod
    def validate_hash(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _digest(value, getattr(info, "field_name", "runtime hash"))

    @model_validator(mode="after")
    def validate_identity(self) -> ChronosRuntimeIdentity:
        if self.candidate_name != CHRONOS_MODEL:
            raise ValueError("runtime admission is not for Chronos-2-small")
        if self.native_context_min <= 0 or self.native_context_min > self.native_context_max:
            raise ValueError("Chronos native context range is invalid")
        if not self.native_context_min <= CHRONOS_CONTEXT_BARS <= self.native_context_max:
            raise ValueError("Chronos runtime is incompatible with the 48-bar V3-Core context")
        if self.output_bars != CHRONOS_OUTPUT_BARS:
            raise ValueError("Chronos output contract is not the qualified 30-value contract")
        if self.device.strip().lower() != "cuda":
            raise ValueError("Chronos qualification is not bound to its CUDA runtime")
        if not self.dependencies:
            raise ValueError("Chronos runtime admission has no dependency identity")
        return self

    @property
    def preprocessing_hash(self) -> str:
        return _sha256_bytes(CHRONOS_PREPROCESSING_IDENTITY.encode())

    @property
    def model_identity_hash(self) -> str:
        return _sha256_bytes(
            _canonical(
                {
                    "schema": CHRONOS_PREDICTION_IDENTITY_SCHEMA,
                    **self.model_dump(mode="json"),
                }
            )
        )

    @classmethod
    def from_admission(
        cls,
        admission_path: Path,
        *,
        qualification_evidence_path: Path,
        repository_root: Path,
    ) -> ChronosRuntimeIdentity:
        admission_path = admission_path.resolve()
        qualification_evidence_path = qualification_evidence_path.resolve()
        if not admission_path.is_file() or not qualification_evidence_path.is_file():
            raise ValueError("Chronos admission or qualification evidence is missing")
        admission_payload = json.loads(admission_path.read_text(encoding="utf-8"))
        admission = LocalCandidateAdmission.model_validate(admission_payload)
        candidate = next(
            (item for item in default_runtime_candidates() if item.name == CHRONOS_MODEL), None
        )
        if candidate is None:
            raise ValueError("Chronos-2-small is absent from the registered candidate roster")
        admitted_candidate = apply_local_candidate_admission(candidate, admission)
        checkpoint = admitted_candidate.external_checkpoint
        runtime = admitted_candidate.runtime_pin
        if checkpoint is None or runtime is None:
            raise ValueError("Chronos admission is missing checkpoint/runtime identity")
        verify_checkpoint_artifacts(checkpoint, repository_root=repository_root)
        verify_runtime_pin(runtime, repository_root=repository_root)

        qualification_payload = json.loads(qualification_evidence_path.read_text(encoding="utf-8"))
        if not isinstance(qualification_payload, Mapping):
            raise ValueError("Chronos qualification evidence must be an object")
        qualification_candidate = qualification_payload.get("candidate")
        qualification_resource = qualification_payload.get("resource")
        if not isinstance(qualification_resource, Mapping):
            raise ValueError("Chronos qualification evidence has no resource result")
        if (
            qualification_payload.get("status") != "measured"
            or not isinstance(qualification_candidate, Mapping)
            or qualification_candidate.get("name") != CHRONOS_MODEL
            or qualification_payload.get("network_access_attempted") is not False
            or qualification_payload.get("offline_cached_inference") is not True
            or qualification_payload.get("output_schema_valid") is not True
            or qualification_payload.get("output_shape") != "forecast[30]"
            or qualification_resource.get("resource_limit_passed") is not True
        ):
            raise ValueError("Chronos role qualification evidence is not a passing offline record")
        environment = qualification_payload.get("environment")
        if not isinstance(environment, Mapping) or environment.get("device") != "cuda":
            raise ValueError("Chronos role qualification is not bound to the CUDA device")

        artifacts = {
            item.relative_path: item.sha256
            for item in checkpoint.repository.runtime_artifacts
            if item.sha256 is not None
        }
        config_hash = artifacts.get("config.json")
        checkpoint_hash = artifacts.get("model.safetensors")
        if not config_hash or not checkpoint_hash:
            raise ValueError("Chronos admission is missing pinned config/checkpoint hashes")
        config_path = (
            Path(checkpoint.cache_path).expanduser().resolve(strict=True)
            / checkpoint.repository.cache_subdir
            / "config.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        chronos_config = config.get("chronos_config")
        native_context = (
            chronos_config.get("context_length") if isinstance(chronos_config, Mapping) else None
        )
        if not isinstance(native_context, int):
            raise ValueError("Chronos config has no integer native context length")
        if native_context != CHRONOS_NATIVE_CONTEXT_MAX:
            raise ValueError("Chronos config native context differs from the qualified identity")
        return cls(
            candidate_name=CHRONOS_MODEL,
            checkpoint_repository=checkpoint.repository.repository_id,
            checkpoint_revision=checkpoint.repository.revision,
            cache_path=checkpoint.cache_path,
            cache_subdir=checkpoint.repository.cache_subdir,
            config_hash=_digest(config_hash, "Chronos config hash"),
            checkpoint_hash=_digest(checkpoint_hash, "Chronos checkpoint hash"),
            dependencies=tuple(runtime.dependencies),
            environment_fingerprint=_digest(runtime.environment_fingerprint or "", "environment"),
            installed_environment_manifest_path=str(runtime.installed_environment_manifest_path),
            installed_environment_sha256=_digest(
                runtime.installed_environment_sha256 or "", "installed environment"
            ),
            lock_artifact_path=str(runtime.lock_artifact_path),
            lock_hash=_digest(runtime.lock_hash or "", "lock hash"),
            python_constraint=runtime.python_constraint,
            python_launcher=str(runtime.python_launcher or runtime.python_executable),
            python_launcher_hash=_digest(
                runtime.python_launcher_hash or runtime.python_executable_hash or "",
                "Python launcher hash",
            ),
            python_launcher_target=runtime.python_launcher_target,
            pyvenv_cfg_hash=runtime.pyvenv_cfg_hash,
            resolved_python_binary_hash=_digest(
                runtime.resolved_python_binary_hash or runtime.python_executable_hash or "",
                "resolved Python hash",
            ),
            runner_version=str(runtime.runner_version),
            runner_hash=_digest(runtime.runner_hash or "", "runner hash"),
            runner_script=str(runtime.worker_script),
            runner_script_hash=_sha256_file(Path(str(runtime.worker_script))),
            admission_path=str(admission_path),
            admission_sha256=_sha256_file(admission_path),
            qualification_evidence_path=str(qualification_evidence_path),
            qualification_evidence_sha256=_sha256_file(qualification_evidence_path),
            device=str(environment["device"]),
            native_context_min=CHRONOS_NATIVE_CONTEXT_MIN,
            native_context_max=native_context,
            output_bars=CHRONOS_OUTPUT_BARS,
        )


class ChronosInferenceResult(BaseModel):
    """Sanitized output and resource sample from one isolated inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast: tuple[Decimal, ...]
    native_lower: tuple[Decimal, ...] = ()
    native_upper: tuple[Decimal, ...] = ()
    latency_ms: Decimal = Field(ge=0)
    device: str
    resource_peak_rss_mib: Decimal = Field(ge=0)
    resource_peak_cpu_percent: Decimal = Field(ge=0)
    resource_sample_count: int = Field(ge=0)

    @field_validator("forecast", "native_lower", "native_upper")
    @classmethod
    def finite_forecast(cls, value: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not item.is_finite() for item in value):
            raise ValueError("Chronos forecast values must be finite")
        return value

    @model_validator(mode="after")
    def validate_intervals(self) -> ChronosInferenceResult:
        if bool(self.native_lower) != bool(self.native_upper):
            raise ValueError("Chronos native intervals require both bounds")
        if self.native_lower and (
            len(self.native_lower) != len(self.native_upper)
            or any(
                lower > upper
                for lower, upper in zip(self.native_lower, self.native_upper, strict=True)
            )
        ):
            raise ValueError("Chronos native interval bounds are inconsistent")
        return self


def _input_snapshot_hash(context: Sequence[V3CoreBar], cutoff: datetime) -> str:
    return _sha256_bytes(
        _canonical(
            {
                "schema": "advisorai.phase4.v3-core-forward.prediction-input.v1",
                "cutoff": _aware(cutoff, "cutoff").isoformat(),
                "context": [bar.model_dump(mode="json") for bar in context],
            }
        )
    )


def _return_bps(price: Decimal, last_close: Decimal) -> Decimal:
    return (price / last_close - Decimal("1")) * Decimal("10000")


def context_for_cutoff(
    bars: Sequence[V3CoreBar], *, instrument: str, cutoff: datetime, now: datetime
) -> tuple[V3CoreBar, ...] | None:
    """Return exactly 48 healthy forward bars available by the cutoff."""

    cutoff = _aware(cutoff, "cutoff")
    now = _aware(now, "now")
    if now > cutoff:
        return None
    normalized_instrument = instrument.strip().upper()
    if normalized_instrument not in V3_CORE_SYMBOLS:
        raise ValueError("Chronos predictions are restricted to BTCUSDT and ETHUSDT")
    by_end = {
        bar.interval_end: bar
        for bar in bars
        if bar.instrument == normalized_instrument
        and bar.evidence_class == "forward_pit_admission"
        and bar.collected_at <= cutoff
        and bar.provider_available_at <= cutoff
        and bar.provenance.source_health_state == "HEALTHY"
    }
    context_times = tuple(
        cutoff - timedelta(seconds=FORWARD_INTERVAL_SECONDS * (CHRONOS_CONTEXT_BARS - index))
        for index in range(CHRONOS_CONTEXT_BARS)
    )
    context = tuple(by_end.get(item) for item in context_times)
    if any(item is None for item in context):
        return None
    resolved = tuple(item for item in context if item is not None)
    if any(
        prior.interval_end + timedelta(seconds=FORWARD_INTERVAL_SECONDS) != current.interval_end
        for prior, current in zip(resolved, resolved[1:], strict=False)
    ):
        return None
    if len({bar.source_snapshot_hash for bar in resolved}) != 1:
        return None
    return resolved


def _request_for_inference(
    identity: ChronosRuntimeIdentity, context: Sequence[V3CoreBar]
) -> dict[str, object]:
    if len(context) != CHRONOS_CONTEXT_BARS:
        raise ValueError("Chronos inference requires exactly 48 context bars")
    values = [float(bar.close) for bar in context]
    return {
        "trust_remote_code": False,
        "local_files_only": True,
        "lock_artifact_path": identity.lock_artifact_path,
        "lock_hash": identity.lock_hash,
        "python_launcher_hash": identity.python_launcher_hash,
        "python_launcher_target": identity.python_launcher_target,
        "resolved_python_binary_hash": identity.resolved_python_binary_hash,
        "pyvenv_cfg_hash": identity.pyvenv_cfg_hash,
        "installed_environment_manifest_path": identity.installed_environment_manifest_path,
        "installed_environment_sha256": identity.installed_environment_sha256,
        "dependencies": list(identity.dependencies),
        "runner_version": identity.runner_version,
        "runner_hash": identity.runner_hash,
        "environment_fingerprint": identity.environment_fingerprint,
        "python_constraint": identity.python_constraint,
        "family": CHRONOS_MODEL,
        "worker_kind": CHRONOS_MODEL,
        "cache_path": identity.cache_path,
        "sample_input": values,
        "batch_input": [values],
        "repeats": 2,
        "repeatability_policy": "deterministic_required",
    }


def infer_chronos(
    *, identity: ChronosRuntimeIdentity, context: Sequence[V3CoreBar], timeout_seconds: float
) -> ChronosInferenceResult:
    """Invoke the exact offline worker with a minimal environment."""

    request = _request_for_inference(identity, context)
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - psutil is a core dependency
        raise ChronosInferenceFailure("ResourceMonitorUnavailable") from exc
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUNBUFFERED": "1",
    }
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [identity.python_launcher, identity.runner_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=environment,
            start_new_session=True,
            text=True,
        )
        if process.stdin is None:
            raise OSError("worker stdin unavailable")
        process.stdin.write(json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n")
        process.stdin.flush()
    except OSError as exc:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            process.wait()
            process.communicate()
        raise ChronosInferenceFailure("WorkerProcessError") from exc

    assert process is not None

    monitor = psutil.Process(process.pid)
    peak_rss_mib = 0.0
    peak_cpu_percent = 0.0
    resource_sample_count = 0

    def sample_resources() -> None:
        nonlocal peak_rss_mib, peak_cpu_percent, resource_sample_count
        try:
            processes = [monitor, *monitor.children(recursive=True)]
            rss_bytes = 0
            cpu_percent = 0.0
            for child in processes:
                try:
                    rss_bytes += int(child.memory_info().rss)
                    cpu_percent += float(child.cpu_percent(interval=None))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            peak_rss_mib = max(peak_rss_mib, rss_bytes / (1024**2))
            peak_cpu_percent = max(peak_cpu_percent, cpu_percent)
            resource_sample_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

    try:
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            sample_resources()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                process.communicate()
                raise ChronosInferenceFailure("WorkerTimeout")
            time.sleep(min(0.1, remaining))
        sample_resources()
        stdout, _stderr = process.communicate()
    except OSError as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        process.communicate()
        raise ChronosInferenceFailure("WorkerProcessError") from exc
    if process.returncode != 0:
        raise ChronosInferenceFailure("WorkerProcessError")
    try:
        response = json.loads(stdout.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChronosInferenceFailure("MalformedWorkerResponse") from exc
    if not isinstance(response, Mapping):
        raise ChronosInferenceFailure("MalformedWorkerResponse")
    if response.get("network_access_attempted") is True:
        raise ChronosInferenceFailure("NetworkAccessAttemptError")
    if response.get("error_class") is not None:
        raise ChronosInferenceFailure(str(response["error_class"]))
    worker_identity = response.get("identity")
    if not isinstance(worker_identity, Mapping):
        raise ChronosInferenceFailure("MissingWorkerIdentity")
    if (
        worker_identity.get("model_family") != CHRONOS_MODEL
        or worker_identity.get("runner_hash") != identity.runner_hash
        or worker_identity.get("environment_fingerprint") != identity.environment_fingerprint
    ):
        raise ChronosInferenceFailure("WorkerIdentityMismatch")
    batch_output = response.get("batch_output")
    if (
        not isinstance(batch_output, list)
        or len(batch_output) != 1
        or not isinstance(batch_output[0], list)
    ):
        raise ChronosInferenceFailure("MalformedForecastOutput")
    forecast = tuple(_finite_decimal(value, "Chronos forecast") for value in batch_output[0])
    if len(forecast) != CHRONOS_OUTPUT_BARS:
        raise ChronosInferenceFailure("ForecastLengthMismatch")

    def _interval(name: str) -> tuple[Decimal, ...]:
        value = response.get(name)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], list):
            return ()
        return tuple(_finite_decimal(item, f"Chronos {name}") for item in value[0])

    lower = _interval("forecast_batch_lower")
    upper = _interval("forecast_batch_upper")
    if lower and (len(lower) != CHRONOS_OUTPUT_BARS or len(upper) != CHRONOS_OUTPUT_BARS):
        raise ChronosInferenceFailure("IntervalLengthMismatch")
    try:
        latency = _finite_decimal(response.get("batch_inference_ms"), "Chronos latency")
    except ValueError as exc:
        raise ChronosInferenceFailure("MalformedLatency") from exc
    return ChronosInferenceResult(
        forecast=forecast,
        native_lower=lower,
        native_upper=upper,
        latency_ms=latency,
        device=identity.device,
        resource_peak_rss_mib=Decimal(str(peak_rss_mib)),
        resource_peak_cpu_percent=Decimal(str(peak_cpu_percent)),
        resource_sample_count=resource_sample_count,
    )


def build_chronos_prediction(
    *,
    identity: ChronosRuntimeIdentity,
    instrument: str,
    cutoff: datetime,
    generated_at: datetime,
    context: Sequence[V3CoreBar],
    result: ChronosInferenceResult,
) -> ForwardPredictionRecord:
    """Convert one native Chronos output into the shared prediction schema."""

    cutoff = _aware(cutoff, "cutoff")
    generated_at = _aware(generated_at, "generated_at")
    if generated_at > cutoff:
        raise ValueError("Chronos prediction completed after its cutoff")
    if len(context) != CHRONOS_CONTEXT_BARS:
        raise ValueError("Chronos prediction requires exactly 48 context bars")
    if len(result.forecast) < CHRONOS_HORIZON_BARS:
        raise ValueError("Chronos output is shorter than the one-hour horizon")
    normalized_instrument = instrument.strip().upper()
    if normalized_instrument not in V3_CORE_SYMBOLS:
        raise ValueError("Chronos predictions are restricted to BTCUSDT and ETHUSDT")
    if any(bar.provenance.source_health_state != "HEALTHY" for bar in context):
        raise ValueError("Chronos context contains a non-healthy source bar")
    source_hashes = {bar.source_snapshot_hash for bar in context}
    if len(source_hashes) != 1:
        raise ValueError("Chronos context contains multiple source snapshots")
    last_close = context[-1].close
    index = CHRONOS_HORIZON_BARS - 1
    lower = _return_bps(result.native_lower[index], last_close) if result.native_lower else None
    upper = _return_bps(result.native_upper[index], last_close) if result.native_upper else None
    source_hash = next(iter(source_hashes))
    provenance = tuple(
        sorted(
            {
                "availability_basis": context[-1].provenance.availability_basis,
                "chronos_native_context_max": str(identity.native_context_max),
                "chronos_native_context_min": str(identity.native_context_min),
                "chronos_output_bars": str(identity.output_bars),
                "evidence_class": context[-1].evidence_class,
                "model_identity_hash": identity.model_identity_hash,
                "provider_identity": context[-1].provider_identity,
                "qualification_evidence_sha256": identity.qualification_evidence_sha256,
                "source_snapshot_hash": source_hash,
                "v3core_context_bars": str(CHRONOS_CONTEXT_BARS),
                "v3core_horizon_bars": str(CHRONOS_HORIZON_BARS),
            }.items()
        )
    )
    return ForwardPredictionRecord(
        prediction_id=f"{normalized_instrument}:{cutoff.isoformat()}:{CHRONOS_MODEL}",
        instrument=normalized_instrument,
        model=CHRONOS_MODEL,
        model_identity_hash=identity.model_identity_hash,
        cutoff=cutoff,
        input_snapshot_hash=_input_snapshot_hash(context, cutoff),
        predicted_return_bps=_return_bps(result.forecast[index], last_close),
        generated_at=generated_at,
        runtime_latency_ms=result.latency_ms,
        source_snapshot_hash=source_hash,
        checkpoint_hash=identity.checkpoint_hash,
        runner_hash=identity.runner_hash,
        preprocessing_identity=CHRONOS_PREPROCESSING_IDENTITY,
        preprocessing_hash=identity.preprocessing_hash,
        dependency_lock_hash=identity.lock_hash,
        runtime_environment_hash=identity.environment_fingerprint,
        device=result.device,
        native_interval_lower_bps=lower,
        native_interval_upper_bps=upper,
        resource_peak_rss_mib=result.resource_peak_rss_mib,
        resource_peak_cpu_percent=result.resource_peak_cpu_percent,
        resource_sample_count=result.resource_sample_count,
        provenance=provenance,
    )


def _git_head(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _source_manifest(source_root: Path) -> dict[str, object]:
    value = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("source manifest must be a JSON object")
    if value.get("credentials_loaded") is not False:
        raise ValueError("source root is not explicitly credential-free")
    if value.get("order_writes_attempted") is not False:
        raise ValueError("source root is not explicitly order-write-free")
    if value.get("provider_identity") != V3_CORE_MARKET_DATA_PROVIDER:
        raise ValueError("Chronos requires the admitted Binance public market-data source")
    if value.get("endpoint") != V3_CORE_MARKET_DATA_REST_ENDPOINT:
        raise ValueError("Chronos requires the reviewed Binance market-data endpoint")
    return value


def expected_manifest(
    *,
    source_root: Path,
    source_manifest: Mapping[str, object],
    admission_path: Path,
    qualification_evidence_path: Path,
    repository_root: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    until: datetime,
    identity: ChronosRuntimeIdentity,
) -> dict[str, object]:
    source_snapshot_hash = source_manifest.get("source_snapshot_hash")
    if not isinstance(source_snapshot_hash, str):
        raise ValueError("source manifest has no source snapshot hash")
    script_path = repository_root / "scripts/run_phase4_v3core_chronos_predictions.py"
    if not script_path.is_file():
        raise ValueError("Chronos prediction runner script is missing")
    return {
        "schema": CHRONOS_RUN_SCHEMA,
        "source_root": str(source_root),
        "source_manifest_sha256": _sha256_file(source_root / "manifest.json"),
        "source_snapshot_hash": _digest(source_snapshot_hash, "source snapshot hash"),
        "source_provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
        "admission_path": str(admission_path),
        "admission_sha256": identity.admission_sha256,
        "qualification_evidence_path": str(qualification_evidence_path),
        "qualification_evidence_sha256": identity.qualification_evidence_sha256,
        "preregistration_sha256": _digest(preregistration_sha256, "preregistration hash"),
        "phase3_gate_record_sha256": _digest(phase3_gate_sha256, "Phase-3 gate hash"),
        "repository_commit": _git_head(repository_root),
        "prediction_code_sha256": _sha256_file(Path(__file__).resolve()),
        "prediction_script_sha256": _sha256_file(script_path),
        "runner_script_hash": identity.runner_script_hash,
        "runner_hash": identity.runner_hash,
        "checkpoint_hash": identity.checkpoint_hash,
        "preprocessing_identity": CHRONOS_PREPROCESSING_IDENTITY,
        "preprocessing_hash": identity.preprocessing_hash,
        "model_identity_hash": identity.model_identity_hash,
        "model_identity": identity.model_dump(mode="json"),
        "device": identity.device,
        "native_context_min": identity.native_context_min,
        "native_context_max": identity.native_context_max,
        "output_bars": identity.output_bars,
        "context_bars": CHRONOS_CONTEXT_BARS,
        "horizon_bars": CHRONOS_HORIZON_BARS,
        "cadence": {
            "interval_seconds": FORWARD_INTERVAL_SECONDS,
            "horizon_bars": CHRONOS_HORIZON_BARS,
        },
        "target_end_at": _aware(until, "until").isoformat(),
    }


def validate_resume_manifest(
    manifest: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    mismatches = [
        field
        for field in CHRONOS_RESUME_IDENTITY_FIELDS
        if manifest.get(field) != expected.get(field)
    ]
    if mismatches:
        raise ValueError("Chronos prediction resume identity mismatch: " + ", ".join(mismatches))


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n")
    os.replace(temporary, path)


def _status(
    *,
    state: str,
    ledger: ForwardPredictionLedger,
    rejections: ForwardRejectionSpool,
    identity: ChronosRuntimeIdentity,
    updated_at: datetime | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": f"{CHRONOS_RUN_SCHEMA}.status",
        "state": state,
        "updated_at": (updated_at or datetime.now(UTC)).isoformat(),
        "pid": os.getpid(),
        "model": CHRONOS_MODEL,
        "model_identity_hash": identity.model_identity_hash,
        "prediction_count": len(ledger.records),
        "rejection_count": len(rejections.records),
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    if extra:
        result.update(extra)
    return result


def _candidate_cutoffs(bars: Sequence[V3CoreBar], symbol: str) -> tuple[datetime, ...]:
    return tuple(
        sorted(
            {
                bar.interval_end + timedelta(seconds=FORWARD_INTERVAL_SECONDS)
                for bar in bars
                if bar.instrument == symbol
                and (bar.interval_end + timedelta(seconds=FORWARD_INTERVAL_SECONDS)).minute % 60
                == 0
                and (bar.interval_end + timedelta(seconds=FORWARD_INTERVAL_SECONDS)).second == 0
                and (bar.interval_end + timedelta(seconds=FORWARD_INTERVAL_SECONDS)).microsecond
                == 0
            }
        )
    )


def run(
    *,
    admission_path: Path,
    qualification_evidence_path: Path,
    source_root: Path,
    run_root: Path,
    repository_root: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    until: datetime,
    poll_seconds: float = CHRONOS_POLL_SECONDS,
    worker_timeout_seconds: float = CHRONOS_WORKER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Run the durable prospective Chronos candidate worker."""

    admission_path = admission_path.resolve()
    qualification_evidence_path = qualification_evidence_path.resolve()
    source_root = source_root.resolve()
    run_root = run_root.resolve()
    repository_root = repository_root.resolve()
    until = _aware(until, "until")
    if poll_seconds <= 0 or worker_timeout_seconds <= 0:
        raise ValueError("poll and worker timeout values must be positive")
    identity = ChronosRuntimeIdentity.from_admission(
        admission_path,
        qualification_evidence_path=qualification_evidence_path,
        repository_root=repository_root,
    )
    source_manifest = _source_manifest(source_root)
    if source_manifest.get("preregistration_sha256") != preregistration_sha256:
        raise ValueError("source root is bound to a different preregistration")
    if source_manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise ValueError("source root is bound to a different Phase-3 gate")
    expected = expected_manifest(
        source_root=source_root,
        source_manifest=source_manifest,
        admission_path=admission_path,
        qualification_evidence_path=qualification_evidence_path,
        repository_root=repository_root,
        preregistration_sha256=preregistration_sha256,
        phase3_gate_sha256=phase3_gate_sha256,
        until=until,
        identity=identity,
    )
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "manifest.json"
    if manifest_path.exists():
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest_value, Mapping):
            raise ValueError("Chronos prediction manifest must be an object")
        validate_resume_manifest(manifest_value, expected)
    else:
        if any(run_root.iterdir()):
            raise ValueError("Chronos run root is non-empty without a frozen manifest")
        _write_atomic(
            manifest_path,
            {
                **expected,
                "started_at": datetime.now(UTC).isoformat(),
                "network_calls": 0,
                "credentials_loaded": False,
                "order_writes_attempted": False,
            },
        )
    ledger = ForwardPredictionLedger(run_root / "predictions.jsonl")
    rejections = ForwardRejectionSpool(run_root / "rejections.jsonl")
    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    prior_term = signal.getsignal(signal.SIGTERM)
    prior_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop and datetime.now(UTC) < until:
            now = datetime.now(UTC)
            bars = ForwardNormalizedBarSpool(source_root / "normalized-bars.jsonl").read()
            for symbol in V3_CORE_SYMBOLS:
                for cutoff in _candidate_cutoffs(bars, symbol):
                    if any(
                        record.instrument == symbol and record.cutoff == cutoff
                        for record in rejections.records
                    ) or any(
                        record.prediction.instrument == symbol
                        and record.prediction.cutoff == cutoff
                        for record in ledger.records
                    ):
                        continue
                    if now > cutoff:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason="MISSED_FOR_CHRONOS_2_SMALL",
                        )
                        continue
                    context = context_for_cutoff(bars, instrument=symbol, cutoff=cutoff, now=now)
                    if context is None:
                        continue
                    try:
                        inference = infer_chronos(
                            identity=identity,
                            context=context,
                            timeout_seconds=worker_timeout_seconds,
                        )
                        generated_at = datetime.now(UTC)
                        if generated_at > cutoff:
                            rejections.append(
                                instrument=symbol,
                                cutoff=cutoff,
                                reason="INFERENCE_COMPLETED_AFTER_CUTOFF",
                            )
                            continue
                        ledger.append(
                            build_chronos_prediction(
                                identity=identity,
                                instrument=symbol,
                                cutoff=cutoff,
                                generated_at=generated_at,
                                context=context,
                                result=inference,
                            )
                        )
                    except ChronosInferenceFailure as exc:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason=f"INFERENCE_{exc.error_class.upper()}",
                        )
                    except ValueError:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason="INFERENCE_OUTPUT_CONTRACT_ERROR",
                        )
            _write_atomic(
                run_root / "status.json",
                _status(state="running", ledger=ledger, rejections=rejections, identity=identity),
            )
            time.sleep(poll_seconds)
    finally:
        signal.signal(signal.SIGTERM, prior_term)
        signal.signal(signal.SIGINT, prior_int)
    result = _status(
        state="stopped_with_evidence" if stop else "deadline_reached",
        ledger=ledger,
        rejections=rejections,
        identity=identity,
    )
    _write_atomic(run_root / "status.json", result)
    return result


__all__ = [
    "CHRONOS_CONTEXT_BARS",
    "CHRONOS_HORIZON_BARS",
    "CHRONOS_MODEL",
    "CHRONOS_NATIVE_CONTEXT_MAX",
    "CHRONOS_NATIVE_CONTEXT_MIN",
    "CHRONOS_OUTPUT_BARS",
    "CHRONOS_POLL_SECONDS",
    "CHRONOS_PREPROCESSING_IDENTITY",
    "ChronosInferenceFailure",
    "ChronosInferenceResult",
    "ChronosRuntimeIdentity",
    "build_chronos_prediction",
    "context_for_cutoff",
    "expected_manifest",
    "infer_chronos",
    "run",
    "validate_resume_manifest",
]
