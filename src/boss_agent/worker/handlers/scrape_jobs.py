"""
src/boss_agent/worker/handlers/scrape_jobs.py
=============================================
Handler for SCRAPE_JOBS task: searches, filters, and extracts structured job postings.
"""

from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.models import FilterConfig
from boss_agent.pages import (
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobDetailPage,
    JobListPage,
    SearchPage,
    StartupDialogPage,
)
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult


class ScrapeJobsHandler(BaseTaskHandler):
    """Executes search, filtering, and job posting extraction."""

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
        keyword = payload.get("keyword")
        max_jobs = int(payload.get("max_jobs", 3))

        await broker.append_log(
            task.id, f"Starting SCRAPE_JOBS (keyword='{keyword}', max_jobs={max_jobs})"
        )

        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        list_page = JobListPage(driver)
        list_page.navigate_to_home()

        enable_search = bool(payload.get("enable_search", True))
        if enable_search and keyword:
            search_page = SearchPage(driver)
            if not search_page.is_search_page():
                list_page.open_search(timeout_sec=5.0)
            search_page.search(keyword)
            await broker.append_log(task.id, f"Executed search for keyword '{keyword}'")
        elif not enable_search:
            await broker.append_log(task.id, "enable_search is False; browsing home recommendations without search keyword")

        enable_filter = bool(payload.get("enable_filter", True))
        filter_raw = payload.get("filter")
        if enable_filter and isinstance(filter_raw, dict):
            filter_cfg = FilterConfig(
                education=filter_raw.get("education"),
                salary=filter_raw.get("salary"),
                experience=filter_raw.get("experience"),
                activity=filter_raw.get("activity"),
                company_scales=filter_raw.get("company_scales", []),
                industries=filter_raw.get("industries", []),
                enable_filter=True,
            )
            if filter_cfg.has_industry_filters:
                industry_page = IndustryFilterDialogPage(driver)
                await broker.append_log(task.id, f"Applying industry filter: {filter_cfg.industries}")
                try:
                    industry_page.apply_industry_filters(filter_cfg.industries, timeout_sec=5.0)
                except Exception as ex:
                    await broker.append_log(task.id, f"Notice applying industry filter: {ex}")

            if filter_cfg.has_filters:
                filter_page = FilterDialogPage(driver)
                await broker.append_log(
                    task.id,
                    f"Applying general filters: education={filter_cfg.education}, salary={filter_cfg.salary}, experience={filter_cfg.experience}",
                )
                try:
                    filter_page.apply_filters(filter_cfg, timeout_sec=5.0)
                except Exception as ex:
                    await broker.append_log(task.id, f"Notice applying general filters: {ex}")
        elif not enable_filter:
            await broker.append_log(task.id, "enable_filter is False; skipped job filtering")


        scraped_jobs: list[dict[str, Any]] = []
        detail_page = JobDetailPage(driver)

        try:
            job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
            job_data = {
                "title": job_posting.title,
                "company_name": job_posting.company_name,
                "salary_range": job_posting.salary_range,
                "job_description": job_posting.job_description,
            }
            scraped_jobs.append(job_data)
            await broker.append_log(
                task.id,
                f"Extracted job: {job_posting.title} @ {job_posting.company_name} ({job_posting.salary_range})",
            )
        except Exception as e:
            await broker.append_log(task.id, f"Notice on job extraction: {e}")

        await broker.append_log(task.id, f"Finished scraping {len(scraped_jobs)} job postings")
        return HandlerResult(
            success=True,
            output={"scraped_count": len(scraped_jobs), "jobs": scraped_jobs},
        )
