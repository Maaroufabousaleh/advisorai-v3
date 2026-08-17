"""Prospective TTM-R2 prediction boundary for the V3-Core forward ledger.

This module is deliberately narrower than the execution and acquisition
surfaces.  It reads only the normalized forward bar spool, launches the exact
already-qualified offline runtime when the frozen input contract matches, and
writes immutable prediction or rejection evidence.  It has no credential
resolver, network client, account operation, order operation, or OMS access.

The currently qualified TTM-R2 worker is pinned to a 512-value context while
the V3-Core prospective contract supplies 48 closed five-minute bars.  That is
an identity/contract mismatch, not permission to pad or otherwise transform
the context.  The worker therefore quarantines itself until a separate
48-value runtime qualification exists.
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

from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
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

TTM_R2_MODEL = "ttm-r2"
TTM_R2_CONTEXT_BARS = 48
TTM_R2_HORIZON_BARS = 12
TTM_R2_OUTPUT_BARS = 96
QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS = 512
TTM_R2_PREPROCESSING_IDENTITY = "v3core-raw-close-48-direct-v1"
TTM_R2_RUN_SCHEMA = "advisorai.phase4.v3-core-forward.ttm-r2-predictions.v1"
TTM_R2_FAILURE_SCHEMA = f"{TTM_R2_RUN_SCHEMA}.failure"
TTM_R2_PREDICTION_CODE_SCHEMA = "advisorai.phase4.v3-core-forward.ttm-r2-identity.v1"
TTM_R2_POLL_SECONDS = 5.0
TTM_R2_WORKER_TIMEOUT_SECONDS = 120.0

TTM_RESUME_IDENTITY_FIELDS = (
    "schema",
    "source_root",
    "source_manifest_sha256",
    "source_snapshot_hash",
    "source_provider_identity",
    "admission_path",
    "admission_sha256",
    "preregistration_sha256",
    "phase3_gate_record_sha256",
    "repository_commit",
    "prediction_code_sha256",
    "prediction_script_sha256",
    "runner_script_sha256",
    "runner_hash",
    "checkpoint_hash",
    "preprocessing_identity",
    "preprocessing_hash",
    "model_identity_hash",
    "model_identity",
    "device",
    "context_bars",
    "horizon_bars",
    "qualified_runner_context_bars",
    "cadence",
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


def _launcher_identity_hash(path: Path) -> str:
    if path.is_symlink():
        return _sha256_bytes(os.readlink(path).encode())
    return _sha256_file(path)


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


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"admission is missing {field_name}")
    return value.strip()


def _required_mapping(payload: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"admission is missing {field_name}")
    return value


class TTMR2RuntimeContractMismatch(ValueError):
    """The qualified worker cannot consume the frozen V3-Core context."""

    def __init__(self, *, required: int, configured: int) -> None:
        self.required = required
        self.configured = configured
        super().__init__(
            "qualified TTM-R2 runner context contract mismatch "
            f"(runner={required}, v3core={configured})"
        )


class TTMR2InferenceFailure(RuntimeError):
    """A sanitized failure returned by the isolated TTM worker."""

    def __init__(self, error_class: str) -> None:
        self.error_class = error_class.strip() or "UnknownWorkerError"
        super().__init__(self.error_class)


class TTMR2RuntimeIdentity(BaseModel):
    """The exact immutable identity of the separately qualified TTM runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_name: str
    checkpoint_repository: str
    checkpoint_revision: str
    cache_path: str
    cache_subdir: str = "model"
    config_hash: str
    generation_config_hash: str
    checkpoint_hash: str
    dependencies: tuple[str, ...]
    environment_fingerprint: str
    installed_environment_manifest_path: str
    installed_environment_sha256: str
    lock_artifact_path: str
    lock_hash: str
    python_constraint: str
    python_executable: str
    python_executable_hash: str
    python_launcher: str
    python_launcher_hash: str
    python_launcher_target: str | None = None
    pyvenv_cfg_hash: str | None = None
    resolved_python_binary_hash: str
    runner_version: str
    runner_hash: str
    runner_script: str
    runner_script_hash: str
    worker_kind: str
    runner_context_bars: int = QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS
    output_bars: int = TTM_R2_OUTPUT_BARS
    device: str = "cpu"

    @field_validator(
        "config_hash",
        "generation_config_hash",
        "checkpoint_hash",
        "environment_fingerprint",
        "installed_environment_sha256",
        "lock_hash",
        "python_executable_hash",
        "python_launcher_hash",
        "pyvenv_cfg_hash",
        "resolved_python_binary_hash",
        "runner_hash",
        "runner_script_hash",
    )
    @classmethod
    def validate_hash(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _digest(value, getattr(info, "field_name", "runtime hash"))

    @field_validator("cache_path", "installed_environment_manifest_path", "lock_artifact_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("runtime identity paths must be non-empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_identity(self) -> TTMR2RuntimeIdentity:
        if self.candidate_name != TTM_R2_MODEL or self.worker_kind != TTM_R2_MODEL:
            raise ValueError("runtime admission is not for TTM-R2")
        if self.runner_context_bars <= 0 or self.output_bars != TTM_R2_OUTPUT_BARS:
            raise ValueError("runtime TTM output contract is invalid")
        if not self.dependencies:
            raise ValueError("runtime admission has no dependencies")
        return self

    @property
    def model_identity_hash(self) -> str:
        return _sha256_bytes(
            _canonical(
                {
                    "schema": TTM_R2_PREDICTION_CODE_SCHEMA,
                    **self.model_dump(mode="json"),
                }
            )
        )

    @property
    def preprocessing_hash(self) -> str:
        return _sha256_bytes(TTM_R2_PREPROCESSING_IDENTITY.encode())

    @classmethod
    def from_admission(cls, path: Path, *, verify_files: bool = True) -> TTMR2RuntimeIdentity:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("runtime admission must be a JSON object")
        if payload.get("schema_version") != "advisorai.phase0.local-candidate-admission.v1":
            raise ValueError("unsupported local candidate admission schema")
        checkpoint = _required_mapping(payload, "checkpoint")
        repository = _required_mapping(checkpoint, "repository")
        runtime = _required_mapping(payload, "runtime_pin")
        artifacts_value = repository.get("runtime_artifacts")
        if not isinstance(artifacts_value, list):
            raise ValueError("runtime admission has no runtime artifacts")
        artifacts: dict[str, str] = {}
        for artifact in artifacts_value:
            if not isinstance(artifact, Mapping):
                raise ValueError("runtime artifact is malformed")
            relative_path = _required_string(artifact, "relative_path")
            artifacts[relative_path] = _required_string(artifact, "sha256")
        for required in ("config.json", "generation_config.json", "model.safetensors"):
            if required not in artifacts:
                raise ValueError(f"runtime admission is missing {required}")
        if _required_string(payload, "candidate_name") != TTM_R2_MODEL:
            raise ValueError("runtime admission candidate is not TTM-R2")
        if _required_string(runtime, "status") != "approved":
            raise ValueError("TTM-R2 runtime admission is not approved")
        if _required_string(runtime, "worker_kind") != TTM_R2_MODEL:
            raise ValueError("TTM-R2 worker kind is not admitted")
        worker_script = Path(_required_string(runtime, "worker_script"))
        runner_script_hash = _sha256_file(worker_script) if worker_script.is_file() else "0" * 64
        identity = cls(
            candidate_name=TTM_R2_MODEL,
            checkpoint_repository=_required_string(repository, "repository_id"),
            checkpoint_revision=_required_string(repository, "revision"),
            cache_path=_required_string(checkpoint, "cache_path"),
            cache_subdir=_required_string(repository, "cache_subdir"),
            config_hash=_digest(artifacts["config.json"], "config hash"),
            generation_config_hash=_digest(
                artifacts["generation_config.json"], "generation config hash"
            ),
            checkpoint_hash=_digest(artifacts["model.safetensors"], "checkpoint hash"),
            dependencies=tuple(str(item) for item in runtime.get("dependencies", [])),
            environment_fingerprint=_digest(
                _required_string(runtime, "environment_fingerprint"), "environment fingerprint"
            ),
            installed_environment_manifest_path=_required_string(
                runtime, "installed_environment_manifest_path"
            ),
            installed_environment_sha256=_digest(
                _required_string(runtime, "installed_environment_sha256"),
                "installed environment hash",
            ),
            lock_artifact_path=_required_string(runtime, "lock_artifact_path"),
            lock_hash=_digest(_required_string(runtime, "lock_hash"), "lock hash"),
            python_constraint=_required_string(runtime, "python_constraint"),
            python_executable=_required_string(runtime, "python_executable"),
            python_executable_hash=_digest(
                _required_string(runtime, "python_executable_hash"), "Python executable hash"
            ),
            python_launcher=_required_string(runtime, "python_launcher"),
            python_launcher_hash=_digest(
                _required_string(runtime, "python_launcher_hash"), "Python launcher hash"
            ),
            python_launcher_target=(
                str(runtime["python_launcher_target"])
                if runtime.get("python_launcher_target") is not None
                else None
            ),
            pyvenv_cfg_hash=(
                _digest(str(runtime["pyvenv_cfg_hash"]), "pyvenv.cfg hash")
                if runtime.get("pyvenv_cfg_hash") is not None
                else None
            ),
            resolved_python_binary_hash=_digest(
                _required_string(runtime, "resolved_python_binary_hash"),
                "resolved Python binary hash",
            ),
            runner_version=_required_string(runtime, "runner_version"),
            runner_hash=_digest(_required_string(runtime, "runner_hash"), "runner hash"),
            runner_script=str(worker_script),
            runner_script_hash=runner_script_hash,
            worker_kind=TTM_R2_MODEL,
        )
        if verify_files:
            identity.verify_files()
        return identity

    def verify_files(self) -> None:
        """Verify the files named by the admission without loading model code."""

        cache_root = Path(self.cache_path).expanduser().resolve(strict=True)
        model_root = cache_root / self.cache_subdir
        if not model_root.is_dir():
            raise ValueError("admitted TTM model cache directory is missing")
        expected_artifacts = {
            "config.json": self.config_hash,
            "generation_config.json": self.generation_config_hash,
            "model.safetensors": self.checkpoint_hash,
        }
        for name, expected in expected_artifacts.items():
            path = model_root / name
            if not path.is_file() or _sha256_file(path) != expected:
                raise ValueError(f"admitted TTM artifact hash mismatch: {name}")
        lock_path = Path(self.lock_artifact_path).expanduser().resolve(strict=True)
        if _sha256_file(lock_path) != self.lock_hash:
            raise ValueError("admitted TTM lock hash mismatch")
        environment_manifest = (
            Path(self.installed_environment_manifest_path).expanduser().resolve(strict=True)
        )
        if _sha256_file(environment_manifest) != self.installed_environment_sha256:
            raise ValueError("admitted TTM environment manifest hash mismatch")
        launcher = Path(self.python_launcher).expanduser().resolve(strict=False)
        launcher_path = Path(self.python_launcher).expanduser()
        if not launcher_path.is_file():
            raise ValueError("admitted TTM Python launcher is missing")
        if _launcher_identity_hash(launcher_path) != self.python_launcher_hash:
            raise ValueError("admitted TTM Python launcher hash mismatch")
        if not launcher.is_file() or _sha256_file(launcher) != self.resolved_python_binary_hash:
            raise ValueError("admitted TTM Python binary hash mismatch")
        executable = Path(self.python_executable).expanduser().resolve(strict=True)
        if _sha256_file(executable) != self.python_executable_hash:
            raise ValueError("admitted TTM Python executable hash mismatch")
        if self.python_launcher_target != (str(launcher) if launcher_path.is_symlink() else None):
            raise ValueError("admitted TTM Python launcher target mismatch")
        if self.pyvenv_cfg_hash is not None:
            cfg_path = launcher_path.parent.parent / "pyvenv.cfg"
            if not cfg_path.is_file() or _sha256_file(cfg_path) != self.pyvenv_cfg_hash:
                raise ValueError("admitted TTM pyvenv.cfg hash mismatch")
        worker_script = Path(self.runner_script).expanduser().resolve(strict=True)
        if _sha256_file(worker_script) != self.runner_script_hash:
            raise ValueError("admitted TTM worker script hash mismatch")
        runner_hash = _sha256_bytes(f"{self.runner_version}\n{self.runner_script_hash}".encode())
        if runner_hash != self.runner_hash:
            raise ValueError("admitted TTM runner hash mismatch")


class TTMR2InferenceResult(BaseModel):
    """Sanitized output needed to build one immutable prediction record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast: tuple[Decimal, ...]
    latency_ms: Decimal = Field(ge=0)
    device: str = "cpu"
    native_lower: tuple[Decimal, ...] = ()
    native_upper: tuple[Decimal, ...] = ()
    native_confidence: Decimal | None = None
    resource_peak_rss_mib: Decimal | None = None
    resource_peak_cpu_percent: Decimal | None = None
    resource_sample_count: int = Field(default=0, ge=0)

    @field_validator("forecast", "native_lower", "native_upper")
    @classmethod
    def finite_forecasts(cls, value: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not item.is_finite() for item in value):
            raise ValueError("TTM forecast values must be finite")
        return value

    @field_validator(
        "latency_ms",
        "native_confidence",
        "resource_peak_rss_mib",
        "resource_peak_cpu_percent",
    )
    @classmethod
    def finite_numbers(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("TTM runtime values must be finite")
        return value

    @model_validator(mode="after")
    def validate_intervals(self) -> TTMR2InferenceResult:
        if bool(self.native_lower) != bool(self.native_upper) or len(self.native_lower) != len(
            self.native_upper
        ):
            raise ValueError("native TTM intervals must have matching bounds")
        return self


def _preprocessing_hash() -> str:
    return _sha256_bytes(TTM_R2_PREPROCESSING_IDENTITY.encode())


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


def build_ttm_prediction(
    *,
    identity: TTMR2RuntimeIdentity,
    instrument: str,
    cutoff: datetime,
    generated_at: datetime,
    context: Sequence[V3CoreBar],
    result: TTMR2InferenceResult,
) -> ForwardPredictionRecord:
    """Convert one exact runtime output into the shared prediction schema."""

    cutoff = _aware(cutoff, "cutoff")
    generated_at = _aware(generated_at, "generated_at")
    if generated_at > cutoff:
        raise ValueError("TTM prediction completed after its cutoff")
    if len(context) != TTM_R2_CONTEXT_BARS:
        raise ValueError("TTM prediction requires exactly 48 context bars")
    if len(result.forecast) < TTM_R2_HORIZON_BARS:
        raise ValueError("TTM output is shorter than the one-hour horizon")
    normalized_instrument = instrument.strip().upper()
    if normalized_instrument not in V3_CORE_SYMBOLS:
        raise ValueError("TTM predictions are restricted to BTCUSDT and ETHUSDT")
    source_hashes = {bar.source_snapshot_hash for bar in context}
    if len(source_hashes) != 1:
        raise ValueError("TTM context contains multiple source snapshots")
    last_close = context[-1].close
    predicted_return = _return_bps(result.forecast[TTM_R2_HORIZON_BARS - 1], last_close)
    lower = (
        _return_bps(result.native_lower[TTM_R2_HORIZON_BARS - 1], last_close)
        if len(result.native_lower) >= TTM_R2_HORIZON_BARS
        else None
    )
    upper = (
        _return_bps(result.native_upper[TTM_R2_HORIZON_BARS - 1], last_close)
        if len(result.native_upper) >= TTM_R2_HORIZON_BARS
        else None
    )
    provenance = tuple(
        sorted(
            {
                "availability_basis": context[-1].provenance.availability_basis,
                "evidence_class": context[-1].evidence_class,
                "model_identity_hash": identity.model_identity_hash,
                "provider_identity": context[-1].provider_identity,
                "source_snapshot_hash": next(iter(source_hashes)),
                "v3core_context_bars": str(TTM_R2_CONTEXT_BARS),
                "v3core_horizon_bars": str(TTM_R2_HORIZON_BARS),
            }.items()
        )
    )
    return ForwardPredictionRecord(
        prediction_id=f"{normalized_instrument}:{cutoff.isoformat()}:{TTM_R2_MODEL}",
        instrument=normalized_instrument,
        model=TTM_R2_MODEL,
        model_identity_hash=identity.model_identity_hash,
        cutoff=cutoff,
        input_snapshot_hash=_input_snapshot_hash(context, cutoff),
        predicted_return_bps=predicted_return,
        generated_at=generated_at,
        runtime_latency_ms=result.latency_ms,
        source_snapshot_hash=next(iter(source_hashes)),
        checkpoint_hash=identity.checkpoint_hash,
        runner_hash=identity.runner_hash,
        preprocessing_identity=TTM_R2_PREPROCESSING_IDENTITY,
        preprocessing_hash=_preprocessing_hash(),
        dependency_lock_hash=identity.lock_hash,
        runtime_environment_hash=identity.environment_fingerprint,
        device=result.device,
        native_interval_lower_bps=lower,
        native_interval_upper_bps=upper,
        native_confidence=result.native_confidence,
        resource_peak_rss_mib=result.resource_peak_rss_mib,
        resource_peak_cpu_percent=result.resource_peak_cpu_percent,
        resource_sample_count=result.resource_sample_count,
        provenance=provenance,
    )


def context_for_cutoff(
    bars: Sequence[V3CoreBar], *, instrument: str, cutoff: datetime, now: datetime
) -> tuple[V3CoreBar, ...] | None:
    """Return only forward-observed, closed bars available by ``cutoff``."""

    cutoff = _aware(cutoff, "cutoff")
    now = _aware(now, "now")
    if now > cutoff:
        return None
    normalized_instrument = instrument.strip().upper()
    by_end = {
        bar.interval_end: bar
        for bar in bars
        if bar.instrument == normalized_instrument
        and bar.evidence_class == "forward_pit_admission"
        and bar.collected_at <= cutoff
    }
    context_times = tuple(
        cutoff - timedelta(seconds=FORWARD_INTERVAL_SECONDS * (TTM_R2_CONTEXT_BARS - index))
        for index in range(TTM_R2_CONTEXT_BARS)
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


def _git_head(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def expected_manifest(
    *,
    source_root: Path,
    source_manifest: Mapping[str, object],
    admission_path: Path,
    repository_root: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    identity: TTMR2RuntimeIdentity,
) -> dict[str, object]:
    """Build the complete frozen identity for a future worker resume."""

    source_snapshot_hash = source_manifest.get("source_snapshot_hash")
    if not isinstance(source_snapshot_hash, str):
        raise ValueError("source run manifest has no source snapshot hash")
    source_provider_identity = source_manifest.get("provider_identity")
    if source_provider_identity != V3_CORE_MARKET_DATA_PROVIDER:
        raise ValueError("TTM worker requires the admitted Binance public data source")
    module_hash = _sha256_file(Path(__file__).resolve())
    script_path = repository_root / "scripts/run_phase4_v3core_ttm_predictions.py"
    if not script_path.is_file():
        raise ValueError("TTM prediction runner script is missing")
    if not admission_path.is_file():
        raise ValueError("TTM runtime admission file is missing")
    return {
        "schema": TTM_R2_RUN_SCHEMA,
        "source_root": str(source_root),
        "source_manifest_sha256": _sha256_file(source_root / "manifest.json"),
        "source_snapshot_hash": _digest(source_snapshot_hash, "source snapshot hash"),
        "source_provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
        "admission_path": str(admission_path),
        "admission_sha256": _sha256_file(admission_path),
        "preregistration_sha256": _digest(preregistration_sha256, "preregistration hash"),
        "phase3_gate_record_sha256": _digest(phase3_gate_sha256, "Phase-3 gate hash"),
        "repository_commit": _git_head(repository_root),
        "prediction_code_sha256": module_hash,
        "prediction_script_sha256": _sha256_file(script_path),
        "runner_script_sha256": identity.runner_script_hash,
        "runner_hash": identity.runner_hash,
        "checkpoint_hash": identity.checkpoint_hash,
        "preprocessing_identity": TTM_R2_PREPROCESSING_IDENTITY,
        "preprocessing_hash": _preprocessing_hash(),
        "model_identity_hash": identity.model_identity_hash,
        "model_identity": identity.model_dump(mode="json"),
        "device": identity.device,
        "context_bars": TTM_R2_CONTEXT_BARS,
        "horizon_bars": TTM_R2_HORIZON_BARS,
        "qualified_runner_context_bars": identity.runner_context_bars,
        "cadence": {
            "interval_seconds": FORWARD_INTERVAL_SECONDS,
            "horizon_bars": TTM_R2_HORIZON_BARS,
        },
    }


def validate_resume_manifest(
    manifest: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    mismatches = [
        field for field in TTM_RESUME_IDENTITY_FIELDS if manifest.get(field) != expected.get(field)
    ]
    if mismatches:
        raise ValueError("TTM prediction resume identity mismatch: " + ", ".join(mismatches))


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n")
    os.replace(temporary, path)


def _write_immutable(path: Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    if path.exists():
        if path.read_bytes() != encoded:
            raise RuntimeError(f"immutable TTM evidence changed: {path.name}")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _request_for_inference(
    identity: TTMR2RuntimeIdentity, context: Sequence[V3CoreBar]
) -> dict[str, object]:
    if len(context) != TTM_R2_CONTEXT_BARS:
        raise ValueError("TTM inference requires exactly 48 context bars")
    if identity.runner_context_bars != TTM_R2_CONTEXT_BARS:
        raise TTMR2RuntimeContractMismatch(
            required=identity.runner_context_bars, configured=TTM_R2_CONTEXT_BARS
        )
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
        "family": TTM_R2_MODEL,
        "worker_kind": TTM_R2_MODEL,
        "cache_path": identity.cache_path,
        "sample_input": values,
        "batch_input": [values],
        "repeats": 2,
        "repeatability_policy": "seeded_reproducible",
        "repeatability_seed": 0,
    }


def infer_ttm_r2(
    *, identity: TTMR2RuntimeIdentity, context: Sequence[V3CoreBar], timeout_seconds: float
) -> TTMR2InferenceResult:
    """Invoke only the exact offline worker after its cadence contract passes."""

    request = _request_for_inference(identity, context)
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - core dependency is locked
        raise TTMR2InferenceFailure("ResourceMonitorUnavailable") from exc
    clean_environment = {
        "PATH": os.environ.get("PATH", ""),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUNBUFFERED": "1",
    }
    try:
        process = subprocess.Popen(
            [identity.python_launcher, identity.runner_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=clean_environment,
            start_new_session=True,
            text=True,
        )
        process.stdin.write(json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n")
        process.stdin.flush()
    except OSError as exc:
        raise TTMR2InferenceFailure("WorkerProcessError") from exc
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
                raise TTMR2InferenceFailure("WorkerTimeout")
            time.sleep(min(0.1, remaining))
        sample_resources()
        stdout, _stderr = process.communicate()
    except OSError as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        process.communicate()
        raise TTMR2InferenceFailure("WorkerProcessError") from exc
    if process.returncode != 0:
        raise TTMR2InferenceFailure("WorkerProcessError")
    try:
        response = json.loads(stdout.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise TTMR2InferenceFailure("MalformedWorkerResponse") from exc
    if not isinstance(response, Mapping):
        raise TTMR2InferenceFailure("MalformedWorkerResponse")
    if response.get("network_access_attempted") is True:
        raise TTMR2InferenceFailure("NetworkAccessAttemptError")
    error_class = response.get("error_class")
    if error_class is not None:
        raise TTMR2InferenceFailure(str(error_class))
    worker_identity = response.get("identity")
    if not isinstance(worker_identity, Mapping):
        raise TTMR2InferenceFailure("MissingWorkerIdentity")
    if (
        worker_identity.get("model_family") != TTM_R2_MODEL
        or worker_identity.get("runner_hash") != identity.runner_hash
        or worker_identity.get("environment_fingerprint") != identity.environment_fingerprint
    ):
        raise TTMR2InferenceFailure("WorkerIdentityMismatch")
    batch_output = response.get("batch_output")
    if (
        not isinstance(batch_output, list)
        or len(batch_output) != 1
        or not isinstance(batch_output[0], list)
    ):
        raise TTMR2InferenceFailure("MalformedForecastOutput")
    forecast = tuple(_finite_decimal(value, "TTM forecast") for value in batch_output[0])
    lower_value = response.get("forecast_batch_lower", ())
    upper_value = response.get("forecast_batch_upper", ())
    lower = (
        tuple(_finite_decimal(value, "TTM lower interval") for value in lower_value[0])
        if isinstance(lower_value, list)
        and len(lower_value) == 1
        and isinstance(lower_value[0], list)
        else ()
    )
    upper = (
        tuple(_finite_decimal(value, "TTM upper interval") for value in upper_value[0])
        if isinstance(upper_value, list)
        and len(upper_value) == 1
        and isinstance(upper_value[0], list)
        else ()
    )
    return TTMR2InferenceResult(
        forecast=forecast,
        latency_ms=_finite_decimal(response.get("batch_inference_ms"), "TTM latency"),
        device=identity.device,
        native_lower=lower,
        native_upper=upper,
        resource_peak_rss_mib=Decimal(str(peak_rss_mib)),
        resource_peak_cpu_percent=Decimal(str(peak_cpu_percent)),
        resource_sample_count=resource_sample_count,
    )


def _source_manifest(source_root: Path) -> dict[str, object]:
    value = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("source manifest must be a JSON object")
    if value.get("credentials_loaded") is not False:
        raise ValueError("source root is not explicitly credential-free")
    if value.get("order_writes_attempted") is not False:
        raise ValueError("source root is not explicitly order-write-free")
    return value


def _status(
    *,
    state: str,
    ledger: ForwardPredictionLedger,
    rejections: ForwardRejectionSpool,
    updated_at: datetime | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": f"{TTM_R2_RUN_SCHEMA}.status",
        "state": state,
        "updated_at": (updated_at or datetime.now(UTC)).isoformat(),
        "prediction_count": len(ledger.records),
        "rejection_count": len(rejections.records),
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    if extra:
        result.update(extra)
    return result


def run(
    *,
    admission_path: Path,
    source_root: Path,
    run_root: Path,
    repository_root: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    until: datetime,
    poll_seconds: float = TTM_R2_POLL_SECONDS,
    worker_timeout_seconds: float = TTM_R2_WORKER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Run a durable, append-only prospective TTM-R2 worker."""

    admission_path = admission_path.resolve()
    source_root = source_root.resolve()
    run_root = run_root.resolve()
    until = _aware(until, "until")
    if poll_seconds <= 0 or worker_timeout_seconds <= 0:
        raise ValueError("poll and worker timeout values must be positive")
    identity = TTMR2RuntimeIdentity.from_admission(admission_path)
    source_manifest = _source_manifest(source_root)
    expected = expected_manifest(
        source_root=source_root,
        source_manifest=source_manifest,
        admission_path=admission_path,
        repository_root=repository_root.resolve(),
        preregistration_sha256=preregistration_sha256,
        phase3_gate_sha256=phase3_gate_sha256,
        identity=identity,
    )
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "manifest.json"
    if manifest_path.exists():
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest_value, Mapping):
            raise ValueError("TTM prediction manifest must be a JSON object")
        validate_resume_manifest(manifest_value, expected)
        manifest = dict(manifest_value)
    else:
        manifest = {
            **expected,
            "started_at": datetime.now(UTC).isoformat(),
            "model": TTM_R2_MODEL,
            "network_calls": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
        _write_atomic(manifest_path, manifest)
    ledger = ForwardPredictionLedger(run_root / "predictions.jsonl")
    rejections = ForwardRejectionSpool(run_root / "rejections.jsonl")
    if identity.runner_context_bars != TTM_R2_CONTEXT_BARS:
        failure = {
            "schema": TTM_R2_FAILURE_SCHEMA,
            "failure_class": "QUALIFIED_RUNNER_CONTEXT_CONTRACT_MISMATCH",
            "required_runner_context_bars": identity.runner_context_bars,
            "configured_v3core_context_bars": TTM_R2_CONTEXT_BARS,
            "model_identity_hash": identity.model_identity_hash,
            "runner_hash": identity.runner_hash,
            "checkpoint_hash": identity.checkpoint_hash,
            "network_calls": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
        _write_immutable(run_root / "failure.json", failure)
        result = _status(
            state="quarantined_context_contract_mismatch",
            ledger=ledger,
            rejections=rejections,
            extra={
                "required_runner_context_bars": identity.runner_context_bars,
                "configured_v3core_context_bars": TTM_R2_CONTEXT_BARS,
                "model_identity_hash": identity.model_identity_hash,
            },
        )
        _write_atomic(run_root / "status.json", result)
        return result
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
                symbol_bars = tuple(bar for bar in bars if bar.instrument == symbol)
                if not symbol_bars:
                    continue
                cutoff = max(bar.interval_end for bar in symbol_bars) + timedelta(
                    seconds=FORWARD_INTERVAL_SECONDS
                )
                if cutoff.minute % 60 or cutoff.second or cutoff.microsecond:
                    continue
                if any(
                    record.instrument == symbol and record.cutoff == cutoff
                    for record in rejections.records
                ) or any(
                    record.prediction.instrument == symbol and record.prediction.cutoff == cutoff
                    for record in ledger.records
                ):
                    continue
                context = context_for_cutoff(bars, instrument=symbol, cutoff=cutoff, now=now)
                if now > cutoff:
                    rejections.append(instrument=symbol, cutoff=cutoff, reason="MISSED_FOR_TTM_R2")
                    continue
                if context is None:
                    continue
                try:
                    inference = infer_ttm_r2(
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
                    prediction = build_ttm_prediction(
                        identity=identity,
                        instrument=symbol,
                        cutoff=cutoff,
                        generated_at=generated_at,
                        context=context,
                        result=inference,
                    )
                    ledger.append(prediction)
                except TTMR2InferenceFailure as exc:
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
            status = _status(
                state="running",
                ledger=ledger,
                rejections=rejections,
                extra={"model": TTM_R2_MODEL},
            )
            _write_atomic(run_root / "status.json", status)
            time.sleep(poll_seconds)
    finally:
        signal.signal(signal.SIGTERM, prior_term)
        signal.signal(signal.SIGINT, prior_int)
    result = _status(
        state="stopped_with_evidence" if stop else "deadline_reached",
        ledger=ledger,
        rejections=rejections,
        extra={"model": TTM_R2_MODEL},
    )
    _write_atomic(run_root / "status.json", result)
    return result


__all__ = [
    "QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS",
    "TTM_R2_CONTEXT_BARS",
    "TTM_R2_HORIZON_BARS",
    "TTM_R2_MODEL",
    "TTM_R2_OUTPUT_BARS",
    "TTM_R2_POLL_SECONDS",
    "TTM_R2_PREPROCESSING_IDENTITY",
    "TTMR2InferenceFailure",
    "TTMR2InferenceResult",
    "TTMR2RuntimeContractMismatch",
    "TTMR2RuntimeIdentity",
    "build_ttm_prediction",
    "context_for_cutoff",
    "expected_manifest",
    "infer_ttm_r2",
    "run",
    "validate_resume_manifest",
]
