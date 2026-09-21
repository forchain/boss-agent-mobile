"""
tests/unit/test_search_initiation_flow.py
=========================================
Unit tests for worker handler search initiation (Issue #196).

Search initiation must delegate straight to the fast two-anchor entry engine
(``JobListPage.open_search``): no redundant pre-navigation delay, no second
``navigate_to_home`` between attempts, and retry logging that reports the
current attempt and marks exhaustion instead of promising another retry.
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.pages import JobListPage, SearchPage
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def context():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    driver.find_elements.return_value = []
    config = WorkerConfig(worker_id="test-search-flow", poll_interval_sec=0.01)
    return WorkerContext(config=config, driver=driver)


def _patch_search_layer(search_ok: bool = True, entry_ok: bool = True):
    """Patch the page layer so handler-level flow decisions can be observed."""
    return (
        patch.object(JobListPage, "navigate_to_home", autospec=True, return_value=True),
        patch.object(JobListPage, "open_search", autospec=True, return_value=entry_ok),
        patch.object(SearchPage, "is_search_page", autospec=True, return_value=False),
        patch.object(SearchPage, "search", autospec=True, return_value=search_ok),
    )


@pytest.mark.asyncio
async def test_scrape_jobs_delegates_search_to_fast_entry_without_pre_navigation(broker, context):
    handler = ScrapeJobsHandler()
    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "agent", "max_jobs": 1}
    )

    home_patch, open_patch, is_search_patch, search_patch = _patch_search_layer()
    with home_patch as spy_home, open_patch as spy_open, is_search_patch, search_patch as spy_search:
        await handler.handle(task, broker, context)

    assert spy_search.call_count == 1
    assert spy_open.call_count == 1, "handler must delegate entry to JobListPage.open_search"
    assert spy_home.call_count == 0, (
        "search initiation must not run a redundant navigate_to_home() first"
    )


@pytest.mark.asyncio
async def test_scrape_jobs_retry_logging_marks_exhaustion_and_never_lies(broker, context):
    handler = ScrapeJobsHandler()
    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "agent", "max_jobs": 1}
    )

    home_patch, open_patch, is_search_patch, search_patch = _patch_search_layer(
        search_ok=False, entry_ok=False
    )
    with home_patch as spy_home, open_patch, is_search_patch, search_patch:
        result = await handler.handle(task, broker, context)

    assert result.success is False
    finished = await broker.get_task(task.id)
    logs = "\n".join(finished.logs)

    assert "第 1/2 次" in logs, f"each attempt must report its index, got:\n{logs}"
    assert "正在重试" not in logs, f"must not promise a retry it will not make:\n{logs}"
    assert "耗尽" in logs, f"terminal attempt must be marked as exhausted:\n{logs}"
    assert spy_home.call_count == 0, "retry loop must not interleave navigate_to_home() calls"


@pytest.mark.asyncio
async def test_auto_apply_delegates_search_to_fast_entry_without_pre_navigation(broker, context):
    llm_client = MagicMock()
    llm_client.chat_completion_json.return_value = {"match_score": 50, "greeting_message": "hi"}
    handler = AutoApplyHandler(llm_client=llm_client)
    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "agent",
            "min_score": 75,
            "candidate_profile": {"name": "Candidate", "core_skills": ["Python"]},
        },
    )

    home_patch, open_patch, is_search_patch, search_patch = _patch_search_layer()
    with home_patch as spy_home, open_patch as spy_open, is_search_patch, search_patch:
        await handler.handle(task, broker, context)

    assert spy_open.call_count == 1, "handler must delegate entry to JobListPage.open_search"
    assert spy_home.call_count == 0, (
        "search initiation must not run a redundant navigate_to_home() first"
    )
