"""
tests/unit/test_scrape_jobs_dedup.py
====================================
Unit tests for card-level fingerprint extraction and deduplication skip in ScrapeJobsHandler.
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import JobPosting, compute_job_fingerprint
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


@pytest.mark.asyncio
async def test_scrape_jobs_handler_direct_ingestion_even_when_detail_fails():
    """If detail page extraction fails or times out, the job card is still persisted as unmatched."""
    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    card_elem = MagicMock()
    card = JobCardBrief(
        title="Agent研发架构师",
        company_name="知名互联网公司",
        recruiter_name="李先生·猎头顾问",
        salary_range="7-10万元·16薪",
        location="上海",
        tags=["3-5年", "本科"],
        snippet="负责基于agent的devops体系的架构",
        element=card_elem,
    )

    from boss_agent.worker.config import WorkerConfig

    handler = ScrapeJobsHandler()
    context = WorkerContext(config=WorkerConfig(worker_id="test-worker"), driver=mock_driver)
    task = AutomationTask(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "agent", "max_jobs": 3},
    )

    with (
        patch("boss_agent.worker.handlers.scrape_jobs.StartupDialogPage") as mock_startup_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobListPage") as mock_list_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.SearchPage") as mock_search_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobDetailPage") as mock_detail_cls,
    ):
        mock_startup_cls.return_value.is_dialog_present.return_value = False
        mock_list = mock_list_cls.return_value
        mock_list.extract_visible_job_cards.return_value = [card]

        mock_search = mock_search_cls.return_value
        mock_search.is_search_page.return_value = True

        # Detail extraction fails/times out
        mock_detail = mock_detail_cls.return_value
        mock_detail.extract_job_posting.side_effect = RuntimeError("Detail screen did not load")

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["scraped_count"] == 1
    # Card is safely persisted in unmatched job records despite detail failure!
    records = await broker.list_job_records(status="unmatched")
    assert len(records) == 1
    assert records[0]["title"] == "Agent研发架构师"
    assert records[0]["company_name"] == "知名互联网公司"
    assert records[0]["recruiter_name"] == "李先生"
    assert records[0]["recruiter_title"] == "猎头顾问"
    assert records[0]["is_headhunter"] is True
    assert records[0]["salary_range"] == "7-10万元·16薪"
    assert records[0]["location"] == "上海"
    assert records[0]["digest"] == "负责基于agent的devops体系的架构"
    assert records[0]["job_description"] == ""
    assert records[0]["search_keywords"] == ["agent"]


def test_extract_visible_job_cards_parser():
    """extract_visible_job_cards accurately parses cards with salary, location, recruiter, tags, and snippet."""
    from boss_agent.pages import JobListPage

    mock_driver = MagicMock()
    mock_card = MagicMock()

    # Emulate the sub-elements of card 1 from Image 1:
    # ["Agent研发架构师", "7-10万元·16薪", "某大型知名互联网公司 10000人... 互联网", "3-5年", "本科", "负责基于agent的devops体系的架构", "李先生·猎头顾问 上海"]
    texts = [
        "猎",
        "Agent研发架构师",
        "7-10万元·16薪",
        "某大型知名互联网公司 10000人... 互联网",
        "3-5年",
        "本科",
        "负责基于agent的devops体系的架构",
        "李先生·猎头顾问 上海",
    ]
    sub_elems = []
    for t in texts:
        e = MagicMock()
        e.text = t
        sub_elems.append(e)

    def mock_find(by, value):
        if by == "xpath" and "@text" in value:
            return sub_elems
        return []

    mock_card.find_elements.side_effect = mock_find
    mock_driver.find_elements.return_value = [mock_card]

    page = JobListPage(mock_driver)
    briefs = page.extract_visible_job_cards(max_cards=5)

    assert len(briefs) == 1
    b = briefs[0]
    assert b.title == "Agent研发架构师"
    assert b.salary_range == "7-10万元·16薪"
    assert b.company_name == "某大型知名互联网公司"
    assert b.company_scale == "10000人"
    assert "互联网" in b.industry
    assert b.recruiter_name == "李先生"
    assert b.recruiter_title == "猎头顾问"
    assert b.is_headhunter is True
    assert b.location == "上海"
    assert "3-5年" in b.tags
    assert "本科" in b.tags
    assert b.snippet == "负责基于agent的devops体系的架构"
    assert b.fingerprint != ""


@pytest.mark.asyncio
async def test_scrape_jobs_handler_preliminary_card_screening_and_enrichment():
    """Verify that cards failing preliminary screening skip detail click and are saved as ignored, while passing cards enrich full JD."""
    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Card 1: Disqualified by preliminary screening (blacklist in digest)
    card1_elem = MagicMock()
    card1 = JobCardBrief(
        title="Python开发工程师",
        company_name="外包服务公司",
        recruiter_name="外包HR",
        tags=["Python"],
        digest="此岗位需长期在客户现场驻场办公开发",
        element=card1_elem,
    )

    # Card 2: Passes preliminary screening
    card2_elem = MagicMock()
    card2 = JobCardBrief(
        title="AI Agent研发架构师",
        company_name="前沿智能",
        recruiter_name="技术总监",
        tags=["LLM", "Agent"],
        digest="负责核心智能体工作流平台搭建",
        element=card2_elem,
    )

    from boss_agent.worker.config import WorkerConfig

    handler = ScrapeJobsHandler()
    context = WorkerContext(config=WorkerConfig(worker_id="test-worker"), driver=mock_driver)
    task = AutomationTask(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "agent",
            "max_jobs": 5,
            "screening_policy": {
                "title_whitelist": ["Agent", "Python"],
                "jd_blacklist": ["驻场", "外包"],
            },
        },
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
            title="AI Agent研发架构师",
            company_name="前沿智能",
            salary_range="40-60K",
            job_description="完整详细岗位职责：1. 负责LangGraph落地；2. 多智能体架构设计与实现...",
            recruiter_name="技术总监",
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["total_scanned"] == 2
    assert result.output["skipped_count"] == 1
    assert result.output["scraped_count"] == 1

    # Card 1 must NOT have been clicked (zero UI navigation)
    card1_elem.click.assert_not_called()

    # Card 2 was clicked
    card2_elem.click.assert_called_once()

    # Verify Card 1 persisted as ignored
    ignored_records = await broker.list_job_records(status="ignored")
    assert len(ignored_records) == 1
    assert ignored_records[0]["title"] == "Python开发工程师"
    assert ignored_records[0]["digest"] == "此岗位需长期在客户现场驻场办公开发"
    assert ignored_records[0]["job_description"] == ""

    # Verify Card 2 persisted as unmatched with enriched full JD and digest
    unmatched_records = await broker.list_job_records(status="unmatched")
    assert len(unmatched_records) == 1
    assert unmatched_records[0]["title"] == "AI Agent研发架构师"
    assert unmatched_records[0]["digest"] == "负责核心智能体工作流平台搭建"
    assert unmatched_records[0]["job_description"].startswith("完整详细岗位职责")


@pytest.mark.asyncio
async def test_scrape_jobs_handler_facet_persistence_and_recruitment_type_telemetry():
    """Verify ScrapeJobsHandler persists company_scale, industry, tags, recruiter_title, and is_headhunter with logs."""
    from boss_agent.worker.config import WorkerConfig

    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    card_hh_elem = MagicMock()
    card_hh = JobCardBrief(
        title="技术负责人-CTO级别 | 医疗AI",
        company_name="某中型人工智能公司",
        recruiter_name="钟先生",
        recruiter_title="猎头顾问",
        company_scale="100-499人",
        industry="人工智能",
        tags=["10年以上", "硕士", "容器技术"],
        digest="核心研发与平台建设领导团队研发医疗领域专用的大模型",
        element=card_hh_elem,
    )

    card_dir_elem = MagicMock()
    card_dir = JobCardBrief(
        title="技术负责人",
        company_name="深至科技",
        recruiter_name="农女士",
        recruiter_title="高级招聘专员",
        company_scale="100-499人",
        industry="人工智能",
        tags=["10年以上", "硕士", "互联网/AI"],
        digest="负责核心研发团队管理与前沿算法落地",
        element=card_dir_elem,
    )

    handler = ScrapeJobsHandler()
    context = WorkerContext(config=WorkerConfig(worker_id="test-worker"), driver=mock_driver)
    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "agent", "max_jobs": 2},
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
        mock_list.extract_visible_job_cards.return_value = [card_hh, card_dir]

        mock_search = mock_search_cls.return_value
        mock_search.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.extract_job_posting.side_effect = [
            JobPosting(
                title="技术负责人-CTO级别 | 医疗AI",
                company_name="某中型人工智能公司",
                salary_range="20-26万元·18月",
                job_description="完整职位要求：精通分布式大模型训练与平台架构...",
                recruiter_name="钟先生",
                recruiter_title="猎头顾问",
                company_scale="100-499人",
                industry="人工智能",
                is_headhunter=True,
            ),
            JobPosting(
                title="技术负责人",
                company_name="深至科技",
                salary_range="10-20万元·18月",
                job_description="完整职位要求：负责医疗AI影像分析与模型工程化...",
                recruiter_name="农女士",
                recruiter_title="高级招聘专员",
                company_scale="100-499人",
                industry="人工智能",
                is_headhunter=False,
            ),
        ]

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["scraped_count"] == 2

    records = await broker.list_job_records()
    assert len(records) == 2

    hh_rec = next(r for r in records if r["company_name"] == "某中型人工智能公司")
    assert hh_rec["recruiter_title"] == "猎头顾问"
    assert hh_rec["is_headhunter"] is True
    assert hh_rec["company_scale"] == "100-499人"
    assert hh_rec["industry"] == "人工智能"
    assert hh_rec["tags"] == ["10年以上", "硕士", "容器技术"]
    assert hh_rec["job_description"].startswith("完整职位要求：精通分布式大模型")

    dir_rec = next(r for r in records if r["company_name"] == "深至科技")
    assert dir_rec["recruiter_title"] == "高级招聘专员"
    assert dir_rec["is_headhunter"] is False
    assert dir_rec["company_scale"] == "100-499人"
    assert dir_rec["industry"] == "人工智能"
    assert dir_rec["tags"] == ["10年以上", "硕士", "互联网/AI"]

    # Verify telemetry logs in task
    updated_task = await broker.get_task(task.id)
    assert updated_task is not None
    logs = updated_task.logs
    assert any("[猎头]" in line for line in logs)
    assert any("[直招]" in line for line in logs)


@pytest.mark.asyncio
async def test_scrape_jobs_handler_logs_error_when_jd_contains_view_more():
    """Verify that when JD still contains '查看更多', an explicit error log is recorded in task logs."""
    from boss_agent.worker.config import WorkerConfig

    broker = InMemoryTaskBroker()
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    card_elem = MagicMock()
    card = JobCardBrief(
        title="Agent开发专家",
        company_name="某大厂",
        recruiter_name="张总监",
        element=card_elem,
    )

    handler = ScrapeJobsHandler()
    context = WorkerContext(config=WorkerConfig(worker_id="test-worker"), driver=mock_driver)
    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={"keyword": "agent", "max_jobs": 1},
    )

    with (
        patch("boss_agent.worker.handlers.scrape_jobs.StartupDialogPage") as mock_startup_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobListPage") as mock_list_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.SearchPage") as mock_search_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobDetailPage") as mock_detail_cls,
    ):
        mock_startup_cls.return_value.is_dialog_present.return_value = False
        mock_list_cls.return_value.extract_visible_job_cards.return_value = [card]
        mock_search_cls.return_value.is_search_page.return_value = True

        mock_detail = mock_detail_cls.return_value
        mock_detail.extract_job_posting.return_value = JobPosting(
            title="Agent开发专家",
            company_name="某大厂",
            salary_range="50-80K",
            job_description="岗位职责: 1. 核心智能体研发... 查看更多",
            recruiter_name="张总监",
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    updated_task = await broker.get_task(task.id)
    assert updated_task is not None
    logs = updated_task.logs
    assert any("Incomplete JD Error" in line and "查看更多" in line for line in logs)



