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
#: Owned here for the same reason the lifecycle events are: all five Dedicated Runner
#: Scripts follow their own service through this watch, and "which service stopped" is a
#: contract with the operator that must not drift between copies. Four reach it through
#: `runner_attached_logs` — `appium.sh`, `dashboard.sh`, `pocketbase.sh` and `worker.sh` —
#: and `emulator.sh` calls `runner_watch_log_stream` from its own `attach_logs`, because a
#: Virtual Device Session is followed by ADB serial rather than by an attach banner. The PID
#: is substituted by the seam below.
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

# Follow LOG_FILE until a watched service is gone, then announce the stop and return.
#
# `runner_watch_log_stream PID LOG_FILE LABEL [INITIAL_LINES] [PID_FILE] [SECOND_PID] [SECOND_LABEL]`
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
# `SECOND_PID`/`SECOND_LABEL` watch a *second* process under its own name, for the runners
# whose service is not the only thing their stream depends on. `emulator.sh` is the case
# that makes it necessary: the Remote ADB Bridge is the process a shutdown signal is
# actually sent to, while the AVD is the process whose log is being displayed — and since
# no shutdown signal stops the AVD, watching only one of the two leaves a terminal that
# never notices a stop it was attached to see. Either handle ending the watch is correct,
# and each is announced under the name of the process that actually stopped.
#
# The primary handle is checked first, so when both are gone in the same poll the service
# proper is the one named — the AVD outliving its bridge is the ordinary case, and a notice
# about the wrong one of the two would misreport it. The secondary is never treated as a
# restart: only the primary has a pidfile to supply that evidence, and claiming a restart
# from the *other* service's pidfile would be exactly the dishonesty
# `runner_restart_replacement_pid` exists to prevent.
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
    local SECOND_PID="${6:-}"
    local SECOND_LABEL="${7:-service}"
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
    local SECOND_WATCHABLE=0
    if runner_pid_watchable "${SECOND_PID}"; then
        SECOND_WATCHABLE=1
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
        if [[ ${SECOND_WATCHABLE} -eq 1 ]] && runner_process_gone "${SECOND_PID}"; then
            runner_watch_detach
            runner_log_attached_stop "${SECOND_LABEL}" "${SECOND_PID}"
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
# runners; boss_agent.adapters.infra.teardown mirrors the worker's half.

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
# Parameterized service-command choreography
# --------------------------------------------------------------------------- #
#
# Five Dedicated Runner Scripts used to hand-roll the same four commands — status, stop,
# attach and restart — and the copies drifted into two live hazards:
#
#   * `dashboard.sh` narrated its own lifecycle without the shared wording and emitted no
#     restart telemetry at all, so the E2E Pre-Test Teardown Gate could see a stop but not
#     a restart;
#   * `emulator.sh` fell back to a raw kill-and-sleep for the one process it owns, giving
#     the Remote ADB Bridge none of the Graceful Shutdown Protocol's budget.
#
# This section owns the choreography. A runner describes its service once, by setting the
# variables below, and then calls the shared commands. What stays in the runner is what is
# genuinely that runner's: the preflight checks, the launch command, and the decisions only
# it can make (which transports exist, what "running" means for a device).
#
# The description is a set of plain shell variables rather than a struct, because bash 3.2
# has no associative arrays and this library has to run on the macOS its users develop on.
#
#   RUNNER_SERVICE_LABEL                     the name the lifecycle events carry
#   RUNNER_SERVICE_NAME                      the name the operator's report carries
#   RUNNER_SERVICE_PID_FILE                  where the service's PID is recorded
#   RUNNER_SERVICE_LOG_FILE                  the service's own log
#   RUNNER_SERVICE_PORT                      the TCP port it listens on ("" when it has none)
#   RUNNER_SERVICE_HEALTH_URL                the URL that answers when it is up ("" when none)
#   RUNNER_SERVICE_HEALTH_URLS               several URLs to try, space-separated
#   RUNNER_SERVICE_ENDPOINT                  the address shown in the operator's report
#   RUNNER_SERVICE_STOP_TIMEOUT_SEC          the Graceful Shutdown Protocol budget
#   RUNNER_SERVICE_STOP_ACK_LABEL            the label the shutdown acknowledgment carries
#   RUNNER_SERVICE_STOP_ACK_DETAIL           the service named in the acknowledgment ("" = none)
#   RUNNER_SERVICE_STOP_DETAIL               what the stop request says it is stopping
#   RUNNER_SERVICE_STOP_COMPLETE_DETAIL     what the stop completion says it stopped
#   RUNNER_SERVICE_RESTART_DETAIL            what the restart request says it is restarting
#   RUNNER_SERVICE_RESTART_COMPLETE_DETAIL   what the restart completion says
#   RUNNER_SERVICE_RESIDUAL_PATTERN          pgrep pattern for processes that outlived a parent
#   RUNNER_SERVICE_RECLAIM_PATTERN           pgrep pattern reclaimed with the same budget
#   RUNNER_SERVICE_FIND_PATTERN              pgrep pattern used to find an unrecorded service
#   RUNNER_SERVICE_STOP_CHILDREN             "1" when the service's children go first
#   RUNNER_SERVICE_STALE_CWD                 "1" when a deleted working directory is reported
#   RUNNER_SERVICE_RESTART_ISOLATED          "1" when the start half runs in a subshell
#   RUNNER_SERVICE_RESTART_TOLERATES_STOP    "1" when a failed stop does not abort a restart
#   RUNNER_SERVICE_IDLE_MESSAGE              what an idle stop says
#   RUNNER_SERVICE_STOPPED_MESSAGE           what a completed stop says
#   RUNNER_SERVICE_STATUS_CHECKING           what the status probe announces
#   RUNNER_SERVICE_STATUS_UP                 what a healthy service reports
#   RUNNER_SERVICE_STATUS_DOWN               what an unreachable service reports
#   RUNNER_SERVICE_STATUS_PID_LINE           the PID line, when the service reports one
#   RUNNER_SERVICE_STATUS_LOG_LINE           the log line, when the service reports one
#   RUNNER_SERVICE_STATUS_STALE_WARNING      the warning for a process that outlived its port
#   RUNNER_SERVICE_ATTACH_MISSING            what an attach to a stopped service says

# Reset every field of the service description to its default.
#
# A runner calls this *before* it fills the description in, so a field it does not set is
# empty rather than inherited from whatever the previous runner in the same shell left
# behind — a sourced library in a shared shell is exactly the place where a stale value
# would quietly change a stop's behaviour.
runner_service_reset() {
    RUNNER_SERVICE_LABEL="Service"
    RUNNER_SERVICE_NAME="service"
    RUNNER_SERVICE_PID_FILE=""
    RUNNER_SERVICE_LOG_FILE=""
    RUNNER_SERVICE_PORT=""
    RUNNER_SERVICE_HEALTH_URL=""
    RUNNER_SERVICE_HEALTH_URLS=""
    RUNNER_SERVICE_ENDPOINT=""
    RUNNER_SERVICE_STOP_TIMEOUT_SEC="10"
    RUNNER_SERVICE_RESIDUAL_TIMEOUT_SEC=""
    RUNNER_SERVICE_STOP_ACK_LABEL=""
    RUNNER_SERVICE_STOP_ACK_DETAIL=""
    RUNNER_SERVICE_STOP_DETAIL=""
    RUNNER_SERVICE_STOP_COMPLETE_DETAIL=""
    RUNNER_SERVICE_RESTART_DETAIL=""
    RUNNER_SERVICE_RESTART_COMPLETE_DETAIL=""
    RUNNER_SERVICE_RESIDUAL_PATTERN=""
    RUNNER_SERVICE_RECLAIM_PATTERN=""
    RUNNER_SERVICE_FIND_PATTERN=""
    RUNNER_SERVICE_STOP_CHILDREN="0"
    RUNNER_SERVICE_STALE_CWD="0"
    RUNNER_SERVICE_RESTART_ISOLATED="0"
    RUNNER_SERVICE_RESTART_TOLERATES_STOP="0"
    RUNNER_SERVICE_RESTART_COMMAND="./run.sh restart"
    RUNNER_SERVICE_RESTART_DELAY_SEC="0.5"
    RUNNER_SERVICE_STOP_BANNER="Stopping service..."
    RUNNER_SERVICE_IDLE_MESSAGE="No running service found."
    RUNNER_SERVICE_STOPPED_MESSAGE="Service stopped."
    RUNNER_SERVICE_STATUS_CHECKING="Checking service status..."
    RUNNER_SERVICE_STATUS_UP="Service is RUNNING."
    RUNNER_SERVICE_STATUS_DOWN="Service is NOT RUNNING."
    RUNNER_SERVICE_STATUS_PID_LINE=""
    RUNNER_SERVICE_STATUS_LOG_LINE=""
    RUNNER_SERVICE_STATUS_STALE_WARNING=""
    RUNNER_SERVICE_ATTACH_MISSING="Cannot attach to log stream."
}

# The PID of the service this description names, or empty.
#
# Three sources, in this order:
#
#   1. the pidfile, when it names a live process whose working directory still exists —
#      a service whose source tree was deleted under it is a stale record, not a service;
#   2. the LISTEN-only owner of the service's port, when it has one, adopted into the
#      pidfile so the next lookup is cheap;
#   3. the find pattern, for a service with neither a pidfile nor a port (the Automation
#      Worker, whose launcher is found by the script it runs).
#
# A merely *connected* client is never a candidate: every port probe in this library is
# LISTEN-only, because a dashboard holding an SSE stream to PocketBase must not be
# mistaken for PocketBase and signalled.
runner_service_resolve_pid() {
    local PID
    PID="$(runner_pidfile_read "${RUNNER_SERVICE_PID_FILE}")"
    if [[ -n "${PID}" ]] \
        && runner_process_alive "${PID}" \
        && runner_process_cwd_alive "${PID}"; then
        echo "${PID}"
        return 0
    fi

    if [[ -n "${RUNNER_SERVICE_PORT}" ]]; then
        local PORT_PID
        PORT_PID="$(runner_port_listener_pid "${RUNNER_SERVICE_PORT}")"
        if [[ -n "${PORT_PID}" ]] && runner_process_cwd_alive "${PORT_PID}"; then
            runner_pidfile_write "${RUNNER_SERVICE_PID_FILE}" "${PORT_PID}"
            echo "${PORT_PID}"
            return 0
        fi
    fi

    if [[ -n "${RUNNER_SERVICE_FIND_PATTERN}" ]]; then
        local FOUND_PID
        while IFS= read -r FOUND_PID; do
            [[ -z "${FOUND_PID}" ]] && continue
            if runner_process_alive "${FOUND_PID}" && runner_process_cwd_alive "${FOUND_PID}"; then
                runner_pidfile_write "${RUNNER_SERVICE_PID_FILE}" "${FOUND_PID}"
                echo "${FOUND_PID}"
                return 0
            fi
        done < <(pgrep -f "${RUNNER_SERVICE_FIND_PATTERN}" 2>/dev/null || true)
    fi

    echo ""
}

# Whether the service answers its health probe.
#
# A service with no health URL has no probe to make: it is judged by its process alone,
# which is the Automation Worker's case — it has no port and no endpoint of its own.
runner_service_healthy() {
    local PID="${1:-}"
    local URL
    if [[ -z "${RUNNER_SERVICE_HEALTH_URL}" && -z "${RUNNER_SERVICE_HEALTH_URLS}" ]]; then
        # A service with no probe of its own is judged by its process alone.
        [[ -n "${PID}" ]] && runner_process_alive "${PID}"
        return
    fi
    for URL in ${RUNNER_SERVICE_HEALTH_URL} ${RUNNER_SERVICE_HEALTH_URLS}; do
        if curl -s -f "${URL}" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

# Substitute the placeholders a status template may carry.
runner_service_expand() {
    local TEMPLATE="${1:-}"
    local PID="${2:-}"
    local EXPANDED="${TEMPLATE}"
    EXPANDED="${EXPANDED//\{pid\}/${PID}}"
    EXPANDED="${EXPANDED//\{log\}/${RUNNER_SERVICE_LOG_FILE}}"
    EXPANDED="${EXPANDED//\{endpoint\}/${RUNNER_SERVICE_ENDPOINT}}"
    EXPANDED="${EXPANDED//\{port\}/${RUNNER_SERVICE_PORT}}"
    printf '%s\n' "${EXPANDED}"
}

# The shared `status` command.
#
# Read-only, and it never touches the service: a status probe that repaired something
# would make "is it up?" a question that changes the answer. The one thing it does change
# is a stale pidfile — a record of a service that is demonstrably gone — which is removed
# so the next stop does not chase a PID that was never ours.
runner_service_status() {
    local PID
    PID="$(runner_service_resolve_pid)"

    echo "🔍 ${RUNNER_SERVICE_STATUS_CHECKING}"

    # A port held by a process whose working directory was deleted is a service that can
    # never serve again, and it is worth naming: the remedy (restart) is different from
    # the one for "not running" (start).
    if [[ "${RUNNER_SERVICE_STALE_CWD}" == "1" && -n "${RUNNER_SERVICE_PORT}" ]]; then
        local PORT_PID STALE_CWD
        PORT_PID="$(runner_port_listener_pid "${RUNNER_SERVICE_PORT}")"
        if [[ -n "${PORT_PID}" ]] && ! runner_process_cwd_alive "${PORT_PID}"; then
            STALE_CWD="$(runner_process_cwd "${PORT_PID}")"
            echo "🔴 ${RUNNER_SERVICE_NAME} port ${RUNNER_SERVICE_PORT} is held by a STALE process (PID: ${PORT_PID})"
            echo "   Deleted CWD: ${STALE_CWD:-unknown}"
            echo "   Run '${RUNNER_SERVICE_RESTART_COMMAND}' to reclaim port and start a fresh server."
            return 1
        fi
    fi

    if runner_service_healthy "${PID}"; then
        echo "$(runner_service_expand "${RUNNER_SERVICE_STATUS_UP}" "${PID}")"
        if [[ -n "${RUNNER_SERVICE_STATUS_PID_LINE}" && -n "${PID}" ]]; then
            echo "$(runner_service_expand "${RUNNER_SERVICE_STATUS_PID_LINE}" "${PID}")"
        fi
        if [[ -n "${RUNNER_SERVICE_STATUS_LOG_LINE}" ]]; then
            echo "$(runner_service_expand "${RUNNER_SERVICE_STATUS_LOG_LINE}" "${PID}")"
        fi
        return 0
    fi

    echo "$(runner_service_expand "${RUNNER_SERVICE_STATUS_DOWN}" "${PID}")"
    if [[ -n "${RUNNER_SERVICE_STATUS_STALE_WARNING}" && -n "${PID}" ]]; then
        echo "$(runner_service_expand "${RUNNER_SERVICE_STATUS_STALE_WARNING}" "${PID}")"
    fi
    runner_pidfile_clear "${RUNNER_SERVICE_PID_FILE}"
    return 1
}

# The shared `stop` command.
#
# One order for every service, so no runner can drift into a different escalation or a
# different event:
#
#   1. the service itself, its children first when it has any;
#   2. whatever outlived it and still matches the residual pattern;
#   3. whatever still holds the port, reclaimed with the same budget;
#   4. the pidfile cleared, and the port waited for;
#   5. the completion event, or the plain statement that nothing was running.
#
# An idle stop writes nothing at all: lifecycle events record real stops, not heartbeats,
# and a runner that logged unconditionally would create its service's log on every
# `./run.sh stop`.
runner_service_stop() {
    local LABEL="${RUNNER_SERVICE_LABEL}"
    local LOG_FILE="${RUNNER_SERVICE_LOG_FILE}"
    local TIMEOUT="${RUNNER_SERVICE_STOP_TIMEOUT_SEC}"
    # A leftover process gets the same budget by default; a service whose leftovers are
    # known to be quick may narrow it.
    local RESIDUAL_TIMEOUT="${RUNNER_SERVICE_RESIDUAL_TIMEOUT_SEC:-${TIMEOUT}}"
    local STOPPED=0
    local PID
    PID="$(runner_service_resolve_pid)"

    echo "${RUNNER_SERVICE_STOP_BANNER}"

    if [[ -n "${PID}" ]]; then
        # The Web Dashboard's acknowledgment is a cross-process contract the teardown gate
        # greps for, so a service that declares one records it before the stop request.
        if [[ -n "${RUNNER_SERVICE_STOP_ACK_DETAIL}" ]]; then
            runner_ack_shutdown "${LOG_FILE}" "${RUNNER_SERVICE_STOP_ACK_LABEL}" "${PID}" \
                "${RUNNER_SERVICE_STOP_ACK_DETAIL}"
        fi
        runner_log_stop_request "${LOG_FILE}" "${LABEL}" "${PID}" "${RUNNER_SERVICE_STOP_DETAIL}"
        if [[ "${RUNNER_SERVICE_STOP_CHILDREN}" == "1" ]]; then
            # Children first: the recorded PID is the launcher, and the child that holds
            # the device session is its own. Signalling the parent first re-parents the
            # child to init, where it can no longer be found or stopped at all.
            pkill -P "${PID}" 2>/dev/null || true
        fi
        runner_graceful_stop "${PID}" "${TIMEOUT}" "${LABEL}" "${LOG_FILE}"
        STOPPED=1
    fi

    # Processes that outlived their parent: the same budget, the same escalation.
    local PATTERN
    for PATTERN in "${RUNNER_SERVICE_RESIDUAL_PATTERN}" "${RUNNER_SERVICE_RECLAIM_PATTERN}"; do
        [[ -n "${PATTERN}" ]] || continue
        local RESIDUAL_PIDS RESIDUAL_PID
        RESIDUAL_PIDS="$(pgrep -f "${PATTERN}" 2>/dev/null || true)"
        for RESIDUAL_PID in ${RESIDUAL_PIDS}; do
            runner_graceful_stop "${RESIDUAL_PID}" "${RESIDUAL_TIMEOUT}" "${LABEL}" "${LOG_FILE}"
            STOPPED=1
        done
    done

    # Whatever still holds the port. The probe is LISTEN-only, so a connected client is
    # never a candidate for being signalled.
    if [[ -n "${RUNNER_SERVICE_PORT}" ]]; then
        local PORT_PID
        PORT_PID="$(runner_port_listener_pid "${RUNNER_SERVICE_PORT}")"
        if [[ -n "${PORT_PID}" ]]; then
            echo "⚠️ Port ${RUNNER_SERVICE_PORT} still held by PID ${PORT_PID}; reclaiming."
            runner_graceful_stop "${PORT_PID}" "${TIMEOUT}" "port ${RUNNER_SERVICE_PORT} listener" "${LOG_FILE}"
            STOPPED=1
        fi
    fi

    runner_pidfile_clear "${RUNNER_SERVICE_PID_FILE}"

    if [[ -n "${RUNNER_SERVICE_PORT}" ]]; then
        if ! runner_wait_until "${TIMEOUT}" runner_port_released "${RUNNER_SERVICE_PORT}"; then
            echo "❌ Error: port ${RUNNER_SERVICE_PORT} is still occupied after shutdown." >&2
            runner_log_event "${LOG_FILE}" "❌ [${LABEL}] Port ${RUNNER_SERVICE_PORT} is still occupied after shutdown."
            return 1
        fi
    fi

    if [[ ${STOPPED} -eq 1 ]]; then
        runner_log_stop_complete "${LOG_FILE}" "${LABEL}" "${RUNNER_SERVICE_STOP_COMPLETE_DETAIL}"
        echo "✅ ${RUNNER_SERVICE_STOPPED_MESSAGE}"
    else
        echo "ℹ️ ${RUNNER_SERVICE_IDLE_MESSAGE}"
    fi
    return 0
}

# The shared `attach` command.
#
# Attaching to a service that is not running is a refusal, not an empty stream: the
# operator needs to know that there is nothing to watch before they wait for a line that
# will never come.
runner_service_attach() {
    local PID
    PID="$(runner_service_resolve_pid)"
    if [[ -z "${PID}" ]] || ! runner_process_alive "${PID}"; then
        echo "🔴 ${RUNNER_SERVICE_NAME} is NOT RUNNING." >&2
        echo "   ${RUNNER_SERVICE_ATTACH_MISSING}" >&2
        return 1
    fi
    runner_attached_logs \
        "${PID}" \
        "${RUNNER_SERVICE_LOG_FILE}" \
        "${RUNNER_SERVICE_NAME}" \
        "${RUNNER_SERVICE_ENDPOINT}" \
        "${RUNNER_SERVICE_PID_FILE}"
}

# The shared `restart` command.
#
# A restart is a stop, a pause, and a start — and the two events that bracket it are
# recorded either side of the work, so a supervisor reading the service's own log can tell
# a restart that was asked for from one that finished.
#
# The start half runs in a subshell for the runners whose `cmd_start` ends in an `exit`:
# in the parent shell that exit would take the completion event down with it. For the
# runners whose start half attaches to a live log stream, the subshell would swallow the
# Ctrl+C that detaches it — so those run it in place, and say so.
runner_service_restart() {
    local LABEL="${RUNNER_SERVICE_LABEL}"

    runner_log_restart_request \
        "${RUNNER_SERVICE_LOG_FILE}" "${LABEL}" "${RUNNER_SERVICE_RESTART_DETAIL}"

    if [[ "${RUNNER_SERVICE_RESTART_TOLERATES_STOP}" == "1" ]]; then
        runner_service_stop || true
    else
        runner_service_stop
    fi

    sleep "${RUNNER_SERVICE_RESTART_DELAY_SEC:-0.5}"

    if declare -f cmd_start >/dev/null 2>&1; then
        if [[ "${RUNNER_SERVICE_RESTART_ISOLATED}" == "1" ]]; then
            # The status is captured rather than propagated, so a start that failed cannot
            # be recorded below as a service that came back up. Not written as
            # `( cmd_start ) || return 1`: a subshell used as an operand of `||` has
            # errexit suspended *inside* it, which would let a failed pre-flight fall
            # through to the foreground log stream and hang forever. Suspension is
            # therefore lifted only for the outer shell, and restored for the subshell
            # where `cmd_start` depends on it to abort.
            set +e
            ( set -e; cmd_start "$@" )
            local START_STATUS=$?
            set -e
            if [[ ${START_STATUS} -ne 0 ]]; then
                return 1
            fi
        else
            cmd_start "$@"
        fi
    fi

    runner_log_restart_complete \
        "${RUNNER_SERVICE_LOG_FILE}" "${LABEL}" "${RUNNER_SERVICE_RESTART_COMPLETE_DETAIL}"
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

