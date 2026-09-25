"""
tests/unit/test_run_sh_orchestration.py
=======================================
Unit tests for the master service orchestrator script (run.sh).
Verifies service group dispatch (app, infra, all), single-service delegation,
default lifecycle targets, and live test harness pass-through.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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

    for script in ("worker.sh", "web.sh", "pb.sh", "emulator.sh", "appium.sh", "doctor.sh"):
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

    # Delegate web
    res = _run(orchestrator_runtime, "web", "status")
    assert res.returncode == 0
    assert "web.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate pb
    res = _run(orchestrator_runtime, "pb", "status")
    assert res.returncode == 0
    assert "pb.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate emu
    res = _run(orchestrator_runtime, "emu", "status")
    assert res.returncode == 0
    assert "emulator.sh status" in calls_log.read_text(encoding="utf-8")

    # Delegate appium
    res = _run(orchestrator_runtime, "appium", "status")
    assert res.returncode == 0
    assert "appium.sh status" in calls_log.read_text(encoding="utf-8")


def test_run_sh_app_group_orchestration(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Test app stop
    res = _run(orchestrator_runtime, "app", "stop")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh stop" in content
    assert "web.sh stop" in content

    # Test app restart
    calls_log.unlink()
    res = _run(orchestrator_runtime, "app", "restart")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh stop" in content
    assert "web.sh stop" in content
    assert "worker.sh start --daemon" in content
    assert "web.sh start --daemon" in content


def test_run_sh_default_restart_operates_on_app(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Bare `./run.sh restart` defaults to app restart
    res = _run(orchestrator_runtime, "restart")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "worker.sh stop" in content
    assert "web.sh stop" in content
    assert "worker.sh start --daemon" in content
    assert "web.sh start --daemon" in content


def test_run_sh_infra_group_orchestration(orchestrator_runtime: Path):
    calls_log = orchestrator_runtime / "calls.log"

    # Test infra stop
    res = _run(orchestrator_runtime, "infra", "stop")
    assert res.returncode == 0
    content = calls_log.read_text(encoding="utf-8")
    assert "pb.sh stop" in content
    assert "emulator.sh stop" in content
    assert "appium.sh stop" in content


def test_run_sh_status_dashboard(orchestrator_runtime: Path):
    res = _run(orchestrator_runtime, "status")
    assert res.returncode == 0
    assert "Service Status Dashboard" in res.stdout
    assert "Infrastructure Services Status:" in res.stdout
    assert "Application Services Status:" in res.stdout
