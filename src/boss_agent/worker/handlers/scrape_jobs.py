"""
src/boss_agent/worker/handlers/scrape_jobs.py
=============================================
Handler for SCRAPE_JOBS: streams screened job postings out of the mobile search feed.

The feed navigation, card screening, JD extraction and persistence all live in the
deep ``JobFeedPipeline`` (ADR 0013); this handler only configures a run and reports
its result.
"""

import logging
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.models import TargetAction
from boss_agent.screening import CandidateScreener
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger(__name__)


class ScrapeJobsHandler(BaseTaskHandler):
    """Executes search, screening, and job posting extraction."""

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client

    @property
    def task_type(self) -> TaskType:
        return TaskType.SCRAPE_JOBS

    async def handle(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        context: WorkerContext,
    ) -> HandlerResult:
        driver = context.driver
        if not driver:
            await broker.append_log(task.id, "Error: No driver session initialized")
            return HandlerResult(success=False, error_message="Driver session is unavailable")

        payload = task.payload or {}
        config = FeedStreamConfig.from_payload(payload)
        config.source_task_id = task.id
        # The task type names the action: a SCRAPE_JOBS task never sends a greeting.
        config.target_action = TargetAction.SAVE_JD

        search_name = payload.get("search_name") or payload.get("saved_search_name") or ""
        strategy_desc = f"strategy='{search_name}', " if search_name else ""
        await broker.append_log(
            task.id,
            f"Starting SCRAPE_JOBS ({strategy_desc}keyword='{config.keyword}', "
            f"target_action='{config.target_action.value}', max_jobs={config.max_jobs})",
        )

        pipeline = JobFeedPipeline.for_task(
            broker=broker,
            task_id=task.id,
            driver=driver,
            screener=CandidateScreener(llm_client=self.llm_client),
        )
        result = await pipeline.stream_jobs(config)

        if result.search_failed:
            return HandlerResult(
                success=False,
                output={"error": result.error_message, "search_failed": True},
            )

        scraped = len(result.jobs)
        if result.cancelled:
            summary = (
                f"Finished scraping: task was cancelled by user (scanned {result.scanned}, "
                f"scraped {scraped}, skipped {result.skipped})"
            )
        else:
            summary = (
                f"Finished scraping: scanned {result.scanned}, scraped {scraped} job postings, "
                f"skipped {result.skipped}"
            )
        await broker.append_log(task.id, summary)

        return HandlerResult(
            success=True,
            output={
                "total_scanned": result.scanned,
                "total_scraped": scraped,
                "scraped_count": scraped,
                "skipped_count": result.skipped,
                "scraped_jobs": result.jobs,
                "jobs": result.jobs,
            },
        )
