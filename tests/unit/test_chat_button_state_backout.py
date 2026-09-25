"""
tests/unit/test_chat_button_state_backout.py
============================================
Unit tests for `btn_chat` communication-state inspection and immediate backout (Issue #200).

The Boss 直聘 job detail page exposes its engagement state through the call-to-action button
(`com.hpbr.bosszhipin:id/btn_chat`):
  - "继续沟通"  → the candidate already contacted this job (platform historical contact)
  - "停止招聘" / "职位已关闭" / "已下线" → the posting is dead
  - "立即沟通" / "聊一聊" / "去沟通" / "发消息" → untouched, safe to evaluate
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import ChatButtonState, classify_chat_button
from boss_agent.pages import JobCardBrief, JobDetailPage
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


@pytest.mark.parametrize(
    "button_text",
    ["立即沟通", "聊一聊", "去沟通", "发消息"],
)
def test_classify_uncontacted_button_texts(button_text):
    """Untouched job postings must classify as UNCONTACTED."""
    assert classify_chat_button(button_text) == ChatButtonState.UNCONTACTED


def test_classify_communicated_button_text():
    """A previously contacted job must classify as COMMUNICATED."""
    assert classify_chat_button("继续沟通") == ChatButtonState.COMMUNICATED


@pytest.mark.parametrize(
    "button_text",
    ["停止招聘", "职位已关闭", "已下线"],
)
def test_classify_closed_button_texts(button_text):
    """Expired or closed postings must classify as CLOSED."""
    assert classify_chat_button(button_text) == ChatButtonState.CLOSED


@pytest.mark.parametrize("button_text", ["", "   ", None, "一键投递"])
def test_classify_unknown_button_texts(button_text):
    """Unrecognised or missing button text must classify as UNKNOWN (fail-open)."""
    assert classify_chat_button(button_text) == ChatButtonState.UNKNOWN


def test_classify_disabled_contact_button_as_unknown():
    """A disabled call-to-action must fail open rather than being treated as a dead posting."""
    assert classify_chat_button("立即沟通", enabled=False) == ChatButtonState.UNKNOWN
    assert classify_chat_button("立即沟通", enabled=True) == ChatButtonState.UNCONTACTED


def test_classify_prefers_communicated_over_generic_substring():
    """'继续沟通' contains '沟通' but must never be mistaken for an untouched posting."""
    assert classify_chat_button("继续沟通") == ChatButtonState.COMMUNICATED
    assert classify_chat_button("立即沟通") == ChatButtonState.UNCONTACTED


def _mock_driver_with_button(text: str, enabled: bool = True) -> MagicMock:
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    button = MagicMock()
    button.text = text
    button.get_attribute.return_value = "true" if enabled else "false"
    driver.find_elements.return_value = [button]
    return driver


def test_job_detail_page_reads_chat_button_state():
    """JobDetailPage.get_chat_button_state must read the btn_chat widget text."""
    page = JobDetailPage(_mock_driver_with_button("继续沟通"))

    assert page.get_chat_button_state() == ChatButtonState.COMMUNICATED


def test_job_detail_page_reports_unknown_without_button():
    """JobDetailPage.get_chat_button_state must not raise when btn_chat is absent."""
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    driver.find_elements.return_value = []

    assert JobDetailPage(driver).get_chat_button_state() == ChatButtonState.UNKNOWN


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


def _communicated_card() -> JobCardBrief:
    return JobCardBrief(
        title="大模型算法工程师",
        company_name="深至科技",
        recruiter_name="王女士",
        salary_range="40-60K",
        digest="负责大模型算法研发",
    )


@pytest.mark.asyncio
async def test_scrape_records_platform_historical_contact_and_backs_out(broker):
    """A '继续沟通' detail page must be recorded as applied (no JD) and immediately backed out."""
    card = _communicated_card()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    config = WorkerConfig(worker_id="test-worker-communicated", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "算法", "max_jobs": 1},
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as mock_startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as mock_list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as mock_search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as mock_detail_cls,
    ):
        mock_startup_cls.return_value.is_dialog_present.return_value = False
        mock_list = mock_list_cls.return_value
        mock_list.get_feed_bottom_boundary.return_value = None
        mock_list.extract_visible_job_cards.return_value = [card]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.COMMUNICATED

        assert await worker.run_once() is True

        # Full JD extraction must be skipped entirely for already-contacted jobs
        mock_detail.extract_job_posting.assert_not_called()
        mock_detail.navigate_back.assert_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert finished.status == TaskStatus.SUCCESS
    assert any("既有沟通" in log for log in finished.logs)

    records = await broker.job_store.list_job_records(status="applied")
    assert len(records) == 1
    assert records[0]["company_name"] == "深至科技"
    assert not records[0].get("applied_at")
    assert records[0].get("applied_source") == "platform_historical"
    assert await broker.job_store.count_today_applied_jobs() == 0


@pytest.mark.asyncio
async def test_scrape_marks_expired_posting_as_ignored_and_backs_out(broker):
    """A '停止招聘' detail page must be recorded as ignored (expired) and immediately backed out."""
    card = JobCardBrief(
        title="Agent 平台开发",
        company_name="游族网络",
        recruiter_name="李先生",
        salary_range="35-55K",
        digest="负责 Agent 平台建设",
    )
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    config = WorkerConfig(worker_id="test-worker-expired", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "agent", "max_jobs": 1},
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as mock_startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as mock_list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as mock_search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as mock_detail_cls,
    ):
        mock_startup_cls.return_value.is_dialog_present.return_value = False
        mock_list = mock_list_cls.return_value
        mock_list.get_feed_bottom_boundary.return_value = None
        mock_list.extract_visible_job_cards.return_value = [card]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.CLOSED

        assert await worker.run_once() is True
        mock_detail.extract_job_posting.assert_not_called()
        mock_detail.navigate_back.assert_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert finished.status == TaskStatus.SUCCESS
    assert any("岗位失效" in log for log in finished.logs)

    records = await broker.job_store.list_job_records(status="ignored")
    assert len(records) == 1
    assert "停止招聘" in records[0].get("screened_reason", "")


@pytest.mark.asyncio
async def test_auto_apply_aborts_without_llm_waste_on_communicated_job(broker):
    """AutoApplyHandler must abort drafting/sending when btn_chat shows '继续沟通'."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    config = WorkerConfig(worker_id="test-worker-apply-communicated", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm = MagicMock()
    apply_handler = AutoApplyHandler(llm_client=mock_llm)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    dispatched = await broker.job_store.upsert_job_record(
        {
            "fingerprint": "fp-dispatched-communicated",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "matched",
        }
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "算法",
            "direct_job_id": dispatched["id"],
            "job_title": dispatched["title"],
            "company_name": dispatched["company_name"],
            "preview_only": False,
            "auto_send": True,
            "candidate_profile": {"name": "Candidate", "core_skills": ["Python"]},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as mock_startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage"),
        patch("boss_agent.feed_pipeline.JobDetailPage") as mock_detail_cls,
    ):
        mock_startup_cls.return_value.is_dialog_present.return_value = False
        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.COMMUNICATED

        assert await worker.run_once() is True

        mock_detail.extract_job_posting.assert_not_called()
        mock_detail.navigate_back.assert_called()

    mock_llm.chat_completion_json.assert_not_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert any("既有沟通" in log for log in finished.logs)

    records = await broker.job_store.list_job_records(status="applied")
    assert len(records) == 1
    assert records[0].get("applied_source") == "platform_historical"
    assert await broker.job_store.count_today_applied_jobs() == 0
