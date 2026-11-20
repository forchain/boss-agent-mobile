"""
tests/unit/test_store_port_injection.py
=======================================
Contract tests for explicit store port injection (Candidate 2 / Ticket #471):
1. Pure BaseTaskBroker doubles without store attributes work with handlers and scheduler.
2. _TaskLoggingBrokerProxy exposes only BaseTaskBroker verbs (no __getattr__ store forwarding).
3. build_resume_memory_manager & build_resume_pipeline do not construct PocketBaseTaskBroker
   (and therefore carry zero broker file-cleanup side effects).
4. Resume lifecycle graph accepts CandidateMemoryStore directly without a broker.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from _feed_harness import build_feed_device_world

from boss_agent.adapters.inbound.worker.config import WorkerConfig
from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.inbound.worker.daemon import _TaskLoggingBrokerProxy
from boss_agent.adapters.inbound.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.adapters.inbound.worker.handlers.scrape_jobs import ScrapeJobsHandler
from boss_agent.adapters.inbound.worker.scheduler import AutomationScheduler
from boss_agent.adapters.memory.candidate_memory_store import InMemoryCandidateMemoryStore
from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore
from boss_agent.adapters.memory.saved_search_store import InMemorySavedSearchStore
from boss_agent.adapters.memory.task_broker import InMemoryTaskBroker
from boss_agent.adapters.outbound.config.settings import load_feed_config_defaults
from boss_agent.adapters.outbound.resume.graph import run_resume_lifecycle_graph
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.search.domain.models import FilterConfig, SavedSearch, SearchConfig
from boss_agent.core.task.domain.models import AutomationTask, TaskStatus, TaskType
from boss_agent.core.task.interfaces.broker import BaseTaskBroker
from boss_agent.core.task.use_cases.startup_cleanup import StartupCleanupGate


class _PureTaskBroker(BaseTaskBroker):
    """Minimal BaseTaskBroker with ZERO store attributes."""

    def __init__(self) -> None:
        self._delegate = InMemoryTaskBroker()

    async def create_task(self, task_type, payload=None, source="manual"):
        return await self._delegate.create_task(task_type, payload=payload, source=source)

    async def claim_task(self, task_id, worker_id):
        return await self._delegate.claim_task(task_id, worker_id)

    async def get_task(self, task_id):
        return await self._delegate.get_task(task_id)

    async def update_heartbeat(self, task_id, worker_id):
        return await self._delegate.update_heartbeat(task_id, worker_id)

    async def update_task_status(
        self, task_id, status, logs=None, error_message=None, worker_id=None
    ):
        return await self._delegate.update_task_status(
            task_id, status, logs=logs, error_message=error_message, worker_id=worker_id
        )

    async def append_log(self, task_id, log_line):
        return await self._delegate.append_log(task_id, log_line)

    async def flush_logs(self, task_id=None):
        await self._delegate.flush_logs(task_id)

    async def list_pending_tasks(self, limit=10):
        return await self._delegate.list_pending_tasks(limit=limit)

    async def list_stale_running_tasks(self, lease_timeout_sec=60.0):
        return await self._delegate.list_stale_running_tasks(lease_timeout_sec=lease_timeout_sec)

    async def requeue_task(self, task_id, retry_count):
        return await self._delegate.requeue_task(task_id, retry_count)

    def subscribe_tasks(self, callback):
        self._delegate.subscribe_tasks(callback)


def test_task_logging_broker_proxy_has_no_getattr_store_forwarding() -> None:
    broker = InMemoryTaskBroker()
    proxy = _TaskLoggingBrokerProxy(broker)
    assert isinstance(proxy, BaseTaskBroker)
    assert not hasattr(_TaskLoggingBrokerProxy, "__getattr__")
    for hidden in ("job_store", "candidate_memory", "saved_searches"):
        assert not hasattr(proxy, hidden)


@pytest.mark.asyncio
async def test_scheduler_works_with_pure_task_broker_and_injected_saved_search_store() -> None:
    pure_broker = _PureTaskBroker()
    saved_searches = InMemorySavedSearchStore()
    await saved_searches.save_saved_search(
        SavedSearch(
            id="s1",
            name="Agent岗",
            search=SearchConfig(keyword="Agent"),
            filter=FilterConfig(),
            cron_expression="0 9 * * *",
            is_enabled=True,
            target_task_type="scrape_jobs",
        )
    )
    scheduler = AutomationScheduler(
        broker=pure_broker,
        saved_searches=saved_searches,
        startup_gate=StartupCleanupGate(pure_broker, enabled=False),
    )

    tasks = await scheduler.run_once(now=datetime(2026, 9, 7, 9, 0, tzinfo=UTC))
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.SCRAPE_JOBS


@pytest.mark.asyncio
async def test_handlers_work_with_pure_task_broker_and_injected_stores() -> None:
    pure_broker = _PureTaskBroker()
    job_store = InMemoryJobRecordStore()
    candidate_memory = InMemoryCandidateMemoryStore(load_local_profile=False)
    await candidate_memory.save_candidate_profile(
        {"name": "张三", "core_skills": ["Python"], "target_positions": ["AI工程师"]}
    )

    scrape_handler = ScrapeJobsHandler(
        screener=CandidateScreener(llm_client=MagicMock()),
        pages=build_feed_device_world,
        job_store=job_store,
        feed_config_defaults_loader=load_feed_config_defaults,
    )
    apply_handler = AutoApplyHandler(
        screener=CandidateScreener(llm_client=MagicMock()),
        pages=build_feed_device_world,
        job_store=job_store,
        candidate_memory=candidate_memory,
        feed_config_defaults_loader=load_feed_config_defaults,
    )

    task = AutomationTask(
        id="t1",
        task_type=TaskType.AUTO_APPLY,
        status=TaskStatus.RUNNING,
        payload={
            "keyword": "AI",
            "company_name": "测试公司",
            "job_title": "AI 工程师",
        },
    )
    # No driver -> clean failure without touching broker attributes
    ctx = WorkerContext(config=WorkerConfig(), driver=None)
    res_scrape = await scrape_handler.handle(task, pure_broker, ctx)
    res_apply = await apply_handler.handle(task, pure_broker, ctx)
    assert res_scrape.success is False
    assert res_apply.success is False


def test_build_resume_memory_manager_avoids_broker_construction_and_cleanup(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    fallback_dir = tmp_path / ".boss_agent"
    fallback_dir.mkdir()
    fallback_file = fallback_dir / "job_records_fallback.json"
    fallback_file.write_text("{}", encoding="utf-8")

    from boss_agent import main as main_mod

    def _forbid_broker(*_args, **_kwargs):
        raise AssertionError("PocketBaseTaskBroker must not be constructed for profile reading")

    monkeypatch.setattr(main_mod, "PocketBaseTaskBroker", _forbid_broker)

    manager = main_mod.build_resume_memory_manager(llm_client=MagicMock())
    assert manager is not None
    assert fallback_file.exists(), "Standalone profile reader must not run broker file cleanup"


def test_resume_lifecycle_graph_accepts_candidate_memory_without_broker() -> None:
    store = InMemoryCandidateMemoryStore(load_local_profile=False)
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "name": "赵敏",
        "years_of_experience": 5,
        "core_skills": ["Python"],
        "target_positions": ["后端工程师"],
        "profile_document": "# 画像\n赵敏",
        "raw_summary": "# 画像\n赵敏",
    }

    result = run_resume_lifecycle_graph(
        raw_resume_text="赵敏，5年Python后端经验",
        file_name="zhaomin.txt",
        user_id="default",
        llm_client=llm,
        candidate_memory=store,
    )
    assert result["status"] == "completed"
    assert result["final_profile"]["name"] == "赵敏"
