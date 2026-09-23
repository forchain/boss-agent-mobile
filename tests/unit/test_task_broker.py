"""
tests/unit/test_task_broker.py
==============================
Unit tests for State Stream Task Broker and PocketBase Schema Adapter (Issue #27).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import (
    POCKETBASE_AUTOMATION_TASKS_SCHEMA,
    AutomationTask,
    TaskStatus,
    TaskType,
)
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker, PocketBaseTaskBroker


def test_pocketbase_schema_definition_validity():
    """Verify automation_tasks collection schema conforms to PocketBase requirements."""
    schema = POCKETBASE_AUTOMATION_TASKS_SCHEMA
    assert schema["name"] == "automation_tasks"
    assert schema["type"] == "base"

    field_names = [f["name"] for f in schema["fields"]]
    assert "task_type" in field_names
    assert "status" in field_names
    assert "payload" in field_names
    assert "worker_id" in field_names
    assert "locked_at" in field_names
    assert "last_heartbeat_at" in field_names
    assert "logs" in field_names
    assert "error_message" in field_names


@pytest.mark.asyncio
async def test_create_task_persists_pending():
    """Verify create_task persists a new task in pending state."""
    broker = InMemoryTaskBroker()

    task = await broker.create_task(
        task_type=TaskType.CHECK_LOGIN,
        payload={"device_id": "emulator-5554", "timeout_seconds": 30},
    )

    assert isinstance(task, AutomationTask)
    assert task.id is not None
    assert task.task_type == TaskType.CHECK_LOGIN
    assert task.status == TaskStatus.PENDING
    assert task.payload["device_id"] == "emulator-5554"
    assert task.worker_id is None
    assert task.locked_at is None
    assert task.last_heartbeat_at is None
    assert task.logs == []

    fetched = await broker.get_task(task.id)
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_claim_task_success():
    """Verify a worker can successfully claim a pending task."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.AUTO_APPLY, payload={"max_applications": 10})

    claimed = await broker.claim_task(task.id, worker_id="worker-node-1")
    assert claimed is not None
    assert claimed.status == TaskStatus.RUNNING
    assert claimed.worker_id == "worker-node-1"
    assert claimed.locked_at is not None
    assert claimed.last_heartbeat_at is not None

    # Verify state in broker
    persisted = await broker.get_task(task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.RUNNING
    assert persisted.worker_id == "worker-node-1"


@pytest.mark.asyncio
async def test_concurrent_claim_task_race_condition():
    """Verify that under race conditions, exactly one worker claims the task."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "python"})

    num_workers = 10
    results = await asyncio.gather(
        *(broker.claim_task(task.id, worker_id=f"worker-{i}") for i in range(num_workers))
    )

    claimed_successes = [r for r in results if r is not None]
    assert len(claimed_successes) == 1
    winner = claimed_successes[0]
    assert winner.status == TaskStatus.RUNNING

    # All others failed to claim
    claimed_failures = [r for r in results if r is None]
    assert len(claimed_failures) == num_workers - 1


@pytest.mark.asyncio
async def test_update_heartbeat():
    """Verify heartbeat update succeeds only for the owning worker."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.CHECK_LOGIN)
    await broker.claim_task(task.id, worker_id="worker-owner")

    # Non-owning worker fails heartbeat
    unauthorized_res = await broker.update_heartbeat(task.id, worker_id="worker-imposter")
    assert unauthorized_res is False

    # Owning worker updates heartbeat
    ok = await broker.update_heartbeat(task.id, worker_id="worker-owner")
    assert ok is True

    updated = await broker.get_task(task.id)
    assert updated is not None
    assert updated.last_heartbeat_at is not None


@pytest.mark.asyncio
async def test_update_task_status_and_logs():
    """Verify task status transition and log appending."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    await broker.claim_task(task.id, worker_id="worker-1")

    await broker.append_log(task.id, "Navigated to Chat Page")
    await broker.append_log(task.id, "Found 3 new unread messages")

    updated_task = await broker.update_task_status(
        task.id,
        status=TaskStatus.SUCCESS,
        error_message=None,
    )

    assert updated_task.status == TaskStatus.SUCCESS
    assert len(updated_task.logs) == 2
    assert "Navigated to Chat Page" in updated_task.logs[0]
    assert "Found 3 new unread messages" in updated_task.logs[1]


@pytest.mark.asyncio
async def test_subscribe_tasks_realtime_events():
    """Verify subscription triggers callbacks on task lifecycle events."""
    broker = InMemoryTaskBroker()
    events_received = []

    async def on_event(event_type: str, task: AutomationTask):
        events_received.append((event_type, task.id, task.status))

    broker.subscribe_tasks(on_event)

    task = await broker.create_task(task_type=TaskType.CHECK_LOGIN)
    assert len(events_received) == 1
    assert events_received[0] == ("create", task.id, TaskStatus.PENDING)

    await broker.claim_task(task.id, worker_id="worker-1")
    assert len(events_received) == 2
    assert events_received[1] == ("update", task.id, TaskStatus.RUNNING)

    await broker.update_task_status(task.id, status=TaskStatus.SUCCESS)
    assert len(events_received) == 3
    assert events_received[2] == ("update", task.id, TaskStatus.SUCCESS)


@pytest.mark.asyncio
async def test_pocketbase_broker_create_task_mocked():
    """Verify PocketBaseTaskBroker sends correct payload over HTTP."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "rec12345",
        "task_type": "CHECK_LOGIN",
        "status": "pending",
        "payload": {"device_id": "emulator-5554"},
        "worker_id": None,
        "locked_at": None,
        "last_heartbeat_at": None,
        "logs": [],
        "error_message": None,
        "created": "2026-08-20T12:00:00Z",
        "updated": "2026-08-20T12:00:00Z",
    }
    mock_session.post.return_value = mock_resp

    broker = PocketBaseTaskBroker(
        base_url="http://pb.test",
        auth_token="test-jwt-token",
        session=mock_session,
    )

    task = await broker.create_task(TaskType.CHECK_LOGIN, payload={"device_id": "emulator-5554"})
    assert task.id == "rec12345"
    assert task.task_type == TaskType.CHECK_LOGIN
    assert task.status == TaskStatus.PENDING

    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == "http://pb.test/api/collections/automation_tasks/records"
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-jwt-token"
    assert call_args[1]["json"]["task_type"] == "CHECK_LOGIN"
    assert call_args[1]["json"]["status"] == "pending"


@pytest.mark.asyncio
async def test_pocketbase_broker_claim_task_optimistic_lock():
    """Verify PocketBaseTaskBroker verifies pending status before claiming."""
    mock_session = MagicMock()
    # 1st call: GET record (pending)
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "id": "rec12345",
        "task_type": "AUTO_APPLY",
        "status": "pending",
        "payload": {},
    }
    # 2nd call: PATCH record
    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {
        "id": "rec12345",
        "task_type": "AUTO_APPLY",
        "status": "running",
        "payload": {},
        "worker_id": "worker-1",
        "locked_at": "2026-08-20T12:01:00Z",
        "last_heartbeat_at": "2026-08-20T12:01:00Z",
        "logs": [],
        "created": "2026-08-20T12:00:00Z",
        "updated": "2026-08-20T12:01:00Z",
    }
    mock_session.get.return_value = get_resp
    mock_session.patch.return_value = patch_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    claimed = await broker.claim_task("rec12345", worker_id="worker-1")

    assert claimed is not None
    assert claimed.status == TaskStatus.RUNNING
    assert claimed.worker_id == "worker-1"


@pytest.mark.asyncio
async def test_pocketbase_broker_claim_task_conflict():
    """Verify PocketBaseTaskBroker rejects claiming already running task."""
    mock_session = MagicMock()
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "id": "rec12345",
        "task_type": "AUTO_APPLY",
        "status": "running",
        "worker_id": "worker-other",
    }
    mock_session.get.return_value = get_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    claimed = await broker.claim_task("rec12345", worker_id="worker-1")

    assert claimed is None
    mock_session.patch.assert_not_called()


@pytest.mark.asyncio
async def test_saved_search_broker_crud():
    """Verify broker supports CRUD for SavedSearch presets."""
    from boss_agent.models import FilterConfig, SavedSearch, SearchConfig

    broker = InMemoryTaskBroker()
    search = SavedSearch(
        id="search-1",
        name="Python Engineer",
        description="Search for python engineers",
        search=SearchConfig(keyword="python"),
        filter=FilterConfig(education="本科", salary="20-30k"),
        cron_expression="0 9 * * *",
        is_enabled=True,
    )
    saved = await broker.save_saved_search(search)
    assert saved.id == "search-1"

    fetched = await broker.get_saved_search("search-1")
    assert fetched is not None
    assert fetched.name == "Python Engineer"
    assert fetched.search.keyword == "python"
    assert fetched.filter.education == "本科"
    assert fetched.cron_expression == "0 9 * * *"
    assert fetched.is_enabled is True

    searches = await broker.list_saved_searches()
    assert len(searches) == 1
    assert searches[0].id == "search-1"

    deleted = await broker.delete_saved_search("search-1")
    assert deleted is True
    assert await broker.get_saved_search("search-1") is None


@pytest.mark.asyncio
async def test_list_pending_tasks_handles_404_gracefully():
    """Verify PocketBaseTaskBroker handles 404 without crashing."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    tasks = await broker.list_pending_tasks()
    assert tasks == []


@pytest.mark.asyncio
async def test_list_pending_tasks_handles_request_exception():
    """Verify PocketBaseTaskBroker handles network/connection error gracefully."""
    import requests

    mock_session = MagicMock()
    mock_session.get.side_effect = requests.exceptions.ConnectionError("Connection refused")

    broker = PocketBaseTaskBroker(session=mock_session)
    tasks = await broker.list_pending_tasks()
    assert tasks == []


@pytest.mark.asyncio
async def test_list_stale_running_tasks_handles_404_gracefully():
    """Verify list_stale_running_tasks handles 404 without crashing."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    tasks = await broker.list_stale_running_tasks()
    assert tasks == []


# ---------------------------------------------------------------------------
# Execution cursor: the newest SUCCESS of a task type (issue #239)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_successful_completions_is_empty_without_any_success():
    """No successful run yet means no cursor, which is what triggers a full scan."""
    broker = InMemoryTaskBroker()
    await broker.create_task(task_type=TaskType.CHECK_CHAT)

    assert await broker.list_recent_successful_completions(TaskType.CHECK_CHAT) == []


@pytest.mark.asyncio
async def test_recent_successful_completions_returns_the_newest_success_first():
    broker = InMemoryTaskBroker()
    older = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    newer = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    await broker.claim_task(older.id, worker_id="w1")
    await broker.update_task_status(older.id, status=TaskStatus.SUCCESS)
    await broker.claim_task(newer.id, worker_id="w1")
    await broker.update_task_status(newer.id, status=TaskStatus.SUCCESS)

    completed = await broker.list_recent_successful_completions(TaskType.CHECK_CHAT)

    assert [t.id for t in completed] == [newer.id, older.id]


@pytest.mark.asyncio
async def test_recent_successful_completions_skips_runs_that_did_not_succeed():
    """A failed or still-running run must never become the window a later run trusts."""
    broker = InMemoryTaskBroker()
    succeeded = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    failed = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    running = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    pending = await broker.create_task(task_type=TaskType.CHECK_CHAT)

    await broker.claim_task(succeeded.id, worker_id="w1")
    await broker.update_task_status(succeeded.id, status=TaskStatus.SUCCESS)
    await broker.claim_task(failed.id, worker_id="w1")
    await broker.update_task_status(failed.id, status=TaskStatus.FAILED, error_message="boom")
    await broker.claim_task(running.id, worker_id="w1")

    completed = await broker.list_recent_successful_completions(TaskType.CHECK_CHAT)

    assert [t.id for t in completed] == [succeeded.id]
    assert pending.id not in {t.id for t in completed}


@pytest.mark.asyncio
async def test_recent_successful_completions_is_scoped_to_one_task_type():
    """CHECK_CHAT's cursor is not another workflow's completion time."""
    broker = InMemoryTaskBroker()
    chat = await broker.create_task(task_type=TaskType.CHECK_CHAT)
    login = await broker.create_task(task_type=TaskType.CHECK_LOGIN)
    await broker.claim_task(login.id, worker_id="w1")
    await broker.update_task_status(login.id, status=TaskStatus.SUCCESS)
    await broker.claim_task(chat.id, worker_id="w1")
    await broker.update_task_status(chat.id, status=TaskStatus.SUCCESS)

    completed = await broker.list_recent_successful_completions(TaskType.CHECK_CHAT)

    assert [t.id for t in completed] == [chat.id]


@pytest.mark.asyncio
async def test_recent_successful_completions_honours_the_lookback_limit():
    broker = InMemoryTaskBroker()
    for _ in range(4):
        task = await broker.create_task(task_type=TaskType.CHECK_CHAT)
        await broker.claim_task(task.id, worker_id="w1")
        await broker.update_task_status(task.id, status=TaskStatus.SUCCESS)

    assert len(await broker.list_recent_successful_completions(TaskType.CHECK_CHAT)) == 4
    assert len(await broker.list_recent_successful_completions(TaskType.CHECK_CHAT, limit=2)) == 2


@pytest.mark.asyncio
async def test_recent_successful_completions_exposes_the_payload_for_caller_filtering():
    """The cursor excludes unactioned runs, and only the caller knows which those are."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={"dry_run": True})
    await broker.claim_task(task.id, worker_id="w1")
    await broker.update_task_status(task.id, status=TaskStatus.SUCCESS)

    completed = await broker.list_recent_successful_completions(TaskType.CHECK_CHAT)

    assert completed[0].payload == {"dry_run": True}


@pytest.mark.asyncio
async def test_pocketbase_recent_successful_completions_queries_successes_of_one_type():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "items": [
            {
                "id": "rec-new",
                "task_type": "CHECK_CHAT",
                "status": "success",
                "payload": {},
                "logs": [],
                "created": "2026-09-23T09:00:00Z",
                "updated": "2026-09-23T10:00:00Z",
            }
        ]
    }
    mock_session.get.return_value = mock_resp

    broker = PocketBaseTaskBroker(base_url="http://pb.test", session=mock_session)
    completed = await broker.list_recent_successful_completions(TaskType.CHECK_CHAT, limit=5)

    assert [t.id for t in completed] == ["rec-new"]
    assert completed[0].updated == datetime(2026, 9, 23, 10, 0, tzinfo=UTC)

    url = mock_session.get.call_args[0][0]
    assert url.startswith("http://pb.test/api/collections/automation_tasks/records?")
    assert "task_type='CHECK_CHAT'" in url
    assert "status='success'" in url
    assert "sort=-updated" in url
    assert "perPage=5" in url


@pytest.mark.asyncio
async def test_pocketbase_recent_successful_completions_handles_404_gracefully():
    """An unprovisioned collection means no cursor, which falls back to a full scan."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    broker = PocketBaseTaskBroker(session=mock_session)
    assert await broker.list_recent_successful_completions(TaskType.CHECK_CHAT) == []


@pytest.mark.asyncio
async def test_pocketbase_recent_successful_completions_handles_request_exception():
    import requests

    mock_session = MagicMock()
    mock_session.get.side_effect = requests.exceptions.ConnectionError("Connection refused")

    broker = PocketBaseTaskBroker(session=mock_session)
    assert await broker.list_recent_successful_completions(TaskType.CHECK_CHAT) == []
