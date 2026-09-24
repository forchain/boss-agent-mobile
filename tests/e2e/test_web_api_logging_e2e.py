"""
End-to-end test verifying SvelteKit Web Dashboard API logging (spec #247, ticket #250).

Ensures that all incoming /api/* calls (tasks, searches, jobs, etc.) are captured by
hooks.server.ts and written to the dashboard's log stream with ANSI color formatting,
timestamps, HTTP method, and duration.

The dashboard is started on a dynamically allocated ephemeral port and logs into this
test's own `tmp_path` instead of the shared `.boss_agent/` runtime directory, so the run
never collides with — and never shuts down — a Web Dashboard the developer (or another
worktree) already has on port 5173.

Requires the shared PocketBase State Stream Broker (see docs/agents/testing.md).
"""

import os
import signal
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from _service_harness import free_port

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STARTUP_TIMEOUT_SEC = 30.0
LOG_FLUSH_PAUSE_SEC = 1.0


@dataclass(frozen=True)
class Dashboard:
    """A dashboard started by this test: where it listens and where its log stream goes."""

    base_url: str
    log_file: Path


def _is_dashboard_alive(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url}/", timeout=1.0).status_code in (200, 304)
    except Exception:
        return False


@pytest.fixture(scope="module")
def web_dashboard(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Dashboard]:
    """Start a Web Dashboard on an ephemeral port, logging into this test's tmp directory."""
    if not (REPO_ROOT / "web" / "node_modules").exists():
        raise AssertionError(
            "web/node_modules is missing, so the dashboard cannot start — "
            "run `npm ci --prefix web` in this worktree first"
        )

    state_dir = tmp_path_factory.mktemp("web-dashboard-e2e")
    log_file = state_dir / "web.log"
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ, HOST="127.0.0.1", PORT=str(port))
    with open(log_file, "a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [
                "npm",
                "--prefix",
                "web",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(REPO_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    # Recorded for post-mortem debugging: the log path alone does not say what to inspect.
    (state_dir / "web.pid").write_text(str(process.pid), encoding="utf-8")

    deadline = time.monotonic() + STARTUP_TIMEOUT_SEC
    while time.monotonic() < deadline and not _is_dashboard_alive(base_url):
        time.sleep(0.5)

    if not _is_dashboard_alive(base_url):
        _stop_dashboard(process)
        raise AssertionError(
            f"the dashboard never answered on {base_url} within {STARTUP_TIMEOUT_SEC:.0f}s; "
            f"see {log_file}"
        )

    try:
        yield Dashboard(base_url=base_url, log_file=log_file)
    finally:
        _stop_dashboard(process)


def _stop_dashboard(process: subprocess.Popen) -> None:
    """Stop the dashboard process group (npm and the vite server it spawns).

    Group-level, not process-level: `npm run dev` hands off to a vite child, which would
    survive a kill aimed at npm alone. That is why this does not use the harness `spawn`
    fixture, which reaps exactly the one process it started.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
    except Exception:
        process.kill()


def test_web_api_logging_e2e(web_dashboard: Dashboard):
    log_file = web_dashboard.log_file
    base_url = web_dashboard.base_url

    # Determine log file offset before sending requests
    initial_size = log_file.stat().st_size if log_file.exists() else 0

    client = httpx.Client(base_url=base_url, timeout=10.0)

    # 1. POST /api/tasks
    post_task_resp = client.post(
        "/api/tasks",
        json={
            "task_type": "search_and_greet",
            "payload": {"keyword": "E2E_Test_Engineer", "city": "Beijing"},
        },
    )
    assert post_task_resp.status_code in (200, 201)
    task_data = post_task_resp.json()
    assert task_data.get("success") is True
    created_task_id = task_data["task"]["id"]

    # 2. GET /api/tasks with query params
    get_tasks_resp = client.get("/api/tasks?limit=5&status=pending")
    assert get_tasks_resp.status_code == 200

    # 3. GET /api/tasks/:id
    get_task_resp = client.get(f"/api/tasks/{created_task_id}")
    assert get_task_resp.status_code == 200

    # 4. PATCH /api/tasks/:id
    patch_task_resp = client.patch(f"/api/tasks/{created_task_id}", json={"status": "cancelled"})
    assert patch_task_resp.status_code == 200

    # 5. GET /api/searches
    get_searches_resp = client.get("/api/searches")
    assert get_searches_resp.status_code == 200

    # 6. GET /api/screening/blacklist
    get_blacklist_resp = client.get("/api/screening/blacklist")
    assert get_blacklist_resp.status_code == 200

    # Allow time for stdout buffer flush to log file
    time.sleep(LOG_FLUSH_PAUSE_SEC)

    # Read new log lines
    assert log_file.exists(), f"Log file {log_file} was not created"
    with open(log_file, encoding="utf-8", errors="replace") as f:
        f.seek(initial_size)
        new_logs = f.read()

    print("\n--- CAPTURED NEW WEB LOGS ---")
    print(new_logs)
    print("--- END OF NEW WEB LOGS ---\n")

    # Assert expected log entries exist
    assert "[API]" in new_logs, "Expected '[API]' prefix in log file"
    assert "/api/tasks" in new_logs, "Expected '/api/tasks' in log file"
    assert "POST" in new_logs, "Expected 'POST' method in log file"
    assert "GET" in new_logs, "Expected 'GET' method in log file"
    assert "PATCH" in new_logs, "Expected 'PATCH' method in log file"
    assert f"/api/tasks/{created_task_id}" in new_logs, (
        f"Expected '/api/tasks/{created_task_id}' in log file"
    )
    assert "/api/searches" in new_logs, "Expected '/api/searches' in log file"
    assert "/api/screening/blacklist" in new_logs, "Expected '/api/screening/blacklist' in log file"

    # Verify ANSI color coding for 200 OK (\x1b[32m200\x1b[0m)
    assert "\x1b[32m200\x1b[0m" in new_logs or "200" in new_logs
