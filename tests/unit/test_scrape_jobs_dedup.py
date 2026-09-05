"""
tests/unit/test_scrape_jobs_dedup.py
====================================
Unit tests for card-level fingerprint extraction and deduplication skip in ScrapeJobsHandler.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import compute_job_fingerprint, JobPosting
from boss_agent.pages import JobCardBrief
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


def test_job_card_brief_fingerprint():
    """JobCardBrief should compute canonical fingerprint matching compute_job_fingerprint."""
    card = JobCardBrief(
        title="Agent应用开发工程师",
        company_name="字节跳动(上海)",
        recruiter_name="买先生 · 产品研发",
    )
    expected = compute_job_fingerprint("字节跳动(上海)", "Agent应用开发工程师", "买先生 · 产品研发")
    assert card.fingerprint == expected


@pytest.mark.asyncio
async def test_scrape_jobs_handler_skips_existing_and_scrapes_new():
    """ScrapeJobsHandler should skip duplicates at card level without clicking detail, and extract new."""
    broker = InMemoryTaskBroker()

    # Pre-populate an existing job into broker
    existing_fp = compute_job_fingerprint("字节跳动", "已抓取的岗位", "张HR")
    await broker.upsert_job_record(
        {
            "fingerprint": existing_fp,
            "title": "已抓取的岗位",
            "company_name": "字节跳动",
            "recruiter_name": "张HR",
            "status": "matched",
        }
    )

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Card 1: Existing (should be skipped)
    card1_elem = MagicMock()
    card1 = JobCardBrief(
        title="已抓取的岗位",
        company_name="字节跳动",
        recruiter_name="张HR",
        element=card1_elem,
        fingerprint=existing_fp,
    )

    # Card 2: New job
    card2_elem = MagicMock()
    card2 = JobCardBrief(
        title="新AI岗位",
        company_name="新公司",
        recruiter_name="李总",
        element=card2_elem,
    )

    from boss_agent.worker.config import WorkerConfig

    handler = ScrapeJobsHandler()
    context = WorkerContext(config=WorkerConfig(worker_id="test-worker"), driver=mock_driver)
    task = AutomationTask(

        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "agent", "max_jobs": 5},
    )

    with (
        patch("boss_agent.worker.handlers.scrape_jobs.StartupDialogPage") as mock_startup_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobListPage") as mock_list_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.SearchPage") as mock_search_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobDetailPage") as mock_detail_cls,
    ):
        mock_startup = mock_startup_cls.return_value
        mock_startup.is_dialog_present.return_value = False

        mock_list = mock_list_cls.return_value
        mock_list.extract_visible_job_cards.return_value = [card1, card2]

        mock_search = mock_search_cls.return_value
        mock_search.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.extract_job_posting.return_value = JobPosting(
            title="新AI岗位",
            company_name="新公司",
            salary_range="30-50K",
            job_description="详细JD内容...",
            recruiter_name="李总",
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["total_scanned"] == 2
    assert result.output["skipped_count"] == 1
    assert result.output["scraped_count"] == 1

    # Verify card 1 was NOT clicked
    card1_elem.click.assert_not_called()

    # Verify new job was persisted into broker
    unmatched_records = await broker.list_job_records(status="unmatched")
    assert len(unmatched_records) == 1
    assert unmatched_records[0]["title"] == "新AI岗位"
    assert unmatched_records[0]["company_name"] == "新公司"
