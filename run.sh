#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Unified Master Runner & Service Orchestrator
# ==============================================================================
# Master orchestration entrypoint for managing infrastructure and application
# services, or delegating directly to dedicated subsystem runners.
#
# Usage:
#   ./run.sh                              # Start application services (worker + dashboard) & attach logs
#   ./run.sh start                        # Start application services (worker + dashboard)
#   ./run.sh restart                      # Restart application services (worker + dashboard) & attach logs
#   ./run.sh restart --daemon             # Restart application services in background (no attach)
#   ./run.sh stop                         # Stop application services (worker + dashboard)
#   ./run.sh status                       # Service status dashboard (infra + app)
#
# Service Group Orchestration:
#   ./run.sh app [start|stop|restart|status]   # Manage application layer (worker + dashboard)
#   ./run.sh infra [start|stop|restart|status] # Manage infrastructure (pb + emu + appium)
#   ./run.sh all [start|stop|restart|status]   # Manage full stack (infra + app)
#
# Single Service Routing:
#   ./run.sh worker [args...]             # Dedicated Automation Worker (./worker.sh)
#   ./run.sh dashboard [args...]          # SvelteKit Web Dashboard (./dashboard.sh)
#   ./run.sh web [args...]                # Compatibility route for ./run.sh dashboard
#   ./run.sh pb [args...]                 # PocketBase State Stream (./pocketbase.sh)
#   ./run.sh emu [args...]                # Dedicated Android AVD (./emulator.sh)
#   ./run.sh appium [args...]             # Appium Server (./appium.sh)
#   ./run.sh doctor [args...]             # Diagnostic Health Check (./doctor.sh)
#   ./run.sh live [args...]               # Live Mobile Test Harness
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

mkdir -p ".boss_agent"

# Pre-flight environment check
if command -v uv >/dev/null 2>&1; then
    RUNNER=(uv run python3)
elif command -v python3 >/dev/null 2>&1; then
    RUNNER=(python3)
else
    echo "❌ Error: Neither 'uv' nor 'python3' was found in PATH." >&2
    exit 1
fi

show_help() {
    cat << 'EOF'
Boss Agent Mobile - Unified Master Runner & Service Orchestrator

Usage:
  ./run.sh [command] [action] [options]

Default Actions (operates on Application services: worker + dashboard):
  ./run.sh                            Start application services & auto-attach to worker logs
  ./run.sh start                      Start application services in background
  ./run.sh stop                       Stop application services
  ./run.sh restart                    Restart application services & auto-attach to worker logs
  ./run.sh restart --daemon           Restart application services in background (no attach)
  ./run.sh status                     Show overall system status dashboard
  ./run.sh attach                     Attach to live Automation Worker logs

Service Group Orchestration:
  ./run.sh app [action]               Manage application services (worker, dashboard)
  ./run.sh infra [action]             Manage infrastructure (pb, emulator, appium)
  ./run.sh all [action]               Manage all services (infra + app)

Single Service Delegation:
  ./run.sh worker [action]            Manage Automation Worker (./worker.sh)
  ./run.sh dashboard [action]         Manage Web Dashboard (./dashboard.sh)
  ./run.sh web [action]               Alias for ./run.sh dashboard (kept for muscle memory)
  ./run.sh pb [action]                Manage PocketBase (./pocketbase.sh)
  ./run.sh emu [action]               Manage Android Emulator (./emulator.sh)
  ./run.sh appium [action]            Manage Appium Server (./appium.sh)
  ./run.sh doctor                     Run system diagnostic check (./doctor.sh)
  ./run.sh live [args...]             Run live mobile test harness

Examples:
  ./run.sh restart                    Restart worker + dashboard and watch the new run
  ./run.sh restart --daemon           Restart worker + dashboard and return to the prompt
  ./run.sh infra start                Start PocketBase, Emulator, and Appium in background
  ./run.sh app status                 Check status of worker and dashboard
  ./run.sh live --keyword "AI"        Run live harness with test arguments
EOF
}

cmd_app() {
    local ACTION="${1:-start}"
    shift || true
    case "${ACTION}" in
        start)
            echo "🚀 Starting Application Services (Worker + Web Dashboard)..."
            ./worker.sh start --daemon "$@"
            ./dashboard.sh start --daemon "$@"
            echo "✅ Application services started in background."
            echo "   Worker Logs: .boss_agent/worker.log"
            echo "   Dashboard Logs: .boss_agent/web.log"
            ;;
        stop)
            echo "🛑 Stopping Application Services..."
            ./worker.sh stop
            ./dashboard.sh stop
            ;;
        restart)
            echo "🔄 Restarting Application Services..."
            ./worker.sh stop || true
            ./dashboard.sh stop || true
            sleep 0.5
            ./worker.sh start --daemon "$@"
            ./dashboard.sh start --daemon "$@"
            echo "✅ Application services restarted in background."
            echo "   Worker Logs: .boss_agent/worker.log"
            echo "   Dashboard Logs: .boss_agent/web.log"
            ;;
        status)
            echo "📊 Application Services Status:"
            ./worker.sh status || true
            ./dashboard.sh status || true
            ;;
        *)
            echo "❌ Unknown app action: ${ACTION}. Valid actions: start, stop, restart, status" >&2
            return 1
            ;;
    esac
}

cmd_restart_app() {
    # `restart` mirrors bare `./run.sh`: restarting mid-development is a "watch what the new
    # code does" action, so it lands on the live Worker log stream. `--daemon` / `-d` is the
    # explicit opt-out for supervisors and scripts, which have no terminal to follow.
    #
    # The flag is consumed here rather than forwarded: the runners already get `--daemon`
    # from `cmd_app restart`, so passing a second copy down would be noise.
    local RESTART_ARGS=()
    local ATTACH_WORKER=1
    for arg in "$@"; do
        case "${arg}" in
            --daemon|-d) ATTACH_WORKER=0 ;;
            *) RESTART_ARGS+=("${arg}") ;;
        esac
    done

    if [[ ${#RESTART_ARGS[@]} -gt 0 ]]; then
        cmd_app restart "${RESTART_ARGS[@]}"
    else
        cmd_app restart
    fi

    if [[ ${ATTACH_WORKER} -eq 1 ]]; then
        exec ./worker.sh attach
    fi
}

# Restart the dedicated AVD only when it is not already usable.
#
# `./emulator.sh stop` issues `emu kill`, and a cold AVD boot costs 30-60s. Restarting
# PocketBase and Appium is cheap, so killing a perfectly healthy device to bring the rest
# of the infrastructure back up made every restart pay a full cold boot.
#
# `emulator.sh status` is the single bounded authority on whether the AVD is online and
# booted (the drift ticket #242 removed from this file), so the decision is made from the
# runner's own verdict rather than a second adb probe here. When the device is up, it is
# left running and `./emulator.sh start` re-validates the ADB and remote-bridge connection
# on the reuse path.
restart_emulator_reusing_if_online() {
    local STATUS_OUT=""
    if STATUS_OUT="$(./emulator.sh status 2>&1)"; then
        echo "♻️  Reusing the online dedicated AVD — skipping the cold restart."
        printf '%s\n' "${STATUS_OUT}" | sed 's/^/   /'
        return 0
    fi

    # The verdict and its reason are printed on both paths: "absent", "still booting" and
    # "adb is missing" all mean "cold restart", and without the reason the operator cannot
    # tell which of the three they are looking at.
    echo "🌙 No online dedicated AVD found — performing a cold restart."
    printf '%s\n' "${STATUS_OUT}" | sed 's/^/   /'
    ./emulator.sh stop || true
}

cmd_infra() {
    local ACTION="${1:-start}"
    shift || true
    case "${ACTION}" in
        start)
            echo "🚀 Starting Infrastructure Services (PocketBase + Emulator + Appium)..."
            ./pocketbase.sh start --daemon "$@"
            ./emulator.sh start --daemon
            ./appium.sh start --daemon "$@"
            echo "✅ Infrastructure services started."
            ;;
        stop)
            echo "🛑 Stopping Infrastructure Services..."
            ./appium.sh stop || true
            ./emulator.sh stop || true
            ./pocketbase.sh stop || true
            echo "✅ Infrastructure services stopped."
            ;;
        restart)
            echo "🔄 Restarting Infrastructure Services..."
            ./appium.sh stop || true
            restart_emulator_reusing_if_online
            ./pocketbase.sh stop || true
            sleep 0.5
            ./pocketbase.sh start --daemon "$@"
            # `--daemon` keeps the emulator in the background: the AVD boots for tens of
            # seconds and this command is a batch operation, so it must return. The runner
            # reuses an already-booted device instead of starting a second one.
            ./emulator.sh start --daemon
            ./appium.sh start --daemon "$@"
            echo "✅ Infrastructure services restarted."
            ;;
        status)
            echo "📊 Infrastructure Services Status:"
            ./pocketbase.sh status || true
            ./emulator.sh status || true
            ./appium.sh status || true
            ;;
        *)
            echo "❌ Unknown infra action: ${ACTION}. Valid actions: start, stop, restart, status" >&2
            return 1
            ;;
    esac
}

cmd_all() {
    local ACTION="${1:-start}"
    shift || true
    case "${ACTION}" in
        start)
            cmd_infra start "$@"
            cmd_app start "$@"
            ;;
        stop)
            cmd_app stop
            cmd_infra stop
            ;;
        restart)
            cmd_app stop
            cmd_infra restart "$@"
            cmd_app start "$@"
            ;;
        status)
            cmd_infra status
            echo ""
            cmd_app status
            ;;
        *)
            echo "❌ Unknown all action: ${ACTION}. Valid actions: start, stop, restart, status" >&2
            return 1
            ;;
    esac
}

SUBCOMMAND="${1:-}"

case "${SUBCOMMAND}" in
    # Single Service Delegation
    worker|wk)
        shift
        exec ./worker.sh "$@"
        ;;
    # `web` stays routed to the same runner: the script was renamed to match the `web/`
    # source directory, but every existing muscle-memory invocation keeps working.
    dashboard|web|svelte)
        shift
        exec ./dashboard.sh "$@"
        ;;
    pb|pocketbase)
        shift
        exec ./pocketbase.sh "$@"
        ;;
    emu|emulator)
        shift
        exec ./emulator.sh "$@"
        ;;
    appium|app)
        # Distinguish between 'appium' runner and 'app' service group
        if [[ "${SUBCOMMAND}" == "appium" ]]; then
            shift
            exec ./appium.sh "$@"
        else
            # 'app' can be appium if next arg is missing and no action, or app service group
            # Canonical: 'appium' for appium server, 'app' for application service group
            shift
            cmd_app "$@"
        fi
        ;;
    doctor|check)
        shift
        exec ./doctor.sh "$@"
        ;;
    attach|logs)
        shift
        exec ./worker.sh attach "$@"
        ;;
    live)
        shift
        exec "${RUNNER[@]}" scripts/run_live_test.py "$@"
        ;;

    # Group Orchestration
    apps)
        shift
        cmd_app "$@"
        ;;
    infra|base)
        shift
        cmd_infra "$@"
        ;;
    all)
        shift
        cmd_all "$@"
        ;;

    # Top-Level Lifecycle Defaults (operates on app)
    "")
        cmd_app start
        exec ./worker.sh attach
        ;;
    start)
        shift || true
        cmd_app start "$@"
        ;;
    restart)
        shift || true
        cmd_restart_app "$@"
        ;;
    stop)
        shift || true
        cmd_app stop
        ;;
    status)
        shift || true
        echo "========================================================================"
        echo " Boss Agent Mobile - Service Status Dashboard"
        echo "========================================================================"
        cmd_infra status
        echo "------------------------------------------------------------------------"
        cmd_app status
        echo "========================================================================"
        ;;
    --help|-h|help)
        show_help
        ;;
    *)
        # If user passed arguments like --keyword "AI" without subcommand, route to live test
        if [[ "${SUBCOMMAND}" == -* ]]; then
            exec "${RUNNER[@]}" scripts/run_live_test.py "$@"
        fi
        echo "❌ Unknown command: ${SUBCOMMAND}" >&2
        echo "Run './run.sh --help' for usage." >&2
        exit 1
        ;;
esac
