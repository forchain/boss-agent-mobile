#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Dedicated Runner Script lifecycle library
# ==============================================================================
# Sourced by worker.sh, web.sh, pocketbase.sh, appium.sh and emulator.sh. Each of
# them used to hand-roll the same process lifecycle — roughly 250-300 duplicated
# lines — and the copies drifted into a live hazard: web.sh probed port ownership
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
runner_graceful_stop() {
    local PID="${1:-}"
    local TIMEOUT_SEC="${2:-10}"
    local LABEL="${3:-process}"

    if ! runner_process_alive "${PID}"; then
        return 0
    fi

    kill "${PID}" 2>/dev/null || true
    if runner_wait_until "${TIMEOUT_SEC}" runner_process_gone "${PID}"; then
        return 0
    fi

    echo "⚠️ ${LABEL} did not shut down gracefully within ${TIMEOUT_SEC}s; sending SIGKILL to PID ${PID}."
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

# Print the tail of a service log and follow it, without killing the daemon on
# detach. `runner_attached_logs PID LOG_FILE LABEL ENDPOINT`.
runner_attached_logs() {
    local PID="${1:-}"
    local LOG_FILE="${2:-}"
    local LABEL="${3:-service}"
    local ENDPOINT="${4:-}"

    echo "ℹ️ ${LABEL} is already running (PID: ${PID})${ENDPOINT:+ at ${ENDPOINT}}"
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    # Detaching must not signal the background service.
    trap 'echo -e "\n👋 Detached from '"${LABEL}"' logs ('"${LABEL}"' is still running in background)."; exit 0' INT TERM

    touch "${LOG_FILE}"
    tail -n 30 -f "${LOG_FILE}"
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
