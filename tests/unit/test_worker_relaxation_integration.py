"""
tests/unit/test_worker_relaxation_integration.py
================================================
End-to-end worker integration for App-Enforced Filters & Whitelist Relaxation
(Ticket #191, Spec #187): graph output state (app_rule_violation,
relaxed_by_whitelist, relaxation_reason) must be captured by AUTO_APPLY and
SCRAPE_JOBS handlers and persisted in job_records audit fields.
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import JobPosting
from boss_agent.pages import JobCardBrief
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


def _mock_driver():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    return driver


def _headhunter_posting():
    return JobPosting(
        title="大模型 Agent 平台架构师",
        company_name="某人力资源服务公司",
        salary_range="40-60K",
        job_description=(
            "主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
            "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
        ),
        recruiter_name="钟先生 · 猎头顾问",
    )


@pytest.mark.asyncio
async def test_auto_apply_rescued_headhunter_job_persists_relaxation_audit():
    """AUTO_APPLY: headhunter job violating direct_only but rescued by whitelist
    relaxation proceeds to greeting draft and persists relaxed_by_whitelist +
    screening_audit in the job record."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-relax-apply"), driver=_mock_driver())
    handler = AutoApplyHandler(llm_client=_drafting_llm())

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "preview_only": True,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["大模型"],
            },
        },
    )

    with (
        patch("boss_agent.worker.handlers.auto_apply.StartupDialogPage") as startup_cls,
        patch("boss_agent.worker.handlers.auto_apply.JobListPage"),
        patch("boss_agent.worker.handlers.auto_apply.SearchPage") as search_cls,
        patch("boss_agent.worker.handlers.auto_apply.JobDetailPage") as detail_cls,
        patch("boss_agent.worker.handlers.auto_apply.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _headhunter_posting()

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output.get("applied") is False  # preview/offline draft mode

    records = await broker.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    assert rec["status"] == "matched"
    assert rec.get("relaxed_by_whitelist") is True
    audit = rec.get("screening_audit") or ""
    assert "direct_only" in audit, "app rule violation must be captured in audit trail"
    assert "大模型" in audit, "matched whitelist token must be captured in audit trail"

    finished = await broker.get_task(task.id)
    assert any("白名单放宽" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_auto_apply_app_rule_rejection_persists_ignored_record():
    """AUTO_APPLY: headhunter job violating direct_only without whitelist rescue
    must persist an ignored record with the violation reason and never reach LLM."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-reject-apply"), driver=_mock_driver())
    llm = _drafting_llm()
    handler = AutoApplyHandler(llm_client=llm)

    posting = _headhunter_posting()
    posting.title = "云原生平台工程师"
    posting.job_description = (
        "负责容器平台与云原生基础设施建设，精通Go与Kubernetes，主导服务网格治理。"
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "平台",
            "preview_only": True,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["量子计算"],
            },
        },
    )

    with (
        patch("boss_agent.worker.handlers.auto_apply.StartupDialogPage") as startup_cls,
        patch("boss_agent.worker.handlers.auto_apply.JobListPage"),
        patch("boss_agent.worker.handlers.auto_apply.SearchPage") as search_cls,
        patch("boss_agent.worker.handlers.auto_apply.JobDetailPage") as detail_cls,
        patch("boss_agent.worker.handlers.auto_apply.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = posting

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output.get("status") == "filtered_by_app_rule"
    assert result.output.get("applied") is False

    ignored = await broker.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert "direct_only" in (ignored[0].get("screened_reason") or "")
    assert ignored[0].get("relaxed_by_whitelist") in (False, None)

    # Rejected job must never consume LLM tokens
    llm.chat_completion_json.assert_not_called()


def _drafting_llm():
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 91,
        "jd_key_requirements": ["大模型平台架构"],
        "match_reasons": ["具备LLM推理链编排实战经验"],
        "greeting_message": "您好，看到贵司大模型Agent平台架构师岗位，我在推理链编排与多智能体落地有成熟经验...",
    }
    return llm


@pytest.mark.asyncio
async def test_scrape_jobs_relaxed_headhunter_card_persists_audit_fields():
    """SCRAPE_JOBS: headhunter card violating direct_only but hitting whitelist is
    rescued, navigated, and persisted with relaxed_by_whitelist + screening_audit."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-relax-scrape"), driver=_mock_driver())
    handler = ScrapeJobsHandler()

    card_elem = MagicMock()
    card = JobCardBrief(
        title="大模型技术负责人",
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
        tags=["LLM"],
        digest="负责大模型应用平台与Agent工作流架构",
        element=card_elem,
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "大模型",
            "max_jobs": 5,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["大模型"],
            },
        },
    )

    with (
        patch("boss_agent.worker.handlers.scrape_jobs.StartupDialogPage") as startup_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobListPage") as list_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.SearchPage") as search_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [card]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = JobPosting(
            title="大模型技术负责人",
            company_name="某人力资源服务公司",
            salary_range="40-60K",
            job_description="完整职位要求：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排。",
            recruiter_name="钟先生",
            recruiter_title="猎头顾问",
            is_headhunter=True,
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["skipped_count"] == 0
    assert result.output["scraped_count"] == 1
    card_elem.click.assert_called_once()

    records = await broker.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型技术负责人")
    assert rec.get("relaxed_by_whitelist") is True
    audit = rec.get("screening_audit") or ""
    assert "direct_only" in audit and "大模型" in audit


@pytest.mark.asyncio
async def test_scrape_jobs_app_rule_violation_without_rescue_is_ignored():
    """SCRAPE_JOBS: headhunter card violating direct_only without whitelist hit is
    short-circuited to an ignored record without opening the detail page."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-reject-scrape"), driver=_mock_driver())
    handler = ScrapeJobsHandler()

    card_elem = MagicMock()
    card = JobCardBrief(
        title="云原生平台工程师",
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
        tags=["K8s"],
        digest="负责容器平台与云原生基础设施建设",
        element=card_elem,
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "平台",
            "max_jobs": 5,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["量子计算"],
            },
        },
    )

    with (
        patch("boss_agent.worker.handlers.scrape_jobs.StartupDialogPage") as startup_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobListPage") as list_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.SearchPage") as search_cls,
        patch("boss_agent.worker.handlers.scrape_jobs.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [card]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = JobPosting(
            title="云原生平台工程师",
            company_name="某人力资源服务公司",
            salary_range="30-45K",
            job_description="完整职位要求：负责容器平台与云原生基础设施建设，精通Go语言。",
            recruiter_name="钟先生",
            recruiter_title="猎头顾问",
            is_headhunter=True,
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["skipped_count"] == 1
    assert result.output["scraped_count"] == 0
    card_elem.click.assert_not_called()
    detail_cls.return_value.extract_job_posting.assert_not_called()

    ignored = await broker.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert "direct_only" in (ignored[0].get("screened_reason") or "")
    assert "direct_only" in (ignored[0].get("screening_audit") or "")
