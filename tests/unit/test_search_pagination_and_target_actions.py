"""
tests/unit/test_search_pagination_and_target_actions.py
======================================================
Unit tests for Spec #134, Tickets #135, #136, #137, #138:
- Feed bottom boundary detection (tv_tips: 暂无符合职位，为你推荐)
- Two-tier target actions (save_jd, auto_apply)
- Monotonic state progression and state-aware deduplication
- Daily greeting safety limit & quota degradation
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import (
    STATE_RANK,
    TARGET_ACTION_RANK,
    JobRecordStatus,
    SavedSearch,
    SearchConfig,
    TargetAction,
)
from boss_agent.pages import JobListPage
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler

# ---------------------------------------------------------------------------
# Ticket 1 (Issue #135): Feed Bottom Boundary & Pagination Tests
# ---------------------------------------------------------------------------

def test_job_list_page_is_feed_bottom_reached():
    """Verify is_feed_bottom_reached and get_feed_bottom_boundary recognize all divider text patterns."""
    driver = MagicMock()
    page = JobListPage(driver)

    # Patterns seen in Boss 直聘:
    patterns = [
        "暂无符合职位，为你推荐",
        "✦暂无其他符合职位，为你推荐 ──",
        "为你推荐",
        "暂无其他符合职位",
        "没有更多",
    ]

    for pattern in patterns:
        mock_tip = MagicMock()
        mock_tip.text = pattern
        driver.find_element.return_value = mock_tip
        driver.find_elements.return_value = [mock_tip]
        assert page.is_feed_bottom_reached() is True, f"Failed on pattern: {pattern}"
        assert page.get_feed_bottom_boundary() is mock_tip, f"Failed boundary on pattern: {pattern}"

    # When bottom_tips is not found
    from selenium.common.exceptions import NoSuchElementException
    driver.find_element.side_effect = NoSuchElementException("Not found")
    driver.find_elements.return_value = []
    assert page.is_feed_bottom_reached() is False
    assert page.get_feed_bottom_boundary() is None


def test_job_list_page_scroll_job_list_uses_human_swipe():
    """Verify scroll_job_list calls gestures.human_swipe upwards."""
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    page = JobListPage(driver)

    page.gestures.human_swipe = MagicMock()
    page.scroll_job_list()

    page.gestures.human_swipe.assert_called_once()
    args, kwargs = page.gestures.human_swipe.call_args
    # Verify swipe is upwards (start.y > end.y)
    start, end = args
    assert start.y > end.y


# ---------------------------------------------------------------------------
# Ticket 2 (Issue #136): Target Actions & State Progression Models
# ---------------------------------------------------------------------------

def test_models_target_actions_and_state_ranks():
    """Verify TargetAction and JobRecordStatus enum definitions and monotonic ranks."""
    assert TargetAction.SAVE_JD.value == "save_jd"
    assert TargetAction.AUTO_APPLY.value == "auto_apply"
    assert not hasattr(TargetAction, "DIGEST_ONLY")

    assert JobRecordStatus.JD_SAVED.value == "jd_saved"
    assert JobRecordStatus.UNMATCHED.value == "unmatched"
    assert JobRecordStatus.MATCHED.value == "matched"
    assert JobRecordStatus.APPLIED.value == "applied"
    assert JobRecordStatus.IGNORED.value == "ignored"

    # Monotonic progression ranks
    assert STATE_RANK["digest_only"] == STATE_RANK["jd_saved"] == STATE_RANK["unmatched"] == 1
    assert STATE_RANK["jd_saved"] < STATE_RANK["matched"]
    assert STATE_RANK["matched"] < STATE_RANK["applied"]
    assert STATE_RANK["ignored"] == -1

    assert TARGET_ACTION_RANK[TargetAction.SAVE_JD] == 1
    assert TARGET_ACTION_RANK[TargetAction.AUTO_APPLY] == 2


def test_saved_search_serialization_with_target_action_and_max_jobs():
    """Verify SavedSearch model serializes and deserializes target_action and max_jobs."""
    search = SavedSearch(
        id="test_search",
        name="Agent Jobs",
        search=SearchConfig(keyword="AI Agent"),
        target_action="save_jd",
        max_jobs=50,
    )
    d = search.to_dict()
    assert d["target_action"] == "save_jd"
    assert d["max_jobs"] == 50

    restored = SavedSearch.from_dict("test_search", d)
    assert restored.target_action == "save_jd"
    assert restored.max_jobs == 50


# ---------------------------------------------------------------------------
# Broker Tests: get_by_fingerprint, count_today_applied, status upgrade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broker_get_job_record_by_fingerprint_and_count_today():
    """Verify broker fingerprint lookup and daily applied counter."""
    broker = InMemoryTaskBroker()

    # Record 1: digest_only
    rec1 = await broker.upsert_job_record({
        "fingerprint": "fp_001",
        "title": "Python Engineer",
        "company_name": "Tech Corp",
        "recruiter_name": "HR Alice",
        "status": "digest_only",
    })
    assert rec1["id"] is not None

    found = await broker.get_job_record_by_fingerprint("fp_001")
    assert found is not None
    assert found["title"] == "Python Engineer"
    assert found["status"] == "digest_only"

    not_found = await broker.get_job_record_by_fingerprint("fp_nonexistent")
    assert not_found is None

    # Count today applied: initially 0
    assert await broker.count_today_applied_jobs() == 0

    # Upgrade rec1 to applied
    await broker.upsert_job_record({
        "fingerprint": "fp_001",
        "status": "applied",
    })
    assert await broker.count_today_applied_jobs() == 1


@pytest.mark.asyncio
async def test_broker_upsert_monotonic_status_upgrade():
    """Verify upsert_job_record does not downgrade status when lower target action is processed."""
    broker = InMemoryTaskBroker()

    # Initial: applied
    await broker.upsert_job_record({
        "fingerprint": "fp_senior",
        "title": "Staff Architect",
        "company_name": "Big Tech",
        "recruiter_name": "Bob",
        "status": "applied",
        "job_description": "Full JD text",
    })

    # Attempt to upsert with digest_only
    updated = await broker.upsert_job_record({
        "fingerprint": "fp_senior",
        "status": "digest_only",
    })

    # Status should remain applied
    assert updated["status"] == "applied"
    # Existing JD should be preserved
    assert updated["job_description"] == "Full JD text"


# ---------------------------------------------------------------------------
# Ticket 2 & 1 Handler Tests: ScrapeJobsHandler with target_action & pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_jobs_handler_save_jd_enriches_full_jd():
    """Verify target_action=save_jd clicks card, navigates to detail, and extracts full JD."""
    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_card = MagicMock()
    mock_title = MagicMock(text="AI Agent Engineer")
    mock_company = MagicMock(text="Innovative AI")
    mock_salary = MagicMock(text="40-60K")
    mock_desc = MagicMock(text="Detailed Job Description for AI Agent Engineer.")
    mock_nav_elem = MagicMock()

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "desc" in value or "tv_job_desc" in value or "tv_description" in value:
            return [mock_desc]
        if "bottom_tips" in value or "暂无符合职位" in value:
            return []
        if "job_card" in value or "view_job_card" in value:
            return [mock_card]
        return [mock_nav_elem]

    mock_driver.find_elements.side_effect = mock_find
    mock_card.find_elements.side_effect = mock_find
    # Bottom tips not found initially
    from selenium.common.exceptions import NoSuchElementException
    mock_driver.find_element.side_effect = NoSuchElementException("No bottom")

    config = WorkerConfig(worker_id="test-save-jd-worker", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler()],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "AI", "target_action": "save_jd", "max_jobs": 1},
    )

    await worker.run_once()

    # Verify task finished successfully
    finished = await broker.get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS

    # Verify job record was stored with jd_saved status
    jobs = await broker.list_job_records()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "jd_saved"
    # Card was clicked for detail view
    mock_card.click.assert_called_once()


@pytest.mark.asyncio
async def test_scrape_jobs_handler_terminates_on_feed_bottom_boundary():
    """Verify ScrapeJobsHandler terminates immediately when bottom tips element is hit."""
    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_title = MagicMock(text="Data Scientist")
    mock_company = MagicMock(text="Data Labs")
    mock_salary = MagicMock(text="30-50K")
    bottom_elem = MagicMock()
    bottom_elem.text = "暂无符合职位，为你推荐"

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "bottom_tips" in value or "暂无符合职位" in value:
            return [bottom_elem]
        return [MagicMock()]

    mock_driver.find_elements.side_effect = mock_find
    # Bottom tips element found!
    mock_driver.find_element.return_value = bottom_elem

    config = WorkerConfig(worker_id="test-bottom-worker", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler()],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "Data", "target_action": "save_jd", "max_jobs": 50},
    )

    await worker.run_once()

    finished = await broker.get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    # Check log mentions feed bottom reached
    assert any("feed boundary" in log.lower() or "暂无符合职位" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_scrape_jobs_handler_filters_recommended_cards_below_boundary():
    """Verify ScrapeJobsHandler skips job cards whose vertical position is >= boundary marker Y."""
    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Boundary element located at y=1200
    bottom_elem = MagicMock()
    bottom_elem.text = "✦暂无其他符合职位，为你推荐 ──"
    bottom_elem.location = {"x": 0, "y": 1200}

    # Card 1 (above boundary): y = 600 -> authentic search result, should be scraped
    card1_elem = MagicMock()
    card1_elem.location = {"x": 0, "y": 600}
    title1 = MagicMock(text="Senior Agent Researcher")
    comp1 = MagicMock(text="AI Lab")
    sal1 = MagicMock(text="50-70K")

    # Card 2 (below boundary): y = 1400 -> recommended job, must be skipped!
    card2_elem = MagicMock()
    card2_elem.location = {"x": 0, "y": 1400}
    title2 = MagicMock(text="Irrelevant Recommendation")
    comp2 = MagicMock(text="Other Co")
    sal2 = MagicMock(text="15-20K")

    def mock_card_find(card_instance):
        def _find(by, value):
            if card_instance is card1_elem:
                if "job_name" in value or "tv_job_name" in value:
                    return [title1]
                if "company_name" in value or "tv_company_name" in value:
                    return [comp1]
                if "salary" in value or "tv_job_salary" in value:
                    return [sal1]
            elif card_instance is card2_elem:
                if "job_name" in value or "tv_job_name" in value:
                    return [title2]
                if "company_name" in value or "tv_company_name" in value:
                    return [comp2]
                if "salary" in value or "tv_job_salary" in value:
                    return [sal2]
            return []
        return _find

    card1_elem.find_elements.side_effect = mock_card_find(card1_elem)
    card2_elem.find_elements.side_effect = mock_card_find(card2_elem)

    def mock_driver_find(by, value):
        if "job_card" in value or "view_job_card" in value:
            return [card1_elem, card2_elem]
        if "bottom_tips" in value or "暂无其他符合职位" in value or "为你推荐" in value:
            return [bottom_elem]
        if "job_name" in value or "tv_job_name" in value:
            return [title1]
        if "company_name" in value or "tv_company_name" in value:
            return [comp1]
        if "salary" in value or "tv_job_salary" in value:
            return [sal1]
        return [MagicMock()]

    mock_driver.find_elements.side_effect = mock_driver_find
    mock_driver.find_element.return_value = bottom_elem

    config = WorkerConfig(worker_id="test-boundary-filter-worker", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler()],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "Agent", "target_action": "save_jd", "max_jobs": 10},
    )

    await worker.run_once()

    finished = await broker.get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS

    # Verify only card 1 (above boundary) was saved into broker
    records = await broker.list_job_records()
    assert len(records) == 1
    assert records[0]["title"] == "Senior Agent Researcher"
    assert records[0]["company_name"] == "AI Lab"
    assert not any(r["title"] == "Irrelevant Recommendation" for r in records)


# ---------------------------------------------------------------------------
# Ticket 3 (Issue #137): Daily Greeting Safety Limit & Quota Degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_apply_handler_quota_exhausted_degrades_to_matched():
    """Verify AutoApplyHandler degrades to matched (draft only) when daily greeting limit is reached."""
    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Pre-populate broker with 20 applied jobs today
    for i in range(20):
        await broker.upsert_job_record({
            "fingerprint": f"fp_applied_{i}",
            "title": f"Job {i}",
            "company_name": f"Company {i}",
            "recruiter_name": f"Recruiter {i}",
            "status": "applied",
        })

    assert await broker.count_today_applied_jobs() == 20

    # Job elements for driver
    mock_title = MagicMock(text="AI Specialist")
    mock_company = MagicMock(text="Tech AI")
    mock_salary = MagicMock(text="35-60K")
    mock_desc = MagicMock(
        text="负责企业级大模型与多智能体系统工程化落地，要求精通 Python 核心架构、移动端 Appium 自动化交互及复杂异步事件总线开发。"
    )
    start_chat_btn = MagicMock()

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "desc" in value or "tv_job_desc" in value:
            return [mock_desc]
        if "btn_chat" in value or "btn_op" in value:
            return [start_chat_btn]
        return [MagicMock()]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-quota-worker", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler()],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={"keyword": "AI", "auto_send": True, "preview_only": False, "min_score": 0},
    )

    await worker.run_once()

    finished = await broker.get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    # Log should mention quota limit reached
    assert any("quota limit reached" in log.lower() for log in finished.logs)

    # Start chat button should NOT have been clicked because quota is exhausted
    start_chat_btn.click.assert_not_called()

    # The new job record should be in status 'matched' with generated draft greeting
    all_jobs = await broker.list_job_records()
    target_rec = next((j for j in all_jobs if j["company_name"] == "Tech AI"), None)
    assert target_rec is not None
    assert target_rec["status"] == "matched"
    assert target_rec["greeting_message"] is not None
    assert len(target_rec["greeting_message"]) > 0
