#!/usr/bin/env bash
set -euo pipefail

# UTC-only scheduler for the immutable long-run contract. It waits in the
# operating system and consumes no model interaction while sleeping. It reads
# authoritative status.json artifacts; checker stdout is never a decision
# surface.

usage() {
    echo "usage: $0 --preregistration PATH --preregistration-sha256 SHA --source-root PATH --candidate-root PATH --outcome-root PATH --coordinator-root PATH --watchdog-root PATH --audit-root PATH --scheduler-root PATH --repository-root PATH --admission PATH --qualification-evidence PATH"
}

preregistration=""
preregistration_sha256=""
source_root=""
candidate_root=""
outcome_root=""
coordinator_root=""
watchdog_root=""
audit_root=""
scheduler_root=""
repository_root=""
admission=""
qualification_evidence=""

while (($# > 0)); do
    case "$1" in
        --preregistration) preregistration=$2; shift 2 ;;
        --preregistration-sha256) preregistration_sha256=$2; shift 2 ;;
        --source-root) source_root=$2; shift 2 ;;
        --candidate-root) candidate_root=$2; shift 2 ;;
        --outcome-root) outcome_root=$2; shift 2 ;;
        --coordinator-root) coordinator_root=$2; shift 2 ;;
        --watchdog-root) watchdog_root=$2; shift 2 ;;
        --audit-root) audit_root=$2; shift 2 ;;
        --scheduler-root) scheduler_root=$2; shift 2 ;;
        --repository-root) repository_root=$2; shift 2 ;;
        --admission) admission=$2; shift 2 ;;
        --qualification-evidence) qualification_evidence=$2; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [[ -z "$preregistration" || -z "$preregistration_sha256" || -z "$source_root" || -z "$candidate_root" || -z "$outcome_root" || -z "$coordinator_root" || -z "$watchdog_root" || -z "$audit_root" || -z "$scheduler_root" || -z "$repository_root" || -z "$admission" || -z "$qualification_evidence" ]]; then
    usage >&2
    exit 2
fi

mkdir -p "$scheduler_root"

# A second scheduler would race the canonical status/audit surfaces and could
# make one cutoff appear to have two independent terminal decisions.  Keep the
# lock descriptor open for the entire process lifetime; flock is released by
# the OS on normal exit or an unexpected process death.
exec 9>"$scheduler_root/scheduler.lock"
if ! flock -n 9; then
    echo "another long-run scheduler owns this root" >&2
    exit 1
fi

preregistration_values_output=$(uv run python - "$preregistration" "$preregistration_sha256" <<'PY'
import sys
from pathlib import Path
from advisorai.phase4.v3core_longrun_runtime import load_long_run_preregistration

preregistration = load_long_run_preregistration(Path(sys.argv[1]), expected_sha256=sys.argv[2])
print(preregistration.first_mandatory_cutoff_at.timestamp())
for cutoff in preregistration.mandatory_cutoffs:
    print(cutoff.timestamp())
print(preregistration.terminal_deadline.timestamp())
print(preregistration.terminal_check_at.timestamp())
print(preregistration.prediction_lateness_seconds)
print(preregistration.case_accounting_grace_seconds)
PY
)
mapfile -t preregistration_values <<<"$preregistration_values_output"
if (( ${#preregistration_values[@]} != 85 )); then
    echo "long-run preregistration schedule has an unexpected field count" >&2
    exit 1
fi
for value in "${preregistration_values[@]}"; do
    if [[ ! "$value" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        echo "long-run preregistration schedule contains a non-numeric value" >&2
        exit 1
    fi
done

wait_until() {
    local target="$1"
    local now
    while true; do
        now=$(date -u +%s.%N)
        if awk "BEGIN { exit !($now >= $target) }"; then
            return 0
        fi
        sleep 1
    done
}

write_scheduler_state() {
    local state="$1"
    local detail="$2"
    uv run python - "$scheduler_root/status.json" "$state" "$detail" "$preregistration" "$preregistration_sha256" "$repository_root/scripts/schedule_phase4_v3core_longrun.sh" "$$" <<'PY'
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_longrun_runtime import (
    load_long_run_preregistration,
    process_command_identity,
    process_create_time,
)

destination = Path(sys.argv[1]).resolve()
temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
preregistration = load_long_run_preregistration(Path(sys.argv[4]), expected_sha256=sys.argv[5])
scheduler_pid = int(sys.argv[7])
try:
    import psutil
except ImportError as exc:
    raise SystemExit("scheduler process identity requires psutil") from exc
try:
    command = psutil.Process(scheduler_pid).cmdline()
except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
    raise SystemExit(f"scheduler process identity unavailable: {type(exc).__name__}") from exc
if not command or process_create_time(scheduler_pid) is None:
    raise SystemExit("scheduler process command is empty")
payload = {
    "schema": "advisorai.phase4.v3-core.long-run.scheduler.v1",
    "generation_id": preregistration.generation_id,
    "preregistration_sha256": sys.argv[5],
    "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
    "repository_commit": preregistration.repository_commit,
    "scheduler_pid": scheduler_pid,
    "process_create_time": process_create_time(scheduler_pid),
    "command": command,
    "command_identity": process_command_identity(command),
    "scheduler_code_sha256": sha256_file(Path(sys.argv[6]).resolve()),
    "state": sys.argv[2],
    "detail": sys.argv[3],
    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    "credentials_loaded": False,
    "order_writes_attempted": False,
    "execution_authority_present": False,
}
with temporary.open("w", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, destination)
PY
}

run_watchdog_once() {
    local checkpoint_epoch="$1"
    local require_heartbeat_window="${2:-true}"
    local require_terminal_marker="${3:-false}"
    set +e
uv run python - "$watchdog_root/status.json" "$coordinator_root/events.jsonl" "$checkpoint_epoch" "$require_heartbeat_window" "$require_terminal_marker" "$preregistration" "$preregistration_sha256" "$repository_root/scripts/watch_phase4_v3core_longrun.py" <<'PY'
import sys
import time
from pathlib import Path

from advisorai.phase4.v3core_longrun_runtime import (
    LongRunCoordinator,
    LongRunState,
    load_long_run_preregistration,
    process_identity_matches,
    read_json_stable,
)

status = read_json_stable(Path(sys.argv[1]))
preregistration = load_long_run_preregistration(Path(sys.argv[6]), expected_sha256=sys.argv[7])
coordinator = LongRunCoordinator(preregistration, Path(sys.argv[2]))
checkpoint = float(sys.argv[3])
heartbeat_required = sys.argv[4] == "true"
window_end = checkpoint + 120.0
while True:
    status = read_json_stable(Path(sys.argv[1]))
    coordinator = LongRunCoordinator(preregistration, Path(sys.argv[2]))
    if (
        status.get("generation_id") != preregistration.generation_id
        or status.get("preregistration_sha256") != sys.argv[7]
        or status.get("repository_commit") != preregistration.repository_commit
        or status.get("watchdog_code_sha256") != __import__("advisorai.phase4.v3core_canary", fromlist=["sha256_file"]).sha256_file(Path(sys.argv[8]).resolve())
        or status.get("credentials_loaded") is not False
        or status.get("order_writes_attempted") is not False
        or status.get("decision") != "LONG_RUN_HEALTHY"
        or status.get("scientific_state") == LongRunState.GENERATION_FATAL.value
        or status.get("fatal_history_count") != len(coordinator.fatal_events)
        or coordinator.scientific_state == LongRunState.GENERATION_FATAL
    ):
        raise SystemExit(1)
    if status.get("watchdog_terminal") is not True:
        watchdog_pid = status.get("watchdog_pid")
        watchdog_command = status.get("command")
        watchdog_identity = status.get("command_identity")
        if not (
            isinstance(watchdog_pid, int)
            and isinstance(watchdog_command, list)
            and isinstance(watchdog_identity, str)
            and isinstance(status.get("process_create_time"), (int, float))
            and process_identity_matches(
                watchdog_pid,
                watchdog_identity,
                [str(item) for item in watchdog_command],
                expected_process_create_time=float(status["process_create_time"]),
            )
        ):
            raise SystemExit(1)
    if sys.argv[5] == "true" and status.get("watchdog_terminal") is not True:
        raise SystemExit(1)
    if not heartbeat_required or any(
        event.event_type == "WATCHDOG_CHECK"
        and event.payload.get("decision") == "LONG_RUN_HEALTHY"
        and checkpoint <= event.observed_at.timestamp() <= window_end
        for event in coordinator.events
    ):
        raise SystemExit(0)
    remaining = window_end - time.time()
    if remaining <= 0:
        raise SystemExit(1)
    time.sleep(min(1.0, remaining))
PY
    local result=$?
    set -e
    return "$result"
}

latch_scheduler_fatal() {
    local detail="$1"
    uv run python - "$coordinator_root/events.jsonl" "$preregistration" "$preregistration_sha256" "$detail" <<'PY'
import sys
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_longrun_runtime import (
    LongRunCoordinator,
    LongRunIncident,
    load_long_run_preregistration,
)

coordinator = LongRunCoordinator(
    load_long_run_preregistration(Path(sys.argv[2]), expected_sha256=sys.argv[3]),
    Path(sys.argv[1]),
)
coordinator.fail(LongRunIncident.WATCHDOG_PROCESS_DEATH, at=datetime.now(UTC), detail=sys.argv[4])
coordinator.record_watchdog_check(
    decision="GENERATION_FATAL",
    reasons=(sys.argv[4],),
    at=datetime.now(UTC),
)
PY
}

first_cutoff="${preregistration_values[0]}"
write_scheduler_state "WAITING_FOR_FIRST_CUTOFF" "$first_cutoff"

for index in $(seq 1 80); do
    cutoff_epoch="${preregistration_values[$index]}"
    prediction_deadline_epoch=$(awk "BEGIN { print $cutoff_epoch + ${preregistration_values[83]} }")
    # The scheduler observes the checkpoint after the frozen prediction
    # deadline plus the small append-only accounting grace.  The grace gives
    # the live candidate time to record CASE_EXCLUDED; it never authorizes a
    # late prediction, shifts the cutoff, or extends the terminal deadline.
    checkpoint_epoch=$(awk "BEGIN { print $prediction_deadline_epoch + ${preregistration_values[84]} }")
    wait_until "$checkpoint_epoch"
    if ! run_watchdog_once "$prediction_deadline_epoch"; then
        latch_scheduler_fatal "watchdog checkpoint failed at cutoff ordinal $index"
        write_scheduler_state "GENERATION_FATAL" "watchdog decision is fatal at cutoff ordinal $index"
        exit 1
    fi
    write_scheduler_state "CHECKPOINT_COMPLETE" "cutoff ordinal $index"
done

terminal_deadline="${preregistration_values[81]}"
terminal_check="${preregistration_values[82]}"
wait_until "$terminal_check"

component_states_terminal() {
    uv run python - "$source_root/status.json" "$candidate_root/status.json" "$outcome_root/outcome-status.json" <<'PY'
import sys
from pathlib import Path

from advisorai.phase4.v3core_longrun_runtime import read_json_stable

terminal = {
    "DEADLINE_REACHED",
    "TERMINALIZING",
    "COMPLETED_PENDING_AUDIT",
    "AUDITED",
}
for name in sys.argv[1:]:
    status = read_json_stable(Path(name))
    if status.get("state") not in terminal:
        raise SystemExit(1)
PY
}

for attempt in $(seq 0 6); do
    if component_states_terminal && run_watchdog_once "$(date -u +%s.%N)" false true; then break; fi
    if ((attempt == 6)); then
        write_scheduler_state "CANARY_NOT_TERMINAL" "components remained nonterminal after bounded checks"
        exit 1
    fi
    sleep 300
    if ! run_watchdog_once "$(date -u +%s.%N)" false; then
        latch_scheduler_fatal "watchdog decision is fatal during terminal wait"
        write_scheduler_state "GENERATION_FATAL" "watchdog decision is fatal during terminal wait"
        exit 1
    fi
done

write_scheduler_state "TERMINAL_AUDIT_RUNNING" "$terminal_deadline"
set +e
uv run python "$repository_root/scripts/audit_phase4_v3core_longrun.py" \
    --repository-root "$repository_root" \
    --preregistration "$preregistration" \
    --preregistration-sha256 "$preregistration_sha256" \
    --source-root "$source_root" \
    --candidate-root "$candidate_root" \
    --coordinator-root "$coordinator_root" \
    --watchdog-root "$watchdog_root" \
    --scheduler-root "$scheduler_root" \
    --audit-root "$audit_root" \
    --terminal-observed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    >"$scheduler_root/audit-command-output.json"
audit_exit=$?
set -e
if [[ ! -s "$audit_root/terminal-report.json" ]]; then
    write_scheduler_state "AUDIT_FAILED" "canonical terminal audit did not publish a report (exit $audit_exit)"
    exit 1
fi
write_scheduler_state "AUDIT_COMPLETE" "$audit_root"
