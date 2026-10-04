"""
tests/unit/test_worker_startup_channel_log.py
=============================================
The worker must say which recruitment channel a run is filtering on (issue #368,
ticket #369, user story #9).

The startup header is the one line an operator reads when a strategy behaved
unexpectedly, and the channel was previously invisible there: a run that silently
rejected every headhunter posting looked identical to one that scanned everything. So the
effective — not merely configured — preference is announced by both search handlers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.feed_pipeline import FeedStreamResult
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def context():
    return WorkerContext(config=WorkerConfig(worker_id="test-channel-log"), driver=MagicMock())


def _empty_result() -> FeedStreamResult:
    return FeedStreamResult()


async def _run(handler, broker, context, payload) -> str:
    """Drive one handler end-to-end with the device pipeline stubbed out."""
    task = await broker.create_task(task_type=handler.task_type, payload=payload)
    with patch.object(handler_module(handler), "JobFeedPipeline") as pipeline:
        pipeline.for_task.return_value.stream_jobs = AsyncMock(return_value=_empty_result())
        await handler.handle(task, broker, context)
    finished = await broker.get_task(task.id)
    startup = [line for line in finished.logs if "Starting" in line]
    assert startup, f"no startup header was logged: {finished.logs}"
    return startup[0]


def handler_module(handler):
    import boss_agent.worker.handlers.auto_apply as auto_apply_module
    import boss_agent.worker.handlers.scrape_jobs as scrape_jobs_module

    return auto_apply_module if isinstance(handler, AutoApplyHandler) else scrape_jobs_module


@pytest.mark.parametrize("handler_type", [AutoApplyHandler, ScrapeJobsHandler])
@pytest.mark.asyncio
async def test_startup_log_announces_the_strategy_channel(handler_type, broker, context):
    startup = await _run(
        handler_type(llm_client=MagicMock()),
        broker,
        context,
        {
            "keyword": "AI Agent",
            "search_name": "直招专注",
            "filter": {"channel_preference": "direct_only"},
            "screening_policy": {"channel_preference": "all"},
        },
    )

    assert "channel='direct_only'" in startup


@pytest.mark.parametrize("handler_type", [AutoApplyHandler, ScrapeJobsHandler])
@pytest.mark.asyncio
async def test_startup_log_announces_the_effective_inherited_channel(handler_type, broker, context):
    """No strategy override: the header still states the global policy that will run.

    A silent channel is how this gap started — a run that rejected headhunter postings
    gave no hint why, and the only way to tell was to read the config file afterwards.
    """
    startup = await _run(
        handler_type(llm_client=MagicMock()),
        broker,
        context,
        {
            "keyword": "AI Agent",
            "screening_policy": {"channel_preference": "headhunter_only"},
        },
    )

    assert "channel='headhunter_only'" in startup


@pytest.mark.parametrize("handler_type", [AutoApplyHandler, ScrapeJobsHandler])
@pytest.mark.asyncio
async def test_startup_log_keeps_its_existing_fields(handler_type, broker, context):
    """The channel is added to the header, not substituted for anything in it.

    The two headers state different fields (AUTO_APPLY a candidate, a mode and a depth;
    SCRAPE_JOBS a target action and a job ceiling), so only the two both already carried
    are asserted here — enough to show the channel was appended rather than swapped in.
    """
    startup = await _run(
        handler_type(llm_client=MagicMock()),
        broker,
        context,
        {
            "keyword": "AI Agent",
            "search_name": "直招专注",
            "filter": {"channel_preference": "direct_only"},
            "min_score": 80,
        },
    )

    assert "keyword='AI Agent'" in startup
    assert "strategy='直招专注'" in startup
    assert "channel='direct_only'" in startup
