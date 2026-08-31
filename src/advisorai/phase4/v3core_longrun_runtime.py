"""Executable, credential-free V3-Core Phase-4 long-run contracts.

The long-run runtime is deliberately separate from the four-cutoff canary.
It shares the reviewed raw-bar parser and finality tracker, but has its own
80-opportunity preregistration, append-only coordinator, prediction durability
attestation, watchdog surface, and terminal auditor.

No function in this module grants execution authority.  The collector is
restricted to the public Binance market-data surface and the candidate is
restricted to the already qualified Chronos runtime supplied by the caller.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_MARKET_DATA_WS_ENDPOINT,
    V3_CORE_SYMBOLS,
    V3CoreBar,
)
from advisorai.phase4.v3core_canary import (
    CANARY_FINALITY_GUARD_SECONDS,
    CANARY_REPEAT_RECEIPTS,
    CanaryFinalityTracker,
    CanaryFinalityViolation,
    sha256_file,
)
from advisorai.phase4.v3core_chronos import (
    CHRONOS_HORIZON_BARS,
    CHRONOS_PREPROCESSING_IDENTITY,
    _input_snapshot_hash,
)
from advisorai.phase4.v3core_forward import (
    FORWARD_INTERVAL,
    ForwardNormalizedBarSpool,
    ForwardPredictionRecord,
)
from advisorai.phase4.v3core_longrun import (
    LONG_RUN_CASE_ACCOUNTING_GRACE_SECONDS,
    LONG_RUN_CONTEXT_BARS,
    LONG_RUN_CONTEXT_LAG_SECONDS,
    LONG_RUN_FINALITY_GUARD_SECONDS,
    LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL,
    LONG_RUN_OBSERVATION_INTERVAL_SECONDS,
    LONG_RUN_OUTCOME_HORIZON_SECONDS,
    LONG_RUN_REPEAT_RECEIPTS,
    LONG_RUN_TARGET_CASES_PER_SYMBOL,
    QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
    QUALIFIED_CHRONOS_MODEL,
    QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256,
    QUALIFIED_CHRONOS_REVISION,
    QUALIFIED_PHASE3_GATE_SHA256,
    QUALIFIED_REQUIREMENTS_LOCK_SHA256,
    QUALIFIED_UV_LOCK_SHA256,
    derive_first_long_run_cutoff,
    derive_long_run_cutoffs,
)

LONG_RUN_EVIDENCE_CLASS = "PROSPECTIVE_PHASE4_LONGRUN"
LONG_RUN_PREREGISTRATION_SCHEMA = "advisorai.phase4.v3-core.long-run.preregistration.v1"
LONG_RUN_EVENT_SCHEMA = "advisorai.phase4.v3-core.long-run.event.v1"
LONG_RUN_RAW_SCHEMA = "advisorai.phase4.v3-core.long-run.raw-receipt.v1"
LONG_RUN_PREDICTION_SCHEMA = "advisorai.phase4.v3-core.long-run.prediction.v1"
LONG_RUN_DURABILITY_SCHEMA = "advisorai.phase4.v3-core.long-run.prediction-durability.v1"
LONG_RUN_OUTCOME_SCHEMA = "advisorai.phase4.v3-core.long-run.outcome-link.v1"
LONG_RUN_AUDIT_SCHEMA = "advisorai.phase4.v3-core.long-run.terminal-audit.v1"
LONG_RUN_READINESS_SCHEMA = "advisorai.phase4.v3-core.long-run.launch-readiness.v1"
LONG_RUN_RC_PREFLIGHT_SCHEMA = "advisorai.phase4.v3-core.long-run.release-candidate-preflight.v1"
LONG_RUN_TRANSPORT_FAILURE_SCHEMA = "advisorai.phase4.v3-core.long-run.transport-failure.v1"

LONG_RUN_FINALITY_RULE_ID = "v3core-admitted-final-60s-two-distinct-receipts-v1"
LONG_RUN_CONTEXT_RULE_ID = "v3core-48-admitted-final-newest-minus-10m-v1"
LONG_RUN_OUTCOME_RULE_ID = "v3core-one-hour-12-subsequent-5m-bars-v1"
LONG_RUN_RECOVERY_POLICY_ID = "v3core-longrun-recovery-one-attempt-component-v1"
LONG_RUN_PREDICTION_LATENESS_SECONDS = 300

# These names are the contract between the executable release preflight and
# the separately run focused/artifact-backed checks.  A caller may not omit a
# check and obtain a passing report by supplying a smaller mapping.
LONG_RUN_REQUIRED_PREFLIGHT_CHECKS = (
    "warmup_contract",
    "watchdog_contract",
    "auditor_contract",
    "candidate_contract",
    "gpu_acceptance",
    "scheduler_contract",
    "terminal_workflow",
    "security_boundary",
    "synthetic_80_opportunity",
    "crash_recovery",
    "outcome_contract",
    "source_finality",
    "chronos_contract",
    "readiness_attestation",
    "artifact_backed",
    "runtime_attestation",
    "host_operation_contract",
)

LONG_RUN_RC_BASE_READINESS_CHECKS = (
    "actual_repository_and_components",
    "phase3_gate",
    "model_runtime",
    "dual_lock",
    "security",
    "gpu_lease",
)
LONG_RUN_LAUNCH_BASE_READINESS_CHECKS = (
    *LONG_RUN_RC_BASE_READINESS_CHECKS,
    "immutable_preregistration",
)

_LONG_RUN_COMPONENT_RELATIVE_PATHS = {
    "finality_rule_sha256": "src/advisorai/phase4/v3core_canary.py",
    "context_rule_sha256": "src/advisorai/phase4/v3core_longrun_runtime.py",
    "preprocessing_sha256": "src/advisorai/phase4/v3core_chronos.py",
    "long_run_contract_code_sha256": "src/advisorai/phase4/v3core_longrun.py",
    "forward_contract_code_sha256": "src/advisorai/phase4/v3core_forward.py",
    "cadence_contract_code_sha256": "src/advisorai/phase4/v3core_cadence.py",
    "collector_code_sha256": "scripts/collect_phase4_v3core_longrun.py",
    "candidate_worker_code_sha256": "scripts/run_phase4_v3core_longrun_chronos.py",
    "outcome_linker_code_sha256": "scripts/link_phase4_v3core_longrun_prediction_outcomes.py",
    "watchdog_code_sha256": "scripts/watch_phase4_v3core_longrun.py",
    "auditor_code_sha256": "scripts/audit_phase4_v3core_longrun.py",
    "scheduler_code_sha256": "scripts/schedule_phase4_v3core_longrun.sh",
    "coordinator_code_sha256": "src/advisorai/phase4/v3core_longrun_runtime.py",
    "launcher_code_sha256": "scripts/launch_phase4_v3core_longrun.py",
}


class LongRunState(StrEnum):
    PREREGISTERED = "PREREGISTERED"
    PREFLIGHT_READY = "PREFLIGHT_READY"
    RUNNING_WARMUP = "RUNNING_WARMUP"
    RUNNING = "RUNNING"
    RECOVERING_COMPONENT = "RECOVERING_COMPONENT"
    TERMINALIZING = "TERMINALIZING"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    GENERATION_FATAL = "GENERATION_FATAL"
    COMPLETED_PENDING_AUDIT = "COMPLETED_PENDING_AUDIT"
    AUDITED = "AUDITED"


class LongRunCaseState(StrEnum):
    PENDING_OUTCOME = "PENDING_OUTCOME"
    EXCLUDED = "EXCLUDED"
    CLEAN = "CLEAN"


class LongRunIncident(StrEnum):
    SOURCE_PROCESS_DEATH = "SOURCE_PROCESS_DEATH"
    CANDIDATE_PROCESS_DEATH = "CANDIDATE_PROCESS_DEATH"
    WATCHDOG_PROCESS_DEATH = "WATCHDOG_PROCESS_DEATH"
    MISSED_MANDATORY_CUTOFF = "MISSED_MANDATORY_CUTOFF"
    SOURCE_CONTEXT_UNAVAILABLE = "SOURCE_CONTEXT_UNAVAILABLE"
    CANDIDATE_REJECTION = "CANDIDATE_REJECTION"
    CANDIDATE_SCHEMA_FAILURE = "CANDIDATE_SCHEMA_FAILURE"
    CANDIDATE_RUNTIME_FAILURE = "CANDIDATE_RUNTIME_FAILURE"
    POST_ADMISSION_REVISION = "POST_ADMISSION_REVISION"
    RAW_HASH_CHAIN_FAILURE = "RAW_HASH_CHAIN_FAILURE"
    ADMITTED_IDENTITY_FAILURE = "ADMITTED_IDENTITY_FAILURE"
    PREDICTION_LEDGER_CONFLICT = "PREDICTION_LEDGER_CONFLICT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    RUNTIME_LOCK_DRIFT = "RUNTIME_LOCK_DRIFT"
    FUTURE_LEAKAGE = "FUTURE_LEAKAGE"
    LATE_PREDICTION = "LATE_PREDICTION"
    MISSING_OUTCOME = "MISSING_OUTCOME"
    CREDENTIAL_DETECTED = "CREDENTIAL_DETECTED"
    ORDER_WRITE_DETECTED = "ORDER_WRITE_DETECTED"
    CUDA_FAILURE = "CUDA_FAILURE"
    SOURCE_CLOCK_ERROR = "SOURCE_CLOCK_ERROR"
    CANDIDATE_CLOCK_ERROR = "CANDIDATE_CLOCK_ERROR"
    OUTCOME_CLOCK_ERROR = "OUTCOME_CLOCK_ERROR"
    GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION = "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    UNKNOWN = "UNKNOWN"


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash_payload(payload: object) -> str:
    return sha256(_canonical(payload)).hexdigest()


def derive_long_run_source_snapshot_sha256(
    *,
    generation_id: str,
    repository_commit: str,
    start_at: datetime,
    first_mandatory_cutoff_at: datetime,
    mandatory_cutoffs: Sequence[datetime],
    source_provider: str,
    rest_endpoint: str,
    interval: str,
    finality_rule_id: str,
    finality_rule_sha256: str,
    context_rule_id: str,
    context_rule_sha256: str,
) -> str:
    """Derive the source-snapshot identity without depending on the prereg hash.

    This is the immutable source-contract identity carried by every raw and
    admitted observation.  It binds the schedule and scientific source rules,
    while remaining computable before the enclosing preregistration is hashed.
    """

    return _hash_payload(
        {
            "schema": "advisorai.phase4.v3-core.long-run.source-snapshot.v1",
            "generation_id": generation_id,
            "repository_commit": repository_commit,
            "start_at": _iso(start_at),
            "first_mandatory_cutoff_at": _iso(first_mandatory_cutoff_at),
            "mandatory_cutoffs": [_iso(value) for value in mandatory_cutoffs],
            "source_provider": source_provider,
            "rest_endpoint": rest_endpoint,
            "interval": interval,
            "finality_rule_id": finality_rule_id,
            "finality_rule_sha256": finality_rule_sha256,
            "context_rule_id": context_rule_id,
            "context_rule_sha256": context_rule_sha256,
        }
    )


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _commit(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a Git commit identity")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value.astimezone(UTC)


def _safe_payload(value: object) -> None:
    """Reject credential/order material from append-only control-plane events."""

    forbidden = ("api_key", "apikey", "api_secret", "secret", "password", "token", "private_key")
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in forbidden) or normalized in {
                "credential",
                "credentials",
                "broker_secret",
                "withdrawal_address",
            }:
                raise ValueError("long-run events cannot contain credential material")
            _safe_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe_payload(child)


# Only non-sensitive response metadata needed to distinguish a public HTTP
# acquisition is retained.  In particular, authentication/cookie headers are
# never copied into the scientific raw-receipt ledger.  Unknown headers are
# omitted rather than trusted as provenance.  A positive Age header indicates
# a cached response and cannot satisfy an independent finality observation.
_SAFE_RESPONSE_HEADER_NAMES = frozenset(
    {
        "cache-control",
        "content-length",
        "content-type",
        "date",
        "etag",
        "last-modified",
        "retry-after",
        "cf-cache-status",
        "x-cache",
        "x-cache-hits",
        "x-mbx-used-weight-1m",
        "x-mbx-used-weight-1s",
    }
)
_FORBIDDEN_RESPONSE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "x-api-secret",
    }
)


def _validate_public_klines_url(
    value: str,
    field_name: str,
    *,
    require_query: bool = False,
    expected_symbol: str | None = None,
) -> str:
    """Validate the exact unauthenticated Binance public klines surface.

    A bare endpoint is accepted for synthetic fixtures and response URLs.  A
    live request spool opts into ``require_query`` so a receipt cannot claim
    provenance from an unbound or differently parameterized request.
    """

    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid public klines URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "data-api.binance.vision"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/api/v3/klines"
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} is outside the public Binance klines surface")
    names = [name for name, _value in query_items]
    if len(names) != len(set(names)):
        raise ValueError(f"{field_name} contains duplicate query parameters")
    if require_query and not query_items:
        raise ValueError(f"{field_name} must bind symbol and interval query parameters")
    query = dict(query_items)
    if query_items:
        if query.get("interval") != "5m":
            raise ValueError(f"{field_name} must bind the five-minute interval")
        symbol = query.get("symbol", "").strip().upper()
        if symbol not in V3_CORE_SYMBOLS:
            raise ValueError(f"{field_name} must bind BTCUSDT or ETHUSDT")
        if expected_symbol is not None and symbol != expected_symbol.strip().upper():
            raise ValueError(f"{field_name} symbol does not match the receipt symbol")
        if "limit" in query:
            try:
                limit = int(query["limit"])
            except ValueError as exc:
                raise ValueError(f"{field_name} limit is not an integer") from exc
            if limit < 1 or limit > 2:
                raise ValueError(f"{field_name} limit must be one or two bars")
    return normalized


def _safe_response_headers(
    headers: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    safe: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_name, raw_value in headers:
        name = str(raw_name).strip().lower()
        value = str(raw_value).strip()
        if not name:
            raise ValueError("HTTP response header names cannot be blank")
        if name in _FORBIDDEN_RESPONSE_HEADER_NAMES or any(
            marker in name for marker in ("auth", "credential", "secret", "signature")
        ):
            raise ValueError("HTTP response contains forbidden authentication metadata")
        if name in seen:
            raise ValueError("HTTP response contains duplicate header names")
        seen.add(name)
        if name == "age":
            try:
                age = int(value)
            except ValueError as exc:
                raise ValueError("HTTP Age header is not an integer") from exc
            if age < 0 or age > 0:
                raise ValueError("cached HTTP responses cannot satisfy finality")
            continue
        if name == "cf-cache-status" and value.upper() in {"HIT", "STALE"}:
            raise ValueError("cached HTTP responses cannot satisfy finality")
        if name == "x-cache" and "HIT" in value.upper():
            raise ValueError("cached HTTP responses cannot satisfy finality")
        if name == "x-cache-hits":
            try:
                cache_hits = int(value)
            except ValueError as exc:
                raise ValueError("HTTP cache-hit count is not an integer") from exc
            if cache_hits != 0:
                raise ValueError("cached HTTP responses cannot satisfy finality")
        if name in _SAFE_RESPONSE_HEADER_NAMES:
            safe.append((name, value))
    return tuple(sorted(safe))


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """Publish one derived status snapshot only after a flushed replacement."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json_stable(
    path: Path, *, attempts: int = 3, retry_seconds: float = 0.02
) -> dict[str, object]:
    """Read an atomic status artifact, rejecting a missing/torn snapshot."""

    if attempts < 1 or retry_seconds < 0:
        raise ValueError("stable-read parameters are invalid")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            before = path.stat()
            payload = json.loads(path.read_text(encoding="utf-8"))
            after = path.stat()
            if (
                before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise RuntimeError("status changed during read")
            if not isinstance(payload, dict):
                raise ValueError("status artifact must contain an object")
            return payload
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
    assert last_error is not None
    raise last_error


def read_append_only_lines(
    path: Path, *, attempts: int = 5, retry_seconds: float = 0.02
) -> tuple[str, ...]:
    """Read a flushed JSONL ledger without accepting a torn final line."""

    if attempts < 1 or retry_seconds < 0:
        raise ValueError("stable-read parameters are invalid")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if not path.exists():
                return ()
            before = path.stat()
            text = path.read_text(encoding="utf-8")
            after = path.stat()
            if (
                before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise RuntimeError("append-only ledger changed during read")
            if text and not text.endswith("\n"):
                raise RuntimeError("append-only ledger has an unflushed final line")
            return tuple(line for line in text.splitlines() if line.strip())
        except (FileNotFoundError, OSError, UnicodeDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
    assert last_error is not None
    raise last_error


def read_normalized_bars_stable(
    path: Path, *, attempts: int = 5, retry_seconds: float = 0.02
) -> tuple[object, ...]:
    """Read the long-run bar projection without collapsing duplicate identities.

    The shared forward spool is intentionally permissive for the older
    workflow, where identical duplicate lines are diagnosed by a separate
    auditor.  Long-run monitoring and terminal certification must not lose
    that signal by loading into a dictionary, so this reader rejects every
    duplicate instrument/interval identity, including identical duplicates.
    """

    if attempts < 1 or retry_seconds < 0:
        raise ValueError("stable-read parameters are invalid")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if not path.exists():
                return ()
            before = path.stat()
            lines = read_append_only_lines(path, attempts=1)
            bars: list[V3CoreBar] = []
            seen: set[tuple[str, datetime]] = set()
            for line_number, line in enumerate(lines, 1):
                try:
                    bar = V3CoreBar.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"long-run normalized bar projection is corrupt at line {line_number}"
                    ) from exc
                key = (bar.instrument, bar.interval_end)
                if key in seen:
                    raise RuntimeError(
                        "long-run normalized bar projection contains a duplicate identity"
                    )
                seen.add(key)
                bars.append(bar)
            after = path.stat()
            if (
                before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise RuntimeError("normalized bar projection changed during read")
            return tuple(bars)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
    assert last_error is not None
    raise last_error


def read_long_run_normalized_bars_for_start(
    source_root: Path, *, expected_admitted_final_bars: int | None = None
) -> tuple[object, ...]:
    """Read the projection while allowing a genuinely empty fresh start.

    The collector publishes status before the first public-data receipt is
    available. At that point neither the raw spool nor the normalized
    projection exists yet, which is a valid warm-up state. Raw receipts also
    legitimately precede the first admitted bar while finality is pending. A
    caller that has read the source status can provide the independently
    published admitted-bar count; a missing projection with a non-zero count
    is then rejected as a consistency failure.
    """

    source_root = source_root.resolve()
    normalized_path = source_root / "normalized-bars.jsonl"
    if normalized_path.exists():
        return read_normalized_bars_stable(normalized_path)
    raw_path = source_root / "raw-receipts.jsonl"
    if not raw_path.exists() or raw_path.stat().st_size == 0:
        return ()
    if expected_admitted_final_bars not in (None, 0):
        raise FileNotFoundError(normalized_path)
    return ()


class LongRunRecoveryPolicyContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Literal[LONG_RUN_RECOVERY_POLICY_ID] = LONG_RUN_RECOVERY_POLICY_ID
    collector_max_attempts_total: int = 1
    candidate_max_attempts_total: int = 1
    watchdog_process_death: Literal["GENERATION_FATAL"] = "GENERATION_FATAL"
    no_backfill: Literal[True] = True
    no_deadline_extension: Literal[True] = True
    no_rule_relaxation: Literal[True] = True

    @model_validator(mode="after")
    def fixed_policy(self) -> LongRunRecoveryPolicyContract:
        if self.collector_max_attempts_total != 1 or self.candidate_max_attempts_total != 1:
            raise ValueError(
                "long-run recovery permits at most one automatic attempt per component"
            )
        return self


class LongRunPreregistration(BaseModel):
    """The future long-run contract; this type is not instantiated by launch code automatically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_PREREGISTRATION_SCHEMA] = LONG_RUN_PREREGISTRATION_SCHEMA
    generation_id: str = Field(min_length=1)
    evidence_class: Literal[LONG_RUN_EVIDENCE_CLASS] = LONG_RUN_EVIDENCE_CLASS
    admission_eligible: Literal[False] = False
    repository_commit: str
    branch_or_tag: str = Field(min_length=1)
    created_at: datetime
    start_at: datetime
    first_mandatory_cutoff_at: datetime
    mandatory_cutoffs: tuple[datetime, ...]
    target_opportunities_per_symbol: int = LONG_RUN_TARGET_CASES_PER_SYMBOL
    minimum_clean_cases_per_symbol: int = LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL
    symbols: tuple[str, ...] = V3_CORE_SYMBOLS
    source_provider: Literal[V3_CORE_MARKET_DATA_PROVIDER] = V3_CORE_MARKET_DATA_PROVIDER
    rest_endpoint: Literal[V3_CORE_MARKET_DATA_REST_ENDPOINT] = V3_CORE_MARKET_DATA_REST_ENDPOINT
    websocket_endpoint: Literal[V3_CORE_MARKET_DATA_WS_ENDPOINT] = V3_CORE_MARKET_DATA_WS_ENDPOINT
    interval: Literal[FORWARD_INTERVAL] = FORWARD_INTERVAL
    observation_interval_seconds: int = LONG_RUN_OBSERVATION_INTERVAL_SECONDS
    prediction_cadence_seconds: int = 3600
    finality_rule_id: Literal[LONG_RUN_FINALITY_RULE_ID] = LONG_RUN_FINALITY_RULE_ID
    finality_rule_sha256: str
    context_rule_id: Literal[LONG_RUN_CONTEXT_RULE_ID] = LONG_RUN_CONTEXT_RULE_ID
    context_rule_sha256: str
    context_bars: int = LONG_RUN_CONTEXT_BARS
    context_newest_lag_seconds: int = LONG_RUN_CONTEXT_LAG_SECONDS
    finality_guard_seconds: int = LONG_RUN_FINALITY_GUARD_SECONDS
    repeat_requirement: int = LONG_RUN_REPEAT_RECEIPTS
    distinct_receipts_required: Literal[True] = True
    model_repository: Literal[QUALIFIED_CHRONOS_MODEL] = QUALIFIED_CHRONOS_MODEL
    model_revision: Literal[QUALIFIED_CHRONOS_REVISION] = QUALIFIED_CHRONOS_REVISION
    model_identity_sha256: str = QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256
    checkpoint_sha256: str = QUALIFIED_CHRONOS_CHECKPOINT_SHA256
    preprocessing_identity: Literal[CHRONOS_PREPROCESSING_IDENTITY] = CHRONOS_PREPROCESSING_IDENTITY
    preprocessing_sha256: str
    collector_code_sha256: str
    candidate_worker_code_sha256: str
    outcome_linker_code_sha256: str
    watchdog_code_sha256: str
    auditor_code_sha256: str
    scheduler_code_sha256: str
    coordinator_code_sha256: str
    launcher_code_sha256: str
    long_run_contract_code_sha256: str
    forward_contract_code_sha256: str
    cadence_contract_code_sha256: str
    uv_lock_sha256: str = QUALIFIED_UV_LOCK_SHA256
    requirements_lock_sha256: str = QUALIFIED_REQUIREMENTS_LOCK_SHA256
    phase3_gate_sha256: str = QUALIFIED_PHASE3_GATE_SHA256
    model_runtime_qualification_sha256: str
    source_snapshot_sha256: str
    runtime_attestation_sha256: str
    prediction_lateness_seconds: int = LONG_RUN_PREDICTION_LATENESS_SECONDS
    case_accounting_grace_seconds: int = LONG_RUN_CASE_ACCOUNTING_GRACE_SECONDS
    outcome_maturity_rule_id: Literal[LONG_RUN_OUTCOME_RULE_ID] = LONG_RUN_OUTCOME_RULE_ID
    outcome_bars: int = 12
    recovery_policy: LongRunRecoveryPolicyContract = Field(
        default_factory=LongRunRecoveryPolicyContract
    )
    terminal_deadline: datetime
    terminal_check_at: datetime
    credentials_prohibited: Literal[True] = True
    orders_prohibited: Literal[True] = True
    no_deadline_extension: Literal[True] = True

    @field_validator(
        "created_at",
        "start_at",
        "first_mandatory_cutoff_at",
        "terminal_deadline",
        "terminal_check_at",
    )
    @classmethod
    def aware_timestamps(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("mandatory_cutoffs")
    @classmethod
    def aware_cutoffs(cls, value: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(_aware(item, "mandatory_cutoff") for item in value)

    @field_validator("repository_commit")
    @classmethod
    def valid_repository(cls, value: str) -> str:
        return _commit(value, "repository_commit")

    @field_validator(
        "finality_rule_sha256",
        "context_rule_sha256",
        "checkpoint_sha256",
        "model_identity_sha256",
        "preprocessing_sha256",
        "collector_code_sha256",
        "candidate_worker_code_sha256",
        "outcome_linker_code_sha256",
        "watchdog_code_sha256",
        "auditor_code_sha256",
        "scheduler_code_sha256",
        "coordinator_code_sha256",
        "launcher_code_sha256",
        "long_run_contract_code_sha256",
        "forward_contract_code_sha256",
        "cadence_contract_code_sha256",
        "uv_lock_sha256",
        "requirements_lock_sha256",
        "phase3_gate_sha256",
        "model_runtime_qualification_sha256",
        "source_snapshot_sha256",
        "runtime_attestation_sha256",
    )
    @classmethod
    def valid_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "hash"))

    @field_validator("symbols")
    @classmethod
    def fixed_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(symbol.strip().upper() for symbol in value)
        if normalized != V3_CORE_SYMBOLS:
            raise ValueError("long-run symbols are fixed to BTCUSDT and ETHUSDT")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> LongRunPreregistration:
        if self.start_at.minute or self.start_at.second or self.start_at.microsecond:
            raise ValueError("long-run start must be aligned to a UTC hour")
        if self.created_at > self.start_at:
            raise ValueError("long-run preregistration must be created before its frozen start")
        if self.target_opportunities_per_symbol != LONG_RUN_TARGET_CASES_PER_SYMBOL:
            raise ValueError("long-run target must remain 80 opportunities per symbol")
        if self.minimum_clean_cases_per_symbol != LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL:
            raise ValueError("long-run minimum must remain 64 clean cases per symbol")
        if (
            self.context_bars != LONG_RUN_CONTEXT_BARS
            or self.context_newest_lag_seconds != LONG_RUN_CONTEXT_LAG_SECONDS
        ):
            raise ValueError("long-run context contract is frozen")
        if (
            self.finality_guard_seconds != CANARY_FINALITY_GUARD_SECONDS
            or self.repeat_requirement != CANARY_REPEAT_RECEIPTS
        ):
            raise ValueError("long-run finality contract is frozen")
        if self.checkpoint_sha256 != QUALIFIED_CHRONOS_CHECKPOINT_SHA256:
            raise ValueError("long-run checkpoint is not the qualified Chronos checkpoint")
        if self.model_identity_sha256 != QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256:
            raise ValueError("long-run model identity is not the qualified Chronos model")
        if self.phase3_gate_sha256 != QUALIFIED_PHASE3_GATE_SHA256:
            raise ValueError("long-run Phase-3 gate identity is not the qualified predecessor")
        if (
            self.uv_lock_sha256 != QUALIFIED_UV_LOCK_SHA256
            or self.requirements_lock_sha256 != QUALIFIED_REQUIREMENTS_LOCK_SHA256
        ):
            raise ValueError("long-run dual lock identities are frozen")
        expected_first = derive_first_long_run_cutoff(self.start_at)
        expected_cutoffs = derive_long_run_cutoffs(
            self.start_at, count=self.target_opportunities_per_symbol
        )
        if (
            self.first_mandatory_cutoff_at != expected_first
            or self.mandatory_cutoffs != expected_cutoffs
        ):
            raise ValueError("mandatory cutoff schedule does not match fresh-run geometry")
        expected_source_snapshot = derive_long_run_source_snapshot_sha256(
            generation_id=self.generation_id,
            repository_commit=self.repository_commit,
            start_at=self.start_at,
            first_mandatory_cutoff_at=self.first_mandatory_cutoff_at,
            mandatory_cutoffs=self.mandatory_cutoffs,
            source_provider=self.source_provider,
            rest_endpoint=self.rest_endpoint,
            interval=self.interval,
            finality_rule_id=self.finality_rule_id,
            finality_rule_sha256=self.finality_rule_sha256,
            context_rule_id=self.context_rule_id,
            context_rule_sha256=self.context_rule_sha256,
        )
        if self.source_snapshot_sha256 != expected_source_snapshot:
            raise ValueError("source snapshot identity does not match the frozen source contract")
        if self.prediction_lateness_seconds != LONG_RUN_PREDICTION_LATENESS_SECONDS:
            raise ValueError("prediction lateness boundary is frozen at cutoff plus five minutes")
        if self.case_accounting_grace_seconds != LONG_RUN_CASE_ACCOUNTING_GRACE_SECONDS:
            raise ValueError(
                "case accounting grace is frozen and cannot extend the prediction deadline"
            )
        if self.outcome_bars != CHRONOS_HORIZON_BARS:
            raise ValueError("outcome maturity must contain the twelve subsequent five-minute bars")
        if len(self.mandatory_cutoffs) != self.target_opportunities_per_symbol:
            raise ValueError("long-run preregistration must contain exactly 80 mandatory cutoffs")
        if any(
            right <= left
            for left, right in zip(self.mandatory_cutoffs, self.mandatory_cutoffs[1:], strict=False)
        ):
            raise ValueError("mandatory cutoffs must be strictly ordered")
        if any(
            right - left != timedelta(hours=1)
            for left, right in zip(self.mandatory_cutoffs, self.mandatory_cutoffs[1:], strict=False)
        ):
            raise ValueError("mandatory cutoffs must be hourly")
        if self.terminal_deadline < self.mandatory_cutoffs[-1] + timedelta(
            seconds=LONG_RUN_OUTCOME_HORIZON_SECONDS + 3600
        ):
            raise ValueError("terminal deadline does not include final outcome maturity and margin")
        if self.terminal_check_at < self.terminal_deadline:
            raise ValueError("terminal check cannot precede the terminal deadline")
        return self


def long_run_preregistration_sha256(preregistration: LongRunPreregistration) -> str:
    """Hash the canonical JSON contract, independent of a trailing newline."""

    return _hash_payload(preregistration.model_dump(mode="json"))


def write_immutable_long_run_preregistration(
    path: Path, preregistration: LongRunPreregistration
) -> str:
    """Write one new preregistration, refusing any mutation of an existing one."""

    path = path.resolve()
    encoded = _canonical(preregistration.model_dump(mode="json")) + b"\n"
    canonical_hash = long_run_preregistration_sha256(preregistration)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = LongRunPreregistration.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("existing long-run preregistration is unreadable") from exc
        if long_run_preregistration_sha256(existing) != canonical_hash:
            raise RuntimeError("long-run preregistration already exists with different content")
        return canonical_hash
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return canonical_hash


def load_long_run_preregistration(
    path: Path, *, expected_sha256: str | None = None
) -> LongRunPreregistration:
    path = path.resolve()
    preregistration = LongRunPreregistration.model_validate_json(path.read_text(encoding="utf-8"))
    if expected_sha256 is not None and long_run_preregistration_sha256(preregistration) != _digest(
        expected_sha256, "preregistration hash"
    ):
        raise ValueError("long-run preregistration hash mismatch")
    return preregistration


def required_context_interval_ends(cutoff: datetime) -> tuple[datetime, ...]:
    cutoff = _aware(cutoff, "cutoff")
    newest = cutoff - timedelta(seconds=LONG_RUN_CONTEXT_LAG_SECONDS)
    return tuple(
        newest
        - timedelta(
            seconds=LONG_RUN_OBSERVATION_INTERVAL_SECONDS * (LONG_RUN_CONTEXT_BARS - 1 - index)
        )
        for index in range(LONG_RUN_CONTEXT_BARS)
    )


def fresh_long_run_minimum_interval_end(start_at: datetime) -> datetime:
    """Return the first complete interval whose data belongs to a fresh run.

    A bar ending exactly at ``start_at`` contains observations acquired before
    launch. The fresh-run contract therefore admits only the first complete
    five-minute interval ending strictly after the frozen start.
    """

    return _aware(start_at, "start_at") + timedelta(seconds=LONG_RUN_OBSERVATION_INTERVAL_SECONDS)


def long_run_context_for_cutoff(
    bars: Sequence[object],
    *,
    instrument: str,
    cutoff: datetime,
    available_at: datetime,
    minimum_interval_end: datetime | None = None,
    source_snapshot_hash: str | None = None,
) -> tuple[object, ...] | None:
    """Return exactly 48 admitted bars available at the actual inference time.

    A fresh long-run passes :func:`fresh_long_run_minimum_interval_end` as
    ``minimum_interval_end``. This keeps bars returned in the first post-start
    HTTP response from becoming a scientifically unqualified warm-start
    context merely because they were acquired after launch.
    """

    cutoff = _aware(cutoff, "cutoff")
    available_at = _aware(available_at, "available_at")
    if minimum_interval_end is not None:
        minimum_interval_end = _aware(minimum_interval_end, "minimum_interval_end")
    normalized = instrument.strip().upper()
    if normalized not in V3_CORE_SYMBOLS:
        raise ValueError("long-run context is restricted to BTCUSDT and ETHUSDT")
    by_end = {
        bar.interval_end: bar
        for bar in bars
        if bar.instrument == normalized
        and bar.evidence_class == "forward_pit_admission"
        and (minimum_interval_end is None or bar.interval_end >= minimum_interval_end)
        and bar.provenance.source_health_state == "HEALTHY"
        and bar.provider_available_at <= available_at
        and bar.collected_at <= available_at
    }
    context = tuple(by_end.get(item) for item in required_context_interval_ends(cutoff))
    if any(item is None for item in context):
        return None
    resolved = tuple(item for item in context if item is not None)
    if len(resolved) != LONG_RUN_CONTEXT_BARS:
        return None
    if any(
        prior.interval_end + timedelta(seconds=LONG_RUN_OBSERVATION_INTERVAL_SECONDS)
        != current.interval_end
        for prior, current in zip(resolved, resolved[1:], strict=False)
    ):
        return None
    if resolved[-1].interval_end > cutoff - timedelta(seconds=LONG_RUN_CONTEXT_LAG_SECONDS):
        return None
    if len({bar.source_snapshot_hash for bar in resolved}) != 1:
        return None
    if source_snapshot_hash is not None and any(
        bar.source_snapshot_hash != source_snapshot_hash for bar in resolved
    ):
        return None
    return resolved


def prediction_deadline(
    cutoff: datetime, *, lateness_seconds: int = LONG_RUN_PREDICTION_LATENESS_SECONDS
) -> datetime:
    if lateness_seconds != LONG_RUN_PREDICTION_LATENESS_SECONDS:
        raise ValueError("long-run prediction lateness is fixed at five minutes")
    return _aware(cutoff, "cutoff") + timedelta(seconds=lateness_seconds)


def _iso(value: datetime) -> str:
    return _aware(value, "timestamp").isoformat().replace("+00:00", "Z")


def validate_utc_clock_progress(
    previous: datetime | None, current: datetime, *, component: str
) -> datetime:
    """Reject a backwards UTC wall-clock jump instead of extending a run."""

    current = _aware(current, f"{component}_clock")
    if previous is not None and current < _aware(previous, f"{component}_previous_clock"):
        raise RuntimeError(f"{component} UTC clock moved backwards")
    return current


@dataclass(frozen=True)
class LongRunRawReceiptView:
    sequence: int
    symbol: str
    collected_at: datetime
    payload: bytes
    source_snapshot_hash: str
    receipt_identity: str
    response_capture_hash: str
    previous_record_hash: str | None
    record_hash: str


class LongRunRawReceipt(BaseModel):
    """Raw HTTP receipt with a retry-independent acquisition identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_RAW_SCHEMA] = LONG_RUN_RAW_SCHEMA
    sequence: int = Field(ge=1)
    symbol: str
    endpoint: Literal[V3_CORE_MARKET_DATA_REST_ENDPOINT] = V3_CORE_MARKET_DATA_REST_ENDPOINT
    request_url: str = Field(min_length=1)
    request_attempt_id: str = Field(min_length=1)
    collected_at: datetime
    source_snapshot_hash: str
    status_code: int = Field(ge=100, le=599)
    response_url: str = Field(min_length=1)
    response_headers: tuple[tuple[str, str], ...] = ()
    response_sha256: str
    response_capture_hash: str
    payload_b64: str
    receipt_identity: str
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("symbol")
    @classmethod
    def fixed_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in V3_CORE_SYMBOLS:
            raise ValueError("long-run raw receipts are restricted to BTCUSDT and ETHUSDT")
        return value

    @field_validator("collected_at")
    @classmethod
    def aware_collection(cls, value: datetime) -> datetime:
        return _aware(value, "collected_at")

    @field_validator("request_url", "response_url")
    @classmethod
    def public_endpoint(cls, value: str, info: object) -> str:
        return _validate_public_klines_url(
            value,
            getattr(info, "field_name", "url"),
            expected_symbol=None,
        )

    @field_validator(
        "response_sha256",
        "response_capture_hash",
        "source_snapshot_hash",
        "receipt_identity",
        "previous_record_hash",
        "record_hash",
    )
    @classmethod
    def valid_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def verify_receipt(self) -> LongRunRawReceipt:
        if _safe_response_headers(self.response_headers) != self.response_headers:
            raise ValueError("long-run raw receipt contains non-canonical response headers")
        _validate_public_klines_url(
            self.request_url,
            "request_url",
            expected_symbol=self.symbol,
        )
        _validate_public_klines_url(
            self.response_url,
            "response_url",
            expected_symbol=self.symbol,
        )
        try:
            payload = base64.b64decode(self.payload_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("long-run raw payload is not valid base64") from exc
        if sha256(payload).hexdigest() != self.response_sha256:
            raise ValueError("long-run raw payload hash mismatch")
        expected_identity = _hash_payload(
            {
                "request_attempt_id": self.request_attempt_id,
                "request_url": self.request_url,
                "status_code": self.status_code,
                "response_sha256": self.response_sha256,
                "response_capture_hash": self.response_capture_hash,
                "source_snapshot_hash": self.source_snapshot_hash,
            }
        )
        if expected_identity != self.receipt_identity:
            raise ValueError("long-run raw receipt identity mismatch")
        expected_capture_hash = _hash_payload(
            {
                "response_url": self.response_url,
                "request_url": self.request_url,
                "status_code": self.status_code,
                "fetched_at": _iso(self.collected_at),
                "headers": [list(item) for item in self.response_headers],
                "response_sha256": self.response_sha256,
            }
        )
        if expected_capture_hash != self.response_capture_hash:
            raise ValueError("long-run HTTP capture identity mismatch")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("long-run raw receipt record hash mismatch")
        return self

    @property
    def payload(self) -> bytes:
        return base64.b64decode(self.payload_b64, validate=True)

    @classmethod
    def from_response(
        cls,
        response: HttpResponse,
        *,
        sequence: int,
        symbol: str,
        request_url: str,
        request_attempt_id: str,
        source_snapshot_hash: str,
        previous_record_hash: str | None,
    ) -> LongRunRawReceipt:
        payload = bytes(response.body)
        response_sha = sha256(payload).hexdigest()
        safe_headers = _safe_response_headers(response.headers)
        capture_hash = _hash_payload(
            {
                "response_url": response.url,
                "request_url": request_url,
                "status_code": response.status_code,
                "fetched_at": _iso(response.fetched_at),
                "headers": [list(item) for item in safe_headers],
                "response_sha256": response_sha,
            }
        )
        identity = _hash_payload(
            {
                "request_attempt_id": request_attempt_id,
                "request_url": request_url,
                "status_code": response.status_code,
                "response_sha256": response_sha,
                "response_capture_hash": capture_hash,
                "source_snapshot_hash": source_snapshot_hash,
            }
        )
        unsigned = {
            "schema": LONG_RUN_RAW_SCHEMA,
            "sequence": sequence,
            "symbol": symbol,
            "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
            "request_url": request_url,
            "request_attempt_id": request_attempt_id,
            "collected_at": _iso(response.fetched_at),
            "source_snapshot_hash": source_snapshot_hash,
            "status_code": response.status_code,
            "response_url": response.url,
            "response_headers": safe_headers,
            "response_sha256": response_sha,
            "response_capture_hash": capture_hash,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "receipt_identity": identity,
            "previous_record_hash": previous_record_hash,
        }
        return cls(**unsigned, record_hash=_hash_payload(unsigned))

    def view(self) -> LongRunRawReceiptView:
        return LongRunRawReceiptView(
            sequence=self.sequence,
            symbol=self.symbol,
            collected_at=self.collected_at,
            payload=self.payload,
            source_snapshot_hash=self.source_snapshot_hash,
            receipt_identity=self.receipt_identity,
            response_capture_hash=self.response_capture_hash,
            previous_record_hash=self.previous_record_hash,
            record_hash=self.record_hash,
        )


class LongRunRawSpool:
    """Append-only raw receipts; identical retry identities cannot count twice."""

    def __init__(
        self,
        path: Path,
        *,
        require_request_query: bool = False,
        source_snapshot_hash: str | None = None,
    ) -> None:
        self.path = path
        self.require_request_query = require_request_query
        self.source_snapshot_hash = (
            None
            if source_snapshot_hash is None
            else _digest(source_snapshot_hash, "source_snapshot_hash")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[LongRunRawReceipt] = []
        self.receipt_identities: set[str] = set()
        self.response_capture_hashes: set[str] = set()
        self.request_attempt_ids: set[str] = set()
        previous: str | None = None
        if path.exists():
            for line_number, line in enumerate(read_append_only_lines(path), 1):
                if not line.strip():
                    continue
                try:
                    record = LongRunRawReceipt.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"long-run raw spool is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("long-run raw spool hash chain is not continuous")
                if self.records and record.collected_at < self.records[-1].collected_at:
                    raise RuntimeError("long-run raw spool receipt chronology is not monotonic")
                if (
                    self.source_snapshot_hash is not None
                    and record.source_snapshot_hash != self.source_snapshot_hash
                ):
                    raise RuntimeError("long-run raw spool source snapshot identity differs")
                _validate_public_klines_url(
                    record.request_url,
                    "request_url",
                    require_query=self.require_request_query,
                    expected_symbol=record.symbol,
                )
                _validate_public_klines_url(
                    record.response_url,
                    "response_url",
                    expected_symbol=record.symbol,
                )
                if record.receipt_identity in self.receipt_identities:
                    raise RuntimeError("long-run raw spool contains a duplicate receipt identity")
                if record.response_capture_hash in self.response_capture_hashes:
                    raise RuntimeError("long-run raw spool contains a duplicate HTTP capture")
                if record.request_attempt_id in self.request_attempt_ids:
                    raise RuntimeError("long-run raw spool contains a duplicate request attempt")
                self.records.append(record)
                self.receipt_identities.add(record.receipt_identity)
                self.response_capture_hashes.add(record.response_capture_hash)
                self.request_attempt_ids.add(record.request_attempt_id)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(
        self,
        response: HttpResponse,
        *,
        symbol: str,
        request_url: str,
        request_attempt_id: str,
        source_snapshot_hash: str | None = None,
    ) -> LongRunRawReceipt:
        _validate_public_klines_url(
            request_url,
            "request_url",
            require_query=self.require_request_query,
            expected_symbol=symbol,
        )
        _validate_public_klines_url(response.url, "response_url", expected_symbol=symbol)
        snapshot_hash = source_snapshot_hash or self.source_snapshot_hash
        if snapshot_hash is None:
            raise ValueError("long-run raw receipt requires a source snapshot identity")
        snapshot_hash = _digest(snapshot_hash, "source_snapshot_hash")
        if self.source_snapshot_hash is not None and snapshot_hash != self.source_snapshot_hash:
            raise ValueError("long-run raw receipt source snapshot identity differs from spool")
        record = LongRunRawReceipt.from_response(
            response,
            sequence=len(self.records) + 1,
            symbol=symbol,
            request_url=request_url,
            request_attempt_id=request_attempt_id,
            source_snapshot_hash=snapshot_hash,
            previous_record_hash=self.last_record_hash,
        )
        if record.receipt_identity in self.receipt_identities:
            raise RuntimeError(
                "exact retry response cannot satisfy a distinct-receipt finality rule"
            )
        if record.response_capture_hash in self.response_capture_hashes:
            raise RuntimeError(
                "the same captured HTTP response cannot satisfy a distinct-receipt finality rule"
            )
        if record.request_attempt_id in self.request_attempt_ids:
            raise RuntimeError("a request attempt cannot produce two raw receipts")
        if self.records and record.collected_at < self.records[-1].collected_at:
            raise RuntimeError("long-run raw receipt arrived out of chronological order")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.receipt_identities.add(record.receipt_identity)
        self.response_capture_hashes.add(record.response_capture_hash)
        self.request_attempt_ids.add(record.request_attempt_id)
        return record

    def views(self) -> tuple[LongRunRawReceiptView, ...]:
        return tuple(record.view() for record in self.records)


class LongRunTransportFailure(BaseModel):
    """Non-market-data transport failure; it can never satisfy finality."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_TRANSPORT_FAILURE_SCHEMA] = LONG_RUN_TRANSPORT_FAILURE_SCHEMA
    sequence: int = Field(ge=1)
    symbol: str
    request_url: str = Field(min_length=1)
    request_attempt_id: str = Field(min_length=1)
    observed_at: datetime
    source_snapshot_hash: str
    error_class: str = Field(min_length=1, max_length=120)
    status_code: int | None = Field(default=None, ge=100, le=599)
    retriable: bool
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("symbol")
    @classmethod
    def fixed_failure_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("long-run transport failures are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("request_url")
    @classmethod
    def public_failure_endpoint(cls, value: str) -> str:
        return _validate_public_klines_url(value, "request_url", require_query=True)

    @field_validator("observed_at")
    @classmethod
    def aware_failure_time(cls, value: datetime) -> datetime:
        return _aware(value, "observed_at")

    @field_validator("error_class")
    @classmethod
    def safe_failure_class(cls, value: str) -> str:
        safe = "".join(character for character in value if character.isalnum() or character in "_-")
        if not safe:
            raise ValueError("transport failure class is empty")
        return safe[:120]

    @field_validator("source_snapshot_hash", "previous_record_hash", "record_hash")
    @classmethod
    def failure_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_failure(self) -> LongRunTransportFailure:
        _validate_public_klines_url(
            self.request_url,
            "request_url",
            require_query=True,
            expected_symbol=self.symbol,
        )
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("long-run transport failure hash mismatch")
        return self


class LongRunTransportFailureSpool:
    """Append-only source outage ledger kept separate from market receipts."""

    def __init__(self, path: Path, *, source_snapshot_hash: str | None = None) -> None:
        self.path = path
        self.source_snapshot_hash = (
            None
            if source_snapshot_hash is None
            else _digest(source_snapshot_hash, "source_snapshot_hash")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[LongRunTransportFailure] = []
        self._attempt_ids: set[str] = set()
        previous: str | None = None
        if path.exists():
            for line_number, line in enumerate(read_append_only_lines(path), 1):
                try:
                    record = LongRunTransportFailure.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"long-run transport failure ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("long-run transport failure ledger chain is not continuous")
                if record.request_attempt_id in self._attempt_ids:
                    raise RuntimeError("long-run transport failure ledger reuses a request attempt")
                if (
                    self.source_snapshot_hash is not None
                    and record.source_snapshot_hash != self.source_snapshot_hash
                ):
                    raise RuntimeError(
                        "long-run transport failure source snapshot identity differs"
                    )
                self.records.append(record)
                self._attempt_ids.add(record.request_attempt_id)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(
        self,
        *,
        symbol: str,
        request_url: str,
        request_attempt_id: str,
        observed_at: datetime,
        source_snapshot_hash: str | None = None,
        error_class: str,
        status_code: int | None,
        retriable: bool,
    ) -> LongRunTransportFailure:
        if request_attempt_id in self._attempt_ids:
            raise RuntimeError("long-run transport failure request attempt already recorded")
        snapshot_hash = source_snapshot_hash or self.source_snapshot_hash
        if snapshot_hash is None:
            raise ValueError("long-run transport failure requires a source snapshot identity")
        snapshot_hash = _digest(snapshot_hash, "source_snapshot_hash")
        if self.source_snapshot_hash is not None and snapshot_hash != self.source_snapshot_hash:
            raise ValueError("long-run transport failure source snapshot identity differs")
        unsigned = {
            "schema": LONG_RUN_TRANSPORT_FAILURE_SCHEMA,
            "sequence": len(self.records) + 1,
            "symbol": symbol.strip().upper(),
            "request_url": request_url,
            "request_attempt_id": request_attempt_id,
            "observed_at": _iso(observed_at),
            "source_snapshot_hash": snapshot_hash,
            "error_class": error_class,
            "status_code": status_code,
            "retriable": retriable,
            "previous_record_hash": self.last_record_hash,
        }
        record = LongRunTransportFailure(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self._attempt_ids.add(record.request_attempt_id)
        return record


class LongRunFinalityTracker:
    """Long-run-owned wrapper around the V3B-validated finality implementation."""

    def __init__(
        self,
        normalized_path: Path | ForwardNormalizedBarSpool,
        revision_path: Path,
        *,
        minimum_interval_end: datetime | None = None,
        source_snapshot_hash: str | None = None,
    ) -> None:
        # Validate before constructing the shared dictionary-backed spool.
        # That older spool intentionally collapses duplicate identities;
        # long-run monitoring must preserve duplicate-line corruption signals.
        if isinstance(normalized_path, Path) and normalized_path.exists():
            read_normalized_bars_stable(normalized_path)
        self.normalized = (
            normalized_path
            if isinstance(normalized_path, ForwardNormalizedBarSpool)
            else ForwardNormalizedBarSpool(normalized_path)
        )
        self.minimum_interval_end = (
            None
            if minimum_interval_end is None
            else _aware(minimum_interval_end, "minimum_interval_end")
        )
        self.source_snapshot_hash = (
            None
            if source_snapshot_hash is None
            else _digest(source_snapshot_hash, "source_snapshot_hash")
        )
        self._tracker = CanaryFinalityTracker(
            self.normalized,
            revision_path,
            guard_seconds=LONG_RUN_FINALITY_GUARD_SECONDS,
            repeat_receipts=LONG_RUN_REPEAT_RECEIPTS,
        )

    @property
    def revisions(self):
        return self._tracker.revisions

    @property
    def admissions(self):
        return self._tracker._admissions

    def observe(self, raw: LongRunRawReceipt, payload_bars: Sequence[object]) -> tuple[object, ...]:
        if (
            self.source_snapshot_hash is not None
            and raw.source_snapshot_hash != self.source_snapshot_hash
        ):
            raise RuntimeError("long-run raw receipt source snapshot identity differs")
        bars = tuple(
            bar
            for bar in payload_bars
            if self.minimum_interval_end is None or bar.interval_end >= self.minimum_interval_end
        )
        return self._tracker.observe(raw.view(), bars)

    def replay(
        self,
        raw: Sequence[LongRunRawReceipt | LongRunRawReceiptView],
        source_snapshot_hash: str,
        *,
        persist_missing: bool = True,
    ) -> None:
        source_snapshot_hash = _digest(source_snapshot_hash, "source_snapshot_hash")
        if (
            self.source_snapshot_hash is not None
            and source_snapshot_hash != self.source_snapshot_hash
        ):
            raise RuntimeError("long-run replay source snapshot identity differs from tracker")
        raw_views = tuple(
            item.view() if isinstance(item, LongRunRawReceipt) else item for item in raw
        )
        if any(item.source_snapshot_hash != source_snapshot_hash for item in raw_views):
            raise RuntimeError("raw replay source snapshot identity differs")
        self._tracker.replay(
            raw_views,
            source_snapshot_hash,
            minimum_interval_end=self.minimum_interval_end,
            persist_missing=persist_missing,
        )

    def metrics(self) -> dict[str, object]:
        return self._tracker.metrics()


class LongRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_EVENT_SCHEMA] = LONG_RUN_EVENT_SCHEMA
    sequence: int = Field(ge=1)
    generation_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    state: LongRunState
    observed_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("observed_at")
    @classmethod
    def aware_event_time(cls, value: datetime) -> datetime:
        return _aware(value, "observed_at")

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def event_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_event(self) -> LongRunEvent:
        _safe_payload(self.payload)
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("long-run event hash mismatch")
        return self


class LongRunAccountingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_opportunities_per_symbol: int = LONG_RUN_TARGET_CASES_PER_SYMBOL
    minimum_clean_cases_per_symbol: int = LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL
    opportunities_elapsed: dict[str, int]
    predictions_durable: dict[str, int]
    cases_pending_outcome: dict[str, int]
    clean_cases_provisional: dict[str, int]
    cases_excluded: dict[str, int]
    cases_terminally_certified: dict[str, int]
    opportunities_remaining: dict[str, int]
    feasible: dict[str, bool]


_STATE_TRANSITIONS: dict[LongRunState, frozenset[LongRunState]] = {
    LongRunState.PREREGISTERED: frozenset(
        {LongRunState.PREFLIGHT_READY, LongRunState.GENERATION_FATAL}
    ),
    LongRunState.PREFLIGHT_READY: frozenset(
        {LongRunState.RUNNING_WARMUP, LongRunState.GENERATION_FATAL}
    ),
    LongRunState.RUNNING_WARMUP: frozenset({LongRunState.RUNNING, LongRunState.GENERATION_FATAL}),
    LongRunState.RUNNING: frozenset(
        {
            LongRunState.RECOVERING_COMPONENT,
            LongRunState.TERMINALIZING,
            LongRunState.GENERATION_FATAL,
        }
    ),
    LongRunState.RECOVERING_COMPONENT: frozenset(
        {LongRunState.RUNNING, LongRunState.GENERATION_FATAL}
    ),
    LongRunState.TERMINALIZING: frozenset(
        {
            LongRunState.DEADLINE_REACHED,
            LongRunState.COMPLETED_PENDING_AUDIT,
            LongRunState.GENERATION_FATAL,
        }
    ),
    LongRunState.DEADLINE_REACHED: frozenset(
        {LongRunState.COMPLETED_PENDING_AUDIT, LongRunState.GENERATION_FATAL}
    ),
    LongRunState.GENERATION_FATAL: frozenset(
        {LongRunState.TERMINALIZING, LongRunState.COMPLETED_PENDING_AUDIT, LongRunState.AUDITED}
    ),
    LongRunState.COMPLETED_PENDING_AUDIT: frozenset({LongRunState.AUDITED}),
    LongRunState.AUDITED: frozenset(),
}

_CASE_EVENT_TYPES = frozenset({"CASE_PENDING", "CASE_EXCLUDED", "CASE_CLEAN"})
_CASE_ACCOUNTING_STATES = frozenset(
    {
        LongRunState.RUNNING,
        LongRunState.RECOVERING_COMPONENT,
        LongRunState.TERMINALIZING,
        LongRunState.DEADLINE_REACHED,
    }
)
_CASE_EVENT_STATES = {
    "CASE_PENDING": LongRunCaseState.PENDING_OUTCOME,
    "CASE_EXCLUDED": LongRunCaseState.EXCLUDED,
    "CASE_CLEAN": LongRunCaseState.CLEAN,
}
_KNOWN_EVENT_TYPES = frozenset(
    {
        "PREFLIGHT_READY",
        "CANDIDATE_STARTED",
        "RUNNING_WARMUP",
        "WARMUP_COMPLETE",
        "DEADLINE_REACHED",
        "CANDIDATE_TERMINAL",
        "GENERATION_FATAL",
        "CASE_PENDING",
        "CASE_EXCLUDED",
        "CASE_CLEAN",
        "RECOVERY_ATTEMPT",
        "WATCHDOG_CHECK",
    }
)
_FIXED_EVENT_STATES = {
    "PREFLIGHT_READY": LongRunState.PREFLIGHT_READY,
    "CANDIDATE_STARTED": LongRunState.RUNNING_WARMUP,
    "RUNNING_WARMUP": LongRunState.RUNNING_WARMUP,
    "WARMUP_COMPLETE": LongRunState.RUNNING,
    "DEADLINE_REACHED": LongRunState.TERMINALIZING,
    "CANDIDATE_TERMINAL": LongRunState.DEADLINE_REACHED,
}


class LongRunCoordinator:
    """Append-only state machine and independent 80/64 accounting."""

    def __init__(self, preregistration: LongRunPreregistration, events_path: Path) -> None:
        self.preregistration = preregistration
        self.events_path = events_path
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[LongRunEvent] = []
        self.state = LongRunState.PREREGISTERED
        self.fatal_latched = False
        self.fatal_events: list[LongRunEvent] = []
        self._cases: dict[tuple[str, int], tuple[LongRunCaseState, str | None, str | None]] = {}
        self._recovery_counts: dict[tuple[str, str], int] = {}
        self._recovery_component_counts: dict[str, int] = {}
        if events_path.exists():
            self._load()

    def _load(self) -> None:
        previous: str | None = None
        for line_number, line in enumerate(read_append_only_lines(self.events_path), 1):
            if not line.strip():
                continue
            try:
                event = LongRunEvent.model_validate_json(line)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"long-run event ledger is corrupt at line {line_number}"
                ) from exc
            if event.sequence != len(self.events) + 1 or event.previous_record_hash != previous:
                raise RuntimeError("long-run event hash chain is not continuous")
            if event.generation_id != self.preregistration.generation_id:
                raise RuntimeError("long-run event generation identity mismatch")
            self._apply(event, replay=True)
            self.events.append(event)
            previous = event.record_hash

    def _reset_projection(self) -> None:
        self.events = []
        self.state = LongRunState.PREREGISTERED
        self.fatal_latched = False
        self.fatal_events = []
        self._cases = {}
        self._recovery_counts = {}
        self._recovery_component_counts = {}

    def _refresh(self) -> None:
        """Reconstruct the projection so multiple local roles cannot append from stale state."""

        if not self.events_path.exists():
            return
        self._reset_projection()
        self._load()

    @property
    def last_record_hash(self) -> str | None:
        return self.events[-1].record_hash if self.events else None

    def _validate_append(
        self, event_type: str, state: LongRunState, payload: Mapping[str, object]
    ) -> None:
        """Validate against the locked projection before any bytes are written."""

        # Replay must enforce the same schema/state/security rules as a live
        # append.  The event hash proves byte integrity; this proves semantic
        # integrity after a crash or restart.
        _safe_payload(payload)
        if event_type not in _KNOWN_EVENT_TYPES:
            raise RuntimeError(f"unknown long-run event type: {event_type}")
        expected_event_state = _FIXED_EVENT_STATES.get(event_type)
        if expected_event_state is not None and state != expected_event_state:
            raise RuntimeError(f"event {event_type} cannot use state {state}")
        if state != self.state and state not in _STATE_TRANSITIONS[self.state]:
            raise RuntimeError(f"invalid long-run state transition {self.state} -> {state}")
        if self.fatal_latched and state in {
            LongRunState.PREFLIGHT_READY,
            LongRunState.RUNNING_WARMUP,
            LongRunState.RUNNING,
            LongRunState.RECOVERING_COMPONENT,
        }:
            raise RuntimeError("fatal long-run state is absorbing")
        if (
            state == LongRunState.GENERATION_FATAL
            and event_type != "GENERATION_FATAL"
            and not self.fatal_latched
        ):
            raise RuntimeError("only the generation-fatal event may first enter fatal state")
        if event_type == "GENERATION_FATAL" and state != LongRunState.GENERATION_FATAL:
            raise RuntimeError("generation-fatal event must use the fatal state")
        if event_type == "GENERATION_FATAL":
            self._validate_fatal_payload(payload)
        if event_type == "WATCHDOG_CHECK":
            self._validate_watchdog_payload(state=state, payload=payload)
        if event_type in {"CASE_PENDING", "CASE_EXCLUDED", "CASE_CLEAN"}:
            if self.fatal_latched:
                raise RuntimeError("fatal long-run state cannot accept case accounting")
            if self.state not in _CASE_ACCOUNTING_STATES:
                raise RuntimeError(
                    "case accounting is only valid while the run is active or terminalizing"
                )
            try:
                key = self._validate_key(str(payload["symbol"]), int(payload["ordinal"]))
                desired_state = LongRunCaseState(str(payload["case_state"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("long-run case event has invalid identity") from exc
            expected_cutoff = _iso(self.preregistration.mandatory_cutoffs[key[1] - 1])
            if payload.get("cutoff") != expected_cutoff:
                raise RuntimeError(
                    "long-run case event cutoff is not the exact preregistered cutoff"
                )
            if desired_state != _CASE_EVENT_STATES[event_type]:
                raise RuntimeError("long-run case event type and state disagree")
            if event_type == "CASE_EXCLUDED" and not str(payload.get("reason", "")).strip():
                raise RuntimeError("excluded case requires a non-blank reason")
            desired = (
                desired_state,
                str(payload["prediction_id"]) if payload.get("prediction_id") is not None else None,
                str(payload["outcome_case_id"])
                if payload.get("outcome_case_id") is not None
                else None,
            )
            if event_type == "CASE_PENDING" and not desired[1]:
                raise RuntimeError("pending case requires a prediction identity")
            if event_type == "CASE_CLEAN" and (not desired[1] or not desired[2]):
                raise RuntimeError("clean case requires prediction and outcome identities")
            prior = self._cases.get(key)
            for other_key, other in self._cases.items():
                if other_key == key:
                    continue
                if desired[1] is not None and other[1] == desired[1]:
                    raise RuntimeError("prediction identity is already bound to another case")
                if desired[2] is not None and other[2] == desired[2]:
                    raise RuntimeError("outcome identity is already bound to another case")
            if prior is not None:
                is_pending_to_excluded = (
                    event_type == "CASE_EXCLUDED"
                    and prior[0] == LongRunCaseState.PENDING_OUTCOME
                    and desired[0] == LongRunCaseState.EXCLUDED
                    and prior[1] == desired[1]
                    and desired[2] is None
                )
                is_pending_to_clean = (
                    event_type == "CASE_CLEAN"
                    and prior[0] == LongRunCaseState.PENDING_OUTCOME
                    and desired[0] == LongRunCaseState.CLEAN
                    and prior[1] == desired[1]
                )
                if prior != desired and not (is_pending_to_excluded or is_pending_to_clean):
                    raise RuntimeError("long-run event would contradict existing case accounting")
                if not (is_pending_to_excluded or is_pending_to_clean):
                    raise RuntimeError("duplicate case accounting event")
            if self.fatal_latched:
                raise RuntimeError("case accounting cannot advance after generation fatal")
        if event_type == "RECOVERY_ATTEMPT":
            if self.state not in {
                LongRunState.RUNNING_WARMUP,
                LongRunState.RUNNING,
                LongRunState.RECOVERING_COMPONENT,
            }:
                raise RuntimeError("component recovery is only valid while the run is active")
            self._validate_recovery_payload(payload)
            key = (str(payload["component"]), str(payload["incident_id"]))
            if self._recovery_counts.get(key, 0) >= 1:
                raise RuntimeError("long-run recovery exceeded one attempt per component incident")

    def _validate_watchdog_payload(
        self, *, state: LongRunState, payload: Mapping[str, object]
    ) -> None:
        """Validate watchdog observations against the same append-only state."""

        if state != self.state:
            raise RuntimeError("watchdog observation cannot change coordinator state")
        decision = payload.get("decision")
        if decision not in {"LONG_RUN_HEALTHY", "GENERATION_FATAL"}:
            raise RuntimeError("watchdog observation has an unknown decision")
        reasons = payload.get("reasons")
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
            raise RuntimeError("watchdog observation reasons must be a string list")
        if decision == "GENERATION_FATAL":
            if not self.fatal_latched or state != LongRunState.GENERATION_FATAL:
                raise RuntimeError("watchdog fatal observation lacks a latched fatal state")
        elif self.fatal_latched or state == LongRunState.GENERATION_FATAL:
            raise RuntimeError("healthy watchdog observation contradicts fatal history")

    @staticmethod
    def _validate_fatal_payload(payload: Mapping[str, object]) -> None:
        """Keep the absorbing incident record typed and reconstructible."""

        incident = payload.get("incident")
        detail = payload.get("detail")
        if not isinstance(incident, str) or not incident.strip():
            raise RuntimeError("generation-fatal event has no incident identity")
        try:
            LongRunIncident(incident)
        except ValueError as exc:
            raise RuntimeError("generation-fatal event incident identity is unknown") from exc
        if not isinstance(detail, str):
            raise RuntimeError("generation-fatal event detail is not a string")

    def _append(
        self, event_type: str, state: LongRunState, payload: Mapping[str, object], at: datetime
    ) -> LongRunEvent:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        payload_dict = dict(payload)
        _safe_payload(payload_dict)
        lock_path = self.events_path.with_name(f".{self.events_path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._refresh()
                observed_at = _aware(at, "event_observed_at")
                unsigned = {
                    "schema": LONG_RUN_EVENT_SCHEMA,
                    "sequence": len(self.events) + 1,
                    "generation_id": self.preregistration.generation_id,
                    "event_type": event_type,
                    "state": state,
                    "observed_at": _iso(observed_at),
                    "payload": payload_dict,
                    "previous_record_hash": self.last_record_hash,
                }
                event = LongRunEvent(**unsigned, record_hash=_hash_payload(unsigned))
                self._validate_append(event_type, state, payload_dict)
                with self.events_path.open("a", encoding="utf-8") as handle:
                    handle.write(event.model_dump_json() + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                self._apply(event, replay=False)
                self.events.append(event)
                return event
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _validate_key(self, symbol: str, ordinal: int) -> tuple[str, int]:
        normalized = symbol.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("long-run accounting is restricted to BTCUSDT and ETHUSDT")
        if ordinal < 1 or ordinal > self.preregistration.target_opportunities_per_symbol:
            raise ValueError("cutoff ordinal is outside the preregistered 1..80 range")
        return normalized, ordinal

    def _validate_cutoff(self, symbol: str, ordinal: int, cutoff: datetime) -> tuple[str, int]:
        key = self._validate_key(symbol, ordinal)
        expected = self.preregistration.mandatory_cutoffs[ordinal - 1]
        if _aware(cutoff, "cutoff") != expected:
            raise ValueError("cutoff does not match its preregistered ordinal")
        return key

    def _validate_recovery_payload(self, payload: Mapping[str, object]) -> None:
        """Validate every recovery identity before it enters append-only truth."""

        component = payload.get("component")
        if not isinstance(component, str) or component not in {"collector", "candidate"}:
            raise RuntimeError("long-run recovery component is not recoverable")
        incident_id = payload.get("incident_id")
        failure = payload.get("failure")
        if not isinstance(incident_id, str) or not incident_id.strip():
            raise RuntimeError("recovery incident identity is missing")
        if not isinstance(failure, str) or not failure.strip():
            raise RuntimeError("recovery failure detail is missing")
        for name in ("old_pid", "new_pid"):
            value = payload.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RuntimeError(f"recovery {name} is invalid")
        for name in ("old_command_identity", "new_command_identity"):
            value = payload.get(name)
            if not isinstance(value, str):
                raise RuntimeError(f"recovery {name} is missing")
            try:
                _digest(value, name)
            except ValueError as exc:
                raise RuntimeError(f"recovery {name} is invalid") from exc
        before_hashes = payload.get("before_hashes")
        if not isinstance(before_hashes, Mapping) or not before_hashes:
            raise RuntimeError("recovery state hashes are missing")
        for name, value in before_hashes.items():
            try:
                _digest(str(value), f"before_hashes.{name}")
            except ValueError as exc:
                raise RuntimeError(f"recovery state hash is invalid: {name}") from exc
        if (
            payload.get("recovery_ordinal")
            != self._recovery_counts.get((component, incident_id), 0) + 1
        ):
            raise RuntimeError("recovery ordinal is not monotonic")
        component_limit = (
            self.preregistration.recovery_policy.collector_max_attempts_total
            if component == "collector"
            else self.preregistration.recovery_policy.candidate_max_attempts_total
        )
        if self._recovery_component_counts.get(component, 0) >= component_limit:
            raise RuntimeError("long-run recovery exceeded the component-wide cap")
        if (
            payload.get("component_recovery_ordinal")
            != self._recovery_component_counts.get(component, 0) + 1
        ):
            raise RuntimeError("component recovery ordinal is not monotonic")
        if payload.get("repository_commit") != self.preregistration.repository_commit:
            raise RuntimeError("recovery repository identity differs from preregistration")
        expected_component_hash = (
            self.preregistration.collector_code_sha256
            if component == "collector"
            else self.preregistration.candidate_worker_code_sha256
        )
        if payload.get("component_code_sha256") != expected_component_hash:
            raise RuntimeError("recovery component identity differs from preregistration")
        if payload.get("model_checkpoint_sha256") != self.preregistration.checkpoint_sha256:
            raise RuntimeError("recovery model checkpoint identity differs from preregistration")
        runtime_lock_hashes = payload.get("runtime_lock_hashes")
        if runtime_lock_hashes != {
            "uv_lock_sha256": self.preregistration.uv_lock_sha256,
            "requirements_lock_sha256": self.preregistration.requirements_lock_sha256,
        }:
            raise RuntimeError("recovery runtime-lock identity differs from preregistration")
        if not isinstance(payload.get("resume_validation_passed"), bool):
            raise RuntimeError("recovery validation result is missing")
        impacted = payload.get("cutoffs_impacted")
        if (
            not isinstance(impacted, list)
            or any(
                not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or ordinal < 1
                or ordinal > self.preregistration.target_opportunities_per_symbol
                for ordinal in impacted
            )
            or len(impacted) != len(set(impacted))
            or impacted != sorted(impacted)
        ):
            raise RuntimeError("recovery cutoff impact list is invalid")

    def _apply(self, event: LongRunEvent, *, replay: bool) -> None:
        # Use exactly the live append validator during reconstruction.  A
        # valid record hash must not make a malformed case/recovery/fatal
        # payload acceptable after restart.
        self._validate_append(event.event_type, event.state, event.payload)
        self.state = event.state
        if event.state == LongRunState.GENERATION_FATAL:
            self.fatal_latched = True
            # WATCHDOG_CHECK records the already-latched state as a derived
            # observation.  Only the append-only GENERATION_FATAL event is a
            # fatal incident; otherwise every later health check would inflate
            # the fatal-event count and make history non-reconstructible.
            if event.event_type == "GENERATION_FATAL":
                self.fatal_events.append(event)
            elif not self.fatal_events:
                raise RuntimeError("fatal state has no append-only fatal incident")
        payload = event.payload
        if event.event_type in {"CASE_PENDING", "CASE_EXCLUDED", "CASE_CLEAN"}:
            if self.state not in _CASE_ACCOUNTING_STATES:
                raise RuntimeError(
                    "long-run event ledger contains case accounting outside an active or terminalizing run"
                )
            key = (str(payload["symbol"]), int(payload["ordinal"]))
            normalized_key = self._validate_key(key[0], key[1])
            if payload.get("cutoff") != _iso(
                self.preregistration.mandatory_cutoffs[normalized_key[1] - 1]
            ):
                raise RuntimeError("long-run event ledger contains a non-preregistered case cutoff")
            key = normalized_key
            status = LongRunCaseState(str(payload["case_state"]))
            if status != _CASE_EVENT_STATES[event.event_type]:
                raise RuntimeError("long-run case event type and state disagree")
            prior = self._cases.get(key)
            current = (
                status,
                payload.get("prediction_id"),
                payload.get("outcome_case_id"),
            )
            is_pending_to_clean = (
                event.event_type == "CASE_CLEAN"
                and prior is not None
                and prior[0] == LongRunCaseState.PENDING_OUTCOME
                and status == LongRunCaseState.CLEAN
                and prior[1] == current[1]
            )
            is_pending_to_excluded = (
                event.event_type == "CASE_EXCLUDED"
                and prior is not None
                and prior[0] == LongRunCaseState.PENDING_OUTCOME
                and status == LongRunCaseState.EXCLUDED
                and prior[1] == current[1]
                and current[2] is None
            )
            if (
                prior is not None
                and prior != current
                and not (is_pending_to_clean or is_pending_to_excluded)
            ):
                raise RuntimeError("long-run event ledger contains a contradictory case state")
            for other_key, other in self._cases.items():
                if other_key == key:
                    continue
                if current[1] is not None and other[1] == current[1]:
                    raise RuntimeError("long-run event ledger reuses a prediction identity")
                if current[2] is not None and other[2] == current[2]:
                    raise RuntimeError("long-run event ledger reuses an outcome identity")
            if prior is not None and not (is_pending_to_clean or is_pending_to_excluded):
                raise RuntimeError("duplicate case accounting event")
            self._cases[key] = (
                status,
                str(payload["prediction_id"]) if payload.get("prediction_id") is not None else None,
                str(payload["outcome_case_id"])
                if payload.get("outcome_case_id") is not None
                else None,
            )
        if event.event_type == "RECOVERY_ATTEMPT":
            if self.state not in {
                LongRunState.RUNNING_WARMUP,
                LongRunState.RUNNING,
                LongRunState.RECOVERING_COMPONENT,
            }:
                raise RuntimeError("long-run event ledger contains recovery outside an active run")
            self._validate_recovery_payload(payload)
            component = str(payload["component"])
            incident = str(payload["incident_id"])
            key = (component, incident)
            self._recovery_counts[key] = self._recovery_counts.get(key, 0) + 1
            if self._recovery_counts[key] > 1:
                raise RuntimeError("long-run recovery exceeded one attempt per component incident")
            self._recovery_component_counts[component] = (
                self._recovery_component_counts.get(component, 0) + 1
            )
            component_limit = (
                self.preregistration.recovery_policy.collector_max_attempts_total
                if component == "collector"
                else self.preregistration.recovery_policy.candidate_max_attempts_total
            )
            if self._recovery_component_counts[component] > component_limit:
                raise RuntimeError("long-run recovery exceeded the component-wide cap")

    @property
    def scientific_state(self) -> LongRunState:
        self._refresh()
        return LongRunState.GENERATION_FATAL if self.fatal_latched else self.state

    def transition(
        self,
        state: LongRunState,
        *,
        event_type: str,
        at: datetime,
        payload: Mapping[str, object] = (),
    ) -> LongRunEvent | None:
        self._refresh()
        state = LongRunState(state)
        if self.fatal_latched and state in {
            LongRunState.PREFLIGHT_READY,
            LongRunState.RUNNING_WARMUP,
            LongRunState.RUNNING,
            LongRunState.RECOVERING_COMPONENT,
        }:
            raise RuntimeError("fatal long-run state is absorbing")
        if state == self.state:
            return None
        if state not in _STATE_TRANSITIONS[self.state]:
            raise RuntimeError(f"invalid long-run state transition {self.state} -> {state}")
        return self._append(event_type, state, payload, at)

    def fail(
        self, incident: LongRunIncident | str, *, at: datetime, detail: str = ""
    ) -> LongRunEvent:
        self._refresh()
        if self.fatal_latched:
            return self.fatal_events[0]
        return self._append(
            "GENERATION_FATAL",
            LongRunState.GENERATION_FATAL,
            {"incident": str(incident), "detail": detail},
            at,
        )

    def record_case_pending(
        self, *, symbol: str, ordinal: int, cutoff: datetime, prediction_id: str, at: datetime
    ) -> LongRunEvent | None:
        self._refresh()
        key = self._validate_cutoff(symbol, ordinal, cutoff)
        prior = self._cases.get(key)
        desired = (LongRunCaseState.PENDING_OUTCOME, prediction_id, None)
        if prior == desired:
            return None
        if prior is not None:
            raise RuntimeError("case cannot be assigned a second prediction")
        return self._append(
            "CASE_PENDING",
            self.state,
            {
                "symbol": key[0],
                "ordinal": key[1],
                "cutoff": _iso(cutoff),
                "case_state": desired[0],
                "prediction_id": prediction_id,
            },
            at,
        )

    def record_case_excluded(
        self,
        *,
        symbol: str,
        ordinal: int,
        cutoff: datetime,
        reason: str,
        at: datetime,
        prediction_id: str | None = None,
    ) -> LongRunEvent | None:
        self._refresh()
        key = self._validate_cutoff(symbol, ordinal, cutoff)
        prior = self._cases.get(key)
        if prior is not None and prior[0] == LongRunCaseState.PENDING_OUTCOME:
            if not prediction_id or prediction_id != prior[1]:
                raise RuntimeError("pending case exclusion must retain its prediction identity")
        elif prior is not None and prediction_id is not None:
            raise RuntimeError("excluded case cannot acquire a prediction identity")
        desired = (LongRunCaseState.EXCLUDED, prediction_id, None)
        if prior == desired:
            return None
        if prior is not None and prior[0] != LongRunCaseState.PENDING_OUTCOME:
            raise RuntimeError("a predicted or certified case cannot be silently excluded")
        event = self._append(
            "CASE_EXCLUDED",
            self.state,
            {
                "symbol": key[0],
                "ordinal": key[1],
                "cutoff": _iso(cutoff),
                "case_state": desired[0],
                "reason": reason,
                "prediction_id": prediction_id,
            },
            at,
        )
        if event is not None and not all(self.accounting().feasible.values()):
            self.fail(
                "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
                at=at,
                detail="clean minimum is no longer mathematically attainable",
            )
        return event

    def record_case_clean(
        self,
        *,
        symbol: str,
        ordinal: int,
        cutoff: datetime,
        prediction_id: str,
        outcome_case_id: str,
        at: datetime,
    ) -> LongRunEvent | None:
        self._refresh()
        key = self._validate_cutoff(symbol, ordinal, cutoff)
        prior = self._cases.get(key)
        desired = (LongRunCaseState.CLEAN, prediction_id, outcome_case_id)
        if prior == desired:
            return None
        if prior != (LongRunCaseState.PENDING_OUTCOME, prediction_id, None):
            raise RuntimeError("clean case must follow its exact pending prediction")
        return self._append(
            "CASE_CLEAN",
            self.state,
            {
                "symbol": key[0],
                "ordinal": key[1],
                "cutoff": _iso(cutoff),
                "case_state": desired[0],
                "prediction_id": prediction_id,
                "outcome_case_id": outcome_case_id,
            },
            at,
        )

    def record_recovery(
        self,
        *,
        incident_id: str,
        component: str,
        old_pid: int,
        old_command_identity: str,
        failure: str,
        before_hashes: Mapping[str, str],
        new_pid: int,
        new_command_identity: str,
        resume_valid: bool,
        cutoffs_impacted: Sequence[int] = (),
        at: datetime,
    ) -> LongRunEvent:
        self._refresh()
        if self.fatal_latched:
            raise RuntimeError("component recovery cannot follow a generation fatal")
        if component == "watchdog":
            raise RuntimeError(
                "watchdog process death is generation-fatal in the V1 long-run contract"
            )
        if not isinstance(component, str) or component not in {"collector", "candidate"}:
            raise ValueError("only collector and candidate recovery are supported")
        if not incident_id.strip() or not failure.strip():
            raise ValueError("recovery incidents require stable identity and failure detail")
        _digest(old_command_identity, "old_command_identity")
        _digest(new_command_identity, "new_command_identity")
        for name, value in before_hashes.items():
            _digest(str(value), f"before_hashes.{name}")
        if any(
            int(ordinal) < 1 or int(ordinal) > self.preregistration.target_opportunities_per_symbol
            for ordinal in cutoffs_impacted
        ):
            raise ValueError("recovery cutoffs must be within the preregistered schedule")
        key = (component, incident_id)
        if self._recovery_counts.get(key, 0) >= 1:
            raise RuntimeError("long-run recovery exceeded one attempt per component incident")
        payload = {
            "incident_id": incident_id,
            "component": component,
            "old_pid": old_pid,
            "old_command_identity": old_command_identity,
            "failure": failure,
            "before_hashes": dict(before_hashes),
            "recovery_ordinal": self._recovery_counts.get(key, 0) + 1,
            "component_recovery_ordinal": self._recovery_component_counts.get(component, 0) + 1,
            "repository_commit": self.preregistration.repository_commit,
            "component_code_sha256": (
                self.preregistration.collector_code_sha256
                if component == "collector"
                else self.preregistration.candidate_worker_code_sha256
            ),
            "model_checkpoint_sha256": self.preregistration.checkpoint_sha256,
            "runtime_lock_hashes": {
                "uv_lock_sha256": self.preregistration.uv_lock_sha256,
                "requirements_lock_sha256": self.preregistration.requirements_lock_sha256,
            },
            "new_pid": new_pid,
            "new_command_identity": new_command_identity,
            "resume_validation_passed": resume_valid,
            "cutoffs_impacted": list(cutoffs_impacted),
        }
        event = self._append("RECOVERY_ATTEMPT", self.state, payload, at)
        if not resume_valid:
            self.fail(LongRunIncident.IDENTITY_MISMATCH, at=at, detail="resume validation failed")
        return event

    def record_watchdog_check(
        self, *, decision: str, reasons: Sequence[str], at: datetime
    ) -> LongRunEvent:
        self._refresh()
        if decision not in {"LONG_RUN_HEALTHY", "GENERATION_FATAL"}:
            raise ValueError("watchdog decision is not part of the long-run contract")
        if self.fatal_latched and decision == "LONG_RUN_HEALTHY":
            raise RuntimeError(
                "a latched generation fatal cannot emit a healthy scientific decision"
            )
        if decision == "GENERATION_FATAL" and not self.fatal_latched:
            raise RuntimeError("watchdog fatal observations require a latched fatal state")
        return self._append(
            "WATCHDOG_CHECK",
            self.scientific_state,
            {"decision": decision, "reasons": list(reasons)},
            at,
        )

    def accounting(self) -> LongRunAccountingSnapshot:
        self._refresh()
        result: dict[str, dict[str, int]] = {}
        for symbol in V3_CORE_SYMBOLS:
            states = [
                state
                for (item, _ordinal), (state, _prediction, _outcome) in self._cases.items()
                if item == symbol
            ]
            result[symbol] = {
                "opportunities_elapsed": len(states),
                "predictions_durable": sum(
                    state in {LongRunCaseState.PENDING_OUTCOME, LongRunCaseState.CLEAN}
                    for state in states
                ),
                "cases_pending_outcome": states.count(LongRunCaseState.PENDING_OUTCOME),
                # A durable prediction whose genuine outcome is still pending
                # can still become a clean case.  It therefore remains part of
                # feasibility, while only CLEAN counts as terminally certified.
                "clean_cases_provisional": sum(
                    state in {LongRunCaseState.PENDING_OUTCOME, LongRunCaseState.CLEAN}
                    for state in states
                ),
                "cases_excluded": states.count(LongRunCaseState.EXCLUDED),
                "cases_terminally_certified": states.count(LongRunCaseState.CLEAN),
                "opportunities_remaining": self.preregistration.target_opportunities_per_symbol
                - len(states),
            }
        feasible = {
            symbol: result[symbol]["clean_cases_provisional"]
            + result[symbol]["opportunities_remaining"]
            >= self.preregistration.minimum_clean_cases_per_symbol
            for symbol in V3_CORE_SYMBOLS
        }
        return LongRunAccountingSnapshot(
            opportunities_elapsed={
                symbol: result[symbol]["opportunities_elapsed"] for symbol in V3_CORE_SYMBOLS
            },
            predictions_durable={
                symbol: result[symbol]["predictions_durable"] for symbol in V3_CORE_SYMBOLS
            },
            cases_pending_outcome={
                symbol: result[symbol]["cases_pending_outcome"] for symbol in V3_CORE_SYMBOLS
            },
            clean_cases_provisional={
                symbol: result[symbol]["clean_cases_provisional"] for symbol in V3_CORE_SYMBOLS
            },
            cases_excluded={symbol: result[symbol]["cases_excluded"] for symbol in V3_CORE_SYMBOLS},
            cases_terminally_certified={
                symbol: result[symbol]["cases_terminally_certified"] for symbol in V3_CORE_SYMBOLS
            },
            opportunities_remaining={
                symbol: result[symbol]["opportunities_remaining"] for symbol in V3_CORE_SYMBOLS
            },
            feasible=feasible,
        )

    def case_states(self) -> dict[tuple[str, int], tuple[LongRunCaseState, str | None, str | None]]:
        self._refresh()
        return dict(self._cases)


class LongRunPredictionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_PREDICTION_SCHEMA] = LONG_RUN_PREDICTION_SCHEMA
    sequence: int = Field(ge=1)
    generation_id: str = Field(min_length=1)
    symbol: str
    cutoff: datetime
    cutoff_ordinal: int = Field(ge=1, le=LONG_RUN_TARGET_CASES_PER_SYMBOL)
    prediction: ForwardPredictionRecord
    inference_started_at: datetime
    inference_finished_at: datetime
    append_started_at: datetime
    prediction_deadline_at: datetime
    previous_record_hash: str | None = None
    record_hash: str

    @property
    def prediction_id(self) -> str:
        """Stable shared-ledger identity for this symbol/cutoff record."""

        return self.prediction.prediction_id

    @field_validator("symbol")
    @classmethod
    def fixed_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("long-run predictions are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator(
        "cutoff",
        "inference_started_at",
        "inference_finished_at",
        "append_started_at",
        "prediction_deadline_at",
    )
    @classmethod
    def aware_prediction_time(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def valid_entry_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_entry(self) -> LongRunPredictionEntry:
        if self.prediction.instrument != self.symbol or self.prediction.cutoff != self.cutoff:
            raise ValueError("prediction envelope symbol/cutoff identity mismatch")
        if (
            self.prediction.inference_started_at != self.inference_started_at
            or self.prediction.inference_finished_at != self.inference_finished_at
        ):
            raise ValueError("prediction and durable envelope inference timestamps differ")
        if self.prediction.ledger_persisted_at is not None:
            raise ValueError("long-run prediction must use the post-fsync durability attestation")
        if self.prediction_deadline_at != prediction_deadline(self.cutoff):
            raise ValueError("prediction deadline is not cutoff plus five minutes")
        if self.prediction.generation_deadline_at != self.prediction_deadline_at:
            raise ValueError("prediction is not bound to the frozen generation deadline")
        if self.inference_finished_at < self.inference_started_at:
            raise ValueError("inference finished before it started")
        if self.inference_started_at < self.cutoff:
            raise ValueError("inference cannot start before the preregistered cutoff")
        if self.append_started_at < self.inference_finished_at:
            raise ValueError("append started before inference finished")
        if self.inference_finished_at > self.prediction_deadline_at:
            raise ValueError("inference finished after the frozen lateness boundary")
        if self.prediction.generated_at > self.prediction_deadline_at:
            raise ValueError("candidate generation exceeded the frozen lateness boundary")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("long-run prediction entry hash mismatch")
        return self


class LongRunDurabilityAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_DURABILITY_SCHEMA] = LONG_RUN_DURABILITY_SCHEMA
    sequence: int = Field(ge=1)
    generation_id: str = Field(min_length=1)
    prediction_key: str = Field(min_length=1)
    entry_record_hash: str
    durable_appended_at: datetime
    after_fsync: Literal[True] = True
    recovered_after_restart: bool = False
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("entry_record_hash", "previous_record_hash", "record_hash")
    @classmethod
    def valid_attestation_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @field_validator("durable_appended_at")
    @classmethod
    def aware_durable_time(cls, value: datetime) -> datetime:
        return _aware(value, "durable_appended_at")

    @model_validator(mode="after")
    def validate_attestation(self) -> LongRunDurabilityAttestation:
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("long-run durability attestation hash mismatch")
        return self


class LongRunPredictionLedger:
    """Prediction entries plus a post-fsync, append-only durability attestation."""

    def __init__(
        self,
        path: Path,
        durability_path: Path,
        cutoff_schedule: Sequence[datetime] | None = None,
    ) -> None:
        self.path = path
        self.durability_path = durability_path
        self.cutoff_schedule = (
            tuple(_aware(item, "cutoff_schedule") for item in cutoff_schedule)
            if cutoff_schedule is not None
            else None
        )
        if self.cutoff_schedule is not None and any(
            right <= left
            for left, right in zip(self.cutoff_schedule, self.cutoff_schedule[1:], strict=False)
        ):
            raise ValueError("prediction cutoff schedule must be strictly ordered")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.durability_path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[LongRunPredictionEntry] = []
        self.attestations: list[LongRunDurabilityAttestation] = []
        self._by_key: dict[tuple[str, datetime], LongRunPredictionEntry] = {}
        self._by_prediction_id: dict[str, LongRunPredictionEntry] = {}
        self._attestation_by_key: dict[str, LongRunDurabilityAttestation] = {}
        self._last_ordinal_by_symbol: dict[str, int] = {}
        self._generation_id: str | None = None
        self._load()

    def _load(self) -> None:
        previous: str | None = None
        if self.path.exists():
            for line_number, line in enumerate(read_append_only_lines(self.path), 1):
                if not line.strip():
                    continue
                try:
                    entry = LongRunPredictionEntry.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"long-run prediction ledger is corrupt at line {line_number}"
                    ) from exc
                self._validate_schedule_cutoff(entry.cutoff, entry.cutoff_ordinal)
                key = (entry.symbol, entry.cutoff)
                if (
                    entry.sequence != len(self.entries) + 1
                    or entry.previous_record_hash != previous
                    or key in self._by_key
                ):
                    raise RuntimeError(
                        "long-run prediction ledger chain or cutoff identity is invalid"
                    )
                if entry.prediction_id in self._by_prediction_id:
                    raise RuntimeError("long-run prediction ledger reuses a prediction identity")
                if self._generation_id is None:
                    self._generation_id = entry.generation_id
                elif entry.generation_id != self._generation_id:
                    raise RuntimeError("long-run prediction ledger mixes generation identities")
                prior_ordinal = self._last_ordinal_by_symbol.get(entry.symbol)
                if prior_ordinal is not None and entry.cutoff_ordinal <= prior_ordinal:
                    raise RuntimeError("long-run prediction ledger cutoff order is not monotonic")
                self.entries.append(entry)
                self._by_key[key] = entry
                self._by_prediction_id[entry.prediction_id] = entry
                self._last_ordinal_by_symbol[entry.symbol] = entry.cutoff_ordinal
                previous = entry.record_hash
        previous = None
        if self.durability_path.exists():
            for line_number, line in enumerate(read_append_only_lines(self.durability_path), 1):
                if not line.strip():
                    continue
                try:
                    attestation = LongRunDurabilityAttestation.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"long-run durability ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    attestation.sequence != len(self.attestations) + 1
                    or attestation.previous_record_hash != previous
                ):
                    raise RuntimeError("long-run durability ledger chain is not continuous")
                if attestation.prediction_key in self._attestation_by_key:
                    raise RuntimeError("long-run durability ledger contains a duplicate prediction")
                self.attestations.append(attestation)
                self._attestation_by_key[attestation.prediction_key] = attestation
                previous = attestation.record_hash
        entry_by_id = {entry.prediction_id: entry for entry in self.entries}
        for attestation in self.attestations:
            if attestation.sequence > len(self.entries):
                raise RuntimeError("prediction durability sequence exceeds prediction entries")
            expected_entry = self.entries[attestation.sequence - 1]
            if attestation.prediction_key != expected_entry.prediction_id:
                raise RuntimeError("prediction durability sequence does not match append order")
            entry = entry_by_id.get(attestation.prediction_key)
            if entry is None:
                raise RuntimeError("long-run durability ledger points to an unknown prediction")
            if attestation.generation_id != entry.generation_id:
                raise RuntimeError("prediction durability attestation generation identity differs")
            if attestation.durable_appended_at < entry.append_started_at:
                raise RuntimeError("prediction durability attestation precedes append start")
        for entry in self.entries:
            if entry.prediction_id not in self._attestation_by_key:
                # A crash can leave the durable entry before its attestation.
                # It is resumable, but never silently considered certified.
                continue
            attestation = self._attestation_by_key[entry.prediction_id]
            if attestation.entry_record_hash != entry.record_hash:
                raise RuntimeError("prediction durability attestation points to a different entry")

    def validate_against_preregistration(self, preregistration: LongRunPreregistration) -> None:
        """Bind every persisted entry to the exact frozen ordinal/cutoff set."""

        for entry in self.entries:
            if entry.generation_id != preregistration.generation_id:
                raise RuntimeError("prediction ledger generation differs from preregistration")
            if entry.symbol not in preregistration.symbols:
                raise RuntimeError("prediction ledger symbol is outside the preregistered universe")
            if entry.cutoff_ordinal > len(preregistration.mandatory_cutoffs):
                raise RuntimeError(
                    "prediction ledger cutoff ordinal exceeds the preregistered schedule"
                )
            expected_cutoff = preregistration.mandatory_cutoffs[entry.cutoff_ordinal - 1]
            if entry.cutoff != expected_cutoff:
                raise RuntimeError("prediction ledger cutoff is not the exact preregistered cutoff")
            if (
                entry.prediction.cutoff != expected_cutoff
                or entry.prediction.instrument != entry.symbol
                or entry.prediction.model != preregistration.model_repository
                or entry.prediction.model_identity_hash != preregistration.model_identity_sha256
                or entry.prediction.source_snapshot_hash != preregistration.source_snapshot_sha256
                or entry.prediction.checkpoint_hash != preregistration.checkpoint_sha256
                or entry.prediction.preprocessing_identity != preregistration.preprocessing_identity
                or entry.prediction.preprocessing_hash != preregistration.preprocessing_sha256
                or entry.prediction.dependency_lock_hash != preregistration.requirements_lock_sha256
            ):
                raise RuntimeError(
                    "prediction ledger record is not bound to the frozen candidate identity"
                )

    def _validate_schedule_cutoff(self, cutoff: datetime, cutoff_ordinal: int) -> None:
        if self.cutoff_schedule is None:
            return
        if not isinstance(cutoff_ordinal, int) or isinstance(cutoff_ordinal, bool):
            raise RuntimeError("prediction cutoff ordinal is not an integer")
        if cutoff_ordinal < 1 or cutoff_ordinal > len(self.cutoff_schedule):
            raise RuntimeError("prediction cutoff ordinal is outside the frozen schedule")
        if _aware(cutoff, "cutoff") != self.cutoff_schedule[cutoff_ordinal - 1]:
            raise RuntimeError("prediction cutoff is not the exact frozen schedule cutoff")

    @property
    def last_record_hash(self) -> str | None:
        return self.entries[-1].record_hash if self.entries else None

    @property
    def pending_durability_keys(self) -> tuple[str, ...]:
        return tuple(
            entry.prediction_id
            for entry in self.entries
            if entry.prediction_id not in self._attestation_by_key
        )

    def _write_attestation(self, attestation: LongRunDurabilityAttestation) -> None:
        with self.durability_path.open("a", encoding="utf-8") as handle:
            handle.write(attestation.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.attestations.append(attestation)
        self._attestation_by_key[attestation.prediction_key] = attestation

    def append(
        self,
        *,
        generation_id: str,
        symbol: str,
        cutoff: datetime,
        cutoff_ordinal: int,
        prediction: ForwardPredictionRecord,
        inference_started_at: datetime,
        inference_finished_at: datetime,
        append_started_at: datetime,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> LongRunPredictionEntry:
        cutoff = _aware(cutoff, "cutoff")
        key = (symbol.strip().upper(), cutoff)
        self._validate_schedule_cutoff(cutoff, cutoff_ordinal)
        inference_started_at = _aware(inference_started_at, "inference_started_at")
        inference_finished_at = _aware(inference_finished_at, "inference_finished_at")
        append_started_at = _aware(append_started_at, "append_started_at")
        prior = self._by_key.get(key)
        if prior is not None:
            if prior.generation_id != generation_id:
                raise RuntimeError("conflicting generation identity for an existing cutoff")
            if (
                prior.prediction != prediction
                or prior.cutoff_ordinal != cutoff_ordinal
                or prior.inference_started_at != inference_started_at
                or prior.inference_finished_at != inference_finished_at
                or prior.append_started_at != append_started_at
                or prior.prediction_deadline_at != prediction_deadline(cutoff)
            ):
                raise RuntimeError("conflicting prediction for an existing generation cutoff")
            return prior
        if key[0] not in V3_CORE_SYMBOLS:
            raise ValueError("long-run predictions are restricted to BTCUSDT and ETHUSDT")
        if cutoff_ordinal < 1 or cutoff_ordinal > LONG_RUN_TARGET_CASES_PER_SYMBOL:
            raise ValueError("long-run prediction cutoff ordinal is outside the 1..80 range")
        prior_ordinal = self._last_ordinal_by_symbol.get(key[0])
        if prior_ordinal is not None and cutoff_ordinal <= prior_ordinal:
            raise RuntimeError("long-run prediction cutoff order is not monotonic")
        if self._generation_id is not None and generation_id != self._generation_id:
            raise RuntimeError("long-run prediction generation identity differs")
        prior_prediction = self._by_prediction_id.get(prediction.prediction_id)
        if prior_prediction is not None:
            raise RuntimeError("prediction identity is already bound to another cutoff")
        if prediction.generation_deadline_at != prediction_deadline(cutoff):
            raise ValueError("long-run prediction is not bound to cutoff plus five minutes")
        unsigned = {
            "schema": LONG_RUN_PREDICTION_SCHEMA,
            "sequence": len(self.entries) + 1,
            "generation_id": generation_id,
            "symbol": key[0],
            "cutoff": _iso(cutoff),
            "cutoff_ordinal": cutoff_ordinal,
            "prediction": prediction.model_dump(mode="json"),
            "inference_started_at": _iso(inference_started_at),
            "inference_finished_at": _iso(inference_finished_at),
            "append_started_at": _iso(append_started_at),
            "prediction_deadline_at": _iso(prediction_deadline(cutoff)),
            "previous_record_hash": self.last_record_hash,
        }
        entry = LongRunPredictionEntry(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.entries.append(entry)
        self._by_key[key] = entry
        self._by_prediction_id[entry.prediction_id] = entry
        self._last_ordinal_by_symbol[key[0]] = cutoff_ordinal
        self._generation_id = generation_id
        durable_at = _aware(clock(), "durable_appended_at")
        if durable_at < append_started_at:
            raise RuntimeError("durability clock precedes append start")
        attestation_unsigned = {
            "schema": LONG_RUN_DURABILITY_SCHEMA,
            "sequence": len(self.attestations) + 1,
            "generation_id": generation_id,
            "prediction_key": entry.prediction_id,
            "entry_record_hash": entry.record_hash,
            "durable_appended_at": _iso(durable_at),
            "after_fsync": True,
            "recovered_after_restart": False,
            "previous_record_hash": self.attestations[-1].record_hash
            if self.attestations
            else None,
        }
        self._write_attestation(
            LongRunDurabilityAttestation(
                **attestation_unsigned,
                record_hash=_hash_payload(attestation_unsigned),
            )
        )
        return entry

    def recover_pending_durability(
        self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> tuple[str, ...]:
        """Attest entries that survived a crash, preserving lateness for the caller.

        The entry file is fsync'd before an entry becomes visible in this
        projection.  A crash can therefore leave an entry without its
        follow-up attestation.  Recovery records the conservative observation
        time after restart even when it is beyond the cutoff deadline; the
        coordinator must then classify that case as late/excluded rather than
        silently treating it as a timely prediction or a generation-wide
        identity failure.
        """

        recovered: list[str] = []
        for entry in self.entries:
            if entry.prediction_id in self._attestation_by_key:
                continue
            durable_at = _aware(clock(), "durable_appended_at")
            if durable_at < entry.append_started_at:
                raise RuntimeError("recovered durability clock precedes append start")
            unsigned = {
                "schema": LONG_RUN_DURABILITY_SCHEMA,
                "sequence": len(self.attestations) + 1,
                "generation_id": entry.generation_id,
                "prediction_key": entry.prediction_id,
                "entry_record_hash": entry.record_hash,
                "durable_appended_at": _iso(durable_at),
                "after_fsync": True,
                "recovered_after_restart": True,
                "previous_record_hash": self.attestations[-1].record_hash
                if self.attestations
                else None,
            }
            self._write_attestation(
                LongRunDurabilityAttestation(**unsigned, record_hash=_hash_payload(unsigned))
            )
            recovered.append(entry.prediction_id)
        return tuple(recovered)

    def for_cutoff(self, symbol: str, cutoff: datetime) -> LongRunPredictionEntry | None:
        return self._by_key.get((symbol.strip().upper(), _aware(cutoff, "cutoff")))

    def durability_for(self, entry: LongRunPredictionEntry) -> LongRunDurabilityAttestation | None:
        return self._attestation_by_key.get(entry.prediction_id)

    @staticmethod
    def _prediction_id(entry: LongRunPredictionEntry) -> str:
        return entry.prediction.prediction_id


class LongRunOutcomeLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_OUTCOME_SCHEMA] = LONG_RUN_OUTCOME_SCHEMA
    sequence: int = Field(ge=1)
    generation_id: str = Field(min_length=1)
    prediction_id: str = Field(min_length=1)
    symbol: str
    cutoff: datetime
    cutoff_ordinal: int = Field(ge=1, le=LONG_RUN_TARGET_CASES_PER_SYMBOL)
    outcome_case_id: str = Field(min_length=1)
    source_interval_ends: tuple[datetime, ...]
    source_bar_hashes: tuple[str, ...]
    linked_at: datetime
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("symbol")
    @classmethod
    def fixed_outcome_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in V3_CORE_SYMBOLS:
            raise ValueError("long-run outcomes are restricted to BTCUSDT and ETHUSDT")
        return value

    @field_validator("cutoff", "linked_at")
    @classmethod
    def aware_outcome_time(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("source_interval_ends")
    @classmethod
    def aware_source_times(cls, value: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(_aware(item, "source_interval_end") for item in value)

    @field_validator("source_bar_hashes", "previous_record_hash", "record_hash")
    @classmethod
    def valid_outcome_hashes(cls, value: tuple[str, ...] | str | None, info: object):
        if value is None:
            return None
        if isinstance(value, tuple):
            return tuple(_digest(item, "source_bar_hash") for item in value)
        return _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_outcome(self) -> LongRunOutcomeLink:
        expected_times = tuple(
            self.cutoff + timedelta(seconds=LONG_RUN_OBSERVATION_INTERVAL_SECONDS * (index + 1))
            for index in range(CHRONOS_HORIZON_BARS)
        )
        if (
            self.source_interval_ends != expected_times
            or len(self.source_bar_hashes) != CHRONOS_HORIZON_BARS
        ):
            raise ValueError("outcome must contain exactly the twelve subsequent five-minute bars")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("long-run outcome link hash mismatch")
        return self


class LongRunOutcomeLinkLedger:
    def __init__(self, path: Path, cutoff_schedule: Sequence[datetime] | None = None) -> None:
        self.path = path
        self.cutoff_schedule = (
            tuple(_aware(item, "cutoff_schedule") for item in cutoff_schedule)
            if cutoff_schedule is not None
            else None
        )
        if self.cutoff_schedule is not None and any(
            right <= left
            for left, right in zip(self.cutoff_schedule, self.cutoff_schedule[1:], strict=False)
        ):
            raise ValueError("outcome cutoff schedule must be strictly ordered")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[LongRunOutcomeLink] = []
        self.by_prediction: dict[str, LongRunOutcomeLink] = {}
        self.by_outcome_case: dict[str, LongRunOutcomeLink] = {}
        self._generation_id: str | None = None
        previous: str | None = None
        if path.exists():
            for line_number, line in enumerate(read_append_only_lines(path), 1):
                if not line.strip():
                    continue
                try:
                    record = LongRunOutcomeLink.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"long-run outcome ledger is corrupt at line {line_number}"
                    ) from exc
                self._validate_schedule_cutoff(record.cutoff, record.cutoff_ordinal)
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("long-run outcome ledger chain is not continuous")
                if record.prediction_id in self.by_prediction:
                    raise RuntimeError("one prediction has multiple outcome links")
                if record.outcome_case_id in self.by_outcome_case:
                    raise RuntimeError("long-run outcome ledger reuses an outcome identity")
                if self._generation_id is None:
                    self._generation_id = record.generation_id
                elif record.generation_id != self._generation_id:
                    raise RuntimeError("long-run outcome ledger mixes generation identities")
                self.records.append(record)
                self.by_prediction[record.prediction_id] = record
                self.by_outcome_case[record.outcome_case_id] = record
                previous = record.record_hash

    def append(
        self,
        *,
        generation_id: str,
        prediction_id: str,
        symbol: str,
        cutoff: datetime,
        cutoff_ordinal: int,
        outcome_case_id: str,
        source_interval_ends: Sequence[datetime],
        source_bar_hashes: Sequence[str],
        linked_at: datetime,
    ) -> LongRunOutcomeLink:
        self._validate_schedule_cutoff(cutoff, cutoff_ordinal)
        prior = self.by_prediction.get(prediction_id)
        if prior is not None:
            if (
                prior.generation_id != generation_id
                or prior.symbol != symbol.strip().upper()
                or prior.cutoff != _aware(cutoff, "cutoff")
                or prior.cutoff_ordinal != cutoff_ordinal
                or prior.outcome_case_id != outcome_case_id
                or prior.source_interval_ends
                != tuple(_aware(item, "source_interval_end") for item in source_interval_ends)
                or prior.source_bar_hashes != tuple(source_bar_hashes)
                or prior.linked_at != _aware(linked_at, "linked_at")
            ):
                raise RuntimeError("conflicting outcome link for prediction")
            return prior
        if self._generation_id is not None and generation_id != self._generation_id:
            raise RuntimeError("long-run outcome generation identity differs")
        if outcome_case_id in self.by_outcome_case:
            raise RuntimeError("outcome identity is already bound to another prediction")
        unsigned = {
            "schema": LONG_RUN_OUTCOME_SCHEMA,
            "sequence": len(self.records) + 1,
            "generation_id": generation_id,
            "prediction_id": prediction_id,
            "symbol": symbol.strip().upper(),
            "cutoff": _iso(cutoff),
            "cutoff_ordinal": cutoff_ordinal,
            "outcome_case_id": outcome_case_id,
            "source_interval_ends": [_iso(item) for item in source_interval_ends],
            "source_bar_hashes": list(source_bar_hashes),
            "linked_at": _iso(linked_at),
            "previous_record_hash": self.records[-1].record_hash if self.records else None,
        }
        record = LongRunOutcomeLink(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.by_prediction[prediction_id] = record
        self.by_outcome_case[outcome_case_id] = record
        self._generation_id = generation_id
        return record

    def _validate_schedule_cutoff(self, cutoff: datetime, cutoff_ordinal: int) -> None:
        if self.cutoff_schedule is None:
            return
        if not isinstance(cutoff_ordinal, int) or isinstance(cutoff_ordinal, bool):
            raise RuntimeError("outcome cutoff ordinal is not an integer")
        if cutoff_ordinal < 1 or cutoff_ordinal > len(self.cutoff_schedule):
            raise RuntimeError("outcome cutoff ordinal is outside the frozen schedule")
        if _aware(cutoff, "cutoff") != self.cutoff_schedule[cutoff_ordinal - 1]:
            raise RuntimeError("outcome cutoff is not the exact frozen schedule cutoff")

    def validate_against_preregistration(self, preregistration: LongRunPreregistration) -> None:
        """Bind every persisted outcome link to the exact frozen schedule."""

        for record in self.records:
            if record.generation_id != preregistration.generation_id:
                raise RuntimeError("outcome ledger generation differs from preregistration")
            if record.symbol not in preregistration.symbols:
                raise RuntimeError("outcome ledger symbol is outside the preregistered universe")
            if record.cutoff_ordinal > len(preregistration.mandatory_cutoffs):
                raise RuntimeError(
                    "outcome ledger cutoff ordinal exceeds the preregistered schedule"
                )
            expected_cutoff = preregistration.mandatory_cutoffs[record.cutoff_ordinal - 1]
            if record.cutoff != expected_cutoff:
                raise RuntimeError("outcome ledger cutoff is not the exact preregistered cutoff")


class LongRunRuntimeAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    python_executable: str
    python_version: str
    platform: str
    torch_version: str
    cuda_runtime: str
    chronos_package_version: str
    attestation_hash: str

    @field_validator("attestation_hash")
    @classmethod
    def valid_runtime_hash(cls, value: str) -> str:
        return _digest(value, "attestation_hash")

    @model_validator(mode="after")
    def validate_runtime_hash(self) -> LongRunRuntimeAttestation:
        payload = self.model_dump(mode="json", exclude={"attestation_hash"})
        if _hash_payload(payload) != self.attestation_hash:
            raise ValueError("runtime attestation hash mismatch")
        return self


def _installed_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unavailable"


def collect_runtime_attestation() -> LongRunRuntimeAttestation:
    """Collect package/runtime identity without loading a model or using a GPU."""

    torch_version = _installed_version("torch")
    cuda_runtime = "unavailable"
    try:
        import importlib

        torch = importlib.import_module("torch")
        cuda_runtime = str(getattr(getattr(torch, "version", None), "cuda", None) or "unavailable")
    except (ImportError, RuntimeError):
        pass
    payload = {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "torch_version": torch_version,
        "cuda_runtime": cuda_runtime,
        "chronos_package_version": _installed_version("chronos-forecasting"),
    }
    return LongRunRuntimeAttestation(**payload, attestation_hash=_hash_payload(payload))


class LongRunIdentityAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[f"{LONG_RUN_READINESS_SCHEMA}.identity"] = (
        f"{LONG_RUN_READINESS_SCHEMA}.identity"
    )
    repository_head: str
    worktree_clean: bool
    import_root: str
    component_hashes: dict[str, str]
    model_repository: str
    model_revision: str
    model_identity_sha256: str
    uv_lock_sha256: str
    requirements_lock_sha256: str
    checkpoint_sha256: str
    phase3_gate_sha256: str
    model_runtime_qualification_sha256: str
    runtime: LongRunRuntimeAttestation
    attestation_hash: str

    @field_validator("repository_head")
    @classmethod
    def valid_head(cls, value: str) -> str:
        return _commit(value, "repository_head")

    @field_validator(
        "component_hashes",
        "model_identity_sha256",
        "uv_lock_sha256",
        "requirements_lock_sha256",
        "checkpoint_sha256",
        "phase3_gate_sha256",
        "model_runtime_qualification_sha256",
        "attestation_hash",
    )
    @classmethod
    def valid_identity_hashes(cls, value: dict[str, str] | str, info: object):
        if isinstance(value, dict):
            return {key: _digest(item, f"component_hashes[{key}]") for key, item in value.items()}
        return _digest(value, getattr(info, "field_name", "hash"))

    @field_validator("model_repository")
    @classmethod
    def valid_model_repository(cls, value: str) -> str:
        if value != QUALIFIED_CHRONOS_MODEL:
            raise ValueError(
                "identity attestation model repository is not the qualified Chronos model"
            )
        return value

    @field_validator("model_revision")
    @classmethod
    def valid_model_revision(cls, value: str) -> str:
        if value != QUALIFIED_CHRONOS_REVISION:
            raise ValueError(
                "identity attestation model revision is not the qualified Chronos revision"
            )
        return value

    @model_validator(mode="after")
    def validate_attestation_hash(self) -> LongRunIdentityAttestation:
        if (
            self.model_repository != QUALIFIED_CHRONOS_MODEL
            or self.model_revision != QUALIFIED_CHRONOS_REVISION
            or self.model_identity_sha256 != QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256
        ):
            raise ValueError("long-run identity attestation model identity is not qualified")
        payload = self.model_dump(mode="json", exclude={"attestation_hash"})
        if _hash_payload(payload) != self.attestation_hash:
            raise ValueError("long-run identity attestation hash mismatch")
        return self


def _git_head(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def long_run_component_files(repository_root: Path) -> dict[str, Path]:
    """Return the only source files eligible for long-run identity attestation.

    Keeping this map in the contract module prevents a readiness caller from
    substituting an arbitrary file that merely has the expected digest.  All
    launch-critical hashes must be computed from this checkout, not from paths
    supplied by a caller.
    """

    root = repository_root.resolve()
    return {
        name: root / relative_path
        for name, relative_path in _LONG_RUN_COMPONENT_RELATIVE_PATHS.items()
    }


def _worktree_clean(repository_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    return not result.stdout.strip()


def _qualified_model_identity(model_runtime_qualification_path: Path) -> tuple[str, str]:
    """Read and validate the measured local Chronos qualification artifact.

    The model repository, revision, and checkpoint are derived from the
    immutable qualification JSON rather than accepted as caller-supplied
    readiness claims.  The separately reviewed model identity digest remains
    a frozen contract constant and is included in the returned attestation
    payload only after these artifact fields have been checked.
    """

    try:
        value = json.loads(model_runtime_qualification_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("qualified Chronos runtime artifact is unreadable") from exc
    if not isinstance(value, dict) or value.get("status") != "measured":
        raise ValueError("qualified Chronos runtime artifact is not a measured result")
    candidate = value.get("candidate")
    checkpoint = candidate.get("external_checkpoint") if isinstance(candidate, Mapping) else None
    repository = checkpoint.get("repository") if isinstance(checkpoint, Mapping) else None
    if not isinstance(repository, Mapping):
        raise ValueError("qualified Chronos runtime artifact has no checkpoint repository identity")
    model_repository = repository.get("repository_id")
    model_revision = repository.get("revision")
    if model_repository != QUALIFIED_CHRONOS_MODEL or model_revision != QUALIFIED_CHRONOS_REVISION:
        raise ValueError(
            "qualified Chronos runtime artifact model identity differs from the frozen contract"
        )
    runtime_artifacts = repository.get("runtime_artifacts")
    checkpoint_hash = None
    if isinstance(runtime_artifacts, list):
        for artifact in runtime_artifacts:
            if (
                isinstance(artifact, Mapping)
                and artifact.get("relative_path") == "model.safetensors"
            ):
                checkpoint_hash = artifact.get("sha256")
                break
    if checkpoint_hash != QUALIFIED_CHRONOS_CHECKPOINT_SHA256:
        raise ValueError(
            "qualified Chronos runtime artifact checkpoint differs from the frozen contract"
        )
    runtime_pin = candidate.get("runtime_pin") if isinstance(candidate, Mapping) else None
    if (
        not isinstance(runtime_pin, Mapping)
        or runtime_pin.get("lock_hash") != QUALIFIED_REQUIREMENTS_LOCK_SHA256
    ):
        raise ValueError(
            "qualified Chronos runtime artifact lock identity differs from the frozen contract"
        )
    return str(model_repository), str(model_revision)


def attest_long_run_identity(
    *,
    repository_root: Path,
    expected_repository_commit: str,
    component_files: Mapping[str, Path],
    requirements_lock_path: Path,
    checkpoint_path: Path,
    phase3_gate_path: Path,
    model_runtime_qualification_path: Path,
    runtime: LongRunRuntimeAttestation | None = None,
) -> LongRunIdentityAttestation:
    """Compute identity from actual checkout/files; caller values are never trusted."""

    repository_root = repository_root.resolve()
    actual_head = _git_head(repository_root)
    if actual_head != _commit(expected_repository_commit, "expected_repository_commit"):
        raise ValueError("repository HEAD differs from expected long-run identity")
    expected_paths = long_run_component_files(repository_root)
    paths = {name: path.resolve() for name, path in component_files.items()}
    if paths != expected_paths:
        raise ValueError("component identity paths must be the canonical frozen checkout files")
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "missing long-run component identity files: " + ", ".join(sorted(missing))
        )
    for path in (
        requirements_lock_path,
        checkpoint_path,
        phase3_gate_path,
        model_runtime_qualification_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(f"missing required launch identity artifact: {path}")
    model_repository, model_revision = _qualified_model_identity(model_runtime_qualification_path)
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    if not (repository_root / "uv.lock").is_file():
        raise FileNotFoundError(f"missing repository lock: {repository_root / 'uv.lock'}")
    expected_import_root = (repository_root / "src").resolve()
    package_root = expected_import_root / "advisorai"
    if not package_root.is_dir():
        raise FileNotFoundError(f"missing source package under frozen checkout: {package_root}")
    import importlib
    from importlib.machinery import PathFinder

    importlib.invalidate_caches()
    spec = PathFinder.find_spec("advisorai", [str(expected_import_root)])
    resolved_locations = (
        {Path(location).resolve() for location in (spec.submodule_search_locations or ())}
        if spec is not None
        else set()
    )
    if package_root not in resolved_locations:
        raise ValueError("advisorai import resolution does not point at the frozen checkout")
    loaded_package = sys.modules.get("advisorai")
    loaded_locations = {
        Path(location).resolve() for location in getattr(loaded_package, "__path__", ())
    }
    if loaded_package is not None and loaded_locations != {package_root}:
        raise ValueError("already-loaded advisorai package is shadowed outside the frozen checkout")
    payload = {
        "schema": f"{LONG_RUN_READINESS_SCHEMA}.identity",
        "repository_head": actual_head,
        "worktree_clean": _worktree_clean(repository_root),
        "import_root": str(expected_import_root),
        "component_hashes": hashes,
        "model_repository": model_repository,
        "model_revision": model_revision,
        "model_identity_sha256": QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256,
        "uv_lock_sha256": sha256_file(repository_root / "uv.lock"),
        "requirements_lock_sha256": sha256_file(requirements_lock_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "phase3_gate_sha256": sha256_file(phase3_gate_path),
        "model_runtime_qualification_sha256": sha256_file(model_runtime_qualification_path),
        "runtime": (runtime or collect_runtime_attestation()).model_dump(mode="json"),
    }
    return LongRunIdentityAttestation(**payload, attestation_hash=_hash_payload(payload))


def identity_matches_preregistration(
    preregistration: LongRunPreregistration, attestation: LongRunIdentityAttestation
) -> bool:
    required = {
        "finality_rule_sha256": preregistration.finality_rule_sha256,
        "context_rule_sha256": preregistration.context_rule_sha256,
        "preprocessing_sha256": preregistration.preprocessing_sha256,
        "collector_code_sha256": preregistration.collector_code_sha256,
        "candidate_worker_code_sha256": preregistration.candidate_worker_code_sha256,
        "outcome_linker_code_sha256": preregistration.outcome_linker_code_sha256,
        "watchdog_code_sha256": preregistration.watchdog_code_sha256,
        "auditor_code_sha256": preregistration.auditor_code_sha256,
        "scheduler_code_sha256": preregistration.scheduler_code_sha256,
        "coordinator_code_sha256": preregistration.coordinator_code_sha256,
        "launcher_code_sha256": preregistration.launcher_code_sha256,
        "long_run_contract_code_sha256": preregistration.long_run_contract_code_sha256,
        "forward_contract_code_sha256": preregistration.forward_contract_code_sha256,
        "cadence_contract_code_sha256": preregistration.cadence_contract_code_sha256,
    }
    return (
        attestation.repository_head == preregistration.repository_commit
        and attestation.worktree_clean
        and all(attestation.component_hashes.get(key) == value for key, value in required.items())
        and attestation.model_repository == preregistration.model_repository
        and attestation.model_revision == preregistration.model_revision
        and attestation.model_identity_sha256 == preregistration.model_identity_sha256
        and attestation.uv_lock_sha256 == preregistration.uv_lock_sha256
        and attestation.requirements_lock_sha256 == preregistration.requirements_lock_sha256
        and attestation.checkpoint_sha256 == preregistration.checkpoint_sha256
        and attestation.phase3_gate_sha256 == preregistration.phase3_gate_sha256
        and attestation.model_runtime_qualification_sha256
        == preregistration.model_runtime_qualification_sha256
        and attestation.runtime.attestation_hash == preregistration.runtime_attestation_sha256
    )


class LongRunReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    reason: str


class LongRunLaunchReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_READINESS_SCHEMA] = LONG_RUN_READINESS_SCHEMA
    decision: Literal["LONG_RUN_READY", "LONG_RUN_REFUSED"]
    preregistration_sha256: str
    checks: tuple[LongRunReadinessCheck, ...]
    actual_identity: LongRunIdentityAttestation
    actual_identity_hash: str
    report_hash: str
    long_run_ready: bool = False

    @field_validator("preregistration_sha256", "actual_identity_hash", "report_hash")
    @classmethod
    def valid_readiness_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def verify_report(self) -> LongRunLaunchReadinessReport:
        _validate_readiness_check_set(
            self.checks,
            expected_names=(
                *LONG_RUN_LAUNCH_BASE_READINESS_CHECKS,
                *LONG_RUN_REQUIRED_PREFLIGHT_CHECKS,
            ),
        )
        all_passed = all(check.passed for check in self.checks)
        if self.long_run_ready != all_passed:
            raise ValueError("long-run readiness decision and check results disagree")
        if (self.decision == "LONG_RUN_READY") != all_passed:
            raise ValueError("long-run readiness decision is inconsistent with check results")
        if self.actual_identity_hash != self.actual_identity.attestation_hash:
            raise ValueError("long-run readiness identity hash does not match the attestation")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if _hash_payload(payload) != self.report_hash:
            raise ValueError("long-run readiness report hash mismatch")
        return self


class LongRunReleaseCandidatePreflightReport(BaseModel):
    """Actual-file dry-run result; it cannot authorize a real launch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_RC_PREFLIGHT_SCHEMA] = LONG_RUN_RC_PREFLIGHT_SCHEMA
    decision: Literal[
        "RELEASE_CANDIDATE_READY_FOR_FINAL_HUMAN_REVIEW",
        "RELEASE_CANDIDATE_REFUSED",
    ]
    checks: tuple[LongRunReadinessCheck, ...]
    actual_identity: LongRunIdentityAttestation
    actual_identity_hash: str
    report_hash: str
    long_run_ready: Literal[False] = False

    @field_validator("actual_identity_hash", "report_hash")
    @classmethod
    def valid_release_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def verify_release_report(self) -> LongRunReleaseCandidatePreflightReport:
        _validate_readiness_check_set(
            self.checks,
            expected_names=(
                *LONG_RUN_RC_BASE_READINESS_CHECKS,
                *LONG_RUN_REQUIRED_PREFLIGHT_CHECKS,
            ),
        )
        all_passed = all(check.passed for check in self.checks)
        if (self.decision == "RELEASE_CANDIDATE_READY_FOR_FINAL_HUMAN_REVIEW") != all_passed:
            raise ValueError("release-candidate decision is inconsistent with check results")
        if self.actual_identity_hash != self.actual_identity.attestation_hash:
            raise ValueError("release-candidate identity hash does not match the attestation")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if _hash_payload(payload) != self.report_hash:
            raise ValueError("release-candidate preflight report hash mismatch")
        return self


def _validate_readiness_check_set(
    checks: Sequence[LongRunReadinessCheck], *, expected_names: Sequence[str]
) -> None:
    names = tuple(check.name for check in checks)
    if len(names) != len(set(names)):
        raise ValueError("readiness report contains duplicate check names")
    if set(names) != set(expected_names) or len(names) != len(expected_names):
        missing = sorted(set(expected_names) - set(names))
        extra = sorted(set(names) - set(expected_names))
        raise ValueError(
            "readiness report check set is incomplete or contains unknown checks: "
            f"missing={missing}, extra={extra}"
        )


def _required_preflight_checks(
    supplied: Mapping[str, bool], *, reason: str
) -> list[LongRunReadinessCheck]:
    """Materialize the complete named gate set, failing closed on omissions."""

    checks = [
        LongRunReadinessCheck(
            name=name,
            passed=supplied.get(name) is True,
            reason=reason if name in supplied else "required preflight result was not supplied",
        )
        for name in LONG_RUN_REQUIRED_PREFLIGHT_CHECKS
    ]
    unknown = sorted(set(supplied) - set(LONG_RUN_REQUIRED_PREFLIGHT_CHECKS))
    if unknown:
        raise ValueError(
            "unrecognized preflight results cannot authorize a launch: " + ", ".join(unknown)
        )
    return checks


def evaluate_release_candidate_preflight(
    preregistration: LongRunPreregistration,
    *,
    attestation: LongRunIdentityAttestation,
    phase3_gate_passed: bool,
    model_runtime_passed: bool,
    preflight_checks: Mapping[str, bool],
    gpu_lease_free: bool,
) -> LongRunReleaseCandidatePreflightReport:
    """Evaluate all current launch-critical checks without creating a preregistration."""

    checks = [
        LongRunReadinessCheck(
            name="actual_repository_and_components",
            passed=identity_matches_preregistration(preregistration, attestation),
            reason="actual checkout and component files equal the draft contract",
        ),
        LongRunReadinessCheck(
            name="phase3_gate",
            passed=phase3_gate_passed
            and attestation.phase3_gate_sha256 == preregistration.phase3_gate_sha256,
            reason="the immutable Phase-3 predecessor is present and passed",
        ),
        LongRunReadinessCheck(
            name="model_runtime",
            passed=model_runtime_passed,
            reason="the qualified Chronos runtime artifact is present and passing",
        ),
        LongRunReadinessCheck(
            name="dual_lock",
            passed=attestation.uv_lock_sha256 == preregistration.uv_lock_sha256
            and attestation.requirements_lock_sha256 == preregistration.requirements_lock_sha256,
            reason="both runtime lock identities equal the draft contract",
        ),
        LongRunReadinessCheck(
            name="security",
            passed=preregistration.credentials_prohibited and preregistration.orders_prohibited,
            reason="credentials and order capabilities are prohibited",
        ),
        LongRunReadinessCheck(
            name="gpu_lease", passed=gpu_lease_free, reason="no AdvisorAI GPU lease is held"
        ),
    ]
    checks.extend(
        _required_preflight_checks(
            preflight_checks,
            reason="focused release-candidate check",
        )
    )
    decision = (
        "RELEASE_CANDIDATE_READY_FOR_FINAL_HUMAN_REVIEW"
        if all(check.passed for check in checks)
        else "RELEASE_CANDIDATE_REFUSED"
    )
    unsigned = {
        "schema": LONG_RUN_RC_PREFLIGHT_SCHEMA,
        "decision": decision,
        "checks": [check.model_dump(mode="json") for check in checks],
        "actual_identity": attestation.model_dump(mode="json"),
        "actual_identity_hash": attestation.attestation_hash,
        "long_run_ready": False,
    }
    return LongRunReleaseCandidatePreflightReport(
        **unsigned,
        report_hash=_hash_payload(unsigned),
    )


def evaluate_long_run_readiness(
    preregistration: LongRunPreregistration,
    *,
    attestation: LongRunIdentityAttestation,
    phase3_gate_passed: bool,
    model_runtime_passed: bool,
    preflight_checks: Mapping[str, bool],
    credentials_loaded: bool = False,
    order_writes_attempted: bool = False,
    gpu_lease_free: bool = True,
    immutable_preregistration_created: bool = False,
) -> LongRunLaunchReadinessReport:
    checks = [
        LongRunReadinessCheck(
            name="actual_repository_and_components",
            passed=identity_matches_preregistration(preregistration, attestation),
            reason="actual checkout/file hashes must equal the preregistration",
        ),
        LongRunReadinessCheck(
            name="phase3_gate",
            passed=phase3_gate_passed
            and attestation.phase3_gate_sha256 == preregistration.phase3_gate_sha256,
            reason="Phase-3 predecessor identity must be attested",
        ),
        LongRunReadinessCheck(
            name="model_runtime",
            passed=model_runtime_passed,
            reason="qualified model-runtime evidence must pass",
        ),
        LongRunReadinessCheck(
            name="dual_lock",
            passed=attestation.uv_lock_sha256 == preregistration.uv_lock_sha256
            and attestation.requirements_lock_sha256 == preregistration.requirements_lock_sha256,
            reason="both lock identities must match actual files",
        ),
        LongRunReadinessCheck(
            name="security",
            passed=not credentials_loaded and not order_writes_attempted,
            reason="credentials and order writes are forbidden",
        ),
        LongRunReadinessCheck(
            name="gpu_lease",
            passed=gpu_lease_free,
            reason="no competing AdvisorAI GPU lease may exist",
        ),
        LongRunReadinessCheck(
            name="immutable_preregistration",
            passed=immutable_preregistration_created,
            reason="the real launch requires a human-approved immutable preregistration",
        ),
    ]
    checks.extend(
        _required_preflight_checks(
            preflight_checks,
            reason="launch-critical preflight result",
        )
    )
    decision = "LONG_RUN_READY" if all(check.passed for check in checks) else "LONG_RUN_REFUSED"
    unsigned = {
        "schema": LONG_RUN_READINESS_SCHEMA,
        "decision": decision,
        "preregistration_sha256": long_run_preregistration_sha256(preregistration),
        "checks": [check.model_dump(mode="json") for check in checks],
        "actual_identity": attestation.model_dump(mode="json"),
        "actual_identity_hash": attestation.attestation_hash,
        "long_run_ready": decision == "LONG_RUN_READY",
    }
    return LongRunLaunchReadinessReport(
        **{key: value for key, value in unsigned.items() if key != "schema"},
        report_hash=_hash_payload(unsigned),
    )


def audit_long_run_cutoff_identity(
    preregistration: LongRunPreregistration, *, symbol: str, cutoff: datetime, ordinal: int
) -> None:
    normalized = symbol.strip().upper()
    if normalized not in V3_CORE_SYMBOLS:
        raise ValueError("unknown long-run symbol")
    if ordinal < 1 or ordinal > len(preregistration.mandatory_cutoffs):
        raise ValueError("cutoff ordinal outside preregistration")
    if preregistration.mandatory_cutoffs[ordinal - 1] != _aware(cutoff, "cutoff"):
        raise ValueError("prediction cutoff is not the exact preregistered cutoff")


def long_run_audit_report_sha256(report: Mapping[str, object]) -> str:
    """Recompute the internal hash of a canonical terminal audit report."""

    unsigned = dict(report)
    supplied = unsigned.pop("report_hash", None)
    if not isinstance(supplied, str):
        raise ValueError("long-run terminal audit report hash is missing")
    return _hash_payload(unsigned)


def _source_bar_hash(bar: object) -> str:
    payload = bar.model_dump(mode="json")
    return _hash_payload(payload)


def audit_long_run(
    *,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    coordinator_root: Path,
    watchdog_root: Path,
    scheduler_root: Path,
    repository_root: Path,
    terminal_observed_at: datetime,
) -> dict[str, object]:
    """Independently certify the exact 80-cutoff lifecycle, without materializing Phase-4 input."""

    if long_run_preregistration_sha256(preregistration) != _digest(
        preregistration_sha256, "preregistration hash"
    ):
        raise ValueError("long-run preregistration identity mismatch")
    source_root = source_root.resolve()
    candidate_root = candidate_root.resolve()
    coordinator_root = coordinator_root.resolve()
    watchdog_root = watchdog_root.resolve()
    scheduler_root = scheduler_root.resolve()
    repository_root = repository_root.resolve()
    issues: list[str] = []
    terminal_observed_at = _aware(terminal_observed_at, "terminal_observed_at")
    if terminal_observed_at < preregistration.terminal_check_at:
        issues.append("audit:before_frozen_terminal_check")
    try:
        actual_head = _git_head(repository_root)
    except (OSError, subprocess.SubprocessError) as exc:
        issues.append(f"repository:head_unreadable:{type(exc).__name__}")
    else:
        if actual_head != preregistration.repository_commit:
            issues.append("repository:head_identity_mismatch")
    source_status = read_json_stable(source_root / "status.json")
    candidate_status = read_json_stable(candidate_root / "status.json")
    try:
        outcome_status: dict[str, object] | None = read_json_stable(
            candidate_root / "outcome-status.json"
        )
    except (
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RuntimeError,
        ValueError,
    ):
        outcome_status = None
    try:
        watchdog_status: dict[str, object] | None = read_json_stable(watchdog_root / "status.json")
    except (
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RuntimeError,
        ValueError,
    ):
        watchdog_status = None
    statuses: list[tuple[str, dict[str, object]]] = [
        ("source", source_status),
        ("candidate", candidate_status),
    ]
    if outcome_status is not None:
        statuses.append(("outcome", outcome_status))
    for name, status in statuses:
        if status.get("generation_id") != preregistration.generation_id:
            issues.append(f"{name}:generation_identity_mismatch")
        if status.get("preregistration_sha256") != preregistration_sha256:
            issues.append(f"{name}:preregistration_identity_mismatch")
        if status.get("repository_commit") != preregistration.repository_commit:
            issues.append(f"{name}:repository_identity_mismatch")
        if status.get("evidence_class") != LONG_RUN_EVIDENCE_CLASS:
            issues.append(f"{name}:evidence_class_mismatch")
        if status.get("admission_eligible") is not False:
            issues.append(f"{name}:admission_eligible")
        if status.get("credentials_loaded") is not False:
            issues.append(f"{name}:credentials_loaded")
        if status.get("order_writes_attempted") is not False:
            issues.append(f"{name}:order_write_attempted")
        if status.get("execution_authority_present") is not False:
            issues.append(f"{name}:execution_authority_present")
        if status.get("runtime_attestation_sha256") != preregistration.runtime_attestation_sha256:
            issues.append(f"{name}:runtime_attestation_mismatch")
        if status.get("phase4_materialization_eligible") is not False:
            issues.append(f"{name}:admission_materialization_flag_set")
        if status.get("source_snapshot_hash") != preregistration.source_snapshot_sha256:
            issues.append(f"{name}:source_snapshot_identity_mismatch")
        if name == "source":
            expected_source_status = {
                "collector_code_sha256": preregistration.collector_code_sha256,
                "finality_code_sha256": preregistration.finality_rule_sha256,
                "endpoint": preregistration.rest_endpoint,
                "interval": preregistration.interval,
            }
            for key, expected in expected_source_status.items():
                if status.get(key) != expected:
                    issues.append(f"source:{key}_identity_mismatch")
        if name == "candidate":
            expected_candidate_status = {
                "candidate_worker_code_sha256": preregistration.candidate_worker_code_sha256,
                "preprocessing_code_sha256": preregistration.preprocessing_sha256,
                "model_identity_hash": preregistration.model_identity_sha256,
                "checkpoint_hash": preregistration.checkpoint_sha256,
                "dependency_lock_hash": preregistration.requirements_lock_sha256,
                "model_repository": preregistration.model_repository,
                "model_revision": preregistration.model_revision,
            }
            for key, expected in expected_candidate_status.items():
                if status.get(key) != expected:
                    issues.append(f"candidate:{key}_identity_mismatch")
        if (
            name == "outcome"
            and status.get("outcome_linker_code_sha256")
            != preregistration.outcome_linker_code_sha256
        ):
            issues.append("outcome:outcome_linker_code_identity_mismatch")

    def read_optional_identity(path: Path, label: str) -> dict[str, object] | None:
        try:
            value = read_json_stable(path)
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RuntimeError,
            ValueError,
        ):
            issues.append(f"{label}:missing_or_unreadable")
            return None
        return value

    source_manifest = read_optional_identity(source_root / "manifest.json", "source_manifest")
    candidate_manifest = read_optional_identity(
        candidate_root / "manifest.json", "candidate_manifest"
    )
    launch_metadata = read_optional_identity(source_root.parent / "launch.json", "launch_metadata")
    scheduler_status = read_optional_identity(scheduler_root / "status.json", "scheduler_status")
    if source_manifest is not None:
        expected_source_manifest = {
            "generation_id": preregistration.generation_id,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "repository_commit": preregistration.repository_commit,
            "preregistration_sha256": preregistration_sha256,
            "source_snapshot_hash": preregistration.source_snapshot_sha256,
            "collector_code_sha256": preregistration.collector_code_sha256,
            "finality_code_sha256": preregistration.finality_rule_sha256,
            "endpoint": preregistration.rest_endpoint,
            "interval": preregistration.interval,
            "symbols": list(preregistration.symbols),
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        }
        for key, expected in expected_source_manifest.items():
            if source_manifest.get(key) != expected:
                issues.append(f"source_manifest:{key}_mismatch")
    if candidate_manifest is not None:
        expected_candidate_manifest = {
            "generation_id": preregistration.generation_id,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "repository_commit": preregistration.repository_commit,
            "preregistration_sha256": preregistration_sha256,
            "candidate_worker_code_sha256": preregistration.candidate_worker_code_sha256,
            "model_identity_hash": preregistration.model_identity_sha256,
            "checkpoint_hash": preregistration.checkpoint_sha256,
            "preprocessing_code_sha256": preregistration.preprocessing_sha256,
            "dependency_lock_hash": preregistration.requirements_lock_sha256,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
            "context_bars": preregistration.context_bars,
            "context_newest_lag_seconds": preregistration.context_newest_lag_seconds,
        }
        for key, expected in expected_candidate_manifest.items():
            if candidate_manifest.get(key) != expected:
                issues.append(f"candidate_manifest:{key}_mismatch")
        if candidate_manifest.get("preprocessing_hash") is None:
            issues.append("candidate_manifest:preprocessing_hash_missing")
        elif (
            candidate_manifest.get("dependency_lock_hash")
            != preregistration.requirements_lock_sha256
        ):
            issues.append("candidate_manifest:dependency_lock_hash_mismatch")
        for key in ("model_identity_hash", "runner_hash", "runtime_environment_hash"):
            value = candidate_manifest.get(key)
            try:
                _digest(str(value), f"candidate_manifest.{key}")
            except ValueError:
                issues.append(f"candidate_manifest:{key}_invalid")
    if launch_metadata is not None:
        expected_launch = {
            "state": "RUNNING",
            "generation_id": preregistration.generation_id,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "repository_commit": preregistration.repository_commit,
            "preregistration_sha256": preregistration_sha256,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        }
        for key, expected in expected_launch.items():
            if launch_metadata.get(key) != expected:
                issues.append(f"launch_metadata:{key}_mismatch")
        code_hashes = launch_metadata.get("code_hashes")
        if not isinstance(code_hashes, Mapping):
            issues.append("launch_metadata:code_hashes_missing")
        else:
            expected_code_hashes = {
                "collector": preregistration.collector_code_sha256,
                "candidate": preregistration.candidate_worker_code_sha256,
                "outcomes": preregistration.outcome_linker_code_sha256,
                "watchdog": preregistration.watchdog_code_sha256,
                "auditor": preregistration.auditor_code_sha256,
                "scheduler": preregistration.scheduler_code_sha256,
                "coordinator": preregistration.coordinator_code_sha256,
                "launcher": preregistration.launcher_code_sha256,
                "long_run_contract": preregistration.long_run_contract_code_sha256,
                "forward_contract": preregistration.forward_contract_code_sha256,
                "cadence_contract": preregistration.cadence_contract_code_sha256,
            }
            for key, expected in expected_code_hashes.items():
                if code_hashes.get(key) != expected:
                    issues.append(f"launch_metadata:{key}_code_identity_mismatch")
    if scheduler_status is not None:
        expected_scheduler = {
            "generation_id": preregistration.generation_id,
            "preregistration_sha256": preregistration_sha256,
            "repository_commit": preregistration.repository_commit,
            "scheduler_code_sha256": preregistration.scheduler_code_sha256,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        for key, expected in expected_scheduler.items():
            if scheduler_status.get(key) != expected:
                issues.append(f"scheduler:{key}_mismatch")
        if scheduler_status.get("state") not in {"TERMINAL_AUDIT_RUNNING", "AUDIT_COMPLETE"}:
            issues.append(f"scheduler:state:{scheduler_status.get('state')}")
        scheduler_pid = scheduler_status.get("scheduler_pid")
        scheduler_command = scheduler_status.get("command")
        scheduler_identity = scheduler_status.get("command_identity")
        scheduler_create_time = scheduler_status.get("process_create_time")
        if not (
            isinstance(scheduler_pid, int)
            and isinstance(scheduler_command, list)
            and isinstance(scheduler_identity, str)
            and isinstance(scheduler_create_time, (int, float))
        ):
            issues.append("scheduler:process_identity_missing")
    if watchdog_status is None:
        issues.append("watchdog:status_missing_or_unreadable")
    else:
        expected_watchdog_identity = {
            "generation_id": preregistration.generation_id,
            "preregistration_sha256": preregistration_sha256,
            "repository_commit": preregistration.repository_commit,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "phase4_materialization_eligible": False,
            "watchdog_code_sha256": preregistration.watchdog_code_sha256,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        }
        for key, expected in expected_watchdog_identity.items():
            if watchdog_status.get(key) != expected:
                issues.append(f"watchdog:{key}_identity_mismatch")
        if watchdog_status.get("decision") != "LONG_RUN_HEALTHY":
            issues.append(f"watchdog:decision:{watchdog_status.get('decision')}")
        if watchdog_status.get("scientific_state") == LongRunState.GENERATION_FATAL.value:
            issues.append("watchdog:scientific_fatal_state")
        if watchdog_status.get("credentials_loaded") is not False:
            issues.append("watchdog:credentials_loaded")
        if watchdog_status.get("order_writes_attempted") is not False:
            issues.append("watchdog:order_write_attempted")
        if watchdog_status.get("watchdog_terminal") is not True:
            issues.append("watchdog:terminal_marker_missing")
    if (
        source_status.get("evidence_class") != LONG_RUN_EVIDENCE_CLASS
        or candidate_status.get("evidence_class") != LONG_RUN_EVIDENCE_CLASS
    ):
        raise ValueError("long-run evidence class is missing")
    if (
        source_status.get("admission_eligible") is not False
        or candidate_status.get("admission_eligible") is not False
    ):
        raise ValueError("long-run evidence cannot be admission eligible")
    raw = LongRunRawSpool(
        source_root / "raw-receipts.jsonl",
        require_request_query=True,
        source_snapshot_hash=preregistration.source_snapshot_sha256,
    )
    transport_failures = LongRunTransportFailureSpool(
        source_root / "transport-failures.jsonl",
        source_snapshot_hash=preregistration.source_snapshot_sha256,
    )
    if any(record.collected_at < preregistration.start_at for record in raw.records):
        issues.append("source:raw_receipt_before_frozen_start")
    try:
        persisted_normalized_bars = read_normalized_bars_stable(
            source_root / "normalized-bars.jsonl"
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        issues.append(f"source:normalized_projection_unreadable:{type(exc).__name__}")
        persisted_normalized_bars = ()
    persisted_tracker = LongRunFinalityTracker(
        source_root / "normalized-bars.jsonl",
        source_root / "post-admission-revisions.jsonl",
        minimum_interval_end=fresh_long_run_minimum_interval_end(preregistration.start_at),
        source_snapshot_hash=preregistration.source_snapshot_sha256,
    )
    if persisted_tracker.revisions:
        issues.append("source:persisted_post_admission_revision_history")
    replay_error: str | None = None
    # Replay into a temporary projection.  The terminal audit is read-only and
    # must never repair a missing derived bar in the scientific evidence root.
    with tempfile.TemporaryDirectory(prefix="advisorai-longrun-audit-") as audit_dir:
        audit_path = Path(audit_dir)
        replayed_normalized = ForwardNormalizedBarSpool(audit_path / "normalized-bars.jsonl")
        replayed_tracker = LongRunFinalityTracker(
            replayed_normalized,
            audit_path / "post-admission-revisions.jsonl",
            minimum_interval_end=fresh_long_run_minimum_interval_end(preregistration.start_at),
            source_snapshot_hash=preregistration.source_snapshot_sha256,
        )
        try:
            replayed_tracker.replay(
                tuple(item.view() for item in raw.records),
                str(source_status["source_snapshot_hash"]),
                # This projection lives in the disposable audit directory,
                # not in the scientific evidence root.  Populate it so the
                # auditor can compare the complete chronological replay with
                # the persisted canonical projection without repairing the
                # latter.
                persist_missing=True,
            )
        except (CanaryFinalityViolation, RuntimeError, ValueError) as exc:
            replay_error = str(exc)
            issues.append(f"source:{replay_error}")
        admitted = replayed_normalized.read()
        if replay_error is None and admitted != persisted_normalized_bars:
            issues.append("source:persisted_admitted_bars_differ_from_chronological_replay")
        if replay_error is not None:
            admitted = persisted_normalized_bars
    candidate = LongRunPredictionLedger(
        candidate_root / "predictions.jsonl",
        candidate_root / "prediction-durability.jsonl",
        preregistration.mandatory_cutoffs,
    )
    candidate.validate_against_preregistration(preregistration)
    outcomes = LongRunOutcomeLinkLedger(
        candidate_root / "outcome-links.jsonl", preregistration.mandatory_cutoffs
    )
    outcomes.validate_against_preregistration(preregistration)
    coordinator = LongRunCoordinator(preregistration, coordinator_root / "events.jsonl")
    terminal_component_states = {
        LongRunState.DEADLINE_REACHED.value,
        LongRunState.TERMINALIZING.value,
        LongRunState.COMPLETED_PENDING_AUDIT.value,
        LongRunState.AUDITED.value,
        LongRunState.GENERATION_FATAL.value,
    }
    if watchdog_status is not None:
        if watchdog_status.get("scientific_state") != coordinator.scientific_state.value:
            issues.append("watchdog:scientific_state_mismatch")
        if watchdog_status.get("fatal_history_count") != len(coordinator.fatal_events):
            issues.append("watchdog:fatal_history_count_mismatch")
    if outcome_status is None:
        issues.append("outcome:status_missing_or_unreadable")
    else:
        if outcome_status.get("generation_id") != preregistration.generation_id:
            issues.append("outcome:generation_identity_mismatch")
        if outcome_status.get("preregistration_sha256") != preregistration_sha256:
            issues.append("outcome:preregistration_identity_mismatch")
        if (
            outcome_status.get("outcome_linker_code_sha256")
            != preregistration.outcome_linker_code_sha256
        ):
            issues.append("outcome:code_identity_mismatch")
        if outcome_status.get("prediction_count") != len(candidate.entries):
            issues.append("outcome:prediction_count_mismatch")
        if outcome_status.get("outcome_link_count") != len(outcomes.records):
            issues.append("outcome:link_count_mismatch")
        if outcome_status.get("state") not in terminal_component_states:
            issues.append(f"outcome:not_terminal:{outcome_status.get('state')}")
        if outcome_status.get("scientific_state") != coordinator.scientific_state.value:
            issues.append("outcome:scientific_state_mismatch")
    per_cutoff: list[dict[str, object]] = []
    clean_counts = {symbol: 0 for symbol in V3_CORE_SYMBOLS}
    seen_keys: set[tuple[str, datetime]] = set()
    for name, status in (("source", source_status), ("candidate", candidate_status)):
        if status.get("state") not in terminal_component_states:
            issues.append(f"{name}:not_terminal:{status.get('state')}")
    if source_status.get("raw_receipt_count") != len(raw.records):
        issues.append("source:raw_receipt_count_mismatch")
    if source_status.get("transport_failure_count") != len(transport_failures.records):
        issues.append("source:transport_failure_count_mismatch")
    persisted_counts = {
        symbol: sum(bar.instrument == symbol for bar in admitted) for symbol in V3_CORE_SYMBOLS
    }
    if source_status.get("admitted_final_bar_count") != persisted_counts:
        issues.append("source:admitted_count_mismatch")
    if candidate_status.get("prediction_count") != len(candidate.entries):
        issues.append("candidate:prediction_count_mismatch")
    for field in (
        "schema_failures",
        "nan_inf_failures",
        "cuda_failures",
        "model_load_failures",
        "conflicting_ledger_writes",
    ):
        value = candidate_status.get(field)
        if not isinstance(value, int) or value < 0:
            issues.append(f"candidate:{field}_missing_or_invalid")
        elif value != 0:
            issues.append(f"candidate:{field}_nonzero")
    if candidate.entries and candidate_status.get("model_loaded") is not True:
        issues.append("candidate:model_not_loaded")
    if candidate_status.get("ledger_health") is not True:
        issues.append("candidate:ledger_health_not_true")
    replay_metrics = replayed_tracker.metrics()
    source_finality_status = source_status.get("finality")
    if not isinstance(source_finality_status, Mapping):
        issues.append("source:finality_metrics_missing")
    else:
        for key in (
            "raw_interval_identities",
            "admitted_final_intervals",
            "unresolved_intervals",
            "post_admission_revision_count",
        ):
            if source_finality_status.get(key) != replay_metrics.get(key):
                issues.append(f"source:finality_{key}_mismatch")
    if candidate_status.get("outcome_link_count") is not None and candidate_status.get(
        "outcome_link_count"
    ) != len(outcomes.records):
        issues.append("candidate:outcome_link_count_mismatch")
    for entry in candidate.entries:
        key = (entry.symbol, entry.cutoff)
        if key in seen_keys:
            issues.append(f"duplicate_prediction:{entry.symbol}:{entry.cutoff.isoformat()}")
        seen_keys.add(key)
        try:
            audit_long_run_cutoff_identity(
                preregistration,
                symbol=entry.symbol,
                cutoff=entry.cutoff,
                ordinal=entry.cutoff_ordinal,
            )
        except ValueError as exc:
            issues.append(f"unknown_or_mismatched_cutoff:{entry.prediction_id}:{exc}")
        if entry.generation_id != preregistration.generation_id:
            issues.append(f"generation_identity_mismatch:{entry.prediction_id}")
        prediction = entry.prediction
        expected_prediction_identity = {
            "model_identity_hash": preregistration.model_identity_sha256,
            "source_snapshot_hash": preregistration.source_snapshot_sha256,
            "checkpoint_hash": preregistration.checkpoint_sha256,
            "preprocessing_identity": CHRONOS_PREPROCESSING_IDENTITY,
            "dependency_lock_hash": preregistration.requirements_lock_sha256,
        }
        for field_name, expected in expected_prediction_identity.items():
            if getattr(prediction, field_name) != expected:
                issues.append(f"prediction_identity_mismatch:{entry.prediction_id}:{field_name}")
    if any(
        sum(entry.symbol == symbol for entry in candidate.entries)
        > preregistration.target_opportunities_per_symbol
        for symbol in V3_CORE_SYMBOLS
    ):
        issues.append("prediction_count_exceeds_target")
    case_states = coordinator.case_states()
    # Case accounting is scientific evidence too.  A terminal audit must not
    # accept a hand-written projection that marks opportunities before their
    # frozen cutoff/deadline or certifies a clean case before its durable link.
    for event in coordinator.events:
        if event.event_type not in {"CASE_PENDING", "CASE_EXCLUDED", "CASE_CLEAN"}:
            continue
        ordinal = int(event.payload["ordinal"])
        cutoff = preregistration.mandatory_cutoffs[ordinal - 1]
        if event.event_type == "CASE_PENDING" and event.observed_at < cutoff:
            issues.append(f"case_event_before_cutoff:{event.sequence}")
        if event.event_type == "CASE_EXCLUDED":
            if event.observed_at < cutoff:
                issues.append(f"case_exclusion_before_cutoff:{event.sequence}")
            elif event.observed_at < prediction_deadline(cutoff):
                reason = str(event.payload.get("reason", ""))
                if reason not in {"CANDIDATE_INFERENCE_WORKERTIMEOUT"}:
                    issues.append(f"case_exclusion_before_deadline:{event.sequence}")
        if event.event_type == "CASE_CLEAN":
            outcome_id = event.payload.get("outcome_case_id")
            link = outcomes.by_outcome_case.get(str(outcome_id))
            if link is None or event.observed_at < link.linked_at:
                issues.append(f"case_clean_before_outcome_link:{event.sequence}")
    if coordinator.scientific_state not in {
        LongRunState.DEADLINE_REACHED,
        LongRunState.TERMINALIZING,
        LongRunState.COMPLETED_PENDING_AUDIT,
        LongRunState.AUDITED,
        LongRunState.GENERATION_FATAL,
    }:
        issues.append(f"coordinator:not_terminal:{coordinator.scientific_state.value}")
    if candidate_status.get("fatal_history_count") != len(coordinator.fatal_events):
        issues.append("candidate:fatal_history_count_mismatch")
    expected_prediction_counts = {
        symbol: sum(entry.symbol == symbol for entry in candidate.entries)
        for symbol in V3_CORE_SYMBOLS
    }
    if candidate_status.get("prediction_counts") != expected_prediction_counts:
        issues.append("candidate:prediction_counts_mismatch")
    excluded_counts = {
        symbol: sum(
            state == LongRunCaseState.EXCLUDED
            for (case_symbol, _ordinal), (state, _prediction, _outcome) in case_states.items()
            if case_symbol == symbol
        )
        for symbol in V3_CORE_SYMBOLS
    }
    if candidate_status.get("rejection_count") != sum(excluded_counts.values()):
        issues.append("candidate:rejection_count_mismatch")
    for symbol in V3_CORE_SYMBOLS:
        missing_ordinals = [
            ordinal
            for ordinal in range(1, preregistration.target_opportunities_per_symbol + 1)
            if (symbol, ordinal) not in case_states
        ]
        if missing_ordinals:
            issues.append(f"{symbol}:missing_case_accounting:{len(missing_ordinals)}")
        accounted = sum(1 for (item, _ordinal) in case_states if item == symbol)
        if accounted != preregistration.target_opportunities_per_symbol:
            issues.append(f"{symbol}:case_accounting_not_80:{accounted}")
        for ordinal in range(1, preregistration.target_opportunities_per_symbol + 1):
            case = case_states.get((symbol, ordinal))
            entry = candidate.for_cutoff(symbol, preregistration.mandatory_cutoffs[ordinal - 1])
            if case is None:
                continue
            state_value, prediction_id, outcome_case_id = case
            if state_value == LongRunCaseState.EXCLUDED:
                if entry is None and prediction_id is not None:
                    issues.append(f"{symbol}:{ordinal}:excluded_case_has_identity")
                if entry is not None and prediction_id != entry.prediction_id:
                    issues.append(f"{symbol}:{ordinal}:excluded_case_prediction_mismatch")
                if outcome_case_id is not None or (
                    entry is not None and entry.prediction_id in outcomes.by_prediction
                ):
                    issues.append(f"{symbol}:{ordinal}:excluded_case_has_outcome")
            elif state_value == LongRunCaseState.PENDING_OUTCOME:
                if entry is None or prediction_id != entry.prediction_id:
                    issues.append(f"{symbol}:{ordinal}:pending_case_prediction_mismatch")
                if outcome_case_id is not None or (
                    entry is not None and entry.prediction_id in outcomes.by_prediction
                ):
                    issues.append(f"{symbol}:{ordinal}:pending_case_has_outcome")
            elif state_value == LongRunCaseState.CLEAN:
                if entry is None or prediction_id != entry.prediction_id:
                    issues.append(f"{symbol}:{ordinal}:clean_case_prediction_mismatch")
                linked = entry is not None and outcomes.by_prediction.get(entry.prediction_id)
                if linked is None or outcome_case_id != linked.outcome_case_id:
                    issues.append(f"{symbol}:{ordinal}:clean_case_outcome_mismatch")
            else:
                issues.append(f"{symbol}:{ordinal}:unknown_case_state:{state_value}")
    entry_by_id = {entry.prediction_id: entry for entry in candidate.entries}
    for link in outcomes.records:
        entry = entry_by_id.get(link.prediction_id)
        if entry is None:
            issues.append(f"outcome:unknown_prediction:{link.prediction_id}")
            continue
        if (
            link.generation_id != preregistration.generation_id
            or link.symbol != entry.symbol
            or link.cutoff != entry.cutoff
            or link.cutoff_ordinal != entry.cutoff_ordinal
        ):
            issues.append(f"outcome:identity_mismatch:{link.prediction_id}")
    for ordinal, cutoff in enumerate(preregistration.mandatory_cutoffs, 1):
        for symbol in V3_CORE_SYMBOLS:
            entry = candidate.for_cutoff(symbol, cutoff)
            context = long_run_context_for_cutoff(
                admitted,
                instrument=symbol,
                cutoff=cutoff,
                available_at=(entry.inference_started_at if entry is not None else cutoff),
                minimum_interval_end=fresh_long_run_minimum_interval_end(preregistration.start_at),
                source_snapshot_hash=preregistration.source_snapshot_sha256,
            )
            row: dict[str, object] = {
                "ordinal": ordinal,
                "cutoff": cutoff.isoformat(),
                "symbol": symbol,
                "context_valid": context is not None,
                "prediction_present": entry is not None,
                "outcome_present": False,
                "durability_valid": False,
                "case_state": (
                    case_states.get((symbol, ordinal), (None, None, None))[0].value
                    if case_states.get((symbol, ordinal)) is not None
                    else None
                ),
                "clean": False,
            }
            if context is None and entry is not None:
                issues.append(f"{symbol}:{ordinal}:invalid_context")
            case = case_states.get((symbol, ordinal))
            case_state = case[0] if case is not None else None
            if entry is not None:
                try:
                    audit_long_run_cutoff_identity(
                        preregistration,
                        symbol=symbol,
                        cutoff=entry.cutoff,
                        ordinal=entry.cutoff_ordinal,
                    )
                    attestation = candidate.durability_for(entry)
                    if attestation is None:
                        raise ValueError("missing durability attestation")
                    late = attestation.durable_appended_at > entry.prediction_deadline_at
                    if late and case_state != LongRunCaseState.EXCLUDED:
                        raise ValueError(
                            "prediction durable append exceeded frozen lateness boundary"
                        )
                    if entry.prediction.generated_at > entry.prediction_deadline_at:
                        raise ValueError("prediction generation exceeded frozen lateness boundary")
                    ready_at = (
                        max(bar.collected_at for bar in context) if context is not None else cutoff
                    )
                    if entry.inference_started_at < ready_at:
                        raise ValueError(
                            "prediction started before all context inputs were available"
                        )
                    if entry.prediction.input_snapshot_hash != _input_snapshot_hash_for_context(
                        context or (), cutoff
                    ):
                        raise ValueError("prediction input snapshot differs from admitted context")
                    row["late_prediction"] = late
                    row["durability_valid"] = not late
                    if case_state == LongRunCaseState.EXCLUDED:
                        if entry.prediction_id in outcomes.by_prediction:
                            raise ValueError("excluded prediction has an outcome link")
                        # An excluded late record remains immutable evidence of
                        # the failed opportunity.  It is not a clean case and
                        # its post-deadline durability is expected.
                        outcome = None
                    else:
                        outcome = outcomes.by_prediction.get(entry.prediction_id)
                    if outcome is not None:
                        if outcome.symbol != symbol or outcome.cutoff != cutoff:
                            raise ValueError("outcome link symbol/cutoff identity mismatch")
                        expected_times = tuple(
                            cutoff
                            + timedelta(seconds=LONG_RUN_OBSERVATION_INTERVAL_SECONDS * (index + 1))
                            for index in range(CHRONOS_HORIZON_BARS)
                        )
                        if outcome.source_interval_ends != expected_times:
                            raise ValueError(
                                "outcome intervals do not match the frozen one-hour horizon"
                            )
                        actual_by_end = {
                            bar.interval_end: _source_bar_hash(bar)
                            for bar in admitted
                            if bar.instrument == symbol
                        }
                        if any(
                            actual_by_end.get(interval_end) != bar_hash
                            for interval_end, bar_hash in zip(
                                outcome.source_interval_ends,
                                outcome.source_bar_hashes,
                                strict=True,
                            )
                        ):
                            raise ValueError(
                                "outcome link does not identify the admitted source bars"
                            )
                        source_bar_times = tuple(
                            bar.collected_at
                            for bar in admitted
                            if bar.instrument == symbol
                            and bar.interval_end in outcome.source_interval_ends
                        )
                        if len(source_bar_times) != CHRONOS_HORIZON_BARS:
                            raise ValueError("outcome link is missing an admitted source timestamp")
                        if outcome.linked_at < max(source_bar_times):
                            raise ValueError(
                                "outcome link was durable before its source bars were available"
                            )
                        if entry.prediction.generated_at >= outcome.source_interval_ends[-1]:
                            raise ValueError(
                                "prediction was generated at or after its realized outcome"
                            )
                        row["outcome_present"] = True
                    if (
                        context is not None
                        and not late
                        and outcome is not None
                        and case is not None
                        and case[0] == LongRunCaseState.CLEAN
                        and case[1] == entry.prediction_id
                        and case[2] == outcome.outcome_case_id
                    ):
                        clean_counts[symbol] += 1
                        row["clean"] = True
                except (RuntimeError, ValueError) as exc:
                    issues.append(f"{symbol}:{ordinal}:{type(exc).__name__}:{exc}")
            per_cutoff.append(row)
    status = coordinator.scientific_state.value
    raw_hash_integrity = replay_error is None
    admitted_identity_integrity = replay_error is None and admitted == persisted_normalized_bars
    if not raw_hash_integrity:
        issues.append("source:raw_replay_integrity_failure")
    if not admitted_identity_integrity:
        issues.append("source:admitted_identity_integrity_failure")
    qualification = (
        status != LongRunState.GENERATION_FATAL.value
        and all(
            clean_counts[symbol] >= preregistration.minimum_clean_cases_per_symbol
            for symbol in V3_CORE_SYMBOLS
        )
        and all(
            sum(entry.symbol == symbol for entry in candidate.entries)
            <= preregistration.target_opportunities_per_symbol
            for symbol in V3_CORE_SYMBOLS
        )
        and not replayed_tracker.revisions
        and not issues
        and source_status.get("credentials_loaded") is False
        and source_status.get("order_writes_attempted") is False
        and candidate_status.get("credentials_loaded") is False
        and candidate_status.get("order_writes_attempted") is False
    )
    report = {
        "schema": LONG_RUN_AUDIT_SCHEMA,
        "generation_id": preregistration.generation_id,
        "preregistration_sha256": preregistration_sha256,
        "repository_commit": preregistration.repository_commit,
        "evidence_class": LONG_RUN_EVIDENCE_CLASS,
        "admission_eligible": False,
        "audited_at": _iso(terminal_observed_at),
        "phase4_result": "PHASE4_LONGRUN_CERTIFIED"
        if qualification
        else "PHASE4_LONGRUN_NOT_CERTIFIED",
        "source": {
            "raw_receipts": len(raw.records),
            "transport_failures": len(transport_failures.records),
            "admitted_final_bars": {
                symbol: sum(bar.instrument == symbol for bar in admitted)
                for symbol in V3_CORE_SYMBOLS
            },
            "finality_metrics": replayed_tracker.metrics(),
            "post_admission_revision_count": len(replayed_tracker.revisions)
            + (1 if replay_error else 0),
            "replay_error": replay_error,
            "raw_hash_integrity": raw_hash_integrity,
            "admitted_identity_integrity": admitted_identity_integrity,
        },
        "candidate": {
            "predictions": {
                symbol: sum(entry.symbol == symbol for entry in candidate.entries)
                for symbol in V3_CORE_SYMBOLS
            },
            "durability_pending": list(candidate.pending_durability_keys),
            "outcome_links": {
                symbol: sum(link.symbol == symbol for link in outcomes.records)
                for symbol in V3_CORE_SYMBOLS
            },
        },
        "coverage": {
            "target_per_symbol": preregistration.target_opportunities_per_symbol,
            "minimum_clean_per_symbol": preregistration.minimum_clean_cases_per_symbol,
            "clean": clean_counts,
            "cutoffs": per_cutoff,
        },
        "watchdog": {"scientific_state": status, "fatal_events": len(coordinator.fatal_events)},
        "security": {
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        },
        "issues": issues,
    }
    report["report_hash"] = _hash_payload(report)
    return report


def _input_snapshot_hash_for_context(context: Sequence[object], cutoff: datetime) -> str:
    # Use the production Chronos input identity verbatim.  Reimplementing the
    # hash here would make a terminal audit accept a different context than
    # the candidate actually consumed.
    return _input_snapshot_hash(context, cutoff)


def process_command_identity(command: Sequence[str]) -> str:
    return _hash_payload({"command": list(command)})


def process_create_time(pid: int) -> float | None:
    """Return the OS creation time used to distinguish a reused PID."""

    if pid <= 0:
        return None
    try:
        import psutil
    except ImportError:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def process_identity_matches(
    pid: int,
    expected_command_identity: str,
    command: Sequence[str],
    *,
    expected_process_create_time: float | None = None,
) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
    except ImportError:
        return False
    try:
        process = psutil.Process(pid)
        actual = process.cmdline()
        if (
            expected_process_create_time is not None
            and process.create_time() != expected_process_create_time
        ):
            return False
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return process_command_identity(actual) == expected_command_identity and list(actual) == list(
        command
    )


__all__ = [
    "LONG_RUN_AUDIT_SCHEMA",
    "LONG_RUN_CONTEXT_RULE_ID",
    "LONG_RUN_EVIDENCE_CLASS",
    "LONG_RUN_FINALITY_RULE_ID",
    "LONG_RUN_OUTCOME_RULE_ID",
    "LONG_RUN_PREDICTION_LATENESS_SECONDS",
    "LONG_RUN_PREREGISTRATION_SCHEMA",
    "LONG_RUN_READINESS_SCHEMA",
    "LONG_RUN_RECOVERY_POLICY_ID",
    "LONG_RUN_REQUIRED_PREFLIGHT_CHECKS",
    "LONG_RUN_RC_PREFLIGHT_SCHEMA",
    "LONG_RUN_TRANSPORT_FAILURE_SCHEMA",
    "LongRunAccountingSnapshot",
    "LongRunCaseState",
    "LongRunCoordinator",
    "LongRunDurabilityAttestation",
    "LongRunEvent",
    "LongRunFinalityTracker",
    "LongRunIdentityAttestation",
    "LongRunIncident",
    "LongRunLaunchReadinessReport",
    "LongRunOutcomeLink",
    "LongRunOutcomeLinkLedger",
    "LongRunPredictionEntry",
    "LongRunPredictionLedger",
    "LongRunPreregistration",
    "LongRunRawReceipt",
    "LongRunRawReceiptView",
    "LongRunRawSpool",
    "LongRunReadinessCheck",
    "LongRunRecoveryPolicyContract",
    "LongRunRuntimeAttestation",
    "LongRunTransportFailure",
    "LongRunTransportFailureSpool",
    "LongRunState",
    "audit_long_run",
    "audit_long_run_cutoff_identity",
    "attest_long_run_identity",
    "collect_runtime_attestation",
    "long_run_component_files",
    "derive_long_run_source_snapshot_sha256",
    "fresh_long_run_minimum_interval_end",
    "identity_matches_preregistration",
    "load_long_run_preregistration",
    "long_run_context_for_cutoff",
    "long_run_preregistration_sha256",
    "prediction_deadline",
    "process_command_identity",
    "process_create_time",
    "process_identity_matches",
    "read_append_only_lines",
    "read_long_run_normalized_bars_for_start",
    "read_json_stable",
    "read_normalized_bars_stable",
    "required_context_interval_ends",
    "validate_utc_clock_progress",
    "write_immutable_long_run_preregistration",
]
