"""
tests/unit/test_job_core_injection.py
=====================================
The job aggregate's injected seams (ticket #453).

Three properties this ticket moved out of construction-time convenience and into
explicit arguments:

* the feed engine receives its device world (``FeedDeviceWorld``) instead of building
  page objects, so it never touches Appium;
* ``FeedStreamConfig.from_payload`` overlays a payload on already-resolved defaults and
  reads no file or environment itself;
* ``ScreeningPolicy`` is a pure rule object, and the Job Record Store port lives in core.

The static guard at the bottom is the one that keeps the whole ``core/job`` subtree out
of the adapter import graph.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from boss_agent.core.job.domain.feed_stream import FeedStreamConfig
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.job.interfaces.device import FeedDeviceWorld
from boss_agent.core.job.interfaces.store import JobRecordStore
from boss_agent.core.job.use_cases.feed_pipeline import JobFeedPipeline
from boss_agent.core.job.use_cases.matching import JobMatchGreetingService
from boss_agent.core.shared.enums import ChatButtonState
from boss_agent.job_store import InMemoryJobRecordStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORE_JOB_ROOT = REPO_ROOT / "src" / "boss_agent" / "core" / "job"

FORBIDDEN_CORE_ROOTS = (
    "boss_agent.adapters",
    "boss_agent.job_store",
    "boss_agent.worker",
    "selenium",
    "appium",
    "droid_agent_core.driver",
)


def _world() -> FeedDeviceWorld:
    world = FeedDeviceWorld(
        startup_page=MagicMock(),
        list_page=MagicMock(),
        search_page=MagicMock(),
        detail_page=MagicMock(),
        chat_page=MagicMock(),
        filter_page=MagicMock(),
        industry_filter_page=MagicMock(),
    )
    world.startup_page.is_dialog_present.return_value = False
    world.list_page.get_feed_bottom_boundary.return_value = None
    world.list_page.is_feed_bottom_reached.return_value = True
    world.list_page.extract_visible_job_cards.return_value = []
    world.detail_page.get_chat_button_state.return_value = ChatButtonState.UNCONTACTED
    world.detail_page.extract_job_posting.return_value = JobPosting(
        title="AI Agent 平台工程师",
        company_name="智元创新",
        salary_range="40-60K",
        recruiter_name="王女士",
        job_description=(
            "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
            "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
        ),
    )
    return world


def test_constructing_the_pipeline_never_builds_a_page_object() -> None:
    """The engine composes from the bundle it is handed, not from a driver."""
    world = _world()
    with (
        patch(
            "boss_agent.adapters.outbound.device.pages.feed_bundle.StartupDialogPage"
        ) as startup_cls,
        patch("boss_agent.adapters.outbound.device.pages.feed_bundle.JobListPage") as list_cls,
        patch("boss_agent.adapters.outbound.device.pages.feed_bundle.SearchPage") as search_cls,
        patch("boss_agent.adapters.outbound.device.pages.feed_bundle.JobDetailPage") as detail_cls,
        patch("boss_agent.adapters.outbound.device.pages.feed_bundle.ChatPage") as chat_cls,
    ):
        pipeline = JobFeedPipeline(world=world, store=InMemoryJobRecordStore())

    for page_cls in (startup_cls, list_cls, search_cls, detail_cls, chat_cls):
        page_cls.assert_not_called()

    assert pipeline.startup_page is world.startup_page
    assert pipeline.list_page is world.list_page
    assert pipeline.search_page is world.search_page
    assert pipeline.detail_page is world.detail_page
    assert pipeline.chat_page is world.chat_page


@pytest.mark.asyncio
async def test_a_scripted_bundle_drives_a_run_end_to_end() -> None:
    """The injected bundle is the device surface a real run talks to."""
    world = _world()
    pipeline = JobFeedPipeline(world=world, store=InMemoryJobRecordStore())

    result = await pipeline.stream_jobs(FeedStreamConfig(enable_search=False, max_jobs=1))

    world.list_page.navigate_to_home.assert_called_once()
    world.list_page.extract_visible_job_cards.assert_called()
    world.filter_page.clear_filters.assert_called_once()
    assert result.error_message is None


def test_feed_config_overlays_payload_on_explicit_defaults() -> None:
    """Every operator-resolved default is an argument, not a mid-construction read."""
    config = FeedStreamConfig.from_payload(
        None,
        default_policy=ScreeningPolicy(title_blacklist=["Java"]),
        default_daily_greeting_limit=7,
        default_cooldown_days=45,
    )

    assert config.screening_policy.title_blacklist == ["Java"]
    assert config.daily_greeting_limit == 7
    assert config.cooldown_days == 45


def test_payload_cooldown_zero_is_honoured_over_the_resolved_default() -> None:
    """0 means permanent suppression and must never be confused with "unset"."""
    config = FeedStreamConfig.from_payload(
        {"communication_cooldown_days": 0}, default_cooldown_days=45
    )

    assert config.cooldown_days == 0


def test_screening_policy_is_a_pure_rule_object() -> None:
    """Loading/saving/persisting moved to the Configuration Realm adapter."""
    for removed in ("load_default", "save_default", "persist_company_blacklist"):
        assert not hasattr(ScreeningPolicy, removed), f"ScreeningPolicy still owns {removed}"

    policy = ScreeningPolicy.from_dict({"title_blacklist": ["Java"]})
    passed, reason = policy.matches_card_keywords(title="高级 Java 开发工程师")

    assert passed is False
    assert "Java" in reason
    assert ScreeningPolicy.from_dict(policy.to_dict()).to_dict() == policy.to_dict()


def test_job_record_store_port_lives_in_core() -> None:
    """Consumers depend on the port without importing the adapter module."""
    assert JobRecordStore.__module__ == "boss_agent.core.job.interfaces.store"


def test_greeting_service_requires_an_injected_client() -> None:
    """No constructor may reach for the configuration realm on its own."""
    with pytest.raises(TypeError):
        JobMatchGreetingService()  # type: ignore[call-arg]


def _imported_modules(path: Path) -> set[str]:
    """Every absolute module an import statement in ``path`` names."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ".".join(path.relative_to(REPO_ROOT / "src").with_suffix("").parts[:-1])
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                parts = parts[: max(0, len(parts) - (node.level - 1))]
                if node.module:
                    parts = [*parts, *node.module.split(".")]
                modules.add(".".join(parts))
            elif node.module:
                modules.add(node.module)
    return modules


def test_core_job_imports_only_core_and_the_allowed_framework_seam() -> None:
    """``core/job`` may import stdlib, ``boss_agent.core.*`` and the LLM decision port."""
    offenders: list[str] = []
    for path in CORE_JOB_ROOT.rglob("*.py"):
        for module in _imported_modules(path):
            if any(
                module == root or module.startswith(f"{root}.") for root in FORBIDDEN_CORE_ROOTS
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module}")

    assert offenders == [], "core/job reached outside its layer:\n" + "\n".join(offenders)
