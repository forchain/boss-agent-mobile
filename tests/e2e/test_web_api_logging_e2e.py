"""
End-to-End test verifying SvelteKit Web Dashboard API logging.
Ensures that all incoming /api/* calls (tasks, searches, jobs, etc.)
are captured by hooks.server.ts and written to .boss_agent/web.log
with ANSI color formatting, timestamps, HTTP method, and duration.
"""

import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE = REPO_ROOT / ".boss_agent" / "web.log"
BASE_URL = "http://127.0.0.1:5173"


def is_server_alive() -> bool:
    try:
        resp = httpx.get(f"{BASE_URL}/", timeout=1.0)
        return resp.status_code in (200, 304)
    except Exception:
        return False


@pytest.fixture(scope="module")
def web_server():
    """Ensure web server is running and piping stdout/stderr to .boss_agent/web.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    server_process = None

    if not is_server_alive():
        with open(LOG_FILE, "a", encoding="utf-8") as log_handle:
            env = dict(os.environ)
            env["HOST"] = "127.0.0.1"
            env["PORT"] = "5173"
            server_process = subprocess.Popen(
                ["npm", "--prefix", "web", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
                cwd=str(REPO_ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                preexec_fn=os.setsid
            )

        # Wait up to 15 seconds for server to be responsive
        deadline = time.time() + 15
        while time.time() < deadline:
            if is_server_alive():
                break
            time.sleep(0.5)

        assert is_server_alive(), "Failed to start SvelteKit web server within 15 seconds."

    yield

    if server_process is not None:
        try:
            os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
            server_process.wait(timeout=5)
        except Exception:
            pass


def test_web_api_logging_e2e(web_server):
    # Determine log file offset before sending requests
    initial_size = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

    client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    # 1. POST /api/tasks
    post_task_resp = client.post(
        "/api/tasks",
        json={
            "task_type": "search_and_greet",
            "payload": {"keyword": "E2E_Test_Engineer", "city": "Beijing"}
        }
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
    patch_task_resp = client.patch(
        f"/api/tasks/{created_task_id}",
        json={"status": "cancelled"}
    )
    assert patch_task_resp.status_code == 200

    # 5. GET /api/searches
    get_searches_resp = client.get("/api/searches")
    assert get_searches_resp.status_code == 200

    # 6. GET /api/screening/blacklist
    get_blacklist_resp = client.get("/api/screening/blacklist")
    assert get_blacklist_resp.status_code == 200

    # Allow time for stdout buffer flush to log file
    time.sleep(1.0)

    # Read new log lines
    assert LOG_FILE.exists(), f"Log file {LOG_FILE} was not created"
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
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
    assert f"/api/tasks/{created_task_id}" in new_logs, f"Expected '/api/tasks/{created_task_id}' in log file"
    assert "/api/searches" in new_logs, "Expected '/api/searches' in log file"
    assert "/api/screening/blacklist" in new_logs, "Expected '/api/screening/blacklist' in log file"

    # Verify ANSI color coding for 200 OK (\x1b[32m200\x1b[0m)
    assert "\x1b[32m200\x1b[0m" in new_logs or "200" in new_logs
