"""Unit tests for SavedSearch, SavedSearchRegistry, and SmokeHarness integration."""

from unittest.mock import MagicMock

import pytest

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
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Agent 专家"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "chat" in value or "editText_with_scrollbar" in value or "btn_chat" in value:
            return []
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        saved_search_id="default_agent_search",
    )

    assert harness.search_config.keyword == "agent"
    assert harness.filter_config.industries == ["在线教育", "游戏", "人工智能"]

    job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    assert job.title == "资深 Agent 专家"
