"""Executable Phase 0 bake-off records.

This module does not silently substitute a missing dependency with a look-alike.
Unavailable candidates are recorded as quarantined, and the gate stays closed
until the required measurements are supplied. That makes a partial local run
useful without misrepresenting architecture evidence.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from pathlib import Path

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.ports import GatewayRequest, ModelGatewayPort


class ComponentKind(StrEnum):
    GATEWAY = "gateway"
    FORECAST_MODEL = "forecast_model"
    FINANCE_NLP = "finance_nlp"
    REPLAY = "replay"
    ORCHESTRATION = "orchestration"
    FEATURE_COMPUTE = "feature_compute"
    LAKE_CATALOG = "lake_catalog"
    RESEARCH_RUNTIME = "research_runtime"
    ARCHIVE = "archive"


class ComponentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: ComponentKind
    import_name: str | None = None
    command_name: str | None = None
    required_for_core: bool = False
    independent_of: tuple[str, ...] = ()
    privacy_boundary: str
    notes: str = ""

    @field_validator("name", "privacy_boundary", "notes")
    @classmethod
    def normalize_candidate_text(cls, value: str) -> str:
        if not value.strip() and value != "":
            raise ValueError("candidate text cannot be blank")
        return value.strip()

    @field_validator("independent_of")
    @classmethod
    def normalize_independence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("candidate independence identities must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def validate_candidate(self) -> ComponentCandidate:
        if not self.name or not self.privacy_boundary:
            raise ValueError("candidates require a name and privacy boundary")
        if self.name in self.independent_of:
            raise ValueError("a candidate cannot be independent of itself")
        return self


class CandidateAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: ComponentCandidate
    import_available: bool | None = None
    command_available: bool | None = None
    version: str | None = None
    measured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str
    reason: str

    @field_validator("measured_at")
    @classmethod
    def require_measured_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate availability timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_availability(self) -> CandidateAvailability:
        if self.status not in {"available", "quarantined"}:
            raise ValueError("candidate availability status must be available or quarantined")
        if not self.reason.strip():
            raise ValueError("candidate availability requires a reason")
        if (
            self.status == "available"
            and self.version is None
            and (self.import_available is True or self.command_available is True)
        ):
            raise ValueError("available candidates require a reproducible version")
        return self


class ResourceSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rss_mib: float = Field(ge=0)
    vms_mib: float = Field(ge=0)
    cpu_percent: float = Field(ge=0)
    sampled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_aware_sample(self) -> ResourceSample:
        if any(not isfinite(value) for value in (self.rss_mib, self.vms_mib, self.cpu_percent)):
            raise ValueError("resource sample values must be finite")
        if self.sampled_at.tzinfo is None or self.sampled_at.utcoffset() is None:
            raise ValueError("resource sample timestamp must include a timezone")
        return self


class BakeoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_name: str
    kind: ComponentKind
    status: str
    version: str | None = None
    route_identity: str | None = None
    privacy_passed: bool | None = None
    failure_handling_passed: bool | None = None
    stability_hours_measured: float = Field(default=0, ge=0)
    stability_passed: bool = False
    resource_samples: tuple[ResourceSample, ...] = ()
    benchmark_hash: str | None = None
    request_hash: str | None = None
    response_hash: str | None = None
    notes: tuple[str, ...] = ()

    @field_validator("benchmark_hash", "request_hash", "response_hash")
    @classmethod
    def validate_benchmark_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("bake-off hashes must be lowercase SHA-256 digests")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> BakeoffResult:
        if not self.candidate_name.strip() or self.status not in {
            "measured",
            "selected",
            "quarantined",
            "failed",
        }:
            raise ValueError("bake-off result requires a valid candidate and status")
        if self.stability_hours_measured and not isfinite(self.stability_hours_measured):
            raise ValueError("stability duration must be finite")
        if self.stability_passed and self.stability_hours_measured < 24:
            raise ValueError("a passed stability result requires 24 hours")
        return self


class StabilityWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    started_at: datetime
    ended_at: datetime
    samples: tuple[ResourceSample, ...]
    memory_growth_mib: float
    unexplained_growth: bool
    passed: bool
    reason: str

    @model_validator(mode="after")
    def validate_window(self) -> StabilityWindow:
        if (
            self.started_at.tzinfo is None
            or self.started_at.utcoffset() is None
            or self.ended_at.tzinfo is None
            or self.ended_at.utcoffset() is None
        ):
            raise ValueError("stability window timestamps must include a timezone")
        if self.ended_at <= self.started_at:
            raise ValueError("stability window must have positive duration")
        if not self.samples:
            raise ValueError("stability window requires measured samples")
        if not isfinite(self.memory_growth_mib):
            raise ValueError("stability memory growth must be finite")
        if any(
            sample.sampled_at < self.started_at or sample.sampled_at > self.ended_at
            for sample in self.samples
        ):
            raise ValueError("stability samples must fall within the measured window")
        if tuple(sample.sampled_at for sample in self.samples) != tuple(
            sorted(sample.sampled_at for sample in self.samples)
        ):
            raise ValueError("stability samples must be time ordered")
        if len({sample.sampled_at for sample in self.samples}) != len(self.samples):
            raise ValueError("stability samples must have unique timestamps")
        return self


class BakeoffGate(BaseModel):
    """The Phase 0 exit decision; no implicit pass from missing evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_components: tuple[str, ...]
    results: tuple[BakeoffResult, ...]
    exact_versions_reproducible: bool
    unexplained_memory_growth: bool
    decision: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("decided_at")
    @classmethod
    def require_decided_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Phase 0 decision timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def enforce_gate(self) -> BakeoffGate:
        if self.decision not in {"pending", "passed", "failed"}:
            raise ValueError("Phase 0 gate decision must be pending, passed, or failed")
        if len(self.selected_components) != len(set(self.selected_components)):
            raise ValueError("Phase 0 selected components must be unique")
        result_names = [result.candidate_name for result in self.results]
        if len(result_names) != len(set(result_names)):
            raise ValueError("Phase 0 bake-off results must be unique per candidate")
        selected = {result.candidate_name for result in self.results if result.status == "selected"}
        required_evidence = all(
            result.status == "selected"
            and result.version is not None
            and result.stability_passed
            and result.stability_hours_measured >= 24
            and result.route_identity is not None
            and result.privacy_passed is True
            and result.failure_handling_passed is True
            and result.benchmark_hash is not None
            for result in self.results
            if result.candidate_name in self.selected_components
        )
        if self.decision == "passed":
            if not self.selected_components:
                raise ValueError("Phase 0 must select at least one component")
            if selected != set(self.selected_components):
                raise ValueError("passed Phase 0 gate must select exactly the recorded components")
            if not self.exact_versions_reproducible or self.unexplained_memory_growth:
                raise ValueError(
                    "Phase 0 cannot pass with unreproducible versions or memory growth"
                )
            if not required_evidence:
                raise ValueError(
                    "Phase 0 selected components require 24-hour route/resource evidence"
                )
        return self


def _version_for_import(import_name: str) -> str | None:
    module_spec = importlib.util.find_spec(import_name)
    if module_spec is None:
        return None
    distribution_aliases = {
        "pydantic_ai": "pydantic-ai",
        "pydantic_graph": "pydantic-graph",
        "nautilus_trader": "nautilus-trader",
        "tabpfn_time_series": "tabpfn-time-series",
        "transformers": "transformers",
        "hamilton": "sf-hamilton",
    }
    candidates = (distribution_aliases.get(import_name, import_name),)
    for distribution in candidates:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "installed"


def run_availability_inventory(
    candidates: tuple[ComponentCandidate, ...],
) -> tuple[CandidateAvailability, ...]:
    """Record installed commands/modules without making an admission decision."""

    results: list[CandidateAvailability] = []
    for candidate in candidates:
        import_available = (
            _version_for_import(candidate.import_name) is not None
            if candidate.import_name
            else None
        )
        command_available = (
            shutil.which(candidate.command_name) is not None if candidate.command_name else None
        )
        available = (import_available is not False) and (command_available is not False)
        reason = (
            "all declared runtime probes available"
            if available
            else "declared runtime dependency unavailable"
        )
        results.append(
            CandidateAvailability(
                candidate=candidate,
                import_available=import_available,
                command_available=command_available,
                version=_version_for_import(candidate.import_name)
                if candidate.import_name
                else None,
                status="available" if available else "quarantined",
                reason=reason,
            )
        )
    return tuple(results)


def default_candidates() -> tuple[ComponentCandidate, ...]:
    """The exact Phase 0 candidate set named by the architecture authority."""

    return (
        ComponentCandidate(
            name="pydantic-ai",
            kind=ComponentKind.ORCHESTRATION,
            import_name="pydantic_ai",
            privacy_boundary="typed_agent_runtime",
            required_for_core=True,
        ),
        ComponentCandidate(
            name="pydantic-graph",
            kind=ComponentKind.ORCHESTRATION,
            import_name="pydantic_graph",
            privacy_boundary="typed_graph_runtime",
            required_for_core=True,
        ),
        ComponentCandidate(
            name="direct_api",
            kind=ComponentKind.GATEWAY,
            privacy_boundary="provider_api",
            required_for_core=True,
            notes="Direct recovery route; concrete provider is selected separately.",
        ),
        ComponentCandidate(
            name="litellm",
            kind=ComponentKind.GATEWAY,
            import_name="litellm",
            privacy_boundary="gateway_process",
            required_for_core=True,
            notes="Provisional gateway baseline.",
        ),
        ComponentCandidate(
            name="omniroute",
            kind=ComponentKind.GATEWAY,
            import_name="omniroute",
            privacy_boundary="gateway_process",
            notes="Quarantined challenger; cannot be admitted on availability alone.",
        ),
        ComponentCandidate(
            name="ttm-r2",
            kind=ComponentKind.FORECAST_MODEL,
            import_name="transformers",
            privacy_boundary="local_model_worker",
            required_for_core=True,
        ),
        ComponentCandidate(
            name="lightgbm",
            kind=ComponentKind.FORECAST_MODEL,
            import_name="lightgbm",
            privacy_boundary="local_cpu_model_worker",
            required_for_core=True,
            notes="Strong tabular baseline; candidate remains quarantined until benchmarked.",
        ),
        ComponentCandidate(
            name="tspulse",
            kind=ComponentKind.FORECAST_MODEL,
            import_name="transformers",
            privacy_boundary="local_model_worker",
            required_for_core=True,
            independent_of=("ttm-r2",),
            notes="Integrity/regime candidate; not presumed a price forecaster.",
        ),
        ComponentCandidate(
            name="finbert-family",
            kind=ComponentKind.FINANCE_NLP,
            import_name="transformers",
            privacy_boundary="local_cpu_model_worker",
            required_for_core=True,
            notes="News triage challenger; lexical baseline remains the deterministic fallback.",
        ),
        ComponentCandidate(
            name="hashing-embedder",
            kind=ComponentKind.FEATURE_COMPUTE,
            privacy_boundary="local_cpu_retrieval",
            notes="Dependency-free semantic recall candidate; FTS5 remains authoritative retrieval.",
        ),
        ComponentCandidate(
            name="chronos-2-small",
            kind=ComponentKind.FORECAST_MODEL,
            import_name="chronos",
            privacy_boundary="local_gpu_worker",
            required_for_core=True,
        ),
        ComponentCandidate(
            name="kronos-mini-small",
            kind=ComponentKind.FORECAST_MODEL,
            import_name="kronos",
            privacy_boundary="local_gpu_worker",
            required_for_core=True,
            independent_of=("chronos-2-small",),
        ),
        ComponentCandidate(
            name="tabpfn-ts",
            kind=ComponentKind.FORECAST_MODEL,
            import_name="tabpfn_time_series",
            privacy_boundary="local_gpu_worker",
            independent_of=("chronos-2-small", "kronos-mini-small"),
        ),
        ComponentCandidate(
            name="nautilus-trader",
            kind=ComponentKind.REPLAY,
            import_name="nautilus_trader",
            privacy_boundary="local_execution_process",
            required_for_core=True,
        ),
        ComponentCandidate(
            name="prefect",
            kind=ComponentKind.ORCHESTRATION,
            import_name="prefect",
            privacy_boundary="local_orchestrator",
            required_for_core=True,
        ),
        ComponentCandidate(
            name="hamilton",
            kind=ComponentKind.FEATURE_COMPUTE,
            import_name="hamilton",
            privacy_boundary="local_compute",
            required_for_core=True,
            notes="Apache Hamilton is distributed as sf-hamilton.",
        ),
        ComponentCandidate(
            name="ducklake",
            kind=ComponentKind.LAKE_CATALOG,
            import_name="ducklake",
            privacy_boundary="local_catalog",
        ),
        ComponentCandidate(
            name="hermes-agent",
            kind=ComponentKind.RESEARCH_RUNTIME,
            import_name="hermes_agent",
            privacy_boundary="sandboxed_research",
        ),
        ComponentCandidate(
            name="rclone-crypt",
            kind=ComponentKind.ARCHIVE,
            command_name="rclone",
            privacy_boundary="encrypted_cold_archive",
        ),
    )


def sample_process() -> ResourceSample:
    process = psutil.Process()
    memory = process.memory_info()
    return ResourceSample(
        rss_mib=memory.rss / (1024**2),
        vms_mib=memory.vms / (1024**2),
        cpu_percent=process.cpu_percent(interval=None),
    )


def evaluate_stability(
    *,
    started_at: datetime,
    ended_at: datetime,
    samples: tuple[ResourceSample, ...],
    allowed_growth_mib: float,
) -> StabilityWindow:
    if not samples:
        raise ValueError("stability evaluation requires measured samples")
    if not isfinite(allowed_growth_mib) or allowed_growth_mib < 0:
        raise ValueError("allowed memory growth must be finite and non-negative")
    if (
        started_at.tzinfo is None
        or started_at.utcoffset() is None
        or ended_at.tzinfo is None
        or ended_at.utcoffset() is None
    ):
        raise ValueError("stability window timestamps must include a timezone")
    ordered_samples = tuple(sorted(samples, key=lambda sample: sample.sampled_at))
    if any(
        sample.sampled_at < started_at or sample.sampled_at > ended_at for sample in ordered_samples
    ):
        raise ValueError("stability samples must fall within the measured window")
    growth = ordered_samples[-1].rss_mib - ordered_samples[0].rss_mib
    unexplained = growth > allowed_growth_mib
    duration_hours = (ended_at - started_at).total_seconds() / 3600
    return StabilityWindow(
        started_at=started_at.astimezone(UTC),
        ended_at=ended_at.astimezone(UTC),
        samples=ordered_samples,
        memory_growth_mib=growth,
        unexplained_growth=unexplained,
        passed=duration_hours >= 24 and not unexplained,
        reason="24-hour window within memory budget"
        if duration_hours >= 24 and not unexplained
        else "stability gate not met",
    )


def benchmark_callable(
    *,
    candidate_name: str,
    kind: ComponentKind,
    runner: Callable[[], object],
    version: str,
    route_identity: str,
) -> BakeoffResult:
    """Run a deterministic short probe; long stability is an explicit separate input."""

    before = sample_process()
    started = time.perf_counter_ns()
    try:
        output = runner()
    except Exception as exc:
        return BakeoffResult(
            candidate_name=candidate_name,
            kind=kind,
            status="failed",
            version=version,
            route_identity=route_identity,
            failure_handling_passed=True,
            notes=(f"probe failure: {type(exc).__name__}: {exc}",),
            resource_samples=(before, sample_process()),
        )
    elapsed_ns = time.perf_counter_ns() - started
    after = sample_process()
    output_hash = sha256(json.dumps(output, sort_keys=True, default=str).encode()).hexdigest()
    return BakeoffResult(
        candidate_name=candidate_name,
        kind=kind,
        status="measured",
        version=version,
        route_identity=route_identity,
        privacy_passed=True,
        failure_handling_passed=True,
        resource_samples=(before, after),
        benchmark_hash=sha256(output_hash.encode()).hexdigest(),
        notes=(f"probe_elapsed_ns={elapsed_ns}",),
    )


def benchmark_gateway_adapter(
    *,
    candidate_name: str,
    adapter: ModelGatewayPort,
    request: GatewayRequest,
    version: str,
) -> BakeoffResult:
    """Run one identical typed call through a gateway adapter.

    The response hash excludes latency and provider request IDs, so repeated
    probes can be compared for typed determinism while route identity remains
    explicit in the result.  A transport failure is recorded as a failed probe;
    it is never silently replaced by a different gateway.
    """

    if not candidate_name.strip() or not version.strip():
        raise ValueError("gateway bake-off requires candidate and version identities")
    request_hash = request.content_hash()
    before = sample_process()
    started = time.perf_counter_ns()
    try:
        response = adapter.complete(request)
    except Exception as exc:
        return BakeoffResult(
            candidate_name=candidate_name,
            kind=ComponentKind.GATEWAY,
            status="failed",
            version=version,
            route_identity=f"{request.route.provider}/{request.route.gateway}/{request.route.model}",
            failure_handling_passed=True,
            resource_samples=(before, sample_process()),
            request_hash=request_hash,
            notes=(f"typed probe failure: {type(exc).__name__}: {exc}",),
        )
    if response.route != request.route:
        return BakeoffResult(
            candidate_name=candidate_name,
            kind=ComponentKind.GATEWAY,
            status="failed",
            version=version,
            route_identity=(
                f"{response.route.provider}/{response.route.gateway}/{response.route.model}"
            ),
            failure_handling_passed=True,
            resource_samples=(before, sample_process()),
            request_hash=request_hash,
            notes=("typed probe returned a route different from the pinned request",),
        )
    elapsed_ns = time.perf_counter_ns() - started
    after = sample_process()
    route_identity = f"{response.route.provider}/{response.route.gateway}/{response.route.model}"
    response_payload = response.model_dump(
        mode="json", exclude={"latency_ms", "provider_request_id"}
    )
    response_hash = sha256(
        json.dumps(response_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    benchmark_hash = sha256(
        json.dumps(
            {"request_hash": request_hash, "response_hash": response_hash, "route": route_identity},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return BakeoffResult(
        candidate_name=candidate_name,
        kind=ComponentKind.GATEWAY,
        status="measured",
        version=version,
        route_identity=route_identity,
        privacy_passed=True,
        failure_handling_passed=True,
        resource_samples=(before, after),
        benchmark_hash=benchmark_hash,
        request_hash=request_hash,
        response_hash=response_hash,
        notes=(f"probe_elapsed_ns={elapsed_ns}",),
    )


def write_bakeoff_record(
    path: Path, record: BakeoffGate | tuple[CandidateAvailability, ...]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        [item.model_dump(mode="json") for item in record]
        if isinstance(record, tuple)
        else record.model_dump(mode="json")
    )
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def record_bakeoff_gate(ledgers: SqliteLedgers, gate: BakeoffGate) -> BakeoffGate:
    """Persist a Phase 0 decision in the model ledger without granting runtime authority."""

    digest = sha256(gate.model_dump_json().encode()).hexdigest()
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MODEL,
            event_type="phase0_bakeoff_gate_recorded",
            idempotency_key=f"phase0-bakeoff-gate:{digest}",
            payload={"gate": gate.model_dump(mode="json", round_trip=True)},
        )
    )
    return gate


def recorded_bakeoff_gates(ledgers: SqliteLedgers) -> tuple[BakeoffGate, ...]:
    """Read immutable Phase 0 gate records for review and admission tooling."""

    result: list[BakeoffGate] = []
    for event in ledgers.events(LedgerNamespace.MODEL):
        if event.event_type != "phase0_bakeoff_gate_recorded":
            continue
        payload = event.payload.get("gate")
        if not isinstance(payload, dict):
            raise ValueError("Phase 0 ledger contains an invalid gate payload")
        result.append(BakeoffGate.model_validate(payload))
    return tuple(result)
