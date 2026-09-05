"""
tests/unit/test_handlers_scrape_and_apply.py
============================================
Unit tests for SCRAPE_JOBS and AUTO_APPLY polymorphic task handlers (Issue #30).
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
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
        handlers=[ScrapeJobsHandler()],
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
async def test_auto_apply_handler_preview_mode_drafts_greeting(broker, mock_driver):
    """Verify AutoApplyHandler in preview_only mode types greeting draft and pauses without sending."""
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
            "preview_only": True,
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
        handlers=[ScrapeJobsHandler()],
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
