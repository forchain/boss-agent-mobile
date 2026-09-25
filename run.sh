#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Unified Master Runner & Service Orchestrator
# ==============================================================================
# Master orchestration entrypoint for managing infrastructure and application
# services, or delegating directly to dedicated subsystem runners.
#
# Usage:
#   ./run.sh                              # Start application services (worker + web)
#   ./run.sh start                        # Start application services (worker + web)
#   ./run.sh restart                      # Restart application services (worker + web)
#   ./run.sh stop                         # Stop application services (worker + web)
#   ./run.sh status                       # Service status dashboard (infra + app)
#
# Service Group Orchestration:
#   ./run.sh app [start|stop|restart|status]   # Manage application layer (worker + web)
#   ./run.sh infra [start|stop|restart|status] # Manage infrastructure (pb + emu + appium)
#   ./run.sh all [start|stop|restart|status]   # Manage full stack (infra + app)
#
# Single Service Routing:
#   ./run.sh worker [args...]             # Dedicated Automation Worker (./worker.sh)
#   ./run.sh web [args...]                # SvelteKit Web Dashboard (./web.sh)
#   ./run.sh pb [args...]                 # PocketBase State Stream (./pb.sh)
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

Default Actions (operates on Application services: worker + web):
  ./run.sh                            Start application services & auto-attach to worker logs
  ./run.sh start                      Start application services in background
  ./run.sh stop                       Stop application services
  ./run.sh restart                    Restart application services
  ./run.sh status                     Show overall system status dashboard
  ./run.sh attach                     Attach to live Automation Worker logs

Service Group Orchestration:
  ./run.sh app [action]               Manage application services (worker, web)
  ./run.sh infra [action]             Manage infrastructure (pb, emulator, appium)
  ./run.sh all [action]               Manage all services (infra + app)

Single Service Delegation:
  ./run.sh worker [action]            Manage Automation Worker (./worker.sh)
  ./run.sh web [action]               Manage Web Dashboard (./web.sh)
  ./run.sh pb [action]                Manage PocketBase (./pb.sh)
  ./run.sh emu [action]               Manage Android Emulator (./emulator.sh)
  ./run.sh appium [action]            Manage Appium Server (./appium.sh)
  ./run.sh doctor                     Run system diagnostic check (./doctor.sh)
  ./run.sh live [args...]             Run live mobile test harness

Examples:
  ./run.sh restart                    Restart worker + web to test current worktree
  ./run.sh infra start                Start PocketBase, Emulator, and Appium in background
  ./run.sh app status                 Check status of worker and web
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
            ./web.sh start --daemon "$@"
            echo "✅ Application services started in background."
            echo "   Worker Logs: .boss_agent/worker.log"
            echo "   Web Logs   : .boss_agent/web.log"
            ;;
        stop)
            echo "🛑 Stopping Application Services..."
            ./worker.sh stop
            ./web.sh stop
            ;;
        restart)
            echo "🔄 Restarting Application Services..."
            ./worker.sh stop || true
            ./web.sh stop || true
            sleep 0.5
            ./worker.sh start --daemon "$@"
            ./web.sh start --daemon "$@"
            echo "✅ Application services restarted in background."
            echo "   Worker Logs: .boss_agent/worker.log"
            echo "   Web Logs   : .boss_agent/web.log"
            ;;
        status)
            echo "📊 Application Services Status:"
            ./worker.sh status || true
            ./web.sh status || true
            ;;
        *)
            echo "❌ Unknown app action: ${ACTION}. Valid actions: start, stop, restart, status" >&2
            return 1
            ;;
    esac
}

cmd_infra() {
    local ACTION="${1:-start}"
    shift || true
    case "${ACTION}" in
        start)
            echo "🚀 Starting Infrastructure Services (PocketBase + Emulator + Appium)..."
            ./pb.sh start --daemon "$@"
            ./emulator.sh start
            ./appium.sh start --daemon "$@"
            echo "✅ Infrastructure services started."
            ;;
        stop)
            echo "🛑 Stopping Infrastructure Services..."
            ./appium.sh stop || true
            ./emulator.sh stop || true
            ./pb.sh stop || true
            echo "✅ Infrastructure services stopped."
            ;;
        restart)
            echo "🔄 Restarting Infrastructure Services..."
            ./appium.sh stop || true
            ./emulator.sh stop || true
            ./pb.sh stop || true
            sleep 0.5
            ./pb.sh start --daemon "$@"
            ./emulator.sh start
            ./appium.sh start --daemon "$@"
            echo "✅ Infrastructure services restarted."
            ;;
        status)
            echo "📊 Infrastructure Services Status:"
            ./pb.sh status || true
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
    web|svelte)
        shift
        exec ./web.sh "$@"
        ;;
    pb|pocketbase)
        shift
        exec ./pb.sh "$@"
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
        cmd_app restart "$@"
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
