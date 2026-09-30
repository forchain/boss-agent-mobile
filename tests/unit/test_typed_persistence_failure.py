"""
tests/unit/test_typed_persistence_failure.py
============================================
Fast-tier unit tests for typed persistence failure across JobRecordStore and
CandidateMemoryStore (Ticket #307, ADR 0013).

Proves that:
1. Broker / transport failure raises TransportError rather than returning fabricated
   empty data (0 for quota, empty set for exclusion pool, None for absent profile).
2. Legitimate record absence still returns None or empty values cleanly.
3. Automation Worker handlers and pipeline record degradation through task logs
   instead of silently continuing on empty data.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.candidate_memory_store import PocketBaseCandidateMemoryStore
from boss_agent.errors import TransportError
from boss_agent.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.job_store import PocketBaseJobRecordStore
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler


class FailingSession:
    """Mock requests session simulating broker network or server failures."""

    def __init__(self, failure_type: str = "network", status_code: int = 500) -> None:
        self.failure_type = failure_type
        self.status_code = status_code

    def get(self, url: str, *args, **kwargs):
        if self.failure_type == "network":
            raise requests.ConnectionError("Connection refused by broker")
        resp = MagicMock(spec=requests.Response)
        resp.status_code = self.status_code
        resp.text = f"Broker error: {self.status_code}"
        return resp

    def post(self, url: str, *args, **kwargs):
        if self.failure_type == "network":
            raise requests.ConnectionError("Connection refused by broker")
        resp = MagicMock(spec=requests.Response)
        resp.status_code = self.status_code
        resp.text = f"Broker error: {self.status_code}"
        return resp

    def patch(self, url: str, *args, **kwargs):
        if self.failure_type == "network":
            raise requests.ConnectionError("Connection refused by broker")
        resp = MagicMock(spec=requests.Response)
        resp.status_code = self.status_code
        resp.text = f"Broker error: {self.status_code}"
        return resp

    def delete(self, url: str, *args, **kwargs):
        if self.failure_type == "network":
            raise requests.ConnectionError("Connection refused by broker")
        resp = MagicMock(spec=requests.Response)
        resp.status_code = self.status_code
        resp.text = f"Broker error: {self.status_code}"
        return resp


@pytest.mark.asyncio
async def test_job_record_store_raises_transport_error_on_network_failure():
    """Quota and exclusion pool reads must raise TransportError on network failure."""
    store = PocketBaseJobRecordStore(
        base_url="http://127.0.0.1:8090",
        session=FailingSession(failure_type="network"),
        headers=lambda: {},
    )

    with pytest.raises(TransportError, match="count_today_applied_jobs failed"):
        await store.count_today_applied_jobs()

    with pytest.raises(TransportError, match="get_applied_direct_companies failed"):
        await store.get_applied_direct_companies()

    with pytest.raises(TransportError, match="get_job_record_by_fingerprint failed"):
        await store.get_job_record_by_fingerprint("fp-123")

    with pytest.raises(TransportError, match="get_job_record rec-123 failed"):
        await store.get_job_record("rec-123")


@pytest.mark.asyncio
async def test_job_record_store_raises_transport_error_on_5xx_server_error():
    """Quota and exclusion pool reads must raise TransportError on HTTP 500 error."""
    store = PocketBaseJobRecordStore(
        base_url="http://127.0.0.1:8090",
        session=FailingSession(failure_type="http", status_code=502),
        headers=lambda: {},
    )

    with pytest.raises(TransportError, match="502 Server Error"):
        await store.count_today_applied_jobs()

    with pytest.raises(TransportError, match="502 Server Error"):
        await store.get_applied_direct_companies()


@pytest.mark.asyncio
async def test_candidate_memory_store_raises_transport_error_without_sqlite(tmp_path: Path):
    """Profile read must raise TransportError on network drop when no SQLite fallback exists."""
    store = PocketBaseCandidateMemoryStore(
        base_url="http://127.0.0.1:8090",
        session=FailingSession(failure_type="network"),
        headers=lambda: {},
        sqlite_db_path=tmp_path / "nonexistent.db",
    )

    with pytest.raises(TransportError, match="Connection refused by broker"):
        await store.get_candidate_profile("user-123")

    with pytest.raises(TransportError, match="Connection refused by broker"):
        await store.save_candidate_profile({"name": "Test"}, "user-123")


@pytest.mark.asyncio
async def test_candidate_memory_store_returns_none_on_legitimate_absence(tmp_path: Path):
    """A profile absent on the server returns None without error when the broker is reachable."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = {"items": []}

    session = MagicMock()
    session.get.return_value = resp

    store = PocketBaseCandidateMemoryStore(
        base_url="http://127.0.0.1:8090",
        session=session,
        headers=lambda: {},
        sqlite_db_path=tmp_path / "nonexistent.db",
    )

    profile = await store.get_candidate_profile("nonexistent-user")
    assert profile is None


@pytest.mark.asyncio
async def test_auto_apply_handler_logs_degradation_on_profile_transport_error():
    """AutoApplyHandler must log persistence degradation and refuse to run on fabricated empty profile."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={"keyword": "AI"},
    )

    # Stub candidate memory store raising TransportError
    broker.candidate_memory.get_candidate_profile = AsyncMock(
        side_effect=TransportError("PocketBase unreachable: 503")
    )

    from boss_agent.worker.config import WorkerConfig

    context = WorkerContext(config=WorkerConfig(), driver=MagicMock())
    handler = AutoApplyHandler()

    result = await handler.handle(task, broker, context)
    assert result.success is False
    assert "Persistence degradation" in (result.error_message or "")

    cur_task = await broker.get_task(task.id)
    assert cur_task is not None
    assert any("⚠️ [持久化降级] 候选人画像读取遇到持久化异常" in log for log in cur_task.logs)


@pytest.mark.asyncio
async def test_auto_apply_handler_logs_degradation_on_exclusion_pool_transport_error():
    """AutoApplyHandler preflight must log degradation and refuse to apply if exclusion pool read fails."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "AI",
            "company_name": "腾讯科技",
            "job_title": "AI 工程师",
            "is_headhunter": False,
        },
    )

    # Stub exclusion pool read raising TransportError
    broker.job_store.get_applied_direct_companies = AsyncMock(
        side_effect=TransportError("Database connection lost")
    )

    from boss_agent.worker.config import WorkerConfig

    context = WorkerContext(config=WorkerConfig(), driver=MagicMock())
    handler = AutoApplyHandler()

    result = await handler.handle(task, broker, context)
    assert result.success is False
    assert "Persistence degradation" in (result.error_message or "")

    cur_task = await broker.get_task(task.id)
    assert cur_task is not None
    assert any("⚠️ [持久化降级] 直招避嫌池读取遇到持久化异常" in log for log in cur_task.logs)


@pytest.mark.asyncio
async def test_feed_pipeline_logs_degradation_on_quota_transport_error():
    """JobFeedPipeline quota check must log degradation when count_today_applied_jobs fails."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={},
    )

    broker.job_store.count_today_applied_jobs = AsyncMock(
        side_effect=TransportError("PocketBase 500 error")
    )

    pipeline = JobFeedPipeline.for_task(
        broker=broker,
        task_id=task.id,
        driver=MagicMock(),
        screener=MagicMock(),
    )

    from boss_agent.feed_pipeline import FeedStreamResult, _CardRun

    run = _CardRun(
        config=FeedStreamConfig(),
        result=FeedStreamResult(),
    )

    with pytest.raises(TransportError):
        await pipeline._quota_exhausted(run)

    cur_task = await broker.get_task(task.id)
    assert cur_task is not None
    assert any("⚠️ [持久化降级] 今日投递额度查询遇到持久化异常" in log for log in cur_task.logs)
