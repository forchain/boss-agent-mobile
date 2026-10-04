"""
tests/unit/test_run_sh_orchestration.py
=======================================
Unit tests for the master service orchestrator script (run.sh).
Verifies service group dispatch (app, infra, all), single-service delegation,
default lifecycle targets, and live test harness pass-through.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _service_harness import REPO_ROOT

RUN_SH = REPO_ROOT / "run.sh"
BUDGET_SEC = 10.0


@pytest.fixture
def orchestrator_runtime(tmp_path: Path) -> Path:
    """A throwaway runtime root containing run.sh and mocked subsystem scripts."""
    runtime_root = tmp_path / "repo"
    (runtime_root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(RUN_SH, runtime_root / "run.sh")
    shutil.copy2(RUN_SH.parent / "runner_lib.sh", runtime_root / "runner_lib.sh")
    (runtime_root / "run.sh").chmod(0o755)

    # Create mock runner scripts that record their invocations
    calls_log = runtime_root / "calls.log"

    def _make_mock_script(name: str):
        script_path = runtime_root / name
        content = f"""#!/usr/bin/env bash
echo "{name} $*" >> "{calls_log}"
if [[ "${{1:-}}" == "status" ]]; then
    echo "🟢 {name} is running"
    exit 0
fi
exit 0
"""
        script_path.write_text(content, encoding="utf-8")
        script_path.chmod(0o755)

    for script in (
        "worker.sh",
        "dashboard.sh",
        "pocketbase.sh",
        "emulator.sh",
        "appium.sh",
        "doctor.sh",
    ):
        _make_mock_script(script)

    return runtime_root


def _run(runtime_root: Path, *args: str) -> subprocess.CompletedProcess:
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, "run.sh", *args],
        cwd=str(runtime_root),
        capture_output=True,
        text=True,
        timeout=BUDGET_SEC,
    )


def test_run_sh_help_displays_orchestration_guide(orchestrator_runtime: Path):
    result = _run(orchestrator_runtime, "--help")
    assert result.returncode == 0
    assert "Service Group Orchestration:" in result.stdout
    assert "./run.sh app" in result.stdout
    assert "./run.sh infra" in result.stdout
    assert "./run.sh all" in result.stdout


def test_run_sh_delegates_to_single_services(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Delegate worker
    res = _run(orchestrator_runtime, "worker", "status")
    assert res.returncode == 0
    assert "worker.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate web (compatibility route, kept after the web.sh -> dashboard.sh rename)
    res = _run(orchestrator_runtime, "web", "status")
    assert res.returncode == 0
    assert "dashboard.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate pb
    res = _run(orchestrator_runtime, "pb", "status")
    assert res.returncode == 0
    assert "pocketbase.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate emu
    res = _run(orchestrator_runtime, "emu", "status")
    assert res.returncode == 0
    assert "emulator.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate appium
    res = _run(orchestrator_runtime, "appium", "status")
    assert res.returncode == 0
    assert "appium.sh status" in calls_log.read_text(encoding="utf-8")


def test_run_sh_dashboard_is_the_canonical_route(orchestrator_runtime: Path):
    """`./run.sh dashboard` is the canonical name; `./run.sh web` still reaches it (#361).

    The script was renamed away from `web.sh` because the project root also holds the `web/`
    source directory and `./web<Tab>` stalled on both. Every pre-existing `./run.sh web`
    invocation has to keep working, so both spellings are asserted here.
    """
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime, "dashboard", "status")
    assert res.returncode == 0
    assert "dashboard.sh status" in calls_log.read_text(encoding="utf-8")

    calls_log.unlink()
    res = _run(orchestrator_runtime, "web", "status")
    assert res.returncode == 0
    assert "dashboard.sh status" in calls_log.read_text(encoding="utf-8")


def test_no_root_script_shares_a_name_with_the_web_source_directory(orchestrator_runtime: Path):
    """No root entry may collide with the `web/` directory prefix (#361).

    A single `./web*` candidate is what makes tab-completion usable again: the completion
    engine has nothing to disambiguate, so it completes straight through to `web/`.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    collisions = sorted(p.name for p in repo_root.glob("web*") if p.name != "web")
    assert not collisions, (
        f"root entries {collisions} share the `web/` directory prefix and re-create the "
        "tab-completion conflict"
    )
    assert not (repo_root / "web.sh").exists(), "web.sh must have been renamed (dashboard.sh)"
    assert (repo_root / "dashboard.sh").is_file(), "dashboard.sh must exist"


def test_run_sh_app_group_orchestration(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Test app stop
    res = _run(orchestrator_runtime, "app", "stop")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh stop" in content
    assert "dashboard.sh stop" in content

    # Test app restart
    calls_log.unlink()
    res = _run(orchestrator_runtime, "app", "restart")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh stop" in content
    assert "dashboard.sh stop" in content
    assert "worker.sh start --daemon" in content
    assert "dashboard.sh start --daemon" in content


def test_run_sh_default_restart_operates_on_app(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Bare `./run.sh restart` defaults to app restart
    res = _run(orchestrator_runtime, "restart")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh stop" in content
    assert "dashboard.sh stop" in content
    assert "worker.sh start --daemon" in content
    assert "dashboard.sh start --daemon" in content


def test_run_sh_restart_attaches_to_worker_logs_by_default(orchestrator_runtime: Path):
    """`./run.sh restart` lands on the live Worker log stream, like bare `./run.sh` (#362).

    Restarting mid-development is a "watch what the new code does" action, so returning to
    the prompt left the user with a restart and no way to watch it, forcing a second
    command. Defaulting to the attach removes that second step.
    """
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime, "restart")

    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh start --daemon" in content, "restart must still background the services"
    assert "worker.sh attach" in content, (
        "a bare `./run.sh restart` must attach to the Worker logs the same way bare "
        "`./run.sh` does"
    )


@pytest.mark.parametrize("flag", ["--daemon", "-d"])
def test_run_sh_restart_daemon_stays_detached(orchestrator_runtime: Path, flag: str):
    """`--daemon` / `-d` keeps `restart` a background-only action (#362).

    Scripts and supervisors drive `restart` from a non-interactive context where a log
    follow would hang forever, so the flag is the documented way to opt out of attaching.
    """
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime, "restart", flag)

    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh start --daemon" in content
    assert "worker.sh attach" not in content, f"`restart {flag}` must not follow the log"
    # The flag is consumed by the orchestrator, so it must not be forwarded to the runners
    # as a duplicate `--daemon`.
    assert f"worker.sh start --daemon {flag}" not in content, (
        f"`restart {flag}` forwarded the flag to the runner instead of consuming it"
    )


def test_run_sh_restart_still_forwards_unknown_options(orchestrator_runtime: Path):
    """Options that are not the daemon switch keep reaching the underlying runners."""
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime, "restart", "--poll-interval", "2")

    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh start --daemon --poll-interval 2" in content
    assert "worker.sh attach" in content, "forwarded options must not suppress the attach"


def test_run_sh_infra_group_orchestration(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Test infra stop
    res = _run(orchestrator_runtime, "infra", "stop")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "pocketbase.sh stop" in content
    assert "emulator.sh stop" in content
    assert "appium.sh stop" in content


def test_run_sh_status_dashboard(orchestrator_runtime: Path):
    res = _run(orchestrator_runtime, "status")
    assert res.returncode == 0
    assert "Service Status Dashboard" in res.stdout
    assert "Infrastructure Services Status:" in res.stdout
    assert "Application Services Status:" in res.stdout


def test_run_sh_bare_invocation_starts_app_and_attaches_to_worker(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime)
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh start --daemon" in content
    assert "dashboard.sh start --daemon" in content
    assert "worker.sh attach" in content


def test_run_sh_explicit_start_does_not_attach(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime, "start")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh start --daemon" in content
    assert "dashboard.sh start --daemon" in content
    assert "worker.sh attach" not in content


def test_run_sh_app_start_does_not_attach(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    res = _run(orchestrator_runtime, "app", "start")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh start --daemon" in content
    assert "dashboard.sh start --daemon" in content
    assert "worker.sh attach" not in content

