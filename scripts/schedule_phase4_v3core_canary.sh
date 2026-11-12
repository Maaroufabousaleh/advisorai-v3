#!/usr/bin/env bash
# Run the two bounded-canary read-only checkpoints without Codex polling.
#
# This process never starts, stops, or repairs a canary worker.  The watcher
# remains the machine-level monitor; this script only waits for fixed UTC
# instants, invokes the canonical one-shot watcher, and invokes the canonical
# terminal audit after both canary roots are terminal.

set -u -o pipefail

usage() {
    cat >&2 <<'EOF'
usage: schedule_phase4_v3core_canary.sh \
  --repository-root PATH --run-root PATH --monitor-root PATH \
  --preregistration PATH --preregistration-sha256 SHA256 \
  --phase3-gate-sha256 SHA256 --python PATH \
  --checkpoint-at ISO-8601Z --terminal-check-at ISO-8601Z
EOF
}

REPOSITORY_ROOT=
RUN_ROOT=
MONITOR_ROOT=
PREREGISTRATION=
PREREGISTRATION_SHA256=
PHASE3_GATE_SHA256=
PYTHON=
CHECKPOINT_AT=
TERMINAL_CHECK_AT=
RECHECK_SLEEP_SECONDS=300

while [ "$#" -gt 0 ]; do
    case "$1" in
        --repository-root) REPOSITORY_ROOT=$2; shift 2 ;;
        --run-root) RUN_ROOT=$2; shift 2 ;;
        --monitor-root) MONITOR_ROOT=$2; shift 2 ;;
        --preregistration) PREREGISTRATION=$2; shift 2 ;;
        --preregistration-sha256) PREREGISTRATION_SHA256=$2; shift 2 ;;
        --phase3-gate-sha256) PHASE3_GATE_SHA256=$2; shift 2 ;;
        --python) PYTHON=$2; shift 2 ;;
        --checkpoint-at) CHECKPOINT_AT=$2; shift 2 ;;
        --terminal-check-at) TERMINAL_CHECK_AT=$2; shift 2 ;;
        --recheck-sleep-seconds) RECHECK_SLEEP_SECONDS=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
done

for required in REPOSITORY_ROOT RUN_ROOT MONITOR_ROOT PREREGISTRATION \
    PREREGISTRATION_SHA256 PHASE3_GATE_SHA256 PYTHON CHECKPOINT_AT TERMINAL_CHECK_AT; do
    if [ -z "${!required}" ]; then
        printf 'missing required argument: %s\n' "$required" >&2
        usage
        exit 2
    fi
done

SOURCE_ROOT="$RUN_ROOT/source"
CANDIDATE_ROOT="$RUN_ROOT/candidate"
WATCHDOG_ROOT="$RUN_ROOT/watchdog"
WATCHDOG_SCRIPT="$REPOSITORY_ROOT/scripts/watch_phase4_v3core_canary.py"
AUDIT_SCRIPT="$REPOSITORY_ROOT/scripts/audit_phase4_v3core_canary.py"
EVENT_LOG="$MONITOR_ROOT/scheduler-events.log"

mkdir -p "$MONITOR_ROOT"

log_event() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" "$*" >> "$EVENT_LOG"
}

wait_until_utc() {
    target_iso=$1
    target_ns=$(date -u -d "$target_iso" +%s%N) || return 1
    while :; do
        now_ns=$(date -u +%s%N)
        if [ "$now_ns" -ge "$target_ns" ]; then
            return 0
        fi
        remaining_ns=$((target_ns - now_ns))
        if [ "$remaining_ns" -gt 60000000000 ]; then
            sleep 60
        else
            whole_seconds=$((remaining_ns / 1000000000))
            fractional_ns=$((remaining_ns % 1000000000))
            if [ "$whole_seconds" -eq 0 ] && [ "$fractional_ns" -lt 100000000 ]; then
                sleep 0.1
            else
                sleep "$(printf '%d.%09d' "$whole_seconds" "$fractional_ns")"
            fi
        fi
    done
}

run_health_once() {
    label=$1
    output_root="$MONITOR_ROOT/$label"
    mkdir -p "$output_root"
    if [ -e "$output_root/status.json" ]; then
        printf 'refusing stale monitoring output: %s\n' "$output_root/status.json" >&2
        return 1
    fi
    "$PYTHON" "$WATCHDOG_SCRIPT" \
        --preregistration "$PREREGISTRATION" \
        --preregistration-sha256 "$PREREGISTRATION_SHA256" \
        --source-root "$SOURCE_ROOT" \
        --candidate-root "$CANDIDATE_ROOT" \
        --history-root "$WATCHDOG_ROOT" \
        --output-root "$output_root" \
        --once > "$output_root/stdout.log" 2> "$output_root/stderr.log"
    command_status=$?
    printf '%s\n' "$command_status" > "$output_root/exit-code"
    if [ ! -s "$output_root/status.json" ]; then
        printf 'canonical watcher did not produce authoritative status.json\n' \
            > "$output_root/status-error"
        return 1
    fi
    if ! jq -e '
        type == "object"
        and (.decision | type == "string")
        and (.source_state | type == "string")
        and (.candidate_state | type == "string")
    ' "$output_root/status.json" >/dev/null 2>&1; then
        printf 'canonical watcher status.json is missing required fields or is malformed\n' \
            > "$output_root/status-error"
        return 1
    fi
    return 0
}

status_value() {
    output_root=$1
    field=$2
    jq -er ".${field}" "$output_root/status.json"
}

run_terminal_audit() {
    output_root="$MONITOR_ROOT/terminal-audit"
    mkdir -p "$output_root"
    if [ -e "$output_root/canary-terminal-audit.json" ]; then
        printf 'refusing conflicting terminal audit output\n' >&2
        return 1
    fi
    "$PYTHON" "$AUDIT_SCRIPT" \
        --preregistration "$PREREGISTRATION" \
        --preregistration-sha256 "$PREREGISTRATION_SHA256" \
        --source-root "$SOURCE_ROOT" \
        --candidate-root "$CANDIDATE_ROOT" \
        --watchdog-root "$WATCHDOG_ROOT" \
        --output-root "$output_root" \
        --phase3-gate-sha256 "$PHASE3_GATE_SHA256" \
        --repository-root "$REPOSITORY_ROOT" \
        > "$output_root/stdout.log" 2> "$output_root/stderr.log"
    command_status=$?
    printf '%s\n' "$command_status" > "$output_root/exit-code"
    log_event "TERMINAL_AUDIT_EXIT=$command_status OUTPUT=$output_root"
    return "$command_status"
}

log_event "WAITING_FOR_CHECKPOINT=$CHECKPOINT_AT"
if ! wait_until_utc "$CHECKPOINT_AT"; then
    log_event "SCHEDULER_FAILED_CLOCK_PARSE_CHECKPOINT=$CHECKPOINT_AT"
    exit 1
fi
log_event "CHECKPOINT_START=$CHECKPOINT_AT"
if run_health_once checkpoint-1; then
    if checkpoint_decision=$(status_value "$MONITOR_ROOT/checkpoint-1" decision); then
        log_event "CHECKPOINT_DECISION=$checkpoint_decision"
    else
        log_event "CHECKPOINT_STATUS_INVALID"
    fi
else
    log_event "CHECKPOINT_STATUS_INVALID"
fi

log_event "WAITING_FOR_TERMINAL_CHECK=$TERMINAL_CHECK_AT"
if ! wait_until_utc "$TERMINAL_CHECK_AT"; then
    log_event "SCHEDULER_FAILED_CLOCK_PARSE_TERMINAL=$TERMINAL_CHECK_AT"
    exit 1
fi

terminal_output=terminal-check-0905
log_event "TERMINAL_CHECK_START=$TERMINAL_CHECK_AT"
if ! run_health_once "$terminal_output"; then
    log_event "TERMINAL_CHECK_STATUS_INVALID"
    exit 1
fi

if ! source_state=$(status_value "$MONITOR_ROOT/$terminal_output" source_state); then
    log_event "TERMINAL_CHECK_STATUS_INVALID"
    exit 1
fi
if ! candidate_state=$(status_value "$MONITOR_ROOT/$terminal_output" candidate_state); then
    log_event "TERMINAL_CHECK_STATUS_INVALID"
    exit 1
fi
if [ "$source_state" != running ] && [ "$candidate_state" != running ]; then
    log_event "TERMINAL_STATE_CONFIRMED SOURCE=$source_state CANDIDATE=$candidate_state"
    if ! run_terminal_audit; then
        log_event "TERMINAL_AUDIT_FAILED"
        exit 1
    fi
else
    recheck=1
    while [ "$recheck" -le 6 ]; do
        sleep "$RECHECK_SLEEP_SECONDS"
        label="terminal-recheck-$recheck"
        if ! run_health_once "$label"; then
            log_event "TERMINAL_RECHECK_STATUS_INVALID=$recheck"
            exit 1
        fi
        if ! source_state=$(status_value "$MONITOR_ROOT/$label" source_state); then
            log_event "TERMINAL_RECHECK_STATUS_INVALID=$recheck"
            exit 1
        fi
        if ! candidate_state=$(status_value "$MONITOR_ROOT/$label" candidate_state); then
            log_event "TERMINAL_RECHECK_STATUS_INVALID=$recheck"
            exit 1
        fi
        if [ "$source_state" != running ] && [ "$candidate_state" != running ]; then
            log_event "TERMINAL_STATE_CONFIRMED_RECHECK=$recheck SOURCE=$source_state CANDIDATE=$candidate_state"
            if ! run_terminal_audit; then
                log_event "TERMINAL_AUDIT_FAILED"
                exit 1
            fi
            log_event "SCHEDULER_COMPLETE"
            exit 0
        fi
        log_event "CANARY_NOT_TERMINAL_RECHECK=$recheck SOURCE=$source_state CANDIDATE=$candidate_state"
        recheck=$((recheck + 1))
    done
    printf '%s\n' 'CANARY_NOT_TERMINAL' > "$MONITOR_ROOT/CANARY_NOT_TERMINAL"
    log_event "CANARY_NOT_TERMINAL"
fi
log_event "SCHEDULER_COMPLETE"
