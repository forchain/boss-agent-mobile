"""
tests/unit/test_handler_prompt_tolerance.py
===========================================
Prompt-document degradation at the inbound edge (issue #461, regression from #453).

Before #453 the handlers never loaded the Greeting Prompt, and a missing Screening
Prompt fell back to the screener's built-in text. #453 made both loads eager at task
start, so a missing or unreadable seed document raised before the device was touched
and failed the whole task. These tests pin the restored tolerance: the edge hands the
screener an empty document instead, which is the falsy value the built-in fallback
already keys on.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent import main as composition
from boss_agent.adapters.inbound.worker.config import WorkerConfig
from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.inbound.worker.handlers import auto_apply, scrape_jobs
from boss_agent.adapters.memory.task_broker import InMemoryTaskBroker
from boss_agent.core.job.domain.feed_stream import FeedStreamResult
from boss_agent.core.job.domain.job_entities import JobCardBrief
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.task.domain.models import TaskType


class _FakePipeline:
    """Stands in for the feed engine so the test observes the edge, not a device run."""

    configs: list = []

    @classmethod
    def for_task(cls, **kwargs):  # noqa: ARG003 - signature mirrors JobFeedPipeline
        cls.screener_kwargs = kwargs["screener"]
        return cls()

    async def stream_jobs(self, config):
        type(self).configs.append(config)
        return FeedStreamResult(scanned=1, jobs=[{"title": "x"}], postings=[])


def _context() -> WorkerContext:
    return WorkerContext(config=WorkerConfig(worker_id="prompt-tolerance"), driver=MagicMock())


def _capture_screener(monkeypatch, captured: dict) -> None:
    def factory(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(composition, "create_llm_client", lambda **_kwargs: MagicMock())
    monkeypatch.setattr(composition, "PocketBaseTaskBroker", MagicMock())
    monkeypatch.setattr(composition, "build_screener", factory)


def _worker_handler(task_type: TaskType):
    worker = composition.build_worker()
    return worker._handlers[task_type]


@pytest.mark.asyncio
async def test_scrape_jobs_survives_a_missing_greeting_prompt_document(monkeypatch):
    """SCRAPE_JOBS never greets, so a missing Greeting Prompt must not fail the task."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(TaskType.SCRAPE_JOBS, payload={"keyword": "agent"})
    captured: dict = {}

    def missing():
        raise FileNotFoundError("greeting_prompt.local.md not found")

    monkeypatch.setattr(composition, "load_greeting_prompt", missing)
    monkeypatch.setattr(composition, "load_screening_prompt", lambda: "# screening")
    _capture_screener(monkeypatch, captured)
    monkeypatch.setattr(scrape_jobs, "JobFeedPipeline", _FakePipeline)

    result = await _worker_handler(TaskType.SCRAPE_JOBS).handle(task, broker, _context())

    assert result.success is True
    assert captured["greeting_prompt"] == ""
    assert captured["screening_prompt"] == "# screening"


@pytest.mark.asyncio
async def test_save_only_auto_apply_survives_an_unreadable_greeting_prompt(monkeypatch):
    """A save-only AUTO_APPLY drafts nothing, so an unreadable Greeting Prompt degrades."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(
        TaskType.AUTO_APPLY,
        payload={
            "target_action": "save_jd",
            "candidate_profile": {"name": "Candidate"},
        },
    )
    captured: dict = {}

    def unreadable():
        raise PermissionError("greeting_prompt.local.md is unreadable")

    monkeypatch.setattr(composition, "load_greeting_prompt", unreadable)
    monkeypatch.setattr(composition, "load_screening_prompt", lambda: "# screening")
    _capture_screener(monkeypatch, captured)
    monkeypatch.setattr(auto_apply, "JobFeedPipeline", _FakePipeline)

    result = await _worker_handler(TaskType.AUTO_APPLY).handle(task, broker, _context())

    assert result.success is True
    assert captured["greeting_prompt"] == ""
    assert _FakePipeline.configs[-1].send_greeting is False


@pytest.mark.asyncio
async def test_a_missing_screening_prompt_falls_back_to_the_built_in_prompt(monkeypatch):
    """A missing Screening Prompt leaves the screener on its built-in rules, not failing."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(TaskType.SCRAPE_JOBS, payload={"keyword": "agent"})
    captured: dict = {}

    def missing():
        raise FileNotFoundError("screening_prompt.local.md not found")

    monkeypatch.setattr(composition, "load_greeting_prompt", lambda: "# greeting")
    monkeypatch.setattr(composition, "load_screening_prompt", missing)
    _capture_screener(monkeypatch, captured)
    monkeypatch.setattr(scrape_jobs, "JobFeedPipeline", _FakePipeline)

    result = await _worker_handler(TaskType.SCRAPE_JOBS).handle(task, broker, _context())

    assert result.success is True
    assert not captured["screening_prompt"]

    # The empty document is what selects the built-in rules populated into the prompt.
    llm = MagicMock()
    llm.chat_completion_json.return_value = {"pass": True, "reason": "ok"}
    screener = CandidateScreener(llm_client=llm, screening_prompt=captured["screening_prompt"])
    screener.evaluate_job(
        card=JobCardBrief(
            title="AI Agent 工程师", company_name="智元创新", recruiter_name="周先生"
        ),
        jd_text="岗位职责：负责大模型智能体编排平台研发，需要扎实的 Python 工程能力。",
        profile=None,
        policy=ScreeningPolicy(jd_blacklist=["Java"]),
        draft_greeting=False,
    )

    system_prompt = llm.chat_completion_json.call_args[0][0][0]["content"]
    assert "你是一名严谨的岗位精筛助手" in system_prompt
