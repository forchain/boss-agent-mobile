"""
tests/unit/test_e2e_gate_fixture.py
===================================
Verifies the E2E pre-test teardown gate wiring in `tests/e2e/conftest.py`
(spec #218, ticket #222).

Each scenario assembles a throwaway repository root (a copy of `web.sh`, the real conftest,
and a dummy test module) and runs a nested pytest session against it. The gate inside that
session therefore manages the temporary `.boss_agent` runtime directory only, never the
services used by other worktrees.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _service_harness import (
    REPO_ROOT,
    free_port,
    is_port_free,
    subprocess_env,
    wait_for_log,
    wait_for_port_bound,
)
from _service_harness import (
    spawn as spawn,  # noqa: PLC0414 - re-exported so pytest discovers the fixture
)

CONFTEST_SOURCE = REPO_ROOT / "tests" / "e2e" / "conftest.py"
SKIP_ENV_VAR = "BOSS_AGENT_SKIP_SERVICE_GATE"
INNER_TIMEOUT_SEC = 120.0

SILENT_WORKER_STUB = """
import sys
import time

log_path = sys.argv[1]
with open(log_path, "a", encoding="utf-8") as handle:
    handle.write("2026-01-01 00:00:00 [INFO] boss_agent.worker: worker loop started\\n")

# Exits on SIGTERM without recording any shutdown feedback (pre-#220 worker behaviour).
while True:
    time.sleep(0.05)
"""

DUMMY_TEST = """
def test_e2e_placeholder():
    assert True
"""


@pytest.fixture
def e2e_project(tmp_path: Path) -> Path:
    """A throwaway repo root wired up like a real worktree, ready for a nested pytest run."""
    assert CONFTEST_SOURCE.exists(), "tests/e2e/conftest.py must exist"
    project = tmp_path / "repo"
    (project / ".boss_agent").mkdir(parents=True)
    (project / "tests" / "e2e").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "web.sh", project / "web.sh")
    shutil.copy2(CONFTEST_SOURCE, project / "tests" / "e2e" / "conftest.py")
    (project / "tests" / "e2e" / "test_placeholder.py").write_text(DUMMY_TEST, encoding="utf-8")
    return project


def _inner_env(**overrides: str) -> dict[str, str]:
    """Child environment with any inherited skip flag removed, so the gate really runs."""
    return subprocess_env(strip=[SKIP_ENV_VAR], **overrides)


def _run_inner_pytest(project: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/test_placeholder.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=INNER_TIMEOUT_SEC,
    )


def _spawn_real_worker(spawn, project: Path) -> subprocess.Popen:
    """Launch the real Automation Worker CLI exactly as run.sh does: logs straight to worker.log."""
    log_file = project / ".boss_agent" / "worker.log"
    log_handle = open(log_file, "ab")  # noqa: SIM115 - kept open for the process lifetime
    process = spawn(
        [
            sys.executable,
            "scripts/worker.py",
            "--worker-id",
            "e2e-gate-fixture-test",
            "--pb-url",
            "http://127.0.0.1:1",
            "--appium-url",
            "http://127.0.0.1:1",
            "--poll-interval",
            "0.2",
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=_inner_env(),
    )
    (project / ".boss_agent" / "worker.pid").write_text(str(process.pid), encoding="utf-8")
    wait_for_log(log_file, "Worker daemon loop started")
    return process


def test_gate_stops_residual_services_before_e2e_tests(e2e_project: Path, spawn):
    """Verify the session fixture stops a residual Worker and Web Dashboard, then runs the suite."""
    worker = _spawn_real_worker(spawn, e2e_project)
    web_port = free_port()
    web = spawn(
        [sys.executable, "-m", "http.server", str(web_port), "--bind", "127.0.0.1"],
        env=_inner_env(),
    )
    wait_for_port_bound(web_port)
    (e2e_project / ".boss_agent" / "web.pid").write_text(str(web.pid), encoding="utf-8")

    result = _run_inner_pytest(e2e_project, _inner_env(WEB_PORT=web_port))

    assert result.returncode == 0, f"nested e2e run failed:\n{result.stdout}\n{result.stderr}"
    assert worker.poll() is not None, "residual Automation Worker survived the gate"
    assert web.poll() is not None, "residual Web Dashboard survived the gate"
    assert is_port_free(web_port), f"port {web_port} was not released"
    worker_log = (e2e_project / ".boss_agent" / "worker.log").read_text(encoding="utf-8")
    assert "Received shutdown signal" in worker_log, worker_log
    assert "Stopped residual Automation Worker" in result.stdout, result.stdout
    assert "Stopped residual Web Dashboard" in result.stdout, result.stdout


def test_gate_aborts_session_when_shutdown_cannot_be_verified(e2e_project: Path, spawn):
    """Verify a worker that exits without shutdown feedback fails the suite loudly."""
    log_file = e2e_project / ".boss_agent" / "worker.log"
    log_handle = open(log_file, "ab")  # noqa: SIM115 - kept open for the process lifetime
    stub = spawn(
        [sys.executable, "-c", SILENT_WORKER_STUB, str(log_file)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=_inner_env(),
    )
    (e2e_project / ".boss_agent" / "worker.pid").write_text(str(stub.pid), encoding="utf-8")
    wait_for_log(log_file, "worker loop started")

    result = _run_inner_pytest(e2e_project, _inner_env())

    assert result.returncode != 0, f"gate did not block the run:\n{result.stdout}"
    assert "teardown gate" in result.stdout.lower(), result.stdout
    assert stub.poll() is not None, "silent worker was left running"


def test_gate_can_be_skipped_explicitly(e2e_project: Path, spawn):
    """Verify the documented escape hatch leaves residual services untouched."""
    log_file = e2e_project / ".boss_agent" / "worker.log"
    log_handle = open(log_file, "ab")  # noqa: SIM115 - kept open for the process lifetime
    stub = spawn(
        [sys.executable, "-c", SILENT_WORKER_STUB, str(log_file)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=_inner_env(),
    )
    (e2e_project / ".boss_agent" / "worker.pid").write_text(str(stub.pid), encoding="utf-8")
    wait_for_log(log_file, "worker loop started")

    result = _run_inner_pytest(e2e_project, _inner_env(**{SKIP_ENV_VAR: "1"}))

    assert result.returncode == 0, f"nested run failed:\n{result.stdout}\n{result.stderr}"
    assert stub.poll() is None, "escape hatch must leave residual services running"
    assert SKIP_ENV_VAR in result.stdout, result.stdout
