"""
tests/unit/test_worker_runner_lifecycle.py
==========================================
Unit and lifecycle tests for the dedicated Automation Worker runner script (worker.sh).
Tests verify process state reporting, graceful stop, cross-worktree process termination,
and pre-flight dependency gates without touching live devices.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _service_harness import REPO_ROOT

WORKER_SH = REPO_ROOT / "worker.sh"
BUDGET_SEC = 10.0


@pytest.fixture
def worker_runtime(tmp_path: Path) -> Path:
    """A throwaway runtime root containing worker.sh and an isolated .boss_agent directory."""
    runtime_root = tmp_path / "repo"
    (runtime_root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(WORKER_SH, runtime_root / "worker.sh")
    (runtime_root / "worker.sh").chmod(0o755)
    return runtime_root


def _run_worker_cmd(runtime_root: Path, action: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    if env:
        base_env.update(env)
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, "worker.sh", action, *args],
        cwd=str(runtime_root),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=BUDGET_SEC,
    )


def test_worker_status_reports_not_running_when_idle(worker_runtime: Path):
    result = _run_worker_cmd(worker_runtime, "status")
    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout


def test_worker_status_reports_running_with_valid_pid(worker_runtime: Path):
    # Spawn a sleeping dummy process standing in for worker.py
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file = worker_runtime / ".boss_agent" / "worker.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")

        result = _run_worker_cmd(worker_runtime, "status")
        assert result.returncode == 0
        assert "RUNNING" in result.stdout
        assert str(proc.pid) in result.stdout
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_worker_stop_terminates_running_process_and_cleans_pid_file(worker_runtime: Path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file = worker_runtime / ".boss_agent" / "worker.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")

        result = _run_worker_cmd(worker_runtime, "stop")
        assert result.returncode == 0
        assert "stopped" in result.stdout.lower()
        assert not pid_file.exists()
        assert proc.poll() is not None, "Worker process survived stop command"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_worker_stop_is_safe_noop_when_idle(worker_runtime: Path):
    result = _run_worker_cmd(worker_runtime, "stop")
    assert result.returncode == 0
    assert "No running Worker daemon found" in result.stdout


def test_worker_restart_stops_existing_process(worker_runtime: Path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file = worker_runtime / ".boss_agent" / "worker.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")

        # Restart will attempt stop, then fail early at pocketbase gate (since port is unreachable)
        env = {"POCKETBASE_URL": "http://127.0.0.1:59999"}
        result = _run_worker_cmd(worker_runtime, "restart", env=env)

        # Existing process must be killed even if subsequent start aborts on health check
        assert proc.poll() is not None, "Worker process survived restart command"
        assert "Restarting Automation Worker daemon" in result.stdout
        assert "Stopping Automation Worker daemon" in result.stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_worker_preflight_gate_fails_on_unreachable_pocketbase(worker_runtime: Path):
    env = {"POCKETBASE_URL": "http://127.0.0.1:59999"}
    result = _run_worker_cmd(worker_runtime, "start", env=env)
    assert result.returncode != 0
    assert "PocketBase State Stream is not reachable" in result.stderr


def test_worker_preflight_gate_fails_on_unreachable_appium(worker_runtime: Path, tmp_path: Path):
    import http.server
    import socketserver
    import threading

    class _OkHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *args):
            pass

    # Provide working PocketBase mock
    with socketserver.TCPServer(("127.0.0.1", 0), _OkHandler) as pb_srv:
        pb_port = pb_srv.server_address[1]
        t = threading.Thread(target=pb_srv.serve_forever, daemon=True)
        t.start()
        try:
            # Provide fake emulator.sh that succeeds
            fake_emu = worker_runtime / "emulator.sh"
            fake_emu.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_emu.chmod(0o755)

            env = {
                "POCKETBASE_URL": f"http://127.0.0.1:{pb_port}",
                "APPIUM_URL": "http://127.0.0.1:59998",
            }
            result = _run_worker_cmd(worker_runtime, "start", env=env)
            assert result.returncode != 0
            assert "Appium server is not reachable" in result.stderr
        finally:
            pb_srv.shutdown()
            t.join(timeout=3)
