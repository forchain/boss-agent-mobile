"""Unit tests for SavedSearch, SavedSearchRegistry, and SmokeHarness integration."""

from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import GOOD_JD, ScriptedFeed, _card, _detail_page, script_pages, stub_llm

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.job_entities import JobPosting
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig
from boss_agent.searches import (
    SavedSearchRegistry,
)
from boss_agent.workflows import SmokeHarness, TakeoverHandler


def test_saved_search_model_serialization():
    s = SavedSearch(
        id="test_ai_agent",
        name="AI Agent Test",
        description="Test description",
        search=SearchConfig(keyword="AI 算法", enable_search=True),
        filter=FilterConfig(
            education="硕士",
            salary="5万元以上",
            experience="10年以上",
            activity="今日活跃",
            company_scales=["1000-9999人"],
            industries=["在线教育", "游戏", "人工智能"],
            enable_filter=True,
        ),
        enable_search=True,
        enable_filter=True,
    )
    d = s.to_dict()
    assert d["id"] == "test_ai_agent"
    assert d["search"]["keyword"] == "AI 算法"
    assert "enable_search" not in d
    assert "enable_filter" not in d
    assert d["search"]["enable_search"] is True
    assert d["filter"]["enable_filter"] is True
    assert d["filter"]["industries"] == ["在线教育", "游戏", "人工智能"]

    restored = SavedSearch.from_dict("test_ai_agent", d)
    assert restored.id == "test_ai_agent"
    assert restored.search.keyword == "AI 算法"
    assert restored.enable_search is True
    assert restored.enable_filter is True
    assert restored.search.should_search is True
    assert restored.filter.has_filters is True
    assert restored.filter.industries == ["在线教育", "游戏", "人工智能"]
    assert restored.filter.education == "硕士"


def test_saved_search_disabled_search_and_filter():
    s = SavedSearch(
        id="test_recommendations_only",
        name="Recommendations Only",
        search=SearchConfig(keyword="AI", enable_search=False),
        filter=FilterConfig(education="硕士", enable_filter=False),
        enable_search=False,
        enable_filter=False,
    )
    assert s.search.should_search is False
    assert s.filter.has_filters is False
    assert s.filter.has_industry_filters is False

    d = s.to_dict()
    assert "enable_search" not in d
    assert "enable_filter" not in d
    assert d["search"]["enable_search"] is False
    assert d["filter"]["enable_filter"] is False

    restored = SavedSearch.from_dict("test_recommendations_only", d)
    assert restored.enable_search is False
    assert restored.enable_filter is False
    assert restored.search.should_search is False
    assert restored.filter.has_filters is False


def test_saved_search_dual_shape_migration_nested_is_authoritative():
    """When nested and legacy top-level flags conflict, nested spelling is authoritative (Issue #321)."""
    # 1. Nested False, Top-level True -> Nested False wins
    dual_data = {
        "id": "search_conflict",
        "name": "Conflict Strategy",
        "enable_search": True,
        "enable_filter": True,
        "search": {"keyword": "Python", "enable_search": False},
        "filter": {"education": "本科", "enable_filter": False},
    }
    restored = SavedSearch.from_dict(dual_data["id"], dual_data)
    assert restored.enable_search is False
    assert restored.search.enable_search is False
    assert restored.enable_filter is False
    assert restored.filter.enable_filter is False

    # Rewriting emits the single nested shape without top-level flags
    rewritten = restored.to_dict()
    assert "enable_search" not in rewritten
    assert "enable_filter" not in rewritten
    assert rewritten["search"]["enable_search"] is False
    assert rewritten["filter"]["enable_filter"] is False

    # 2. Legacy top-level only -> Read correctly and migrated on next write
    legacy_data = {
        "id": "legacy_search",
        "name": "Legacy Strategy",
        "enable_search": False,
        "enable_filter": False,
        "search": {"keyword": "Rust"},
        "filter": {"education": "硕士"},
    }
    legacy_restored = SavedSearch.from_dict(legacy_data["id"], legacy_data)
    assert legacy_restored.enable_search is False
    assert legacy_restored.enable_filter is False
    migrated = legacy_restored.to_dict()
    assert "enable_search" not in migrated
    assert "enable_filter" not in migrated
    assert migrated["search"]["enable_search"] is False
    assert migrated["filter"]["enable_filter"] is False


def test_saved_search_registry_default_initialization():
    registry = SavedSearchRegistry(prefer_database=False)
    searches = registry.list_all()
    assert len(searches) >= 2

    # Verify default startup query
    default_search = registry.get("default_agent_search")
    assert default_search is not None
    assert default_search.search.keyword == "agent"
    assert "在线教育" in default_search.filter.industries
    assert "游戏" in default_search.filter.industries
    assert "人工智能" in default_search.filter.industries


def test_saved_search_registry_load_from_dict():
    registry = SavedSearchRegistry(prefer_database=False, initial_searches={})
    custom_data = {
        "searches": {
            "custom_search": {
                "name": "Custom Search",
                "search": {"keyword": "rust"},
                "filter": {"education": "本科"},
            }
        }
    }
    registry.load_from_dict(custom_data)
    assert len(registry.list_all()) == 1
    s = registry.get("custom_search")
    assert s.search.keyword == "rust"
    assert s.filter.education == "本科"


def test_saved_search_registry_unknown_id():
    registry = SavedSearchRegistry(prefer_database=False, initial_searches={})
    with pytest.raises(KeyError, match="Saved search 'unknown_id' not found"):
        registry.get("unknown_id")


def test_saved_search_registry_load_pocketbase():
    from unittest.mock import patch

    registry = SavedSearchRegistry(prefer_database=False)
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "items": [
            {
                "id": "db_agent_search",
                "name": "DB Agent Search",
                "keyword": "agent",
                "filter": {"education": "硕士", "industries": ["人工智能"]},
                "is_enabled": True,
                "cron_expression": "0 9 * * *",
                "target_task_type": "AUTO_APPLY",
            }
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        loaded = registry.load_from_pocketbase("http://127.0.0.1:8090")
        assert loaded is True
        s = registry.get("db_agent_search")
        assert s.name == "DB Agent Search"
        assert s.search.keyword == "agent"
        assert s.filter.education == "硕士"
        assert s.is_enabled is True
        assert s.cron_expression == "0 9 * * *"


def test_smoke_harness_with_saved_search_id():
    """A saved search id becomes the run, and the run's extraction is what comes back.

    Issue #390 collapsed the harness onto the pipeline; this test was left behind, still
    driving a feed nothing scripted and asserting ``isinstance(job, JobPosting)`` over a
    MagicMock driver — a claim any object of that type satisfies, so it pinned the return
    type and not the behaviour its name states. It is re-seated on the same scripted-device
    seam as the harness's siblings, and asserts both halves of what resolving a preset is
    for: the preset's search, filters and screening policy reach the ``FeedStreamConfig``
    the engine was handed, and the posting that comes back is the one that run extracted.
    """
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=TakeoverHandler(mock_driver, auto_confirm_for_test=True),
        saved_search_id="default_agent_search",
        # No greeting. The subject here is what a preset id resolves to, and the greeting
        # path loads a candidate profile from ``config/candidate_memory.json`` — a file on
        # the developer's checkout rather than a fixture, so the run would answer
        # differently on someone else's machine.
        enable_greeting_draft=False,
    )

    assert harness.search_config.keyword == "agent"
    assert harness.filter_config.industries == ["在线教育", "游戏", "人工智能"]

    posting = JobPosting(
        title="资深 Agent 专家",
        company_name="智元创新",
        salary_range="40-60K",
        job_description=GOOD_JD,
    )
    configs: list[FeedStreamConfig] = []

    async def _record(config, on_job=None):
        configs.append(config)
        return await _real_stream(config, on_job)

    _real_stream = harness.pipeline.stream_jobs
    harness.pipeline.stream_jobs = _record
    script_pages(
        harness.pipeline,
        feed=ScriptedFeed([[_card("资深 Agent 专家", "智元创新")]]),
        detail=_detail_page(posting=posting),
    )

    with (
        patch("time.sleep", return_value=None),
        # ``SAVE_JD`` does not skip the semantic screen — the pipeline evaluates before it
        # branches on the target action — so a preset whose policy carries blacklists puts
        # a live client one call away. It has to be a stub: a screener handed none builds a
        # real one and fails open, which costs a round trip and a token bill without
        # failing anything.
        patch("boss_agent.llm_config.create_llm_client", return_value=stub_llm()),
    ):
        job = harness.run_smoke_test()

    assert len(configs) == 1, "a verification run is exactly one feed run"
    assert configs[0].keyword == "agent"
    assert configs[0].filter_config is harness.filter_config
    assert configs[0].screening_policy is harness.screening_policy
    assert job is posting, "the posting handed back is the one the engine extracted"
    assert job.title == "资深 Agent 专家"
