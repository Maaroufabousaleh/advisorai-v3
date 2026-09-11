"""Read-only shutdown readiness for a sealed Phase-4 evidence generation.

This module never writes evidence, stops processes, acquires a model, opens a
network connection, or shuts down the host.  It deliberately requires the
immutable source deadline to have elapsed in addition to terminal root states;
reaching a sample target early is not permission to power off while a bound
candidate process may still be running.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

import psutil

TERMINAL_STATES = frozenset(
    {
        "completed",
        "deadline_reached",
        "sealed",
        "stopped_with_evidence",
        "target_exited",
        "target_reached",
    }
)

SOURCE_REQUIRED_FILES = (
    "manifest.json",
    "status.json",
    "summary.json",
    "heartbeat.json",
    "raw-responses.jsonl",
    "normalized-bars.jsonl",
    "completed-cases.jsonl",
    "case-rejections.jsonl",
    "failures.jsonl",
    "source-health.jsonl",
    "collector.lock",
    "collector.pid",
)
RESOURCE_REQUIRED_FILES = (
    "config.json",
    "status.json",
    "summary.json",
    "heartbeat.json",
    "observations.jsonl",
)
CANDIDATE_REQUIRED_FILES = ("manifest.json", "status.json", "rejections.jsonl")


@dataclass(frozen=True)
class ProcessInspection:
    """Read-only process observation used by the shutdown decision."""

    alive: bool | None
    command_line: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ShutdownReadiness:
    """Fail-closed shutdown decision and human-readable refusal reasons."""

    decision: str
    reasons: tuple[str, ...]

    @property
    def safe(self) -> bool:
        return self.decision == "SAFE_TO_SHUT_DOWN"


ProcessProbe = Callable[[int], ProcessInspection]


def _read_json(path: Path, label: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{label}_unreadable:{type(exc).__name__}"
    if not isinstance(value, dict):
        return None, f"{label}_not_object"
    return value, None


def _parse_timestamp(value: object, label: str) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str):
        return None, f"{label}_missing_or_invalid"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, f"{label}_invalid"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, f"{label}_must_include_timezone"
    return parsed.astimezone(UTC), None


def _pid_from_file(path: Path) -> tuple[int | None, str | None]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return None, f"pid_file_unreadable:{path}:{type(exc).__name__}"
    try:
        pid = int(value)
    except ValueError:
        return None, f"pid_file_invalid:{path}"
    if pid <= 0:
        return None, f"pid_file_invalid:{path}"
    return pid, None


def _live_process(pid: int) -> ProcessInspection:
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return ProcessInspection(alive=False)
        return ProcessInspection(alive=True, command_line=tuple(process.cmdline()))
    except psutil.NoSuchProcess:
        return ProcessInspection(alive=False)
    except (psutil.AccessDenied, psutil.ZombieProcess) as exc:
        return ProcessInspection(alive=None, error=type(exc).__name__)
    except OSError as exc:
        return ProcessInspection(alive=None, error=type(exc).__name__)


def _required_files(root: Path, names: Iterable[str], label: str) -> list[str]:
    reasons: list[str] = []
    for name in names:
        path = root / name
        if not path.is_file():
            reasons.append(f"{label}_missing_file:{name}")
    return reasons


def _temporary_files(root: Path) -> list[str]:
    try:
        return sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and (path.name.startswith(".") and path.name.endswith(".tmp"))
        )
    except OSError:
        return ["<unable_to_scan>"]


def _security_reasons(label: str, documents: Iterable[tuple[str, dict[str, object]]]) -> list[str]:
    reasons: list[str] = []
    observed_security_fields = False
    for name, document in documents:
        for field in ("credentials_loaded", "order_writes_attempted"):
            if field not in document:
                continue
            observed_security_fields = True
            if document[field] is not False:
                reasons.append(f"{label}_{name}_{field}_not_false")
    if not observed_security_fields:
        reasons.append(f"{label}_security_flags_unproven")
    return reasons


def _root_documents(
    root: Path,
    *,
    label: str,
    required_files: Iterable[str],
) -> tuple[list[str], dict[str, dict[str, object]]]:
    reasons: list[str] = []
    if not root.is_dir():
        return [f"{label}_root_missing"], {}
    reasons.extend(_required_files(root, required_files, label))
    documents: dict[str, dict[str, object]] = {}
    for name in ("manifest.json", "status.json", "summary.json", "heartbeat.json", "config.json"):
        path = root / name
        if not path.is_file():
            continue
        document, error = _read_json(path, f"{label}_{name}")
        if error:
            reasons.append(error)
        elif document is not None:
            documents[name] = document
    temporary = _temporary_files(root)
    reasons.extend(f"{label}_temporary_file:{path}" for path in temporary)
    return reasons, documents


def _terminal_root_reasons(
    label: str,
    documents: dict[str, dict[str, object]],
) -> list[str]:
    reasons: list[str] = []
    status = documents.get("status.json")
    summary = documents.get("summary.json")
    if status is None:
        return [f"{label}_status_unproven"]
    state = status.get("state")
    if state not in TERMINAL_STATES:
        reasons.append(f"{label}_state_not_terminal:{state!r}")
    if summary is not None and summary.get("state") != state:
        reasons.append(f"{label}_status_summary_state_mismatch")
    reasons.extend(_security_reasons(label, documents.items()))
    return reasons


def _target_end(
    source_documents: dict[str, dict[str, object]],
) -> tuple[datetime | None, list[str]]:
    reasons: list[str] = []
    values: list[datetime] = []
    for name, document in source_documents.items():
        if "target_end_at" not in document:
            continue
        parsed, error = _parse_timestamp(document["target_end_at"], f"source_{name}_target_end_at")
        if error:
            reasons.append(error)
        elif parsed is not None:
            values.append(parsed)
    if not values:
        return None, ["source_target_end_unproven"]
    if any(value != values[0] for value in values[1:]):
        reasons.append("source_target_end_mismatch")
    return values[0], reasons


def _check_lock(path: Path, label: str) -> list[str]:
    if not path.is_file():
        return [f"{label}_lock_missing"]
    if fcntl is None:
        return [f"{label}_lock_probe_unavailable"]
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return [f"{label}_lock_owned"]
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError as exc:
        return [f"{label}_lock_unreadable:{type(exc).__name__}"]
    return []


def _candidate_pids(
    root: Path, documents: dict[str, dict[str, object]]
) -> tuple[list[int], list[str]]:
    pids: list[int] = []
    reasons: list[str] = []
    pid_file = root / "candidate.pid"
    if pid_file.is_file():
        pid, error = _pid_from_file(pid_file)
        if error:
            reasons.append(error)
        elif pid is not None:
            pids.append(pid)
    for name in ("status.json", "manifest.json"):
        value = documents.get(name, {}).get("pid")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            reasons.append(f"candidate_{name}_pid_invalid")
        else:
            pids.append(value)
    return sorted(set(pids)), reasons


def _process_reasons(
    *,
    label: str,
    pids: Iterable[int],
    expected_tokens: tuple[str, ...],
    probe: ProcessProbe,
) -> list[str]:
    pids = tuple(sorted(set(pids)))
    if not pids:
        return [f"{label}_pid_unproven"]
    reasons: list[str] = []
    for pid in pids:
        inspection = probe(pid)
        if inspection.alive is None:
            reasons.append(f"{label}_pid_{pid}_inspection_unproven:{inspection.error}")
        elif inspection.alive:
            command = " ".join(inspection.command_line)
            if not expected_tokens or not all(token in command for token in expected_tokens):
                reasons.append(f"{label}_pid_{pid}_reused_or_command_mismatch")
            else:
                reasons.append(f"{label}_process_active:{pid}")
    return reasons


def evaluate_shutdown_readiness(
    *,
    source_root: Path,
    resource_root: Path,
    candidate_root: Path,
    source_pid_file: Path | None = None,
    resource_pid_file: Path | None = None,
    candidate_pid_file: Path | None = None,
    now: datetime | None = None,
    source_process_tokens: tuple[str, ...] = ("collect_phase4_v3core_forward.py",),
    resource_process_tokens: tuple[str, ...] = ("monitor_phase3_process_resources.py",),
    candidate_process_tokens: tuple[str, ...] = ("run_phase4_v3core_chronos_predictions.py",),
    process_probe: ProcessProbe = _live_process,
) -> ShutdownReadiness:
    """Evaluate whether the host may be shut down without stopping evidence work."""

    source_root = source_root.resolve()
    resource_root = resource_root.resolve()
    candidate_root = candidate_root.resolve()
    source_pid_file = (source_pid_file or source_root / "collector.pid").resolve()
    resource_pid_file = (resource_pid_file or resource_root / "resource-sidecar.pid").resolve()
    candidate_pid_file = (candidate_pid_file or candidate_root / "candidate.pid").resolve()
    current = (now or datetime.now(UTC)).astimezone(UTC)
    reasons: list[str] = []

    source_file_reasons, source_documents = _root_documents(
        source_root,
        label="source",
        required_files=SOURCE_REQUIRED_FILES,
    )
    resource_file_reasons, resource_documents = _root_documents(
        resource_root,
        label="resource",
        required_files=RESOURCE_REQUIRED_FILES,
    )
    candidate_file_reasons, candidate_documents = _root_documents(
        candidate_root,
        label="candidate",
        required_files=CANDIDATE_REQUIRED_FILES,
    )
    reasons.extend(source_file_reasons)
    reasons.extend(resource_file_reasons)
    reasons.extend(candidate_file_reasons)
    reasons.extend(_terminal_root_reasons("source", source_documents))
    reasons.extend(_terminal_root_reasons("resource", resource_documents))
    reasons.extend(_terminal_root_reasons("candidate", candidate_documents))

    target_end, target_reasons = _target_end(source_documents)
    reasons.extend(target_reasons)
    if target_end is not None and current < target_end:
        reasons.append(f"clock_before_target_end:{target_end.isoformat()}")

    source_pid, source_pid_error = _pid_from_file(source_pid_file)
    if source_pid_error:
        reasons.append(source_pid_error)
    source_status_pid = source_documents.get("status.json", {}).get("pid")
    source_pids = [pid for pid in (source_pid, source_status_pid) if isinstance(pid, int)]
    reasons.extend(
        _process_reasons(
            label="source",
            pids=source_pids,
            expected_tokens=source_process_tokens + (str(source_root),),
            probe=process_probe,
        )
    )

    resource_pid, resource_pid_error = _pid_from_file(resource_pid_file)
    if resource_pid_error and resource_pid_file.exists():
        reasons.append(resource_pid_error)
    resource_status_pid = resource_documents.get("status.json", {}).get("pid")
    resource_pids = [pid for pid in (resource_pid, resource_status_pid) if isinstance(pid, int)]
    reasons.extend(
        _process_reasons(
            label="resource",
            pids=resource_pids,
            expected_tokens=resource_process_tokens,
            probe=process_probe,
        )
        if resource_pids
        else ["resource_pid_unproven"]
    )

    candidate_pids, candidate_pid_reasons = _candidate_pids(
        candidate_root,
        candidate_documents,
    )
    if candidate_pid_file.is_file():
        candidate_pid, candidate_pid_error = _pid_from_file(candidate_pid_file)
        if candidate_pid_error:
            candidate_pid_reasons.append(candidate_pid_error)
        elif candidate_pid is not None:
            candidate_pids.append(candidate_pid)
    reasons.extend(candidate_pid_reasons)
    reasons.extend(
        _process_reasons(
            label="candidate",
            pids=candidate_pids,
            expected_tokens=candidate_process_tokens + (str(candidate_root),),
            probe=process_probe,
        )
        if candidate_pids
        else ["candidate_pid_unproven"]
    )

    reasons.extend(_check_lock(source_root / "collector.lock", "source"))

    unique_reasons = tuple(dict.fromkeys(reasons))
    return ShutdownReadiness(
        decision="SAFE_TO_SHUT_DOWN" if not unique_reasons else "NOT_SAFE_TO_SHUT_DOWN",
        reasons=unique_reasons,
    )


__all__ = [
    "CANDIDATE_REQUIRED_FILES",
    "ProcessInspection",
    "RESOURCE_REQUIRED_FILES",
    "SOURCE_REQUIRED_FILES",
    "ShutdownReadiness",
    "TERMINAL_STATES",
    "evaluate_shutdown_readiness",
]
