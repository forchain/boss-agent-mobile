"""
src/boss_agent/broker/pocketbase_adapter.py
===========================================
PocketBase State Stream Broker Adapter and In-Memory Broker implementations.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import requests

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType

logger = logging.getLogger("boss_agent.broker")


class BaseTaskBroker(ABC):
    """Abstract interface for the State Stream Task Broker."""

    @abstractmethod
    async def create_task(
        self, task_type: TaskType, payload: dict[str, Any] | None = None
    ) -> AutomationTask:
        """Create and persist a new task with PENDING status."""
        pass

    @abstractmethod
    async def claim_task(self, task_id: str, worker_id: str) -> AutomationTask | None:
        """Atomically claim a pending task for a worker. Returns None if already claimed."""
        pass

    @abstractmethod
    async def get_task(self, task_id: str) -> AutomationTask | None:
        """Fetch a task by ID."""
        pass

    @abstractmethod
    async def update_heartbeat(self, task_id: str, worker_id: str) -> bool:
        """Update last_heartbeat_at timestamp for a running task."""
        pass

    @abstractmethod
    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        logs: list[str] | None = None,
        error_message: str | None = None,
        worker_id: str | None = None,
    ) -> AutomationTask:
        """Transition task to a new status and optionally update logs / error message."""
        pass

    @abstractmethod
    async def append_log(self, task_id: str, log_line: str) -> bool:
        """Append a single log line to the task."""
        pass

    @abstractmethod
    async def list_pending_tasks(self, limit: int = 10) -> list[AutomationTask]:
        """List unassigned tasks currently in PENDING state."""
        pass

    @abstractmethod
    def subscribe_tasks(self, callback: Callable[[str, AutomationTask], Any]) -> None:
        """Register a callback for task lifecycle events (create, update)."""
        pass


class InMemoryTaskBroker(BaseTaskBroker):
    """Thread-safe & asyncio-safe in-memory broker for tests and local development."""

    def __init__(self) -> None:
        self._tasks: dict[str, AutomationTask] = {}
        self._lock = asyncio.Lock()
        self._subscribers: list[Callable[[str, AutomationTask], Any]] = []

    async def create_task(
        self, task_type: TaskType, payload: dict[str, Any] | None = None
    ) -> AutomationTask:
        now = datetime.now(UTC)
        task = AutomationTask(
            task_type=task_type,
            status=TaskStatus.PENDING,
            payload=payload or {},
            worker_id=None,
            locked_at=None,
            last_heartbeat_at=None,
            logs=[],
            error_message=None,
            created=now,
            updated=now,
        )
        async with self._lock:
            self._tasks[task.id] = task

        await self._notify_subscribers("create", task)
        return task.model_copy(deep=True)

    async def claim_task(self, task_id: str, worker_id: str) -> AutomationTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status != TaskStatus.PENDING:
                return None

            now = datetime.now(UTC)
            task.status = TaskStatus.RUNNING
            task.worker_id = worker_id
            task.locked_at = now
            task.last_heartbeat_at = now
            task.updated = now
            claimed_task = task.model_copy(deep=True)

        await self._notify_subscribers("update", claimed_task)
        return claimed_task

    async def get_task(self, task_id: str) -> AutomationTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    async def update_heartbeat(self, task_id: str, worker_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.worker_id != worker_id or task.status != TaskStatus.RUNNING:
                return False

            now = datetime.now(UTC)
            task.last_heartbeat_at = now
            task.updated = now
            return True

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        logs: list[str] | None = None,
        error_message: str | None = None,
        worker_id: str | None = None,
    ) -> AutomationTask:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"Task {task_id} not found")

            now = datetime.now(UTC)
            task.status = status
            if logs is not None:
                task.logs = list(logs)
            if error_message is not None:
                task.error_message = error_message
            if worker_id is not None:
                task.worker_id = worker_id
            task.updated = now
            updated_task = task.model_copy(deep=True)

        await self._notify_subscribers("update", updated_task)
        return updated_task

    async def append_log(self, task_id: str, log_line: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            now = datetime.now(UTC)
            formatted = f"[{now.isoformat()}] {log_line}"
            task.logs.append(formatted)
            task.updated = now
            return True

    async def list_pending_tasks(self, limit: int = 10) -> list[AutomationTask]:
        async with self._lock:
            pending = [
                t.model_copy(deep=True)
                for t in self._tasks.values()
                if t.status == TaskStatus.PENDING
            ]
            pending.sort(key=lambda x: x.created)
            return pending[:limit]

    def subscribe_tasks(self, callback: Callable[[str, AutomationTask], Any]) -> None:
        self._subscribers.append(callback)

    async def _notify_subscribers(self, event_type: str, task: AutomationTask) -> None:
        for sub in self._subscribers:
            try:
                res = sub(event_type, task.model_copy(deep=True))
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.exception("Error in task subscription callback: %s", e)


class PocketBaseTaskBroker(BaseTaskBroker):
    """Production PocketBase REST and SSE client adapter."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8090",
        collection_name: str = "automation_tasks",
        auth_token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection_name = collection_name
        self.auth_token = auth_token
        self.session = session or requests.Session()
        self._subscribers: list[Callable[[str, AutomationTask], Any]] = []

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _collection_url(self) -> str:
        return f"{self.base_url}/api/collections/{self.collection_name}/records"

    async def create_task(
        self, task_type: TaskType, payload: dict[str, Any] | None = None
    ) -> AutomationTask:
        url = self._collection_url()
        body: dict[str, Any] = {
            "task_type": task_type.value,
            "status": TaskStatus.PENDING.value,
            "payload": payload or {},
            "worker_id": None,
            "locked_at": None,
            "last_heartbeat_at": None,
            "logs": [],
            "error_message": None,
        }
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self.session.post(url, json=body, headers=self._headers())
        )
        resp.raise_for_status()
        data = resp.json()
        task = self._record_to_task(data)
        await self._notify_subscribers("create", task)
        return task

    async def claim_task(self, task_id: str, worker_id: str) -> AutomationTask | None:
        url = f"{self._collection_url()}/{task_id}"
        now = datetime.now(UTC).isoformat()

        loop = asyncio.get_running_loop()
        # Optimistic verify: check if task is currently pending
        get_resp = await loop.run_in_executor(
            None, lambda: self.session.get(url, headers=self._headers())
        )
        if get_resp.status_code != 200:
            return None

        record = get_resp.json()
        if record.get("status") != TaskStatus.PENDING.value:
            return None

        patch_body = {
            "status": TaskStatus.RUNNING.value,
            "worker_id": worker_id,
            "locked_at": now,
            "last_heartbeat_at": now,
        }
        resp = await loop.run_in_executor(
            None, lambda: self.session.patch(url, json=patch_body, headers=self._headers())
        )
        if resp.status_code != 200:
            return None

        claimed = self._record_to_task(resp.json())
        await self._notify_subscribers("update", claimed)
        return claimed

    async def get_task(self, task_id: str) -> AutomationTask | None:
        url = f"{self._collection_url()}/{task_id}"
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self.session.get(url, headers=self._headers())
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._record_to_task(resp.json())

    async def update_heartbeat(self, task_id: str, worker_id: str) -> bool:
        url = f"{self._collection_url()}/{task_id}"
        loop = asyncio.get_running_loop()
        get_resp = await loop.run_in_executor(
            None, lambda: self.session.get(url, headers=self._headers())
        )
        if get_resp.status_code != 200:
            return False

        record = get_resp.json()
        if record.get("worker_id") != worker_id or record.get("status") != TaskStatus.RUNNING.value:
            return False

        now = datetime.now(UTC).isoformat()
        resp = await loop.run_in_executor(
            None,
            lambda: self.session.patch(
                url, json={"last_heartbeat_at": now}, headers=self._headers()
            ),
        )
        return resp.status_code == 200

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        logs: list[str] | None = None,
        error_message: str | None = None,
        worker_id: str | None = None,
    ) -> AutomationTask:
        url = f"{self._collection_url()}/{task_id}"
        body: dict[str, Any] = {"status": status.value}
        if logs is not None:
            body["logs"] = logs
        if error_message is not None:
            body["error_message"] = error_message
        if worker_id is not None:
            body["worker_id"] = worker_id

        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self.session.patch(url, json=body, headers=self._headers())
        )
        resp.raise_for_status()
        updated = self._record_to_task(resp.json())
        await self._notify_subscribers("update", updated)
        return updated

    async def append_log(self, task_id: str, log_line: str) -> bool:
        task = await self.get_task(task_id)
        if not task:
            return False
        now = datetime.now(UTC)
        formatted = f"[{now.isoformat()}] {log_line}"
        new_logs = task.logs + [formatted]
        await self.update_task_status(task_id, status=task.status, logs=new_logs)
        return True

    async def list_pending_tasks(self, limit: int = 10) -> list[AutomationTask]:
        url = f"{self._collection_url()}?filter=(status='pending')&sort=created&perPage={limit}"
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self.session.get(url, headers=self._headers())
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [self._record_to_task(item) for item in items]

    def subscribe_tasks(self, callback: Callable[[str, AutomationTask], Any]) -> None:
        self._subscribers.append(callback)

    async def _notify_subscribers(self, event_type: str, task: AutomationTask) -> None:
        for sub in self._subscribers:
            try:
                res = sub(event_type, task)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.exception("Error in task subscriber callback: %s", e)

    def _record_to_task(self, record: dict[str, Any]) -> AutomationTask:
        payload = record.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        logs = record.get("logs") or []
        if isinstance(logs, str):
            try:
                logs = json.loads(logs)
            except Exception:
                logs = []

        return AutomationTask(
            id=str(record["id"]),
            task_type=TaskType(record["task_type"]),
            status=TaskStatus(record["status"]),
            payload=payload,
            worker_id=record.get("worker_id"),
            locked_at=self._parse_dt(record.get("locked_at")),
            last_heartbeat_at=self._parse_dt(record.get("last_heartbeat_at")),
            logs=logs,
            error_message=record.get("error_message"),
            created=self._parse_dt(record.get("created")) or datetime.now(UTC),
            updated=self._parse_dt(record.get("updated")) or datetime.now(UTC),
        )

    def _parse_dt(self, val: Any) -> datetime | None:
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return None
