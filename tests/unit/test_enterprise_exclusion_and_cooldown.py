"""
tests/unit/test_enterprise_exclusion_and_cooldown.py
====================================================
Unit tests for two-tier skipping, direct-hire enterprise exclusion, and the re-application
cool-down window (Issues #201 and #202).

Direct-hire enterprises share candidate records across their HR teams, so contacting one role
suppresses the company's other postings. Headhunter channels and masked company names are
strictly exempt, and suppression expires after `communication_cooldown_days` (0 = permanent).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker, PocketBaseTaskBroker
from boss_agent.models import ChatButtonState, is_communication_expired
from boss_agent.pages import JobCardBrief
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def mock_driver():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    return driver


def _card(title: str, company: str, is_headhunter: bool = False) -> JobCardBrief:
    return JobCardBrief(
        title=title,
        company_name=company,
        recruiter_name="王女士",
        salary_range="40-60K",
        digest="负责核心算法研发",
        is_headhunter=is_headhunter,
    )


# --------------------------------------------------------------------------------------
# Cool-down window semantics
# --------------------------------------------------------------------------------------


def test_is_communication_expired_releases_records_past_the_window():
    """A communication older than the configured window must be released for re-engagement."""
    assert is_communication_expired({"applied_at": _iso_days_ago(45)}, cooldown_days=30) is True
    assert is_communication_expired({"applied_at": _iso_days_ago(10)}, cooldown_days=30) is False


def test_is_communication_expired_zero_cooldown_is_permanent():
    """cooldown_days == 0 means permanent suppression until manually cleared."""
    assert is_communication_expired({"applied_at": _iso_days_ago(400)}, cooldown_days=0) is False


def test_is_communication_expired_falls_back_to_created_for_historical_contacts():
    """Platform historical contacts carry no applied_at, so their ingestion date governs expiry."""
    assert (
        is_communication_expired(
            {"applied_at": "", "created": _iso_days_ago(45)}, cooldown_days=30
        )
        is True
    )
    assert (
        is_communication_expired(
            {"applied_at": "", "created": _iso_days_ago(3)}, cooldown_days=30
        )
        is False
    )


def test_is_communication_expired_without_any_timestamp_is_not_expired():
    """A record with no usable timestamp must not be released by accident."""
    assert is_communication_expired({}, cooldown_days=30) is False
    assert is_communication_expired({"applied_at": "not-a-date"}, cooldown_days=30) is False


# --------------------------------------------------------------------------------------
# applied_direct_companies pool
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_applied_direct_companies_excludes_headhunters_and_masked_names(broker):
    """Only genuine direct-hire enterprises with an active application form the exclusion pool."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-direct-hired",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(2),
        }
    )
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-headhunter-hired",
            "title": "Agent 架构师",
            "company_name": "精英猎头咨询",
            "recruiter_name": "李某 · 猎头",
            "status": "applied",
            "is_headhunter": True,
            "applied_at": _iso_days_ago(2),
        }
    )
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-masked-hired",
            "title": "Agent 平台开发",
            "company_name": "某知名互联网公司",
            "recruiter_name": "张某",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(2),
        }
    )
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-direct-saved",
            "title": "Agent 工程师",
            "company_name": "游族网络",
            "recruiter_name": "陈先生",
            "status": "jd_saved",
            "is_headhunter": False,
        }
    )

    assert await broker.get_applied_direct_companies() == {"深至科技"}


@pytest.mark.asyncio
async def test_applied_direct_companies_honours_cooldown_window(broker):
    """Companies whose last communication fell outside the cool-down window are released."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-recent-hired",
            "title": "大模型算法工程师",
            "company_name": "商汤科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(10),
        }
    )
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-stale-hired",
            "title": "Agent 平台开发",
            "company_name": "小红书",
            "recruiter_name": "陈先生",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(45),
        }
    )

    assert await broker.get_applied_direct_companies(cooldown_days=30) == {"商汤科技"}
    assert await broker.get_applied_direct_companies(cooldown_days=0) == {"商汤科技", "小红书"}


# --------------------------------------------------------------------------------------
# Handler-level two-tier skipping
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_skips_card_already_recorded_as_applied(broker, mock_driver):
    """A card whose fingerprint is already applied must be skipped without opening the detail page."""
    card = _card("大模型算法工程师", "深至科技")
    await broker.upsert_job_record(
        {
            "fingerprint": card.fingerprint,
            "title": card.title,
            "company_name": card.company_name,
            "recruiter_name": card.recruiter_name,
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(3),
        }
    )

    config = WorkerConfig(worker_id="test-worker-applied-skip", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "算法", "max_jobs": 1}
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

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_not_called()
        mock_detail.extract_job_posting.assert_not_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert any("已沟通" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_scrape_skips_other_roles_from_communicated_direct_hire_company(broker, mock_driver):
    """Another role at an already-contacted direct-hire enterprise must be skipped at card level."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-company-anchor",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(3),
        }
    )
    other_role = _card("Agent 平台开发工程师", "深至科技")

    config = WorkerConfig(worker_id="test-worker-company-exclusion", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "agent", "max_jobs": 1}
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
        mock_list.extract_visible_job_cards.return_value = [other_role]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_not_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert any("同企已沟通避嫌" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_scrape_admits_headhunter_roles_from_same_company_name(broker, mock_driver):
    """Headhunter channels are exempt: the same company name must not trigger exclusion."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-company-anchor-hh",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(3),
        }
    )
    headhunter_role = _card("Agent 架构师", "深至科技", is_headhunter=True)

    config = WorkerConfig(worker_id="test-worker-hh-exempt", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "agent", "max_jobs": 1}
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
        mock_list.extract_visible_job_cards.return_value = [headhunter_role]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.UNKNOWN

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert not any("同企已沟通避嫌" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_masked_company_names_never_join_the_exclusion_pool(broker, mock_driver):
    """Masked employers (保密/某知名...) must never suppress other postings under that name."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-masked-anchor",
            "title": "Agent 平台开发",
            "company_name": "某知名互联网公司",
            "recruiter_name": "张某",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(3),
        }
    )
    other_role = _card("Agent 架构师", "某知名互联网公司")

    config = WorkerConfig(worker_id="test-worker-masked-exempt", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "agent", "max_jobs": 1}
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
        mock_list.extract_visible_job_cards.return_value = [other_role]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.UNKNOWN

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_called()


@pytest.mark.asyncio
async def test_newly_communicated_company_is_cached_within_the_same_run(broker, mock_driver):
    """A direct-hire company applied mid-run must suppress its later postings immediately."""
    first_role = _card("大模型算法工程师", "深至科技")
    second_role = _card("Agent 平台开发工程师", "深至科技")

    config = WorkerConfig(worker_id="test-worker-dynamic-cache", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS, payload={"keyword": "agent", "max_jobs": 2}
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
        mock_list.extract_visible_job_cards.return_value = [first_role, second_role]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.COMMUNICATED

        assert await worker.run_once() is True

        # Only the first role reaches the detail page; the second is suppressed by the cache
        assert mock_detail.get_chat_button_state.call_count == 1

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert any("同企已沟通避嫌" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_expired_communication_releases_card_for_reevaluation(broker, mock_driver):
    """A card whose communication fell outside the cool-down window must be re-evaluated."""
    card = _card("大模型算法工程师", "深至科技")
    await broker.upsert_job_record(
        {
            "fingerprint": card.fingerprint,
            "title": card.title,
            "company_name": card.company_name,
            "recruiter_name": card.recruiter_name,
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(45),
        }
    )

    config = WorkerConfig(worker_id="test-worker-cooldown-release", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "算法", "max_jobs": 1, "communication_cooldown_days": 30},
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
        mock_detail.get_chat_button_state.return_value = ChatButtonState.UNKNOWN

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_called()


@pytest.mark.asyncio
async def test_clear_job_communication_clears_state_and_keeps_jd(broker):
    """Releasing a communication must reset status/applied_at while preserving the extracted JD."""
    record = await broker.upsert_job_record(
        {
            "fingerprint": "fp-release-keeps-jd",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "applied_source": "platform_historical",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(45),
            "job_description": "负责大模型算法研发与落地（历史 JD）",
        }
    )

    released = await broker.clear_job_communication(record["id"])

    assert released["status"] == "jd_saved"
    assert not released.get("applied_at")
    assert not released.get("applied_source")
    assert released["job_description"] == "负责大模型算法研发与落地（历史 JD）"
    assert await broker.count_today_applied_jobs() == 0


@pytest.mark.asyncio
async def test_expired_communication_releases_record_back_to_candidate_pool(broker, mock_driver):
    """An aged communication must transition back to jd_saved when its card is re-scanned."""
    card = _card("大模型算法工程师", "深至科技")
    stale = await broker.upsert_job_record(
        {
            "fingerprint": card.fingerprint,
            "title": card.title,
            "company_name": card.company_name,
            "recruiter_name": card.recruiter_name,
            "status": "applied",
            "applied_source": "platform_historical",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(45),
            "job_description": "负责大模型算法研发与落地（历史 JD）",
        }
    )

    config = WorkerConfig(worker_id="test-worker-cooldown-transition", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "算法", "max_jobs": 1, "communication_cooldown_days": 30},
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
        mock_detail_cls.return_value.get_chat_button_state.return_value = ChatButtonState.UNKNOWN

        assert await worker.run_once() is True

    refreshed = await broker.get_job_record(stale["id"])
    assert refreshed is not None
    assert refreshed["status"] == "jd_saved"
    assert not refreshed.get("applied_at")
    assert not refreshed.get("applied_source")


@pytest.mark.asyncio
async def test_auto_apply_refuses_other_role_from_communicated_direct_hire_company(
    broker, mock_driver
):
    """A dispatched application to another role of a contacted direct-hire company is refused."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-dispatched-company-anchor",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(3),
        }
    )
    target = await broker.upsert_job_record(
        {
            "fingerprint": "fp-dispatched-target-role",
            "title": "Agent 平台开发工程师",
            "company_name": "深至科技",
            "recruiter_name": "陈先生",
            "status": "matched",
            "is_headhunter": False,
        }
    )

    mock_llm = MagicMock()
    config = WorkerConfig(worker_id="test-worker-apply-excluded", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=mock_llm)],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "agent",
            "direct_job_id": target["id"],
            "job_title": target["title"],
            "company_name": target["company_name"],
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

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_not_called()
        mock_detail.extract_job_posting.assert_not_called()

    mock_llm.chat_completion_json.assert_not_called()

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert any("同企已沟通避嫌" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_auto_apply_permits_headhunter_target_from_communicated_company(broker, mock_driver):
    """Headhunter channels are exempt from enterprise exclusion even under a matching name."""
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-hh-company-anchor",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(3),
        }
    )
    target = await broker.upsert_job_record(
        {
            "fingerprint": "fp-hh-target-role",
            "title": "Agent 架构师",
            "company_name": "深至科技",
            "recruiter_name": "李某 · 猎头",
            "status": "matched",
            "is_headhunter": True,
        }
    )

    config = WorkerConfig(worker_id="test-worker-apply-hh-exempt", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=MagicMock())],
    )

    await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "agent",
            "direct_job_id": target["id"],
            "job_title": target["title"],
            "company_name": target["company_name"],
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
        mock_detail.get_chat_button_state.return_value = ChatButtonState.UNKNOWN

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_called()


@pytest.mark.asyncio
async def test_permanent_cooldown_keeps_excluding_old_communications(broker, mock_driver):
    """cooldown_days == 0 must keep suppressing a long-dormant communication."""
    card = _card("Agent 平台开发工程师", "深至科技")
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-permanent-anchor",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
            "is_headhunter": False,
            "applied_at": _iso_days_ago(400),
        }
    )

    config = WorkerConfig(worker_id="test-worker-permanent-cooldown", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config, broker=broker, context=context, handlers=[ScrapeJobsHandler(llm_client=MagicMock())]
    )

    await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "agent", "max_jobs": 1, "communication_cooldown_days": 0},
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

        assert await worker.run_once() is True

        mock_detail.get_chat_button_state.assert_not_called()


# --------------------------------------------------------------------------------------
# PocketBase adapter: exclusion pool assembly
# --------------------------------------------------------------------------------------


def _applied(company: str, is_headhunter: bool = False, days_ago: int = 2) -> dict[str, object]:
    return {
        "company_name": company,
        "is_headhunter": is_headhunter,
        "applied_at": _iso_days_ago(days_ago),
    }


def _paged_session(pages: list[list[dict[str, object]]], per_page: int = 200) -> MagicMock:
    """Fake a PocketBase collection endpoint serving the given pages in order."""
    session = MagicMock()

    def mock_get(url, params=None, headers=None):
        page = int((params or {}).get("page", 1))
        items = pages[page - 1] if page - 1 < len(pages) else []
        return MagicMock(
            status_code=200,
            json=lambda: {
                "items": items,
                "page": page,
                "perPage": per_page,
                "totalItems": sum(len(p) for p in pages),
                "totalPages": len(pages),
            },
        )

    session.get.side_effect = mock_get
    return session


@pytest.mark.asyncio
async def test_applied_companies_walk_every_page_of_the_collection():
    """A single page silently truncates the pool once a candidate exceeds one page of contacts."""
    session = _paged_session([[_applied(f"企业{i}") for i in range(200)], [_applied("深至科技")]])
    broker = PocketBaseTaskBroker(base_url="http://mock-pb:8090", session=session)

    companies = await broker.get_applied_direct_companies(cooldown_days=30)

    assert "深至科技" in companies, "Companies beyond the first page must still anchor exclusion."
    assert len(companies) == 201
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_applied_companies_request_projects_is_headhunter():
    """Dropping is_headhunter from `fields` silently turns the headhunter guard into a no-op."""
    session = _paged_session([[_applied("深至科技"), _applied("精英猎头", is_headhunter=True)]])
    broker = PocketBaseTaskBroker(base_url="http://mock-pb:8090", session=session)

    companies = await broker.get_applied_direct_companies(cooldown_days=30)

    fields = session.get.call_args_list[0].kwargs["params"]["fields"]
    assert "is_headhunter" in fields
    assert companies == {"深至科技"}
