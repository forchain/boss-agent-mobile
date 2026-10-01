"""
tests/unit/test_atomic_lease_and_buffered_logs.py
=================================================
Unit tests verifying atomic task compare-and-set leasing and buffered task-log flush (Issue #309).
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker, PocketBaseTaskBroker


@pytest.mark.asyncio
async def test_atomic_claim_uses_expect_status_query():
    """Verify claim_task executes a single compare-and-set PATCH carrying expect_status."""
    mock_session = MagicMock()
    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {
        "id": "task_abc",
        "task_type": TaskType.AUTO_APPLY.value,
        "status": "running",
        "worker_id": "worker-1",
        "locked_at": "2026-10-01T00:00:00Z",
        "last_heartbeat_at": "2026-10-01T00:00:00Z",
        "logs": [],
    }
    mock_session.patch.return_value = patch_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    claimed = await broker.claim_task("task_abc", worker_id="worker-1")

    assert claimed is not None
    assert claimed.worker_id == "worker-1"
    assert claimed.status == TaskStatus.RUNNING

    # Verify atomic CAS condition in PATCH url
    mock_session.patch.assert_called_once()
    called_url = mock_session.patch.call_args[0][0]
    assert "task_abc" in called_url
    assert "expect_status=pending" in called_url


@pytest.mark.asyncio
async def test_atomic_claim_conflict_returns_none():
    """Verify that if the CAS condition fails on server (HTTP 404), claim_task yields None."""
    mock_session = MagicMock()
    patch_resp = MagicMock()
    patch_resp.status_code = 404
    mock_session.patch.return_value = patch_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    claimed = await broker.claim_task("task_already_running", worker_id="worker-2")

    assert claimed is None
    mock_session.patch.assert_called_once()


@pytest.mark.asyncio
async def test_buffered_logs_bound_flush():
    """Verify log lines are buffered until the bound is reached, reducing round trips."""
    mock_session = MagicMock()

    # Initial get_task response
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "id": "task_log_1",
        "task_type": TaskType.CHECK_CHAT.value,
        "status": "running",
        "worker_id": "w1",
        "logs": ["initial log"],
    }
    mock_session.get.return_value = get_resp

    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {"id": "task_log_1"}
    mock_session.patch.return_value = patch_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    broker.log_buffer_bound = 5

    # Append 4 lines (under bound 5)
    for i in range(4):
        await broker.append_log("task_log_1", f"Line {i}")

    # Initial get_task was called once, but NO patch flush yet
    assert mock_session.get.call_count == 1
    assert mock_session.patch.call_count == 0

    # 5th line reaches bound -> triggers flush
    await broker.append_log("task_log_1", "Line 4")
    assert mock_session.patch.call_count == 1
    sent_logs = mock_session.patch.call_args[1]["json"]["logs"]
    assert len(sent_logs) == 6  # 1 initial + 5 appended


@pytest.mark.asyncio
async def test_terminal_status_guarantees_log_flush():
    """Verify update_task_status flushes un-flushed buffered logs in the same status PATCH."""
    mock_session = MagicMock()

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "id": "task_term",
        "task_type": TaskType.AUTO_APPLY.value,
        "status": "running",
        "worker_id": "w1",
        "logs": [],
    }
    mock_session.get.return_value = get_resp

    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {
        "id": "task_term",
        "task_type": TaskType.AUTO_APPLY.value,
        "status": TaskStatus.SUCCESS.value,
        "worker_id": "w1",
        "logs": ["line 1", "line 2"],
    }
    mock_session.patch.return_value = patch_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    broker.log_buffer_bound = 10

    await broker.append_log("task_term", "line 1")
    await broker.append_log("task_term", "line 2")
    assert mock_session.patch.call_count == 0  # Still buffered

    # Transition to terminal state (SUCCESS)
    updated = await broker.update_task_status("task_term", status=TaskStatus.SUCCESS)
    assert updated.status == TaskStatus.SUCCESS

    # The status update PATCH contained the buffered logs!
    assert mock_session.patch.call_count == 1
    call_json = mock_session.patch.call_args[1]["json"]
    assert call_json["status"] == TaskStatus.SUCCESS.value
    assert len(call_json["logs"]) == 2
    assert "line 1" in call_json["logs"][0]
    assert "line 2" in call_json["logs"][1]


@pytest.mark.asyncio
async def test_abnormal_run_failure_preserves_logs():
    """Verify an abnormal run failure preserves and flushes all emitted logs."""
    mock_session = MagicMock()

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "id": "task_fail",
        "task_type": TaskType.SCRAPE_JOBS.value,
        "status": "running",
        "worker_id": "w1",
        "logs": [],
    }
    mock_session.get.return_value = get_resp

    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {
        "id": "task_fail",
        "task_type": TaskType.SCRAPE_JOBS.value,
        "status": "failed",
        "error_message": "Unexpected crash",
        "logs": ["step 1", "step 2", "Uncaught exception"],
    }
    mock_session.patch.return_value = patch_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    broker.log_buffer_bound = 20

    await broker.append_log("task_fail", "step 1: started browser")
    await broker.append_log("task_fail", "step 2: clicked search")
    await broker.append_log("task_fail", "Uncaught exception: Crash")

    # Worker enters failure handler
    await broker.update_task_status(
        "task_fail",
        status=TaskStatus.FAILED,
        error_message="Unexpected crash",
    )

    assert mock_session.patch.call_count == 1
    call_json = mock_session.patch.call_args[1]["json"]
    assert call_json["status"] == "failed"
    assert call_json["error_message"] == "Unexpected crash"
    assert len(call_json["logs"]) == 3
    assert "step 1: started browser" in call_json["logs"][0]
    assert "step 2: clicked search" in call_json["logs"][1]
    assert "Uncaught exception: Crash" in call_json["logs"][2]
