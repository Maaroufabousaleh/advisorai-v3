"""Measured admission control for the laptop resource envelope.

Callers may request a workload, but never declare available RAM/VRAM. The
governor samples operating-system state and keeps conservative reservations while
leases are active. Critical trading/account/reconciliation functions never appear
in the load-shedding set.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Protocol
from uuid import UUID, uuid4

import psutil

from advisorai.config import MissionMode, ModeConfig
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class WorkloadClass(StrEnum):
    ARCHIVE = "archive"
    BROWSER = "browser"
    HERMES_SKILL_FOUNDRY = "hermes_skill_foundry"
    LOW_PRIORITY_RESEARCH = "low_priority_research"
    TRAINING_BACKTESTS = "training_backtests"
    OPTIONAL_MODEL_CHALLENGERS = "optional_model_challengers"
    NONCRITICAL_COLLECTORS = "noncritical_collectors"
    REMOTE_LLM = "remote_llm"
    HEAVY_DUCKDB = "heavy_duckdb"
    CPU_BOUND = "cpu_bound"
    GPU = "gpu"
    ACCOUNT_ORDER_FILL_STATE = "account_order_fill_state"
    RECONCILIATION = "reconciliation"
    RISK = "risk"
    KILL_SWITCH = "kill_switch"
    CRITICAL_RAW_MARKET_PERSISTENCE = "critical_raw_market_persistence"


class ResourceProfile(StrEnum):
    """Measured process profiles from the laptop envelope in the plan."""

    TRADE_FAST = "trade_fast"
    STANDARD = "standard"
    DEEP_BUILDER = "deep_builder"
    BROWSER = "browser"
    TRAIN = "train"
    ARCHIVE = "archive"


CRITICAL_WORKLOADS = frozenset(
    {
        WorkloadClass.ACCOUNT_ORDER_FILL_STATE,
        WorkloadClass.RECONCILIATION,
        WorkloadClass.RISK,
        WorkloadClass.KILL_SWITCH,
        WorkloadClass.CRITICAL_RAW_MARKET_PERSISTENCE,
    }
)


@dataclass(frozen=True, slots=True)
class MeasuredResources:
    """Only an OS/process probe can create this production input."""

    memory_used_gib: float
    memory_available_gib: float
    gpu_free_mib: int | None
    gpu_total_mib: int | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            not isfinite(self.memory_used_gib)
            or not isfinite(self.memory_available_gib)
            or self.memory_used_gib < 0
            or self.memory_available_gib < 0
        ):
            raise ValueError("measured memory values cannot be negative")
        if self.gpu_free_mib is not None and self.gpu_free_mib < 0:
            raise ValueError("measured free GPU memory cannot be negative")
        if self.gpu_total_mib is not None and self.gpu_total_mib < 0:
            raise ValueError("measured total GPU memory cannot be negative")
        if (
            self.gpu_free_mib is not None
            and self.gpu_total_mib is not None
            and self.gpu_free_mib > self.gpu_total_mib
        ):
            raise ValueError("measured free GPU memory cannot exceed total GPU memory")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("resource measurement timestamp must include a timezone")


class MetricsProbe(Protocol):
    def measure(self) -> MeasuredResources: ...


class PsutilMetricsProbe:
    """Samples OS memory and, when available, nvidia-smi GPU memory."""

    @staticmethod
    def _gpu_memory() -> tuple[int | None, int | None]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.free,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            first_line = result.stdout.strip().splitlines()[0]
            free, total = (int(item.strip()) for item in first_line.split(","))
            return free, total
        except (FileNotFoundError, IndexError, subprocess.SubprocessError, ValueError):
            return None, None

    def measure(self) -> MeasuredResources:
        memory = psutil.virtual_memory()
        gpu_free_mib, gpu_total_mib = self._gpu_memory()
        gib = 1024**3
        return MeasuredResources(
            memory_used_gib=memory.used / gib,
            memory_available_gib=memory.available / gib,
            gpu_free_mib=gpu_free_mib,
            gpu_total_mib=gpu_total_mib,
            observed_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    workload: WorkloadClass
    memory_reservation_gib: float = 0.0
    requires_gpu: bool = False
    profile: ResourceProfile | None = None
    approved_frozen_inference: bool = False
    headroom_reviewed: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.memory_reservation_gib) or self.memory_reservation_gib < 0:
            raise ValueError("memory_reservation_gib cannot be negative")
        if self.workload in CRITICAL_WORKLOADS and self.requires_gpu:
            raise ValueError("critical safety workloads cannot depend on the GPU")
        if self.workload is WorkloadClass.GPU and not self.requires_gpu:
            raise ValueError("GPU workloads must request the global GPU lease")
        if self.approved_frozen_inference and not self.requires_gpu:
            raise ValueError("frozen inference approval only applies to GPU workloads")
        if not isinstance(self.headroom_reviewed, bool):
            raise ValueError("headroom_reviewed must be a boolean")


@dataclass(frozen=True, slots=True)
class ResourceLease:
    lease_id: UUID
    mode: MissionMode
    request: ResourceRequest
    granted_at: datetime
    profile: ResourceProfile | None = None

    def __post_init__(self) -> None:
        if self.granted_at.tzinfo is None or self.granted_at.utcoffset() is None:
            raise ValueError("resource lease timestamp must include a timezone")


@dataclass(frozen=True, slots=True)
class LeaseDecision:
    granted: bool
    reason: str
    lease: ResourceLease | None = None


class ResourceGovernor:
    def __init__(
        self,
        configs: dict[MissionMode, ModeConfig],
        probe: MetricsProbe,
        ledgers: SqliteLedgers | None = None,
    ) -> None:
        missing = set(MissionMode).difference(configs)
        if missing:
            raise ValueError(f"resource governor is missing mode configs: {sorted(missing)}")
        self.configs = configs
        self.probe = probe
        self.ledgers = ledgers
        self._leases: dict[UUID, ResourceLease] = {}

    def admit(self, mode: MissionMode, request: ResourceRequest) -> LeaseDecision:
        try:
            config = self.configs[mode]
        except KeyError as exc:
            raise ValueError(f"unknown resource mode: {mode}") from exc
        measured = self.probe.measure()
        self._record_measurement(mode, request, measured)
        active = tuple(self._leases.values())
        concurrent_reason = self._concurrency_rejection(config, request, active)
        if concurrent_reason:
            return LeaseDecision(granted=False, reason=concurrent_reason)

        reserved_memory = sum(item.request.memory_reservation_gib for item in active)
        projected_usage = (
            measured.memory_used_gib + reserved_memory + request.memory_reservation_gib
        )
        projected_available = (
            measured.memory_available_gib - reserved_memory - request.memory_reservation_gib
        )
        if projected_usage > config.memory_ceiling_gib:
            return LeaseDecision(
                granted=False,
                reason=(
                    f"measured/projected memory {projected_usage:.2f} GiB exceeds "
                    f"{mode.value} ceiling {config.memory_ceiling_gib:.2f} GiB"
                ),
            )
        if projected_available < config.minimum_headroom_gib:
            return LeaseDecision(
                granted=False,
                reason=(
                    f"projected available memory {projected_available:.2f} GiB is below "
                    f"required headroom {config.minimum_headroom_gib:.2f} GiB"
                ),
            )
        if (
            request.requires_gpu or request.workload is WorkloadClass.GPU
        ) and measured.gpu_free_mib is None:
            return LeaseDecision(
                granted=False, reason="GPU measurement unavailable; refusing GPU admission"
            )
        if (
            mode is MissionMode.TRADE_FAST
            and request.requires_gpu
            and not request.approved_frozen_inference
        ):
            return LeaseDecision(
                granted=False,
                reason="Trade/Fast GPU work requires an explicitly approved frozen inference model",
            )

        lease = ResourceLease(
            lease_id=uuid4(),
            mode=mode,
            request=request,
            granted_at=datetime.now(UTC),
            profile=request.profile,
        )
        self._leases[lease.lease_id] = lease
        self._record_lease("resource_lease_admitted", lease)
        return LeaseDecision(
            granted=True, reason="admitted from measured resource state", lease=lease
        )

    def admit_profile(self, profile: ResourceProfile, request: ResourceRequest) -> LeaseDecision:
        """Admit a non-mission process profile such as Browser or Train.

        Browser and training are separate profiles in the architecture, but
        they still share measured memory/headroom and the global GPU lease.
        """

        if request.profile is not None and request.profile is not profile:
            return LeaseDecision(granted=False, reason="resource request profile mismatch")
        active_profiles = tuple(item.profile for item in self._leases.values())
        active_workloads = tuple(item.request.workload for item in self._leases.values())
        if profile is ResourceProfile.BROWSER and ResourceProfile.BROWSER in active_profiles:
            return LeaseDecision(granted=False, reason="global browser profile cap reached")
        if profile is ResourceProfile.BROWSER and any(
            lease.request.requires_gpu or lease.request.workload is WorkloadClass.GPU
            for lease in self._leases.values()
        ):
            if not request.headroom_reviewed:
                return LeaseDecision(
                    granted=False,
                    reason="browser profile pauses GPU work unless measured headroom is reviewed",
                )
            measured = self.probe.measure()
            deep_config = self.configs[MissionMode.DEEP]
            reserved_memory = sum(
                item.request.memory_reservation_gib for item in self._leases.values()
            )
            projected_headroom = (
                measured.memory_available_gib - reserved_memory - request.memory_reservation_gib
            )
            if projected_headroom < deep_config.minimum_headroom_gib:
                return LeaseDecision(
                    granted=False,
                    reason=(
                        f"browser/GPU overlap requires {deep_config.minimum_headroom_gib:.2f} "
                        f"GiB measured headroom; projected {projected_headroom:.2f} GiB"
                    ),
                )
            if measured.gpu_free_mib is None or measured.gpu_free_mib <= 0:
                return LeaseDecision(
                    granted=False,
                    reason="browser/GPU overlap requires a measured positive GPU headroom",
                )
        if profile is ResourceProfile.TRAIN and any(
            active in {ResourceProfile.BROWSER, ResourceProfile.TRAIN} for active in active_profiles
        ):
            return LeaseDecision(
                granted=False,
                reason="training requires an exclusive GPU/build profile",
            )
        if (
            profile is ResourceProfile.TRAIN
            and WorkloadClass.HERMES_SKILL_FOUNDRY in active_workloads
        ):
            return LeaseDecision(
                granted=False,
                reason="training requires Hermes to be stopped",
            )
        if profile is ResourceProfile.BROWSER and (
            request.workload is not WorkloadClass.BROWSER or request.requires_gpu
        ):
            return LeaseDecision(
                granted=False,
                reason="Browser profile requires a non-GPU browser workload",
            )
        if profile is ResourceProfile.TRAIN and (
            request.workload is not WorkloadClass.TRAINING_BACKTESTS or not request.requires_gpu
        ):
            return LeaseDecision(
                granted=False,
                reason="Train profile requires one GPU-backed training/backtest workload",
            )
        if profile is ResourceProfile.ARCHIVE and request.requires_gpu:
            return LeaseDecision(granted=False, reason="Archive profile cannot use the GPU")

        mode_by_profile = {
            ResourceProfile.TRADE_FAST: MissionMode.TRADE_FAST,
            ResourceProfile.STANDARD: MissionMode.STANDARD,
            ResourceProfile.DEEP_BUILDER: MissionMode.DEEP,
            ResourceProfile.BROWSER: MissionMode.DEEP,
            ResourceProfile.TRAIN: MissionMode.DEEP,
            ResourceProfile.ARCHIVE: MissionMode.TRADE_FAST,
        }
        profile_request = ResourceRequest(
            workload=request.workload,
            memory_reservation_gib=request.memory_reservation_gib,
            requires_gpu=request.requires_gpu,
            profile=profile,
            approved_frozen_inference=request.approved_frozen_inference,
            headroom_reviewed=request.headroom_reviewed,
        )
        decision = self.admit(mode_by_profile[profile], profile_request)
        if not decision.granted or decision.lease is None:
            return decision

        measured = self.probe.measure()
        projected_usage = measured.memory_used_gib + request.memory_reservation_gib
        upper_bounds = {
            ResourceProfile.TRADE_FAST: 6.5,
            ResourceProfile.STANDARD: 8.5,
            ResourceProfile.DEEP_BUILDER: 9.0,
            ResourceProfile.BROWSER: 8.0,
            ResourceProfile.TRAIN: 9.0,
            ResourceProfile.ARCHIVE: 6.5,
        }
        if projected_usage > upper_bounds[profile]:
            self.release(decision.lease.lease_id)
            return LeaseDecision(
                granted=False,
                reason=(
                    f"measured/profile memory {projected_usage:.2f} GiB is outside "
                    f"{profile.value} envelope"
                ),
            )
        return decision

    def release(self, lease_id: UUID) -> None:
        lease = self._leases.pop(lease_id, None)
        if lease is not None:
            self._record_lease("resource_lease_released", lease)

    def active_leases(self) -> tuple[ResourceLease, ...]:
        return tuple(self._leases.values())

    def _record_measurement(
        self, mode: MissionMode, request: ResourceRequest, measured: MeasuredResources
    ) -> None:
        if self.ledgers is None:
            return
        payload = {
            "mode": mode.value,
            "workload": request.workload.value,
            "memory_used_gib": measured.memory_used_gib,
            "memory_available_gib": measured.memory_available_gib,
            "gpu_free_mib": measured.gpu_free_mib,
            "gpu_total_mib": measured.gpu_total_mib,
            "observed_at": measured.observed_at.isoformat(),
        }
        digest = hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest()
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.CAPABILITY,
                event_type="resource_measurement_recorded",
                idempotency_key=f"resource-measurement:{digest}",
                occurred_at=measured.observed_at,
                payload=payload,
            )
        )

    def _record_lease(self, event_type: str, lease: ResourceLease) -> None:
        if self.ledgers is None:
            return
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.CAPABILITY,
                event_type=event_type,
                idempotency_key=f"resource-lease:{lease.lease_id}:{event_type}",
                occurred_at=lease.granted_at,
                payload={
                    "lease_id": str(lease.lease_id),
                    "mode": lease.mode.value,
                    "workload": lease.request.workload.value,
                    "memory_reservation_gib": lease.request.memory_reservation_gib,
                    "requires_gpu": lease.request.requires_gpu,
                    "profile": lease.profile.value if lease.profile is not None else None,
                },
            )
        )

    @staticmethod
    def load_shedding_candidates() -> tuple[WorkloadClass, ...]:
        """Return only the prescribed optional-work shedding sequence."""

        return (
            WorkloadClass.ARCHIVE,
            WorkloadClass.BROWSER,
            WorkloadClass.HERMES_SKILL_FOUNDRY,
            WorkloadClass.LOW_PRIORITY_RESEARCH,
            WorkloadClass.TRAINING_BACKTESTS,
            WorkloadClass.OPTIONAL_MODEL_CHALLENGERS,
            WorkloadClass.NONCRITICAL_COLLECTORS,
        )

    @staticmethod
    def _concurrency_rejection(
        config: ModeConfig,
        request: ResourceRequest,
        active: tuple[ResourceLease, ...],
    ) -> str | None:
        workloads = [lease.request.workload for lease in active]
        if (
            request.workload is WorkloadClass.REMOTE_LLM
            and workloads.count(request.workload) >= config.remote_llm_limit
        ):
            return f"{config.mode.value} remote LLM cap reached"
        if (
            request.workload is WorkloadClass.HERMES_SKILL_FOUNDRY
            and workloads.count(request.workload) >= config.hermes_limit
        ):
            return f"{config.mode.value} Hermes cap reached"
        if (
            request.workload is WorkloadClass.BROWSER
            and request.profile is not ResourceProfile.BROWSER
            and workloads.count(request.workload) >= config.browser_limit
        ):
            return f"{config.mode.value} browser cap reached"
        if (
            request.workload is WorkloadClass.HEAVY_DUCKDB
            and workloads.count(request.workload) >= config.heavy_duckdb_limit
        ):
            return f"{config.mode.value} heavy DuckDB cap reached"
        if (
            request.workload is WorkloadClass.CPU_BOUND
            and workloads.count(request.workload) >= config.cpu_worker_limit
        ):
            return f"{config.mode.value} CPU worker cap reached"
        if request.workload is WorkloadClass.GPU or request.requires_gpu:
            gpu_leases = sum(
                lease.request.requires_gpu or lease.request.workload is WorkloadClass.GPU
                for lease in active
            )
            if gpu_leases >= config.gpu_jobs_limit:
                return "global GPU lease cap reached"
        if (
            request.workload is WorkloadClass.TRAINING_BACKTESTS
            and workloads.count(request.workload) >= 1
        ):
            return "one heavy backtest/trainer may run at a time"
        return None
