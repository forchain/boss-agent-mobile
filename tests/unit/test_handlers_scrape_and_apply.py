"""
tests/unit/test_handlers_scrape_and_apply.py
============================================
Unit tests for SCRAPE_JOBS and AUTO_APPLY polymorphic task handlers (Issue #30).
"""

from unittest.mock import MagicMock, patch

import pytest
from _card_fixtures import located

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import JobCardBrief
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def mock_driver():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    return driver


@pytest.mark.asyncio
async def test_scrape_jobs_handler_extracts_and_persists_jobs(broker, mock_driver):
    """Verify ScrapeJobsHandler extracts job postings and logs results."""
    mock_title = MagicMock(text="AI 架构师")
    mock_company = MagicMock(text="智能科技集团")
    mock_salary = MagicMock(text="50-80K")
    mock_desc = MagicMock(text="负责大模型与移动端自动化架构设计。")

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "desc" in value or "tv_job_desc" in value:
            return [mock_desc]
        if "search" in value or "tv_tab" in value or "ly_menu" in value:
            return [MagicMock()]
        return []

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-scrape", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "AI", "max_jobs": 1},
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("scraped" in log.lower() or "extracted" in log.lower() for log in finished_task.logs)


@pytest.mark.asyncio
async def test_scrape_enrichment_falls_back_to_jd_digest_when_card_has_no_snippet(broker, mock_driver):
    """A popup card without a snippet must not persist an empty digest; the enrichment
    should fall back to the digest derived from the full job description."""
    from boss_agent.models import ChatButtonState, JobPosting

    card = JobCardBrief(
        title="Senior AI Agent Engineer（英语口语）",
        company_name="塔塔",
        recruiter_name="陈女士",
        salary_range="3-4.5万元·13月",
    )

    posting = JobPosting(
        title="Senior AI Agent Engineer（英语口语）",
        company_name="塔塔",
        salary_range="3-4.5万元",
        job_description=(
            "【 Role Summary 】；\n"
            "We are seeking a passionate and experienced Senior Engineer to join our team "
            "to build the architecture of our shared coding agent platform across the organization."
        ),
    )

    config = WorkerConfig(worker_id="test-worker-digest-fallback", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler()],
    )
    await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "agent",
            "max_jobs": 1,
            "screening_policy": {"enable_screening": False},
        },
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
        mock_list.extract_visible_job_cards.return_value = [located(card)]
        mock_search_cls.return_value.is_search_page.return_value = True
        mock_detail = mock_detail_cls.return_value
        mock_detail.get_chat_button_state.return_value = ChatButtonState.UNKNOWN
        mock_detail.extract_job_posting.return_value = posting

        assert await worker.run_once() is True

    records = await broker.job_store.list_job_records(status="jd_saved")
    assert len(records) == 1
    assert records[0]["digest"].startswith("We are seeking")
    assert "Role Summary" not in records[0]["digest"]


@pytest.mark.asyncio
async def test_auto_apply_handler_reads_a_legacy_preview_payload_and_never_sends(broker, mock_driver):
    """A task queued before #302 keeps the depth its writer stated.

    The payload carries the full legacy pair — both keys, as older builders always wrote
    them — and the worker honours it instead of re-deriving depth from the task type, so an
    in-flight draft is never promoted into a message nobody approved. The log names the
    shape it read, which is how the pair can be deleted once nothing writes it.
    """
    mock_title = MagicMock(text="Senior Python Agent Engineer")
    mock_company = MagicMock(text="Future Robotics")
    mock_salary = MagicMock(text="45-70K")
    mock_desc = MagicMock(text="Expertise in Python, LLM agents, and Android automation.")
    mock_elem = MagicMock()

    def mock_find(by, value):
        if "tv_job_name" in value or "job_name" in value:
            return [mock_title]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary]
        if "tv_job_desc" in value or "desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-apply", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm_client = MagicMock()
    mock_llm_client.chat_completion_json.return_value = {
        "match_score": 88,
        "jd_key_requirements": ["精通 Python", "大模型 Agent 经验"],
        "match_reasons": ["具备移动端开发与 Agent 落地经验"],
        "greeting_message": "针对贵司大模型 Agent 落地需求，我具备完整实战经验！",
    }

    apply_handler = AutoApplyHandler(llm_client=mock_llm_client)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Python",
            "min_score": 75,
            "target_action": "auto_apply",
            "preview_only": True,
            "auto_send": False,
            "preview_timeout_sec": 0.01,
            "candidate_profile": {
                "name": "Candidate",
                "years_of_experience": 6,
                "core_skills": ["Python", "Agents"],
            },
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("preview" in log.lower() or "greeting" in log.lower() for log in finished_task.logs)
    assert finished_task.payload.get("preview_only") is True
    # The log says which shape produced this depth, so a legacy task stays visible.
    assert any(
        "draft only" in log.lower() and "legacy" in log.lower() for log in finished_task.logs
    ), finished_task.logs


@pytest.mark.asyncio
async def test_auto_apply_handler_auto_send_mode(broker, mock_driver):
    """Verify AutoApplyHandler in auto_send mode clicks send when score meets threshold."""
    mock_title = MagicMock(text="Senior Python Agent Engineer")
    mock_company = MagicMock(text="Future Robotics")
    mock_salary = MagicMock(text="45-70K")
    mock_desc = MagicMock(text="Expertise in Python, LLM agents, and Android automation.")
    mock_elem = MagicMock()

    def mock_find(by, value):
        if "tv_job_name" in value or "job_name" in value:
            return [mock_title]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary]
        if "tv_job_desc" in value or "desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-send", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm_client = MagicMock()
    mock_llm_client.chat_completion_json.return_value = {
        "match_score": 90,
        "jd_key_requirements": ["精通 Python", "大模型 Agent 经验"],
        "match_reasons": ["具备移动端开发与 Agent 落地经验"],
        "greeting_message": "针对贵司大模型 Agent 落地需求，我具备完整实战经验！",
    }

    apply_handler = AutoApplyHandler(llm_client=mock_llm_client)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Python",
            "min_score": 75,
            "preview_only": False,
            "auto_send": True,
            "candidate_profile": {
                "name": "Candidate",
                "years_of_experience": 6,
                "core_skills": ["Python", "Agents"],
            },
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any(
        "auto_send" in log.lower() or "dispatched" in log.lower() for log in finished_task.logs
    )


@pytest.mark.asyncio
async def test_scrape_jobs_handler_applies_filters(broker, mock_driver):
    """Verify ScrapeJobsHandler parses and applies filter config from payload."""
    mock_elem = MagicMock()
    mock_title = MagicMock(text="AI 工程师")
    mock_company = MagicMock(text="科技公司")
    mock_salary = MagicMock(text="30-50K")
    mock_desc = MagicMock(text="研发岗位")

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "desc" in value or "tv_job_desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-filter", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "agent",
            "filter": {
                "education": "硕士",
                "salary": "5万元以上",
                "industries": ["人工智能", "游戏"],
            },
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("filter" in log.lower() for log in finished_task.logs)


@pytest.mark.asyncio
async def test_auto_apply_handler_filtered_by_keyword(broker, mock_driver):
    """Verify AutoApplyHandler drops job immediately when title hits keyword blacklist."""
    mock_title = MagicMock(text="Senior Java Developer")
    mock_company = MagicMock(text="Legacy Banking")
    mock_salary = MagicMock(text="30-50K")
    mock_desc = MagicMock(text="Maintain enterprise Java 8 services.")
    mock_elem = MagicMock()

    def mock_find(by, value):
        if "tv_job_name" in value or "job_name" in value:
            return [mock_title]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary]
        if "tv_job_desc" in value or "desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-kw-filter", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    mock_llm = MagicMock()

    apply_handler = AutoApplyHandler(llm_client=mock_llm)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Java",
            "screening_policy": {
                "title_blacklist": ["Java"],
            },
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("初筛淘汰" in log and "Java" in log for log in finished_task.logs)
    # LLM should never be called when rejected by keyword screener
    mock_llm.chat_completion_json.assert_not_called()


@pytest.mark.asyncio
async def test_auto_apply_handler_filtered_by_deep_screener(broker, mock_driver):
    """Verify AutoApplyHandler drops job when JD semantic screener rejects."""
    mock_title = MagicMock(text="AI Agent 开发者")
    mock_company = MagicMock(text="某大型外包")
    mock_salary = MagicMock(text="35-50K")
    mock_desc = MagicMock(
        text="负责全栈应用开发与中台微服务建设，要求精通Java、Spring Cloud与JVM底层调优。"
    )
    mock_elem = MagicMock()

    def mock_find(by, value):
        if "tv_job_name" in value or "job_name" in value:
            return [mock_title]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary]
        if "tv_job_desc" in value or "desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-deep-filter", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "pass": False,
        "reason": "JD正文明确要求熟练掌握Java微服务，触犯黑名单技术栈",
    }

    apply_handler = AutoApplyHandler(llm_client=mock_llm)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Agent",
            "screening_policy": {
                "title_whitelist": ["Agent"],
                "jd_blacklist": ["Java"],
            },
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("精筛淘汰" in log and "Java" in log for log in finished_task.logs)


@pytest.mark.asyncio
async def test_scrape_jobs_handler_eliminates_blacklisted_cards_and_persists_reason(
    broker, mock_driver
):
    """Verify ScrapeJobsHandler eliminates blacklisted cards without detail page navigation and persists screened_reason."""
    card = JobCardBrief(
        title="高级销售经理",
        company_name="黑名单外包科技",
        recruiter_name="李某",
        salary_range="15-25K",
        digest="负责大客户拓展",
    )

    config = WorkerConfig(worker_id="test-worker-scrape-elim", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "销售",
            "max_jobs": 1,
            "screening_policy": {
                "company_blacklist": ["黑名单外包科技"],
            },
        },
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
        mock_list.extract_visible_job_cards.return_value = [located(card)]
        mock_search_cls.return_value.is_search_page.return_value = True

        executed = await worker.run_once()
        assert executed is True

        # Detail page must NEVER be navigated into or extracted!
        mock_detail_cls.return_value.extract_job_posting.assert_not_called()

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("初筛淘汰" in log and "黑名单外包科技" in log for log in finished_task.logs)

    # Verify job was recorded in database as ignored with screened_reason
    records = await broker.job_store.list_job_records()
    assert len(records) == 1
    assert records[0].get("status") == "ignored"
    assert "黑名单外包科技" in records[0].get("screened_reason", "")

    # The keyword-stage rejection shares the App-Enforced Filter record shape, so it
    # must carry the same fields — including the commute columns that every job record
    # is documented to persist, even though no detail page was ever opened.
    assert records[0].get("relaxed_by_whitelist") is False
    assert records[0].get("screening_audit") == ""
    assert records[0].get("jd_key_requirements") == []
    assert "commute_distance_km" in records[0]
    assert records[0].get("commute_distance_km") is None
    assert records[0].get("commute_distance_text") == ""


@pytest.mark.asyncio
async def test_auto_apply_handler_preflight_blocks_already_ignored_job(broker, mock_driver):
    """Verify AutoApplyHandler aborts immediately without driver action when target job is already ignored."""
    existing = await broker.job_store.upsert_job_record(
        {
            "fingerprint": "fp-already-ignored",
            "title": "运维工程师",
            "company_name": "某知名外包",
            "status": "ignored",
            "screened_reason": "命中公司黑名单: 某知名外包",
        }
    )

    config = WorkerConfig(worker_id="test-worker-preflight-defense", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm = MagicMock()
    apply_handler = AutoApplyHandler(llm_client=mock_llm)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "direct_job_id": existing["id"],
            "job_title": "运维工程师",
            "company_name": "某知名外包",
            "preview_only": False,
            "auto_send": True,
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("定向投递防御" in log and "淘汰状态" in log for log in finished_task.logs)

    # Driver should NOT have navigated or clicked into chat
    mock_driver.find_elements.assert_not_called()
    mock_llm.chat_completion_json.assert_not_called()


@pytest.mark.asyncio
async def test_auto_apply_handler_preflight_blocks_blacklisted_company(broker, mock_driver):
    """Verify AutoApplyHandler preflight blocks direct application to a blacklisted company and updates DB."""
    rec = await broker.job_store.upsert_job_record(
        {
            "fingerprint": "fp-to-be-blocked",
            "title": "前端开发",
            "company_name": "不良劳务派遣公司",
            "status": "jd_saved",
        }
    )

    config = WorkerConfig(worker_id="test-worker-preflight-blacklist", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm = MagicMock()
    apply_handler = AutoApplyHandler(llm_client=mock_llm)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[apply_handler],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "direct_job_id": rec["id"],
            "job_title": "前端开发",
            "company_name": "不良劳务派遣公司",
            "screening_policy": {
                "company_blacklist": ["不良劳务派遣公司"],
            },
        },
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("定向投递防御" in log and "不良劳务派遣公司" in log for log in finished_task.logs)

    # Verify record was marked as ignored
    updated_rec = await broker.job_store.get_job_record(rec["id"])
    assert updated_rec is not None
    assert updated_rec.get("status") == "ignored"
    assert "不良劳务派遣公司" in updated_rec.get("screened_reason", "")

    # Zero driver clicks on chat or detail
    mock_driver.find_elements.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_jobs_handler_aborts_when_search_fails(broker, mock_driver):
    """Verify ScrapeJobsHandler terminates gracefully and logs failure when search fails."""
    # Mock find_elements to return empty list so search_page.search returns False
    mock_driver.find_elements.return_value = []

    config = WorkerConfig(worker_id="test-worker-search-fail", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "Agent", "enable_search": True, "max_jobs": 10},
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.FAILED
    assert any(
        "未能进入搜索页面或执行关键词搜索" in log and "Agent" in log for log in finished_task.logs
    )
    assert not any("Executed search for keyword 'Agent'" in log for log in finished_task.logs)


@pytest.mark.asyncio
async def test_auto_apply_handler_aborts_when_search_fails(broker, mock_driver):
    """Verify AutoApplyHandler terminates gracefully and logs failure when search fails."""
    mock_driver.find_elements.return_value = []

    config = WorkerConfig(worker_id="test-worker-apply-search-fail", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={"keyword": "Agent", "enable_search": True},
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.FAILED
    assert any(
        "未能进入搜索页面或执行关键词搜索" in log and "Agent" in log for log in finished_task.logs
    )
    assert not any("Navigated to search results for 'Agent'" in log for log in finished_task.logs)


@pytest.mark.asyncio
async def test_scrape_jobs_handler_clears_filters_when_enable_filter_false(broker, mock_driver):
    """Verify ScrapeJobsHandler actively clears filters when enable_filter is False."""
    mock_title = MagicMock(text="AI 工程师")
    mock_company = MagicMock(text="科技公司")
    mock_salary = MagicMock(text="30-50K")
    mock_desc = MagicMock(text="研发岗位")

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "desc" in value or "tv_job_desc" in value:
            return [mock_desc]
        return [MagicMock()]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-clear-filter", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ScrapeJobsHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "agent",
            "enable_filter": False,
            "max_jobs": 1,
        },
    )

    with patch("boss_agent.feed_pipeline.FilterDialogPage") as mock_filter_cls:
        mock_filter_inst = MagicMock()
        mock_filter_cls.return_value = mock_filter_inst

        executed = await worker.run_once()
        assert executed is True

        mock_filter_inst.clear_filters.assert_called_once()

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("clearing" in log.lower() for log in finished_task.logs)


@pytest.mark.asyncio
async def test_auto_apply_handler_clears_filters_when_no_filter(broker, mock_driver):
    """Verify AutoApplyHandler actively clears filters when no filter conditions are specified."""
    mock_title = MagicMock(text="AI 架构师")
    mock_company = MagicMock(text="科技公司")
    mock_salary = MagicMock(text="30-50K")
    mock_desc = MagicMock(text="研发岗位")

    def mock_find(by, value):
        if "job_name" in value or "tv_job_name" in value:
            return [mock_title]
        if "company_name" in value or "tv_company_name" in value:
            return [mock_company]
        if "salary" in value or "tv_job_salary" in value:
            return [mock_salary]
        if "desc" in value or "tv_job_desc" in value:
            return [mock_desc]
        return [MagicMock()]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-apply-clear-filter", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=MagicMock())],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "agent",
            "enable_filter": True,
            "filter": {},  # empty filter dict -> has_filters is False
        },
    )

    with patch("boss_agent.feed_pipeline.FilterDialogPage") as mock_filter_cls:
        mock_filter_inst = MagicMock()
        mock_filter_cls.return_value = mock_filter_inst

        executed = await worker.run_once()
        assert executed is True

        mock_filter_inst.clear_filters.assert_called_once()

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert any("clearing" in log.lower() for log in finished_task.logs)


@pytest.mark.asyncio
async def test_auto_apply_handler_runs_a_contract_built_launch_as_auto_send(broker, mock_driver):
    '''#298's promise, mechanised: the payload the launch contract builds actually sends.

    The reported bug was a strategy whose Target Action said 自动打招呼 producing a run that
    drafted and stopped. Depth is no longer something a caller states, so the honest test is
    the real artifact — `build_search_launch` over an `auto_apply` SavedSearch, read by the
    worker's own parser — and the log line an operator actually reads.
    '''
    from boss_agent import task_launch
    from boss_agent.models import SavedSearch, SearchConfig

    mock_title = MagicMock(text="Agent 平台工程师")
    mock_company = MagicMock(text="智元创新")
    mock_salary = MagicMock(text="45-70K")
    mock_desc = MagicMock(text="Expertise in Python, LLM agents, and Android automation.")
    mock_elem = MagicMock()

    def mock_find(by, value):
        if "job_name" in value:
            return [mock_title]
        if "company_name" in value:
            return [mock_company]
        if "salary" in value:
            return [mock_salary]
        if "desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-contract", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    mock_llm_client = MagicMock()
    mock_llm_client.chat_completion_json.return_value = {
        "match_score": 92,
        "jd_key_requirements": ["Agent 编排"],
        "match_reasons": ["契合"],
        "greeting_message": "您好，我在 Agent 编排上有完整实战经验！",
    }

    worker = AutomationWorker(
        config=config, broker=broker, context=context,
        handlers=[AutoApplyHandler(llm_client=mock_llm_client)],
    )

    search = SavedSearch(
        id="s-contract",
        name="[测试]打招呼",
        search=SearchConfig(keyword="Agent"),
        target_action="auto_apply",
        max_jobs=1,
    )
    # Exactly what the modal, the strategies page, the dashboard and the cron all build.
    launch = task_launch.build_launch(
        task_launch.TaskKind.SEARCH,
        source=task_launch.LaunchSource.MANUAL,
        search=search,
        min_score=70,
    )
    assert set(launch.payload) & {"preview_only", "auto_send"} == set()

    task = await broker.create_task(
        task_type=launch.task_type,
        payload=dict(launch.payload),
        source=launch.source.value,
    )

    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    # The depth the operator configured, stated once, and honoured by the log line.
    assert any("mode='Auto-Send'" in log for log in finished_task.logs), finished_task.logs
    assert any("Dispatched greeting" in log for log in finished_task.logs), finished_task.logs
    assert not any("Legacy" in log for log in finished_task.logs), finished_task.logs

    records = await broker.job_store.list_job_records()
    applied = [r for r in records if r.get("status") == "applied"]
    assert applied, "an 自动打招呼 run advances a qualified posting to applied"
    assert applied[0]["applied_source"] == "agent_auto_send"
