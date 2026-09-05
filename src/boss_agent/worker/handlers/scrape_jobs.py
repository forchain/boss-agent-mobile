"""
src/boss_agent/worker/handlers/scrape_jobs.py
=============================================
Handler for SCRAPE_JOBS task: searches, filters, and extracts structured job postings.
"""

from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.pages import JobDetailPage, JobListPage, SearchPage, StartupDialogPage
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

        if keyword:
            search_page = SearchPage(driver)
            if not search_page.is_search_page():
                list_page.open_search(timeout_sec=5.0)
            search_page.search(keyword)
            await broker.append_log(task.id, f"Executed search for keyword '{keyword}'")

        scraped_jobs: list[dict[str, Any]] = []
        skipped_count = 0
        detail_page = JobDetailPage(driver)

        visible_cards = list_page.extract_visible_job_cards(max_cards=max_jobs * 2)
        total_scanned = len(visible_cards)

        if visible_cards:
            for card in visible_cards:
                if len(scraped_jobs) >= max_jobs:
                    break

                # 1. Card-level deduplication check
                is_duplicate = await broker.has_job_fingerprint(card.fingerprint)
                if is_duplicate:
                    skipped_count += 1
                    await broker.append_log(
                        task.id,
                        f"⏭️ [Dedup] Skipping duplicate job: '{card.title}' @ '{card.company_name}' ({card.recruiter_name})",
                    )
                    continue

                # 2. New job: enter detail page
                await broker.append_log(
                    task.id,
                    f"🔍 [New Job] Inspecting: '{card.title}' @ '{card.company_name}' ({card.recruiter_name})",
                )
                clicked = False
                if card.element and hasattr(card.element, "click"):
                    try:
                        card.element.click()
                        clicked = True
                    except Exception:
                        pass
                if not clicked:
                    clicked = list_page.select_first_job(timeout_sec=3.0)

                try:
                    job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
                    record_data = {
                        "fingerprint": card.fingerprint,
                        "title": job_posting.title or card.title,
                        "company_name": job_posting.company_name or card.company_name,
                        "recruiter_name": card.recruiter_name or job_posting.recruiter_name or "招聘者",
                        "salary_range": job_posting.salary_range,
                        "location": job_posting.location,
                        "job_description": job_posting.job_description,
                        "status": "unmatched",
                        "search_keywords": [keyword] if keyword else [],
                        "source_task_id": task.id,
                    }
                    persisted = await broker.upsert_job_record(record_data)
                    scraped_jobs.append(persisted)
                    await broker.append_log(
                        task.id,
                        f"✅ Extracted & ingested job: {persisted['title']} @ {persisted['company_name']} ({persisted.get('salary_range', '')})",
                    )
                except Exception as e:
                    await broker.append_log(task.id, f"Notice on job extraction: {e}")
                finally:
                    detail_page.navigate_back()
        else:
            # Fallback for mock environments or direct detail view
            total_scanned = 1
            try:
                job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
                from boss_agent.models import compute_job_fingerprint

                fp = compute_job_fingerprint(
                    job_posting.company_name, job_posting.title, job_posting.recruiter_name or "招聘者"
                )
                if await broker.has_job_fingerprint(fp):
                    skipped_count += 1
                else:
                    persisted = await broker.upsert_job_record(
                        {
                            "fingerprint": fp,
                            "title": job_posting.title,
                            "company_name": job_posting.company_name,
                            "recruiter_name": job_posting.recruiter_name or "招聘者",
                            "salary_range": job_posting.salary_range,
                            "location": job_posting.location,
                            "job_description": job_posting.job_description,
                            "status": "unmatched",
                            "search_keywords": [keyword] if keyword else [],
                            "source_task_id": task.id,
                        }
                    )
                    scraped_jobs.append(persisted)
                    await broker.append_log(
                        task.id,
                        f"✅ Extracted job: {job_posting.title} @ {job_posting.company_name} ({job_posting.salary_range})",
                    )
            except Exception as e:
                await broker.append_log(task.id, f"Notice on job extraction: {e}")

        summary = f"Finished scraping: scanned {total_scanned}, scraped {len(scraped_jobs)} job postings, skipped {skipped_count}"
        await broker.append_log(task.id, summary)
        return HandlerResult(
            success=True,
            output={
                "total_scanned": total_scanned,
                "scraped_count": len(scraped_jobs),
                "skipped_count": skipped_count,
                "jobs": scraped_jobs,
            },
        )

