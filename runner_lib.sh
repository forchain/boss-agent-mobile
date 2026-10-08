#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Dedicated Runner Script lifecycle library
# ==============================================================================
# Sourced by worker.sh, dashboard.sh, pocketbase.sh, appium.sh and emulator.sh. Each of
# them used to hand-roll the same process lifecycle — roughly 250-300 duplicated
# lines — and the copies drifted into a live hazard: dashboard.sh probed port ownership
# LISTEN-only, while pocketbase.sh and appium.sh resolved "who owns the port" with a
# bare `lsof -ti` and then signalled that PID. A dashboard merely *connected* to
# PocketBase (holding an SSE stream) could therefore be killed and adopted as the
# service's own process.
#
# This file owns the primitives; the scripts own their preflight checks and command
# dispatch. Nothing here defines a service, a port or a binary.
#
# Sourced, never executed: `source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"`.
# ==============================================================================

# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #

# Whether a PID names a running, non-zombie process.
#
# A defunct process has already terminated and only awaits reaping: it is neither a
# running service nor a reason to burn a shutdown timeout waiting for it.
runner_process_alive() {
    local PID="${1:-}"
    [[ -n "${PID}" ]] || return 1
    ps -p "${PID}" >/dev/null 2>&1 || return 1
    local STATE
    STATE="$(ps -p "${PID}" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    [[ "${STATE}" == Z* ]] && return 1
    return 0
}

runner_process_gone() {
    ! runner_process_alive "${1:-}"
}

# The working directory of a process, or empty.
runner_process_cwd() {
    local PID="${1:-}"
    [[ -n "${PID}" ]] || return 0
    if [[ -d "/proc/${PID}/cwd" || -L "/proc/${PID}/cwd" ]]; then
        readlink "/proc/${PID}/cwd" 2>/dev/null || true
        return 0
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -a -p "${PID}" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true
    fi
}

# Whether a process's working directory is still present on the filesystem.
#
# When a git worktree or temporary directory is deleted while a dev server is running,
# the process stays alive in memory holding the port, but cannot resolve source files
# on disk (e.g. Vite dynamic SSR module imports fail with ERR_LOAD_URL).
runner_process_cwd_alive() {
    local PID="${1:-}"
    local CWD
    CWD="$(runner_process_cwd "${PID}")"
    if [[ -n "${CWD}" && ! -d "${CWD}" ]]; then
        return 1
    fi
    return 0
}

# --------------------------------------------------------------------------- #
# Deadline-aware waiting
# --------------------------------------------------------------------------- #

# Run a predicate until it succeeds or the budget expires, re-checking once at the
# deadline so a state change during the final interval still counts.
runner_wait_until() {
    local TIMEOUT_SEC="${1:-1}"
    shift
    local attempts
    attempts="$(awk -v t="${TIMEOUT_SEC}" 'BEGIN { printf "%d", (t * 10 < 1 ? 1 : t * 10) }')"
    local i
    for ((i = 0; i < attempts; i++)); do
        if "$@"; then
            return 0
        fi
        sleep 0.1
    done
    "$@"
}

# --------------------------------------------------------------------------- #
# Port ownership
# --------------------------------------------------------------------------- #

# The PID *listening* on a TCP port, or empty.
#
# LISTEN-only is the whole point: a browser, a curl, or a test client merely
# *connected* to the port must never be mistaken for the service and must never be
# signalled. Every port probe in every runner goes through this function.
runner_port_listener_pid() {
    local PORT="${1:-}"
    [[ -n "${PORT}" ]] || return 0
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
    fi
}

runner_port_in_use() {
    [[ -n "$(runner_port_listener_pid "${1:-}")" ]]
}

runner_port_released() {
    ! runner_port_in_use "${1:-}"
}

# --------------------------------------------------------------------------- #
# Pidfiles
# --------------------------------------------------------------------------- #

runner_pidfile_read() {
    local PID_FILE="${1:-}"
    [[ -f "${PID_FILE}" ]] || return 0
    cat "${PID_FILE}" 2>/dev/null || true
}

# The PID from `PID_FILE` when it names a live process, else the LISTEN owner of
# `PORT` (adopting it into the pidfile), else empty.
#
# The order matters: the pidfile is the service we started, and a stray listener on
# the port is a *candidate* owner, not a fact — which is why the port fallback goes
# through the LISTEN-only probe rather than a bare `lsof -ti`.
runner_resolve_pid() {
    local PID_FILE="${1:-}"
    local PORT="${2:-}"
    local PID
    PID="$(runner_pidfile_read "${PID_FILE}")"
    if runner_process_alive "${PID}"; then
        echo "${PID}"
        return 0
    fi
    local PORT_PID
    PORT_PID="$(runner_port_listener_pid "${PORT}")"
    if [[ -n "${PORT_PID}" ]]; then
        runner_pidfile_write "${PID_FILE}" "${PORT_PID}"
        echo "${PORT_PID}"
        return 0
    fi
    echo ""
}

runner_pidfile_write() {
    local PID_FILE="${1:-}"
    local PID="${2:-}"
    [[ -n "${PID_FILE}" ]] || return 0
    printf '%s\n' "${PID}" > "${PID_FILE}"
}

runner_pidfile_clear() {
    rm -f "${1:-}" 2>/dev/null || true
}

# --------------------------------------------------------------------------- #
# Graceful stop
# --------------------------------------------------------------------------- #

# Stop a PID per the Graceful Shutdown Protocol: SIGTERM, a budgeted wait for it to
# exit on its own (so in-flight tasks can release leases and device sessions), then
# SIGKILL as a last resort.
#
# Prints the escalation it took; returns 0 when the process is gone.
#
# The return contract is deliberately "gone", not "cooperative": a process that had to be
# killed *is* gone, and reporting that as a stop failure would abort every runner that
# runs under `set -e` at the worst possible moment. The escalation is therefore reported
# on a second channel instead — the same line goes to stdout always, and into `LOG_FILE`
# when the caller names the service's log, so "stopped cooperatively" and "had to be
# SIGKILLed" are distinguishable in the log the E2E Pre-Test Teardown Gate greps, not just
# in the operator's terminal.
runner_graceful_stop() {
    local PID="${1:-}"
    local TIMEOUT_SEC="${2:-10}"
    local LABEL="${3:-process}"
    local LOG_FILE="${4:-}"

    if ! runner_process_alive "${PID}"; then
        return 0
    fi

    kill "${PID}" 2>/dev/null || true
    if runner_wait_until "${TIMEOUT_SEC}" runner_process_gone "${PID}"; then
        return 0
    fi

    local ESCALATION="did not shut down gracefully within ${TIMEOUT_SEC}s; sending SIGKILL to PID ${PID}."
    echo "⚠️ ${LABEL} ${ESCALATION}"
    if [[ -n "${LOG_FILE}" ]]; then
        runner_log_event "${LOG_FILE}" "⚠️ [${LABEL}] Graceful shutdown timed out: ${ESCALATION}"
    fi
    kill -9 "${PID}" 2>/dev/null || true
    runner_wait_until 2 runner_process_gone "${PID}" || true
    return 0
}

# Stop whoever is *listening* on a port. Never signals a merely-connected client:
# the PID comes from the LISTEN-only probe or the call is a no-op.
#
# Echoes the PID it stopped, or nothing when the port had no listener.
runner_stop_port_listener() {
    local PORT="${1:-}"
    local TIMEOUT_SEC="${2:-10}"
    local LABEL="${3:-listener}"
    local PID
    PID="$(runner_port_listener_pid "${PORT}")"
    if [[ -z "${PID}" ]]; then
        return 0
    fi
    echo "${PID}"
    runner_graceful_stop "${PID}" "${TIMEOUT_SEC}" "${LABEL}" >&2
}

# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #

# Append one timestamped line to a service log.
runner_log_event() {
    local LOG_FILE="${1:-}"
    local MESSAGE="${2:-}"
    [[ -n "${LOG_FILE}" ]] || return 0
    mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${MESSAGE}" >> "${LOG_FILE}"
}

# --------------------------------------------------------------------------- #
# Attached log streams
# --------------------------------------------------------------------------- #
# The Graceful Shutdown Protocol tells a supervisor how to *stop* a service. This section
# is the other half: how a supervisor watching a service learns that something else already
# stopped it.
#
# `tail -f` alone cannot do that. It follows a *file*, and a service stopped from another
# terminal neither truncates, rotates, nor removes its log — so an attached terminal kept a
# live cursor on an inert file and the operator could not tell a busy Automation Worker from
# one killed thirty seconds ago. The watch below pairs the stream with a liveness check on
# the service's own PID, which is the only party that actually knows.

#: How often the watch re-asks whether the service is still there.
#:
#: Deliberately a poll rather than an event: `tail --pid` is GNU-only and absent from the
#: BSD `tail` this repo runs on (and CI runs `ubuntu-latest` while development is macOS),
#: and bash 3.2 has no `wait -n` to await either the stream or the service. A bounded
#: polling loop over the primitives above is the one shape that works on both.
RUNNER_WATCH_POLL_SEC="${RUNNER_WATCH_POLL_SEC:-0.2}"

#: The `tail` child currently following a service log, if any.
#:
#: A global rather than a local because the INT/TERM traps have to reach it: bash does not
#: reap background children when a script exits, so a detached watcher would otherwise leave
#: a `tail -f` behind holding the terminal's stdout open.
RUNNER_WATCH_TAIL_PID=""

#: The wording an attached log stream announces when the service it follows is gone.
#:
#: Owned here for the same reason the lifecycle events are: four Dedicated Runner Scripts
#: attach to their own service through `runner_attached_logs` — `appium.sh`, `dashboard.sh`,
#: `pocketbase.sh` and `worker.sh` — and "which service stopped" is a contract with the
#: operator that must not drift between copies. `emulator.sh` is the fifth Dedicated
#: Runner Script and deliberately not in that count: a Virtual Device Session owns no
#: runner-owned process to watch, so it follows its own `attach_logs`. The PID is
#: substituted by the seam below.
RUNNER_ATTACHED_STOP_EVENT="守护进程 (PID: {pid}) 已停止"

#: ... and the wording for the one case that is provably not a plain stop.
#:
#: A log file cannot say whether its writer died or was replaced, so the watcher reaches for
#: the one piece of evidence that can: the service's own pidfile naming a *different* live
#: process. When it does, the operator's next move differs — after a stop they start the
#: service, after a restart they decide whether to reattach — and silently staying on the
#: same file would leave them attached to a process they can no longer Ctrl+C, believing
#: they are watching the current one. Ticket #425 asks for exactly this.
RUNNER_ATTACHED_RESTART_EVENT="守护进程 (PID: {pid}) 已停止并重启 (新 PID: {new_pid})"

# Whether a PID string is one this watcher could actually observe.
#
# `runner_process_alive` requires a number, so anything else — the empty string a runner
# passes when it resolved nothing, or the literal `unknown` that `appium.sh`, `pocketbase.sh`
# and `dashboard.sh` substitute for it — makes `ps -p` fail, which `runner_process_alive`
# reports as GONE. A watcher asking "is this gone?" about `unknown` would announce the stop
# of a service it has never seen run, on the very route a runner takes when it can see its
# service is up but could not resolve the PID owning it.
#
# An unwatchable PID is therefore *no information at all*, never a death: the watch degrades
# to the plain `tail -f` it replaced and ends only with its own stream. Invariant for a
# function this cheap to call: the only acceptable answer is yes or no, and anything the
# kernel cannot be asked about is a no.
runner_pid_watchable() {
    local PID="${1:-}"
    [[ -n "${PID}" ]] || return 1
    [[ "${PID}" != *[!0-9]* ]] || return 1
    return 0
}

# `runner_log_attached_stop LABEL PID`
#
# Printed to the operator's terminal, not to the service's log: the service is already gone,
# and whoever stopped it has already recorded the stop in that log via
# `runner_log_stop_complete`. This line exists to answer the question the log cannot —
# *why did my terminal just go quiet?* — at the moment the stream ends.
runner_log_attached_stop() {
    local LABEL="${1:-Service}"
    local PID="${2:-}"
    echo "🛑 [${LABEL}] ${RUNNER_ATTACHED_STOP_EVENT//\{pid\}/$PID}"
}

# `runner_log_attached_restart LABEL PID NEW_PID`
#
# The stop notice's sibling, for the case the watcher can substantiate: the PID it followed
# is gone, and the service's pidfile now names a different live process. Same channel as
# `runner_log_attached_stop` — the terminal, because whoever performed the restart is the
# one writing the log.
runner_log_attached_restart() {
    local LABEL="${1:-Service}"
    local PID="${2:-}"
    local NEW_PID="${3:-}"
    # `{new_pid}` is substituted first on purpose: it contains the letters `pid}`, but not
    # the placeholder `{pid}`, so the order is safe either way — and doing it in two steps
    # keeps this readable as bash 3.2, where the chaining form is easy to get wrong.
    local MESSAGE="${RUNNER_ATTACHED_RESTART_EVENT//\{new_pid\}/$NEW_PID}"
    echo "🛑 [${LABEL}] ${MESSAGE//\{pid\}/$PID}"
}

# The live PID `PID_FILE` names now, when it is not the PID this watch followed.
#
# Echoes the replacement PID, or nothing when the evidence does not support a restart: no
# pidfile, an unreadable one, one still naming the dead process, or one naming something
# that is not running. That last one matters most — a pidfile naming a *live* process other
# than the watched one is the only shape a restart from another terminal leaves behind, and
# claiming a restart from anything weaker would be exactly the dishonesty this notice
# exists to avoid. Always returns 0: the answer is the output, not the status.
runner_restart_replacement_pid() {
    local PID="${1:-}"
    local PID_FILE="${2:-}"
    if [[ -n "${PID_FILE}" && -n "${PID}" ]]; then
        local NEW_PID
        NEW_PID="$(runner_pidfile_read "${PID_FILE}")"
        if [[ "${NEW_PID}" != "${PID}" ]] \
            && runner_pid_watchable "${NEW_PID}" \
            && runner_process_alive "${NEW_PID}"; then
            echo "${NEW_PID}"
        fi
    fi
    return 0
}

# Stop following a log stream — and only that.
#
# Deliberately narrower than `runner_graceful_stop`: a watcher told to stop watching has no
# business signalling the service it was watching. Detaching is not stopping, and the
# distinction is load-bearing: the operator detaches with Ctrl+C precisely when they intend
# to go on using the running daemon. Only this watcher's own `tail` is signalled here.
runner_watch_detach() {
    local TAIL_PID="${RUNNER_WATCH_TAIL_PID:-}"
    RUNNER_WATCH_TAIL_PID=""
    [[ -n "${TAIL_PID}" ]] || return 0
    kill "${TAIL_PID}" 2>/dev/null || true
    wait "${TAIL_PID}" 2>/dev/null || true
    return 0
}

# Follow LOG_FILE until the watched service is gone, then announce the stop and return.
#
# `runner_watch_log_stream PID LOG_FILE LABEL [INITIAL_LINES] [PID_FILE]`
#
# The `tail` runs in the background as a child of this shell and is polled alongside the
# service:
#
#   * the service disappearing ends the watch — that is the whole point of it;
#   * the `tail` disappearing ends the watch too, so a stream that dies on its own (log
#     rotated away, `tail` killed by something else) cannot leave the loop spinning
#     forever on a child that will never produce another line;
#   * Ctrl+C ends the watch through `runner_watch_detach`, which signals the `tail` and
#     nothing else.
#
# `PID_FILE` is what turns a plain stop into a stop-and-restart: without it the watcher has
# no evidence of a replacement and says only what it saw. With it, a pidfile naming a
# different live process is announced as the restart it is.
#
# Backgrounding the `tail` is not cosmetic. Bash does not run a trap handler while a
# foreground child is still running, so a watcher written as a bare `tail -f` cannot be
# detached from at all: the Ctrl+C is recorded and then ignored until the stream ends by
# itself, which for a log stream is never.
runner_watch_log_stream() {
    local PID="${1:-}"
    local LOG_FILE="${2:-}"
    local LABEL="${3:-service}"
    local INITIAL_LINES="${4:-30}"
    local PID_FILE="${5:-}"
    local NEW_PID=""

    touch "${LOG_FILE}"
    tail -n "${INITIAL_LINES}" -f "${LOG_FILE}" &
    RUNNER_WATCH_TAIL_PID=$!

    # Decided once, before the loop, so the watch's meaning cannot shift mid-stream: a PID
    # that cannot be watched is not a service that has stopped. See `runner_pid_watchable`.
    local WATCHABLE=0
    if runner_pid_watchable "${PID}"; then
        WATCHABLE=1
    fi

    while true; do
        if [[ ${WATCHABLE} -eq 1 ]] && runner_process_gone "${PID}"; then
            runner_watch_detach
            NEW_PID="$(runner_restart_replacement_pid "${PID}" "${PID_FILE}")" || true
            if [[ -n "${NEW_PID}" ]]; then
                runner_log_attached_restart "${LABEL}" "${PID}" "${NEW_PID}"
            else
                runner_log_attached_stop "${LABEL}" "${PID}"
            fi
            return 0
        fi
        if runner_process_gone "${RUNNER_WATCH_TAIL_PID}"; then
            # The stream ended on its own. `runner_process_alive` already reads a defunct
            # PID as gone, so an unreaped `tail` ends this branch instead of hanging it.
            return 0
        fi
        sleep "${RUNNER_WATCH_POLL_SEC}"
    done
}

# Print the tail of a service log and follow it, without killing the daemon on
# detach. `runner_attached_logs PID LOG_FILE LABEL [ENDPOINT] [PID_FILE]`.
#
# Returns once the watched service is gone, so a daemon stopped from another terminal
# unblocks the terminal instead of leaving it on a live cursor over an inert file.
runner_attached_logs() {
    local PID="${1:-}"
    local LOG_FILE="${2:-}"
    local LABEL="${3:-service}"
    local ENDPOINT="${4:-}"
    local PID_FILE="${5:-}"

    echo "ℹ️ ${LABEL} is already running (PID: ${PID})${ENDPOINT:+ at ${ENDPOINT}}"
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    # Detaching must not signal the background service.
    trap 'runner_watch_detach; echo -e "\n👋 Detached from '"${LABEL}"' logs ('"${LABEL}"' is still running in background)."; exit 0' INT TERM

    runner_watch_log_stream "${PID}" "${LOG_FILE}" "${LABEL}" 30 "${PID_FILE}"
}

# --------------------------------------------------------------------------- #
# Runtime state directory
# --------------------------------------------------------------------------- #

# The git common root: the main checkout, shared by every worktree.
runner_common_root() {
    local ROOT_DIR="${1:-}"
    local GIT_COMMON_DIR
    GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "${GIT_COMMON_DIR}" ]]; then
        (cd "${GIT_COMMON_DIR}/.." && pwd)
    else
        echo "${ROOT_DIR}"
    fi
}

# The runtime state directory for one class of service.
#
# The split is a policy, not an accident of symlinks:
#   * Infrastructure Services (PocketBase data, the emulator/AVD, Appium) keep state
#     at the git common root, because exactly one of each exists per machine and
#     every worktree shares it;
#   * Application Services (worker, web dashboard) keep state per worktree, because
#     each worktree runs its own.
#
# `init_worktree.sh` symlinks the worktree's `.boss_agent` at the common root as a
# convenience for the shared parts; this function does not depend on that symlink
# existing, so an un-symlinked worktree still behaves correctly instead of
# half-splitting.
runner_runtime_dir() {
    local CLASS="${1:-app}"
    local ROOT_DIR="${2:-$(pwd)}"
    local DIR
    case "${CLASS}" in
        infra) DIR="$(runner_common_root "${ROOT_DIR}")/.boss_agent" ;;
        app) DIR="${ROOT_DIR}/.boss_agent" ;;
        *)
            echo "runner_runtime_dir: unknown service class '${CLASS}' (expected 'infra' or 'app')" >&2
            return 2
            ;;
    esac
    mkdir -p "${DIR}"
    echo "${DIR}"
}

# --------------------------------------------------------------------------- #
# Shutdown-acknowledgment contract
# --------------------------------------------------------------------------- #
# Supervisors and test harnesses match on these lines to prove a service accepted a
# termination signal and actually shut down. They are a contract across three
# producers and one consumer, so they are defined once, here, and referenced by the
# runners; boss_agent.services.teardown mirrors the worker's half.

#: The Web Dashboard echoes this when its runner accepts a stop command.
RUNNER_WEB_SHUTDOWN_ACK="Received stop command"

# Emit the dashboard's shutdown acknowledgment to the service log.
runner_ack_shutdown() {
    local LOG_FILE="${1:-}"
    local LABEL="${2:-Service}"
    local PID="${3:-}"
    local EXTRA="${4:-}"
    runner_log_event "${LOG_FILE}" "🛑 [${LABEL}] ${RUNNER_WEB_SHUTDOWN_ACK}, shutting down ${EXTRA}... (PID: ${PID})"
}

# --------------------------------------------------------------------------- #
# Lifecycle-event contract
# --------------------------------------------------------------------------- #
# The Graceful Shutdown Protocol has one more obligation than the acknowledgment above:
# a service confirms *completion* in its own log stream, so a supervisor can tell "accepted
# the signal" apart from "actually stopped" without guessing. `dashboard.sh` honoured that
# end to end; the other four Dedicated Runner Scripts narrated their stop to the operator's
# terminal and left their own log files silent — and the log, not the terminal, is what the
# E2E Pre-Test Teardown Gate reads.
#
# So the wording lives here, once, in the same shape as the shutdown acknowledgment: the
# runners reference these four functions and never spell an event of their own.
#
# Every function here is a pure writer: no process is touched and no decision is made, so a
# runner stays in control of *when* a service really was found. That is deliberate. A stop
# that finds nothing running must write nothing at all — an idle stop is a cheap no-op, and
# a runner that logged unconditionally would create a service's log file on every idle
# `./run.sh stop` and fill it with phantom stops.

#: A runner records this once it has found a live service and is about to signal it.
RUNNER_LIFECYCLE_STOP_EVENT="Stop requested"

#: ... and this once the service is confirmed gone.
RUNNER_LIFECYCLE_STOP_COMPLETE="Stop completed"

#: ... and these at the top of a restart, and once the replacement service is up.
RUNNER_LIFECYCLE_RESTART_EVENT="Restart requested"
RUNNER_LIFECYCLE_RESTART_COMPLETE="Restart completed"

# The stop-confirmation. `HANDLE` is whatever identifies the service to this runner — a PID
# for the process-backed runners, an ADB transport for the Virtual Device Session, which has
# no runner-owned process to signal. It is deliberately not called a PID: a runner must not
# invent one it does not have.
#
# `runner_log_stop_request LOG_FILE LABEL HANDLE DETAIL`
runner_log_stop_request() {
    local LOG_FILE="${1:-}"
    local LABEL="${2:-Service}"
    local HANDLE="${3:-}"
    local DETAIL="${4:-}"
    runner_log_event "${LOG_FILE}" "🛑 [${LABEL}] ${RUNNER_LIFECYCLE_STOP_EVENT}: ${DETAIL} (handle: ${HANDLE})"
}

# The completion event, logged only once the service is actually gone.
#
# `runner_log_stop_complete LOG_FILE LABEL DETAIL`
runner_log_stop_complete() {
    local LOG_FILE="${1:-}"
    local LABEL="${2:-Service}"
    local DETAIL="${3:-}"
    runner_log_event "${LOG_FILE}" "✅ [${LABEL}] ${RUNNER_LIFECYCLE_STOP_COMPLETE}: ${DETAIL}"
}

# `runner_log_restart_request LOG_FILE LABEL DETAIL`
runner_log_restart_request() {
    local LOG_FILE="${1:-}"
    local LABEL="${2:-Service}"
    local DETAIL="${3:-}"
    runner_log_event "${LOG_FILE}" "🔄 [${LABEL}] ${RUNNER_LIFECYCLE_RESTART_EVENT}: ${DETAIL}"
}

# `runner_log_restart_complete LOG_FILE LABEL DETAIL`
runner_log_restart_complete() {
    local LOG_FILE="${1:-}"
    local LABEL="${2:-Service}"
    local DETAIL="${3:-}"
    runner_log_event "${LOG_FILE}" "✅ [${LABEL}] ${RUNNER_LIFECYCLE_RESTART_COMPLETE}: ${DETAIL}"
}

# --------------------------------------------------------------------------- #
# Configuration reads
# --------------------------------------------------------------------------- #

# Resolve one Configuration Realm value for a runner script.
#
# Prefers the settings CLI (`scripts/resolve_config.py`), which knows the precedence
# chain, the environment overrides and the defaults table. Falls back to the YAML
# extraction this function replaces when the CLI is unavailable — a copied script root
# (the test harness), or no Python on PATH — so a runner never depends on Python to
# start.
#
# Either way the `grep|awk|tr` pipeline exists exactly once, here, instead of being
# copy-pasted across every runner that needs a URL, a data directory or an AVD name.
#
# `ALIASES` is a `|`-separated list of legacy key spellings, e.g. `pb_url`.
runner_config_value() {
    local KEY="$1"
    local FALLBACK="${2:-}"
    local ALIASES="${3:-}"
    local LIB_ROOT
    LIB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -f "${LIB_ROOT}/scripts/resolve_config.py" ]] && command -v python3 >/dev/null 2>&1; then
        local RESOLVED
        RESOLVED="$(python3 "${LIB_ROOT}/scripts/resolve_config.py" "${KEY}" --key "${KEY}" 2>/dev/null || true)"
        if [[ -n "${RESOLVED}" ]]; then
            printf '%s\n' "${RESOLVED}"
            return 0
        fi
    fi

    local PATTERN="^[[:space:]]*(${KEY}"
    if [[ -n "${ALIASES}" ]]; then
        PATTERN="${PATTERN}|${ALIASES}"
    fi
    PATTERN="${PATTERN}):"

    local FILE VALUE
    for FILE in config/settings.local.yaml config/settings.yaml config/settings.example.yaml; do
        VALUE="$(grep -E "${PATTERN}" "${FILE}" 2>/dev/null | head -n 1 | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
        if [[ -n "${VALUE}" ]]; then
            printf '%s\n' "${VALUE}"
            return 0
        fi
    done

    printf '%s\n' "${FALLBACK}"
}

# --------------------------------------------------------------------------- #
# Binary discovery
# --------------------------------------------------------------------------- #

# Locate an executable binary among candidates.
# Checks arguments in order: if an argument is an executable file, returns it;
# if it is found on PATH via command -v, returns it.
# Echoes the found binary and returns 0, or echoes empty string if none found.
runner_find_binary() {
    local CANDIDATE
    for CANDIDATE in "$@"; do
        [[ -z "${CANDIDATE}" ]] && continue
        if [[ -x "${CANDIDATE}" && ! -d "${CANDIDATE}" ]]; then
            printf '%s\n' "${CANDIDATE}"
            return 0
        fi
        if command -v "${CANDIDATE}" >/dev/null 2>&1; then
            printf '%s\n' "${CANDIDATE}"
            return 0
        fi
    done
    printf ''
    return 0
}

