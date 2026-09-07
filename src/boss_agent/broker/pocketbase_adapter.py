"""
src/boss_agent/broker/pocketbase_adapter.py
===========================================
PocketBase State Stream Broker Adapter and In-Memory Broker implementations.
"""

import asyncio
import contextlib
import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.models import SavedSearch, compute_job_fingerprint
from boss_agent.settings import resolve_pocketbase_url

logger = logging.getLogger("boss_agent.broker")


class BaseTaskBroker(ABC):
    """Abstract interface for the State Stream Task Broker."""

    @abstractmethod
    async def create_task(
        self, task_type: TaskType | str, payload: dict[str, Any] | None = None
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
    async def list_stale_running_tasks(
        self, lease_timeout_sec: float = 60.0
    ) -> list[AutomationTask]:
        """List running tasks whose heartbeat has expired."""
        pass

    @abstractmethod
    async def requeue_task(self, task_id: str, retry_count: int) -> AutomationTask:
        """Re-queue an expired task back to PENDING state."""
        pass

    @abstractmethod
    def subscribe_tasks(self, callback: Callable[[str, AutomationTask], Any]) -> None:
        """Register a callback for task lifecycle events (create, update)."""
        pass

    @abstractmethod
    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        """Fetch candidate structured memory profile for a user."""
        pass

    @abstractmethod
    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        """Save or update candidate structured memory profile for a user."""
        pass

    @abstractmethod
    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        """List resume revisions in reverse chronological order."""
        pass

    @abstractmethod
    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        """Record a new resume upload revision."""
        pass

    @abstractmethod
    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        """Check if a job record with the given fingerprint already exists."""
        pass

    @abstractmethod
    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        """Insert a new job record or update last_seen_at/keywords if already present."""
        pass

    @abstractmethod
    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        """Get a job record by ID."""
        pass

    @abstractmethod
    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List job records, optionally filtered by status."""
        pass

    @abstractmethod
    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update the status and optional match results of a job record."""
        pass

    @abstractmethod
    async def list_saved_searches(self) -> list[SavedSearch]:
        """List all saved search presets from PocketBase."""
        pass

    @abstractmethod
    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        """Fetch a saved search preset by ID."""
        pass

    @abstractmethod
    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        """Create or update a saved search preset."""
        pass

    @abstractmethod
    async def delete_saved_search(self, search_id: str) -> bool:
        """Delete a saved search preset by ID."""
        pass


class InMemoryTaskBroker(BaseTaskBroker):
    """Thread-safe & asyncio-safe in-memory broker for tests and local development."""

    def __init__(self) -> None:
        self._tasks: dict[str, AutomationTask] = {}
        self._candidate_profiles: dict[str, dict[str, Any]] = {}
        self._resume_revisions: dict[str, list[dict[str, Any]]] = {}
        self._job_records: dict[str, dict[str, Any]] = {}
        self._job_fingerprints: dict[str, str] = {}
        self._saved_searches: dict[str, SavedSearch] = {}
        self._lock = asyncio.Lock()
        self._subscribers: list[Callable[[str, AutomationTask], Any]] = []
        self._load_local_profile()

    def _load_local_profile(self) -> None:
        from pathlib import Path

        config_path = Path("config/candidate_memory.json")
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                self._candidate_profiles["default"] = data
            except Exception:
                pass

    async def list_saved_searches(self) -> list[SavedSearch]:
        async with self._lock:
            return list(self._saved_searches.values())

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        async with self._lock:
            return self._saved_searches.get(search_id)

    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        async with self._lock:
            self._saved_searches[saved_search.id] = saved_search
            return saved_search

    async def delete_saved_search(self, search_id: str) -> bool:
        async with self._lock:
            if search_id in self._saved_searches:
                del self._saved_searches[search_id]
                return True
            return False

    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        async with self._lock:
            prof = self._candidate_profiles.get(user_id)
            return dict(prof) if prof else None

    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        async with self._lock:
            self._candidate_profiles[user_id] = dict(profile_data)
            return dict(profile_data)

    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        async with self._lock:
            revs = self._resume_revisions.get(user_id, [])
            return [dict(r) for r in sorted(revs, key=lambda x: x.get("created", ""), reverse=True)]

    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        async with self._lock:
            if user_id not in self._resume_revisions:
                self._resume_revisions[user_id] = []
            rev = dict(revision_data)
            rev.setdefault("id", str(uuid.uuid4())[:15])
            rev.setdefault("user_id", user_id)
            rev.setdefault("created", datetime.now(UTC).isoformat())
            self._resume_revisions[user_id].append(rev)
            return rev

    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        async with self._lock:
            return fingerprint in self._job_fingerprints

    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            fingerprint = record_data.get("fingerprint") or compute_job_fingerprint(
                company_name=record_data.get("company_name", ""),
                title=record_data.get("title", ""),
                recruiter_name=record_data.get("recruiter_name", ""),
            )
            now = datetime.now(UTC).isoformat()
            existing_id = self._job_fingerprints.get(fingerprint)
            if existing_id:
                rec = self._job_records[existing_id]
                rec["last_seen_at"] = now
                new_kw = record_data.get("search_keywords", [])
                merged_kw = list(dict.fromkeys((rec.get("search_keywords") or []) + new_kw))
                rec["search_keywords"] = merged_kw
                return dict(rec)

            rec_id = str(record_data.get("id") or uuid.uuid4().hex[:15])
            new_rec: dict[str, Any] = {
                "id": rec_id,
                "fingerprint": fingerprint,
                "title": record_data.get("title", ""),
                "company_name": record_data.get("company_name", ""),
                "recruiter_name": record_data.get("recruiter_name", ""),
                "salary_range": record_data.get("salary_range", ""),
                "location": record_data.get("location", ""),
                "job_description": record_data.get("job_description", ""),
                "status": record_data.get("status", "unmatched"),
                "match_score": record_data.get("match_score"),
                "jd_key_requirements": record_data.get("jd_key_requirements", []),
                "greeting_message": record_data.get("greeting_message", ""),
                "search_keywords": record_data.get("search_keywords", []),
                "source_task_id": record_data.get("source_task_id"),
                "first_seen_at": now,
                "last_seen_at": now,
                "created": now,
                "updated": now,
            }
            self._job_records[rec_id] = new_rec
            self._job_fingerprints[fingerprint] = rec_id
            return dict(new_rec)

    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        async with self._lock:
            rec = self._job_records.get(record_id)
            return dict(rec) if rec else None

    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        async with self._lock:
            records = list(self._job_records.values())
            if status:
                records = [r for r in records if r.get("status") == status]
            records.sort(key=lambda x: str(x.get("created", "")), reverse=True)
            return [dict(r) for r in records[:limit]]

    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            rec = self._job_records.get(record_id)
            if not rec:
                raise KeyError(f"Job record {record_id} not found")
            rec["status"] = status
            if match_data:
                for k, v in match_data.items():
                    rec[k] = v
            rec["updated"] = datetime.now(UTC).isoformat()
            return dict(rec)

    async def create_task(
        self, task_type: TaskType | str, payload: dict[str, Any] | None = None
    ) -> AutomationTask:
        resolved_type = task_type if isinstance(task_type, TaskType) else TaskType(task_type)
        now = datetime.now(UTC)
        task = AutomationTask(
            task_type=resolved_type,
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

    async def list_stale_running_tasks(
        self, lease_timeout_sec: float = 60.0
    ) -> list[AutomationTask]:
        async with self._lock:
            now = datetime.now(UTC)
            stale: list[AutomationTask] = []
            for t in self._tasks.values():
                if t.status == TaskStatus.RUNNING:
                    hb = t.last_heartbeat_at or t.locked_at or t.created
                    # Normalize timezone
                    if hb.tzinfo is None:
                        hb = hb.replace(tzinfo=UTC)
                    if (now - hb).total_seconds() > lease_timeout_sec:
                        stale.append(t.model_copy(deep=True))
            return stale

    async def requeue_task(self, task_id: str, retry_count: int) -> AutomationTask:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"Task {task_id} not found")
            now = datetime.now(UTC)
            task.status = TaskStatus.PENDING
            task.worker_id = None
            task.locked_at = None
            task.last_heartbeat_at = None
            task.retry_count = retry_count
            task.updated = now
            requeued = task.model_copy(deep=True)

        await self._notify_subscribers("update", requeued)
        return requeued

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
        base_url: str | None = None,
        collection_name: str = "automation_tasks",
        auth_token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = resolve_pocketbase_url(explicit_url=base_url)
        self.collection_name = collection_name
        self.auth_token = auth_token or os.getenv("POCKETBASE_AUTH_TOKEN")
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
        self, task_type: TaskType | str, payload: dict[str, Any] | None = None
    ) -> AutomationTask:
        resolved_type = task_type if isinstance(task_type, TaskType) else TaskType(task_type)
        url = self._collection_url()
        body: dict[str, Any] = {
            "id": uuid.uuid4().hex[:15],
            "task_type": resolved_type.value,
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
        try:
            resp = await loop.run_in_executor(
                None, lambda: self.session.get(url, headers=self._headers())
            )
            if resp.status_code == 404:
                logger.warning(
                    "PocketBase query for pending tasks returned 404: collection '%s' may not be provisioned or cached yet: %s",
                    self.collection_name,
                    url,
                )
                return []
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [self._record_to_task(item) for item in items]
        except requests.exceptions.RequestException as e:
            logger.warning("Network or HTTP error fetching pending tasks: %s", e)
            return []

    async def list_stale_running_tasks(
        self, lease_timeout_sec: float = 60.0
    ) -> list[AutomationTask]:
        url = f"{self._collection_url()}?filter=(status='running')&perPage=50"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self.session.get(url, headers=self._headers())
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            items = resp.json().get("items", [])
            tasks = [self._record_to_task(item) for item in items]
            now = datetime.now(UTC)
            stale: list[AutomationTask] = []
            for t in tasks:
                hb = t.last_heartbeat_at or t.locked_at or t.created
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=UTC)
                if (now - hb).total_seconds() > lease_timeout_sec:
                    stale.append(t)
            return stale
        except requests.exceptions.RequestException as e:
            logger.warning("Error fetching stale running tasks: %s", e)
            return []

    async def requeue_task(self, task_id: str, retry_count: int) -> AutomationTask:
        url = f"{self._collection_url()}/{task_id}"
        body = {
            "status": TaskStatus.PENDING.value,
            "worker_id": "",
            "locked_at": None,
            "last_heartbeat_at": None,
            "retry_count": retry_count,
        }
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self.session.patch(url, json=body, headers=self._headers())
        )
        resp.raise_for_status()
        return self._record_to_task(resp.json())

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

    def _candidate_collection_url(self) -> str:
        return f"{self.base_url}/api/collections/candidate_profiles/records"

    def _revisions_collection_url(self) -> str:
        return f"{self.base_url}/api/collections/resume_revisions/records"

    def _get_sqlite_db_path(self) -> Path | None:
        candidate_paths = [
            os.environ.get("PB_DB_PATH"),
            Path(".boss_agent/pb_data/data.db"),
            Path("pb_data/data.db"),
        ]
        for p in candidate_paths:
            if p and Path(p).is_file():
                return Path(p)
        return None

    def _query_sqlite_profile(self, user_id: str) -> dict[str, Any] | None:
        db_path = self._get_sqlite_db_path()
        if not db_path:
            return None
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM candidate_profiles WHERE user_id = ? ORDER BY updated DESC LIMIT 1",
                    (user_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                for json_col in [
                    "education",
                    "core_skills",
                    "project_highlights",
                    "work_experiences",
                    "projects",
                    "target_positions",
                ]:
                    if data.get(json_col) and isinstance(data[json_col], str):
                        with contextlib.suppress(Exception):
                            data[json_col] = json.loads(data[json_col])
                return data
        except Exception as e:
            logger.warning("Failed to read candidate profile from SQLite fallback: %s", e)
            return None

    def _save_sqlite_profile(self, profile_data: dict[str, Any], user_id: str) -> dict[str, Any]:
        db_path = self._get_sqlite_db_path()
        if not db_path:
            return profile_data
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%fZ")
                p_id = profile_data.get("id") or str(uuid.uuid4())[:15]
                cursor.execute(
                    """
                    INSERT INTO candidate_profiles (
                        id, user_id, name, years_of_experience, education, core_skills,
                        project_highlights, work_experiences, projects, target_positions,
                        raw_summary, raw_resume_text, updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        user_id=excluded.user_id,
                        name=excluded.name,
                        years_of_experience=excluded.years_of_experience,
                        education=excluded.education,
                        core_skills=excluded.core_skills,
                        project_highlights=excluded.project_highlights,
                        work_experiences=excluded.work_experiences,
                        projects=excluded.projects,
                        target_positions=excluded.target_positions,
                        raw_summary=excluded.raw_summary,
                        raw_resume_text=excluded.raw_resume_text,
                        updated=excluded.updated
                    """,
                    (
                        p_id,
                        user_id,
                        profile_data.get("name", ""),
                        profile_data.get("years_of_experience", 0),
                        json.dumps(profile_data.get("education", []), ensure_ascii=False),
                        json.dumps(profile_data.get("core_skills", []), ensure_ascii=False),
                        json.dumps(profile_data.get("project_highlights", []), ensure_ascii=False),
                        json.dumps(profile_data.get("work_experiences", []), ensure_ascii=False),
                        json.dumps(profile_data.get("projects", []), ensure_ascii=False),
                        json.dumps(profile_data.get("target_positions", []), ensure_ascii=False),
                        profile_data.get("raw_summary", ""),
                        profile_data.get("raw_resume_text", ""),
                        now,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to save candidate profile to SQLite fallback: %s", e)
        return profile_data

    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        url = self._candidate_collection_url()
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"filter": f"user_id='{user_id}'", "perPage": "1"},
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 404:
                return self._query_sqlite_profile(user_id)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if items:
                return items[0]
            return self._query_sqlite_profile(user_id)
        except Exception as e:
            logger.warning("PocketBase get_candidate_profile failed, fallback to SQLite: %s", e)
            return self._query_sqlite_profile(user_id)

    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        url = self._candidate_collection_url()
        loop = asyncio.get_running_loop()

        # Attempt to save to PocketBase REST API
        try:
            existing = await self.get_candidate_profile(user_id=user_id)
            body = {**profile_data, "user_id": user_id}
            if existing and existing.get("id"):
                rec_id = existing["id"]
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.patch(
                        f"{url}/{rec_id}",
                        json=body,
                        headers=self._headers(),
                    ),
                )
            else:
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.post(
                        url,
                        json=body,
                        headers=self._headers(),
                    ),
                )
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("PocketBase save_candidate_profile failed, fallback to SQLite: %s", e)

        return self._save_sqlite_profile(profile_data, user_id)

    def _query_sqlite_revisions(self, user_id: str) -> list[dict[str, Any]]:
        db_path = self._get_sqlite_db_path()
        if not db_path:
            return []
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM resume_revisions WHERE user_id = ? ORDER BY created DESC",
                    (user_id,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.warning("Failed to query resume revisions from SQLite: %s", e)
            return []

    def _save_sqlite_revision(self, revision_data: dict[str, Any], user_id: str) -> dict[str, Any]:
        db_path = self._get_sqlite_db_path()
        if not db_path:
            return revision_data
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%fZ")
                r_id = revision_data.get("id") or str(uuid.uuid4())[:15]
                cursor.execute(
                    """
                    INSERT INTO resume_revisions (
                        id, user_id, file_name, file_type, file_size, extracted_text, diff_summary, created, updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r_id,
                        user_id,
                        revision_data.get("file_name", "resume.txt"),
                        revision_data.get("file_type", "txt"),
                        revision_data.get("file_size", 0),
                        revision_data.get("extracted_text", ""),
                        revision_data.get("diff_summary", ""),
                        now,
                        now,
                    ),
                )
                conn.commit()
                return {
                    **revision_data,
                    "id": r_id,
                    "user_id": user_id,
                    "created": now,
                    "updated": now,
                }
        except Exception as e:
            logger.warning("Failed to save resume revision to SQLite: %s", e)
            return revision_data

    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        url = self._revisions_collection_url()
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"filter": f"user_id='{user_id}'", "sort": "-created", "perPage": "100"},
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 404:
                return self._query_sqlite_revisions(user_id)
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception as e:
            logger.warning("PocketBase list_resume_revisions failed, fallback to SQLite: %s", e)
            return self._query_sqlite_revisions(user_id)

    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        url = self._revisions_collection_url()
        loop = asyncio.get_running_loop()
        body = {**revision_data, "user_id": user_id}
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.post(
                    url,
                    json=body,
                    headers=self._headers(),
                ),
            )
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("PocketBase create_resume_revision failed, fallback to SQLite: %s", e)
        return self._save_sqlite_revision(body, user_id)

    def _jobs_collection_url(self) -> str:
        return f"{self.base_url}/api/collections/job_records/records"

    def _read_fallback_jobs(self) -> dict[str, dict[str, Any]]:
        from pathlib import Path

        p = Path(".boss_agent/job_records_fallback.json")
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _write_fallback_job(self, record: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path

        try:
            Path(".boss_agent").mkdir(parents=True, exist_ok=True)
            p = Path(".boss_agent/job_records_fallback.json")
            data = self._read_fallback_jobs()
            key = record.get("fingerprint") or record.get("id")
            if not record.get("id"):
                record["id"] = uuid.uuid4().hex[:15]
            if key:
                data[key] = record
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write fallback job record: %s", e)
        return record

    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        url = self._jobs_collection_url()
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"filter": f"fingerprint='{fingerprint}'", "perPage": "1"},
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if len(items) > 0:
                    return True
        except Exception as e:
            logger.warning("PocketBase has_job_fingerprint failed: %s", e)

        fallback = self._read_fallback_jobs()
        return fingerprint in fallback

    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        url = self._jobs_collection_url()
        fingerprint = record_data.get("fingerprint") or compute_job_fingerprint(
            company_name=record_data.get("company_name", ""),
            title=record_data.get("title", ""),
            recruiter_name=record_data.get("recruiter_name", ""),
        )
        now = datetime.now(UTC).isoformat()
        loop = asyncio.get_running_loop()

        fallback_data = self._read_fallback_jobs()
        existing_fallback = fallback_data.get(fingerprint)

        # Check existing by fingerprint in PocketBase
        try:
            check_resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"filter": f"fingerprint='{fingerprint}'", "perPage": "1"},
                    headers=self._headers(),
                ),
            )
            if check_resp.status_code == 200:
                items = check_resp.json().get("items", [])
                if items:
                    existing = items[0]
                    rec_id = existing["id"]
                    new_kw = record_data.get("search_keywords", [])
                    merged_kw = list(dict.fromkeys((existing.get("search_keywords") or []) + new_kw))
                    patch_body = {
                        "last_seen_at": now,
                        "search_keywords": merged_kw,
                    }
                    resp = await loop.run_in_executor(
                        None,
                        lambda: self.session.patch(
                            f"{url}/{rec_id}",
                            json=patch_body,
                            headers=self._headers(),
                        ),
                    )
                    if resp.status_code == 200:
                        res = resp.json()
                        self._write_fallback_job(res)
                        return res
                    return self._write_fallback_job(existing)
        except Exception as e:
            logger.warning("PocketBase check existing job failed: %s", e)

        # If existing in fallback
        if existing_fallback:
            new_kw = record_data.get("search_keywords", [])
            merged_kw = list(dict.fromkeys((existing_fallback.get("search_keywords") or []) + new_kw))
            existing_fallback["last_seen_at"] = now
            existing_fallback["search_keywords"] = merged_kw
            if record_data.get("job_description") and not existing_fallback.get("job_description"):
                existing_fallback["job_description"] = record_data["job_description"]
            if record_data.get("salary_range") and not existing_fallback.get("salary_range"):
                existing_fallback["salary_range"] = record_data["salary_range"]
            return self._write_fallback_job(existing_fallback)

        # Create new job record
        body = {
            "fingerprint": fingerprint,
            "title": record_data.get("title", ""),
            "company_name": record_data.get("company_name", ""),
            "recruiter_name": record_data.get("recruiter_name", ""),
            "salary_range": record_data.get("salary_range", ""),
            "location": record_data.get("location", ""),
            "job_description": record_data.get("job_description", ""),
            "status": record_data.get("status", "unmatched"),
            "match_score": record_data.get("match_score"),
            "jd_key_requirements": record_data.get("jd_key_requirements", []),
            "greeting_message": record_data.get("greeting_message", ""),
            "search_keywords": record_data.get("search_keywords", []),
            "source_task_id": record_data.get("source_task_id"),
            "first_seen_at": now,
            "last_seen_at": now,
            "created": now,
            "updated": now,
        }
        if record_data.get("id"):
            body["id"] = record_data["id"]
        else:
            body["id"] = uuid.uuid4().hex[:15]

        # Always save to local fallback first
        self._write_fallback_job(body)

        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.post(
                    url,
                    json=body,
                    headers=self._headers(),
                ),
            )
            if resp.status_code in (200, 201):
                res = resp.json()
                self._write_fallback_job(res)
                return res
            logger.error(
                "Failed to insert job record to PocketBase (%d): %s (fallback active)",
                resp.status_code,
                resp.text,
            )
        except Exception as e:
            logger.warning("PocketBase insert job record exception: %s (fallback active)", e)

        return body

    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        url = f"{self._jobs_collection_url()}/{record_id}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(url, headers=self._headers()),
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning("PocketBase get_job_record failed: %s", e)

        fallback_data = self._read_fallback_jobs()
        for item in fallback_data.values():
            if item.get("id") == record_id or item.get("fingerprint") == record_id:
                return item
        return None

    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        url = self._jobs_collection_url()
        params: dict[str, Any] = {"sort": "-created", "perPage": str(limit)}
        if status:
            params["filter"] = f"status='{status}'"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(url, params=params, headers=self._headers()),
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    return items
        except Exception as e:
            logger.warning("PocketBase list_job_records failed: %s", e)

        # Fallback to local store
        fallback_data = self._read_fallback_jobs()
        items = list(fallback_data.values())
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda x: str(x.get("created") or x.get("last_seen_at") or ""), reverse=True)
        return items[:limit]

    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._jobs_collection_url()}/{record_id}"
        body: dict[str, Any] = {"status": status}
        if match_data:
            for k, v in match_data.items():
                body[k] = v

        # Update fallback
        fallback_data = self._read_fallback_jobs()
        target_fp = None
        for fp, item in fallback_data.items():
            if item.get("id") == record_id or item.get("fingerprint") == record_id:
                target_fp = fp
                item.update(body)
                item["updated"] = datetime.now(UTC).isoformat()
                self._write_fallback_job(item)
                break

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.patch(url, json=body, headers=self._headers()),
            )
            if resp.status_code == 200:
                res = resp.json()
                self._write_fallback_job(res)
                return res
        except Exception as e:
            logger.warning("PocketBase update_job_record_status failed: %s", e)

        if target_fp and target_fp in fallback_data:
            return fallback_data[target_fp]
        return {"id": record_id, **body}

    def _saved_searches_collection_url(self) -> str:
        return f"{self.base_url}/api/collections/saved_searches/records"

    async def list_saved_searches(self) -> list[SavedSearch]:
        url = self._saved_searches_collection_url()
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"perPage": "200", "sort": "-created"},
                    headers=self._headers(),
                ),
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [SavedSearch.from_dict(item["id"], item) for item in items]
        except Exception as e:
            logger.warning("PocketBase list_saved_searches failed: %s", e)
            return []

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        url = f"{self._saved_searches_collection_url()}/{search_id}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return SavedSearch.from_dict(data["id"], data)
        except Exception as e:
            logger.warning("PocketBase get_saved_search for %s failed: %s", search_id, e)
            return None

    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        url = self._saved_searches_collection_url()
        loop = asyncio.get_running_loop()
        body = {
            "id": saved_search.id,
            "name": saved_search.name,
            "description": saved_search.description,
            "keyword": saved_search.search.keyword,
            "enable_search": saved_search.enable_search,
            "enable_filter": saved_search.enable_filter,
            "filter": {
                "education": saved_search.filter.education,
                "salary": saved_search.filter.salary,
                "experience": saved_search.filter.experience,
                "activity": saved_search.filter.activity,
                "company_scales": saved_search.filter.company_scales,
                "industries": saved_search.filter.industries,
                "enable_filter": saved_search.enable_filter,
            },
            "cron_expression": saved_search.cron_expression,
            "is_enabled": saved_search.is_enabled,
            "last_run_at": saved_search.last_run_at,
            "target_task_type": saved_search.target_task_type,
        }
        try:
            existing = await self.get_saved_search(saved_search.id)
            if existing:
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.patch(
                        f"{url}/{saved_search.id}",
                        json=body,
                        headers=self._headers(),
                    ),
                )
            else:
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.post(
                        url,
                        json=body,
                        headers=self._headers(),
                    ),
                )
            if resp.ok:
                data = resp.json()
                return SavedSearch.from_dict(data["id"], data)
        except Exception as e:
            logger.warning("PocketBase save_saved_search failed: %s", e)
        return saved_search

    async def delete_saved_search(self, search_id: str) -> bool:
        url = f"{self._saved_searches_collection_url()}/{search_id}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.delete(
                    url,
                    headers=self._headers(),
                ),
            )
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.warning("PocketBase delete_saved_search failed: %s", e)
            return False


PocketBaseBroker = PocketBaseTaskBroker

