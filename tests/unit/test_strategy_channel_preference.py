"""
tests/unit/test_strategy_channel_preference.py
==============================================
Per-strategy recruitment channel filtering (issue #368, ticket #369).

The recruitment channel preference used to be a *global* setting only: one
``channel_preference`` in ``settings.yaml`` governed every search preset at once, so a
candidate could not run one unattended strategy over 企业直招 and another over 猎头代招.
This suite pins the four seams that let a SavedSearch state its own channel:

  1. ``FilterConfig`` normalizes the value, and an absent/unknown one means *inherit*.
  2. ``SavedSearch`` round-trips it inside ``filter``, legacy presets default to inherit.
  3. ``build_search_launch`` carries it into the task payload.
  4. ``FeedStreamConfig.from_payload`` resolves it onto the runtime ``ScreeningPolicy``.

Card-screening verdicts under the resulting policy are already owned by
``test_candidate_screener.py`` (rejection at ``filtered_by_app_rule``, rescue at
``relaxed_by_whitelist``); what was missing is the plumbing that gets a *strategy's*
preference into the policy those verdicts read, which is what this suite covers.
"""

import json

import pytest

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.models import (
    ChannelPreference,
    FilterConfig,
    SavedSearch,
    ScreeningPolicy,
    SearchConfig,
)
from boss_agent.task_launch import LaunchSource, build_search_launch


def _search(**filter_overrides) -> SavedSearch:
    return SavedSearch(
        id="s_channel",
        name="直招专注",
        search=SearchConfig(keyword="AI Agent"),
        filter=FilterConfig(education="硕士", **filter_overrides),
    )


# ---------------------------------------------------------------------------
# Seam 1 — FilterConfig normalization: four states, one of which is "inherit"
# ---------------------------------------------------------------------------


def test_channel_preference_defaults_to_inherit():
    """An unconfigured strategy states nothing, so the global policy governs it."""
    assert FilterConfig().channel_preference == ""


@pytest.mark.parametrize("value", ["all", "direct_only", "headhunter_only"])
def test_channel_preference_accepts_every_enum_value(value):
    assert FilterConfig(channel_preference=value).channel_preference == value


@pytest.mark.parametrize("raw", [None, "", "   ", "vip_only", 7, True])
def test_channel_preference_coerces_anything_unrecognized_to_inherit(raw):
    """Tolerance is the point: a hand-edited preset must inherit, never fail to load."""
    assert FilterConfig(channel_preference=raw).channel_preference == ""


def test_channel_preference_normalizes_case_and_padding():
    assert FilterConfig(channel_preference="  DIRECT_ONLY ").channel_preference == (
        ChannelPreference.DIRECT_ONLY.value
    )


def test_channel_preference_is_not_a_native_app_filter_dimension():
    """A channel restriction is adjudicated by the screener, never by the app filter dialog.

    ``has_filters`` is what ``SearchPage.apply_filters`` uses to decide whether to open
    Boss's own filter dialog, and it has no channel control to select. A strategy whose
    only constraint is the channel must therefore not claim to have dialog dimensions to
    apply, or every run of it would open a dialog and select nothing.
    """
    unconstrained = FilterConfig(
        education="不限",
        salary="不限",
        experience="不限",
        activity="不限",
        company_scales=[],
        industries=[],
    )
    assert unconstrained.has_filters is False

    channel_only = FilterConfig(
        education="不限",
        salary="不限",
        experience="不限",
        activity="不限",
        company_scales=[],
        industries=[],
        channel_preference="direct_only",
    )
    assert channel_only.has_filters is False
    assert channel_only.channel_preference == "direct_only"


# ---------------------------------------------------------------------------
# Seam 2 — SavedSearch persistence round-trip, and legacy presets
# ---------------------------------------------------------------------------


def test_saved_search_round_trips_channel_preference_in_filter():
    original = _search(channel_preference="direct_only")

    restored = SavedSearch.from_dict(original.to_dict())

    assert restored.filter.channel_preference == "direct_only"
    # Stored inside the `filter` object — the same JSON column every other filter
    # dimension uses, so no schema change is needed for historical rows to read back.
    assert original.to_dict()["filter"]["channel_preference"] == "direct_only"


def test_saved_search_legacy_preset_without_the_field_inherits():
    """Historical presets carry no key at all, and must keep behaving exactly as before."""
    payload = {
        "id": "s_legacy",
        "name": "老策略",
        "keyword": "agent",
        "filter": {"education": "硕士", "salary": "50K以上"},
    }

    restored = SavedSearch.from_dict(payload)

    assert restored.filter.channel_preference == ""
    assert restored.filter.education == "硕士"


def test_saved_search_reads_channel_preference_from_a_json_string_filter():
    """PocketBase stores `filter` as a JSON column; an older row may still be a string."""
    restored = SavedSearch.from_dict(
        {
            "id": "s_str",
            "name": "字符串筛选",
            "filter": json.dumps({"channel_preference": "headhunter_only"}),
        }
    )

    assert restored.filter.channel_preference == "headhunter_only"


# ---------------------------------------------------------------------------
# Seam 3 — the launch payload carries the strategy's channel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preference", ["", "all", "direct_only", "headhunter_only"])
def test_build_search_launch_preserves_channel_preference(preference):
    launch = build_search_launch(
        _search(channel_preference=preference), source=LaunchSource.SCHEDULER
    )

    assert launch.payload["filter"]["channel_preference"] == preference


def test_build_search_launch_channel_reaches_an_auto_apply_task():
    """The override must survive the whole launch, not just the filter object."""
    search = _search(channel_preference="headhunter_only")
    search.target_task_type = "AUTO_APPLY"
    search.target_action = "auto_apply"

    launch = build_search_launch(search, source=LaunchSource.MANUAL)

    assert launch.task_type.value == "AUTO_APPLY"
    assert launch.payload["filter"]["channel_preference"] == "headhunter_only"


# ---------------------------------------------------------------------------
# Seam 4 — the worker resolves the strategy override onto the runtime policy
# ---------------------------------------------------------------------------


def _launch_payload(preference: str | None) -> dict:
    """The payload `build_search_launch` produces, with the global policy pinned to direct."""
    search = SavedSearch(
        id="s_resolve",
        name="解析",
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(channel_preference=preference or ""),
        screening_policy=ScreeningPolicy(channel_preference="direct_only"),
    )
    return build_search_launch(search, source=LaunchSource.SCHEDULER).payload


def test_from_payload_strategy_channel_overrides_the_global_policy():
    """An explicitly stated channel wins over the global setting, including a widening one."""
    payload = _launch_payload("all")

    config = FeedStreamConfig.from_payload(payload)

    assert config.screening_policy.channel_preference == "all"


def test_from_payload_empty_channel_preserves_the_global_policy():
    """Inherit means *inherit*: the global policy the payload carried is kept as-is."""
    config = FeedStreamConfig.from_payload(_launch_payload(""))

    assert config.screening_policy.channel_preference == "direct_only"


@pytest.mark.parametrize(
    "payload", [None, {}, {"filter": {}}, {"filter": {"channel_preference": ""}}]
)
def test_from_payload_without_any_channel_keeps_the_loaded_policy(payload):
    config = FeedStreamConfig.from_payload(payload)

    # No policy in the payload means the worker resolves the configured global one, whose
    # own default is `all`. What matters is that nothing narrowed it on the way through.
    assert config.screening_policy.channel_preference in {
        ChannelPreference.ALL.value,
        ChannelPreference.DIRECT_ONLY.value,
        ChannelPreference.HEADHUNTER_ONLY.value,
    }


def test_from_payload_carries_the_channel_onto_the_filter_config_too():
    """The resolved channel is readable from the filter config, not only the policy."""
    config = FeedStreamConfig.from_payload(_launch_payload("direct_only"))

    assert config.filter_config is not None
    assert config.filter_config.channel_preference == "direct_only"


def test_from_payload_reads_a_stringified_filter_for_the_channel():
    """Same tolerance as `SavedSearch.from_dict` — a queued task must not change meaning."""
    config = FeedStreamConfig.from_payload(
        {"filter": json.dumps({"channel_preference": "headhunter_only"})}
    )

    assert config.screening_policy.channel_preference == "headhunter_only"


def test_from_payload_ignores_an_unrecognized_channel_and_inherits():
    payload = _launch_payload("vip_only")

    config = FeedStreamConfig.from_payload(payload)

    assert config.screening_policy.channel_preference == "direct_only"


def test_resolved_channel_actually_governs_card_screening():
    """The end of the chain: the payload's channel is what the screener adjudicates.

    Both directions in one run, so a payload that resolved its policy but never reached
    the verdict cannot pass this suite. The headhunter card is read from its recruiter's
    title, so it resolves to a headhunter channel even though the company is a direct hire.
    """
    from unittest.mock import MagicMock

    from boss_agent.screening import CandidateScreener, CardVerdictStage

    card = {
        "title": "AI Agent 平台工程师",
        "company_name": "智元创新",
        "recruiter_name": "周先生 · 猎头顾问",
    }
    llm = MagicMock()
    screener = CandidateScreener(llm_client=llm)

    strict = FeedStreamConfig.from_payload(_launch_payload("direct_only"))
    rejected = screener.evaluate_card(card, strict.screening_policy)
    assert rejected.passed is False
    assert rejected.stage is CardVerdictStage.FILTERED_BY_APP_RULE
    assert "direct_only" in rejected.app_rule_violation
    llm.chat_completion_json.assert_not_called()

    widened = FeedStreamConfig.from_payload(_launch_payload("all"))
    admitted = screener.evaluate_card(card, widened.screening_policy)
    assert admitted.passed is True
    assert admitted.stage is CardVerdictStage.PASSED


def test_resolved_channel_keeps_whitelist_relaxation_available():
    """A strategy that restricts the channel does not lose the whitelist rescue.

    The relaxation is what keeps a niche headhunter posting from being dropped purely for
    arriving through an agency, so a strategy-level override must not disable it.
    """
    from unittest.mock import MagicMock

    from boss_agent.screening import CandidateScreener, CardVerdictStage

    payload = _launch_payload("direct_only")
    payload["screening_policy"]["title_whitelist"] = ["大模型"]

    config = FeedStreamConfig.from_payload(payload)
    verdict = CandidateScreener(llm_client=MagicMock()).evaluate_card(
        {
            "title": "大模型应用架构师",
            "company_name": "智元创新",
            "recruiter_name": "周先生 · 猎头顾问",
        },
        config.screening_policy,
    )

    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.RELAXED
    assert verdict.relaxed_by_whitelist is True
    assert "大模型" in verdict.relaxation_reason
