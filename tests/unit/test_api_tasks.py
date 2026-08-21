"""
tests/unit/test_api_tasks.py
============================
Unit tests for FastAPI Task Management API (Issue #28).
"""

import pytest
from fastapi.testclient import TestClient

from boss_agent.api.app import create_app
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def client(broker):
    app = create_app(broker=broker)
    return TestClient(app)


def test_submit_task_returns_202_accepted(client, broker):
    """Verify submitting a valid task returns 202 Accepted and creates a pending task."""
    response = client.post(
        "/api/tasks",
        json={
            "task_type": "CHECK_LOGIN",
            "payload": {"device_id": "emulator-5554"},
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "pending"
    assert data["task_type"] == "CHECK_LOGIN"


def test_submit_task_invalid_type_returns_422(client):
    """Verify invalid task type returns 422 Unprocessable Entity."""
    response = client.post(
        "/api/tasks",
        json={
            "task_type": "INVALID_TYPE",
            "payload": {},
        },
    )
    assert response.status_code == 422


def test_get_task_status_found(client, broker):
    """Verify querying an existing task returns its details."""
    submit_res = client.post(
        "/api/tasks",
        json={"task_type": "SCRAPE_JOBS", "payload": {"keyword": "python"}},
    )
    task_id = submit_res.json()["task_id"]

    get_res = client.get(f"/api/tasks/{task_id}")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["id"] == task_id
    assert data["task_type"] == "SCRAPE_JOBS"
    assert data["status"] == "pending"


def test_get_task_status_not_found(client):
    """Verify querying a non-existent task returns 404."""
    response = client.get("/api/tasks/nonexistent-id")
    assert response.status_code == 404
    assert response.json()["detail"] == "Task nonexistent-id not found"


def test_cancel_task_success(client, broker):
    """Verify cancelling a pending task transitions it to CANCELLED."""
    submit_res = client.post(
        "/api/tasks",
        json={"task_type": "AUTO_APPLY", "payload": {"max_applications": 5}},
    )
    task_id = submit_res.json()["task_id"]

    cancel_res = client.post(f"/api/tasks/{task_id}/cancel")
    assert cancel_res.status_code == 200
    data = cancel_res.json()
    assert data["task_id"] == task_id
    assert data["status"] == "cancelled"

    get_res = client.get(f"/api/tasks/{task_id}")
    assert get_res.json()["status"] == "cancelled"


def test_cancel_task_not_found(client):
    """Verify cancelling a non-existent task returns 404."""
    cancel_res = client.post("/api/tasks/nonexistent-id/cancel")
    assert cancel_res.status_code == 404
