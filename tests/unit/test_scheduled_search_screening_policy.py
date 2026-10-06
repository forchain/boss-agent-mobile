"""
tests/unit/test_scheduled_search_screening_policy.py
===================================================
Regression tests: Scheduled searches must inherit global screening policy (Issue / Defect).
When a SavedSearch has no custom screening_policy configured, scheduled task dispatches
must not stamp an empty policy onto the task payload, which would wipe out the global
title_blacklist, company_blacklist, and other screening rules.
"""

from unittest.mock import patch

import pytest

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.job_entities import JobCardBrief
from boss_agent.screening import CandidateScreener
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig
from boss_agent.task_launch import LaunchSource, TaskKind, build_launch, build_search_launch


def test_saved_search_without_policy_defaults_screening_policy_to_none():
    """A SavedSearch without explicit screening_policy must have screening_policy=None."""
    search = SavedSearch(
        id="s_default",
        name="默认搜索",
        search=SearchConfig(keyword="Agent"),
        filter=FilterConfig(channel_preference="direct_only"),
    )
    assert search.screening_policy is None

    # to_dict must NOT include empty screening_policy
    d = search.to_dict()
    assert "screening_policy" not in d or d.get("screening_policy") is None


def test_saved_search_from_dict_without_policy_deserializes_to_none():
    """Deserializing a saved search dictionary without policy preserves screening_policy=None."""
    raw = {
        "id": "s_pb",
        "name": "来自PocketBase的搜索",
        "keyword": "Agent",
        "filter": {"channel_preference": "direct_only"},
    }
    search = SavedSearch.from_dict(raw["id"], raw)
    assert search.screening_policy is None

    dumped = search.to_dict()
    assert "screening_policy" not in dumped or dumped.get("screening_policy") is None


def test_build_search_launch_without_policy_does_not_stamp_screening_policy():
    """build_search_launch must not inject an empty screening_policy into the task payload."""
    search = SavedSearch(
        id="s_scheduled",
        name="定时调度搜索",
        search=SearchConfig(keyword="英语 Agent"),
        filter=FilterConfig(channel_preference="direct_only"),
    )
    launch = build_launch(TaskKind.SEARCH, source=LaunchSource.SCHEDULER, search=search)

    # Payload must NOT carry an empty screening_policy dict
    assert "screening_policy" not in launch.payload or launch.payload.get("screening_policy") is None


def test_feed_stream_config_inherits_global_policy_when_not_in_payload():
    """Scheduled task payload without screening_policy must resolve global title_blacklist."""
    search = SavedSearch(
        id="s_scheduled",
        name="定时调度搜索",
        search=SearchConfig(keyword="英语 Agent"),
        filter=FilterConfig(channel_preference="direct_only"),
    )
    launch = build_launch(TaskKind.SEARCH, source=LaunchSource.SCHEDULER, search=search)

    with patch("boss_agent.screening_policy.ScreeningPolicy.load_default") as mock_load:
        mock_load.return_value = ScreeningPolicy(
            title_blacklist=["解决方案", "Solution", "产品经理"],
            title_whitelist=["英语", "硕士"],
            company_blacklist=["某某公司"],
            channel_preference="all",
        )
        config = FeedStreamConfig.from_payload(launch.payload)

    assert "产品经理" in config.screening_policy.title_blacklist
    assert "某某公司" in config.screening_policy.company_blacklist
    # Strategy's channel preference is applied on top of the inherited policy
    assert config.screening_policy.channel_preference == "direct_only"


def test_card_screener_rejects_product_manager_under_scheduled_search():
    """End-to-end screener check: Card with '产品经理' must be rejected in scheduled search."""
    search = SavedSearch(
        id="s_scheduled",
        name="定时调度搜索",
        search=SearchConfig(keyword="英语 Agent"),
        filter=FilterConfig(channel_preference="direct_only"),
    )
    launch = build_launch(TaskKind.SEARCH, source=LaunchSource.SCHEDULER, search=search)

    with patch("boss_agent.screening_policy.ScreeningPolicy.load_default") as mock_load:
        mock_load.return_value = ScreeningPolicy(
            title_blacklist=["解决方案", "Solution", "产品经理"],
            channel_preference="all",
        )
        config = FeedStreamConfig.from_payload(launch.payload)

    screener = CandidateScreener()

    card1 = JobCardBrief(
        title="AI产品经理（企业智能体平台）",
        company_name="Inspire Group",
        recruiter_name="李女士",
    )
    verdict1 = screener.evaluate_card(card1, config.screening_policy)
    assert verdict1.passed is False
    assert "产品经理" in verdict1.reason

    card2 = JobCardBrief(
        title="B端 Agent应用产品经理",
        company_name="上海百型智能科技",
        recruiter_name="孙女士",
    )
    verdict2 = screener.evaluate_card(card2, config.screening_policy)
    assert verdict2.passed is False
    assert "产品经理" in verdict2.reason


def test_feed_stream_config_defense_in_depth_against_empty_policy_payload():
    """Defense in depth: Even if payload contains a legacy empty policy dict, inherit global blacklists."""
    empty_payload = {
        "keyword": "Agent",
        "filter": {"channel_preference": "direct_only"},
        "screening_policy": {
            "title_blacklist": [],
            "title_whitelist": [],
            "company_blacklist": [],
            "jd_blacklist": [],
            "business_district_blacklist": [],
            "business_district_inspect_list": [],
            "enable_screening": True,
            "channel_preference": "all",
            "max_commute_distance_km": 40.0,
        },
    }

    with patch("boss_agent.screening_policy.ScreeningPolicy.load_default") as mock_load:
        mock_load.return_value = ScreeningPolicy(
            title_blacklist=["解决方案", "Solution", "产品经理"],
            company_blacklist=["某某外包"],
            channel_preference="all",
        )
        config = FeedStreamConfig.from_payload(empty_payload)

    assert "产品经理" in config.screening_policy.title_blacklist
    assert "某某外包" in config.screening_policy.company_blacklist
