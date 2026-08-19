"""Unit tests for SavedSearch, SavedSearchRegistry, and SmokeHarness integration."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent.models import FilterConfig, JobPosting, SavedSearch, SearchConfig
from boss_agent.searches import (
    SavedSearchRegistry,
)
from boss_agent.workflows import SmokeHarness, TakeoverHandler


def test_saved_search_model_serialization():
    s = SavedSearch(
        id="test_ai_agent",
        name="AI Agent Test",
        description="Test description",
        search=SearchConfig(keyword="AI 算法"),
        filter=FilterConfig(
            education="硕士",
            salary="5万元以上",
            experience="10年以上",
            activity="今日活跃",
            company_scales=["1000-9999人"],
            industries=["在线教育", "游戏", "人工智能"],
        ),
    )
    d = s.to_dict()
    assert d["id"] == "test_ai_agent"
    assert d["search"]["keyword"] == "AI 算法"
    assert d["filter"]["industries"] == ["在线教育", "游戏", "人工智能"]

    restored = SavedSearch.from_dict("test_ai_agent", d)
    assert restored.id == "test_ai_agent"
    assert restored.search.keyword == "AI 算法"
    assert restored.filter.industries == ["在线教育", "游戏", "人工智能"]
    assert restored.filter.education == "硕士"


def test_saved_search_registry_load_yaml():
    registry = SavedSearchRegistry()
    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "searches.yaml"
    assert yaml_path.exists(), f"Configuration file missing: {yaml_path}"

    registry.load_from_yaml(str(yaml_path))
    searches = registry.list_all()
    assert len(searches) >= 1

    # Verify default startup query
    default_search = registry.get("default_agent_search")
    assert default_search is not None
    assert default_search.search.keyword == "agent"
    assert "在线教育" in default_search.filter.industries
    assert "游戏" in default_search.filter.industries
    assert "人工智能" in default_search.filter.industries


def test_saved_search_registry_unknown_id():
    registry = SavedSearchRegistry()
    with pytest.raises(KeyError, match="Saved search 'unknown_id' not found"):
        registry.get("unknown_id")


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
