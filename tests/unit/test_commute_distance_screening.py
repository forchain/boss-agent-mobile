"""
tests/unit/test_commute_distance_screening.py
=============================================
Domain-level unit tests for the commute distance App-Enforced Filter
(Spec #209, Ticket #210). The ceiling is a condition the Boss platform cannot
express natively, so our own policy judges it — and, since #437, exceeding it is
a HARD violation that no Whitelist Relaxation can waive.
"""

import tempfile
from pathlib import Path

import yaml

from boss_agent.screening_policy import HARD_CONSTRAINT_MARKER, ScreeningPolicy


def test_commute_limit_defaults_to_40km():
    """A fresh policy filters commutes beyond 40km unless configured otherwise."""
    assert ScreeningPolicy().max_commute_distance_km == 40.0


def test_commute_violation_flags_distance_beyond_ceiling():
    policy = ScreeningPolicy(max_commute_distance_km=40.0)

    verdict = policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=52.0)

    assert verdict.passed is False
    # 硬性违例自带不可豁免标记：理由串会被原样写进审计与记录，所以标记由 policy 拼，
    # 卡片初筛层与详情页阶段说同一句话（GLOSSARY「App-Enforced Filter」）。
    assert verdict.hard_violation == (
        "【App端强制过滤】距离家庭住址 52.0km 超过通勤上限 40.0km" + HARD_CONSTRAINT_MARKER
    )


def test_commute_and_channel_violations_are_both_reported():
    """A job violating both App-Enforced Filters must record both in the audit.

    SCRAPE_JOBS adjudicates the channel from the card and the distance from the
    detail page bottom, so its audit trail can carry both. AUTO_APPLY renders
    every dimension in a single call, where returning only the first violation
    would truncate the trail to whichever dimension is checked first.
    """
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    verdict = policy.evaluate_app_enforced_filters_by_severity(
        is_headhunter=True, commute_distance_km=52.0
    )

    assert verdict.passed is False
    assert "超过通勤上限 40.0km" in verdict.violation
    assert "猎头" in verdict.violation


def test_commute_distance_at_or_below_ceiling_passes():
    policy = ScreeningPolicy(max_commute_distance_km=40.0)

    by_severity = policy.evaluate_app_enforced_filters_by_severity
    assert by_severity(commute_distance_km=40.0).passed is True
    assert by_severity(commute_distance_km=19.5).passed is True


def test_commute_unknown_distance_fails_open():
    """An absent distance widget (no home address, remote job, timeout) must never reject."""
    policy = ScreeningPolicy(max_commute_distance_km=40.0)

    verdict = policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=None)

    assert verdict.passed is True
    assert verdict.violation == ""


def test_commute_filter_disabled_by_none_or_non_positive_limit():
    for disabled in (None, 0.0, -1.0):
        policy = ScreeningPolicy(max_commute_distance_km=disabled)
        assert policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=500.0).passed


def test_commute_filter_bypassed_when_screening_disabled():
    policy = ScreeningPolicy(max_commute_distance_km=10.0, enable_screening=False)

    verdict = policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=99.0)

    assert verdict.passed is True
    assert verdict.violation == ""


def test_evaluate_commute_distance_ignores_channel_dimension():
    """Detail-stage screening evaluates distance alone: the channel verdict was
    already rendered from the card before the page was opened."""
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    assert policy.evaluate_commute_distance(52.0) == (
        False,
        "【App端强制过滤】距离家庭住址 52.0km 超过通勤上限 40.0km" + HARD_CONSTRAINT_MARKER,
    )
    assert policy.evaluate_commute_distance(18.0) == (True, "")
    assert policy.evaluate_commute_distance(None) == (True, "")


def test_evaluate_commute_distance_respects_disabled_filter():
    assert ScreeningPolicy(max_commute_distance_km=None).evaluate_commute_distance(500.0) == (
        True,
        "",
    )
    assert ScreeningPolicy(enable_screening=False).evaluate_commute_distance(500.0) == (True, "")


def test_commute_filter_active_only_with_positive_ceiling():
    """Handlers gate the detail-page bottom probe on this, so it must be exact."""
    assert ScreeningPolicy(max_commute_distance_km=40.0).is_commute_filter_active is True
    assert ScreeningPolicy(max_commute_distance_km=None).is_commute_filter_active is False
    assert ScreeningPolicy(max_commute_distance_km=0.0).is_commute_filter_active is False
    assert ScreeningPolicy(max_commute_distance_km=-5.0).is_commute_filter_active is False
    # Screening off means nothing can be rejected, so no probe should be paid for.
    assert (
        ScreeningPolicy(
            max_commute_distance_km=40.0, enable_screening=False
        ).is_commute_filter_active
        is False
    )


def test_commute_probe_gated_on_channel_with_fail_open_unknown():
    """Ticket #255: only a posting positively known to be a headhunter is spared the
    probe — the platform renders no distance widget for those. An unknown channel must
    still probe, so an unrecognised direct hire is never silently spared screening.

    Spec #328 added a second gate: the posting must also sit in a 考察名单 district, since
    that is the only set of postings whose distance the operator asked to have measured.
    """
    active = ScreeningPolicy(max_commute_distance_km=40.0, business_district_inspect_list=["张江"])
    listed = "上海  浦东新区  张江"
    assert active.should_probe_commute_distance(is_headhunter=False, location=listed) is True
    assert active.should_probe_commute_distance(is_headhunter=None, location=listed) is True
    assert active.should_probe_commute_distance(is_headhunter=True, location=listed) is False

    # With no ceiling to enforce, nothing is probed whatever the channel.
    inactive = ScreeningPolicy(
        max_commute_distance_km=None, business_district_inspect_list=["张江"]
    )
    assert inactive.should_probe_commute_distance(is_headhunter=False, location=listed) is False
    assert inactive.should_probe_commute_distance(is_headhunter=True, location=listed) is False


def test_commute_probe_gated_on_the_inspection_list():
    """Spec #328: an empty 考察名单 means nothing is probed, and only a listed location is."""
    unlisted = ScreeningPolicy(max_commute_distance_km=40.0)
    assert (
        unlisted.should_probe_commute_distance(is_headhunter=False, location="上海  浦东新区  张江")
        is False
    )

    listed = ScreeningPolicy(
        max_commute_distance_km=40.0, business_district_inspect_list=["漕河泾"]
    )
    assert (
        listed.should_probe_commute_distance(is_headhunter=False, location="上海  徐汇区  漕河泾")
        is True
    )
    assert (
        listed.should_probe_commute_distance(is_headhunter=False, location="上海  浦东新区  张江")
        is False
    )
    # A city-only location names no district, so it cannot be in the list and defaults
    # to 距离满足 rather than being measured on a guess.
    assert listed.should_probe_commute_distance(is_headhunter=False, location="上海") is False
    assert listed.should_probe_commute_distance(is_headhunter=False, location="") is False


def test_a_metro_station_alone_can_put_a_posting_on_the_inspection_list():
    """Spec #333: the detail page's location line carries a station the card facet cannot."""
    policy = ScreeningPolicy(
        max_commute_distance_km=40.0, business_district_inspect_list=["华夏中路"]
    )
    line = "上海·浦东新区·张江(近13/16号线华夏中路地铁站)"
    assert policy.should_probe_commute_distance(is_headhunter=False, location=line) is True
    # The card facet alone would not have matched, which is the whole reason the detail
    # stage re-asks: 张江 was never listed, only the station is.
    assert (
        policy.should_probe_commute_distance(is_headhunter=False, location="上海  浦东新区  张江")
        is False
    )


def test_commute_violation_is_a_hard_filter_and_channel_violation_is_soft():
    """Issue #437: 非原生过滤分层。通勤距离是客观物理限制（硬性），
    渠道偏好只是成功率偏好（软性），两者必须分别判定而非揉成一条字符串。"""
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    both = policy.evaluate_app_enforced_filters_by_severity(
        is_headhunter=True, commute_distance_km=52.0
    )

    assert "超过通勤上限 40.0km" in both.hard_violation
    assert "direct_only" in both.soft_violation
    assert both.hard_violation != both.soft_violation


def test_a_clean_card_violates_neither_severity():
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    verdict = policy.evaluate_app_enforced_filters_by_severity(
        is_headhunter=False, commute_distance_km=18.0
    )

    assert verdict.hard_violation == ""
    assert verdict.soft_violation == ""
    assert verdict.passed is True


def test_within_commute_limit_leaves_the_hard_severity_untouched():
    """通勤距离在上限内时，唯一的渠道违例只能落在软性一侧。"""
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    verdict = policy.evaluate_app_enforced_filters_by_severity(
        is_headhunter=True, commute_distance_km=18.0
    )

    assert verdict.hard_violation == ""
    assert "direct_only" in verdict.soft_violation
    assert verdict.passed is False


def test_severity_verdict_keeps_the_full_audit_trail():
    """硬性违例不豁免，但两个维度仍须同时出现在审计串里，否则卡片初筛层
    拒绝时会丢失渠道维度的违例记录。"""
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    verdict = policy.evaluate_app_enforced_filters_by_severity(
        is_headhunter=True, commute_distance_km=52.0
    )

    assert verdict.violation == f"{verdict.hard_violation}；{verdict.soft_violation}"
    assert "猎头" in verdict.violation


def test_commute_violation_token_match_no_longer_implies_exemption():
    """白名单命中本身仍能算出 token，但通勤违例是硬性的：命中不等于豁免。

    ``evaluate_whitelist_relaxation`` 只是卡片面关键词匹配器，判定是否豁免的是
    调用方的路由（见 ``CandidateScreener.evaluate_card``）。
    """
    policy = ScreeningPolicy(max_commute_distance_km=20.0, title_whitelist=["大模型"])

    verdict = policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=35.0)
    assert verdict.passed is False
    assert verdict.violation

    relaxed, token = policy.evaluate_whitelist_relaxation(
        title="大模型 Agent 平台架构师",
        company_name="某科技公司",
        tags=[],
        digest="",
    )
    assert relaxed is True
    assert token == "大模型"

    # 硬性违例已由 policy 显式分层，不再进入白名单放宽。
    assert (
        policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=35.0).hard_violation
        != ""
    )


def test_commute_violation_not_relaxable_without_whitelist_hit():
    policy = ScreeningPolicy(max_commute_distance_km=20.0, title_whitelist=["大模型"])

    passed = policy.evaluate_app_enforced_filters_by_severity(commute_distance_km=35.0).passed
    assert passed is False
    relaxed, _token = policy.evaluate_whitelist_relaxation(
        title="Java 后端开发工程师",
        company_name="某银行",
        tags=["Java"],
        digest="",
    )
    assert relaxed is False


def test_commute_limit_serialization_roundtrip():
    policy = ScreeningPolicy(max_commute_distance_km=25.5)

    data = policy.to_dict()
    assert data["max_commute_distance_km"] == 25.5
    assert ScreeningPolicy.from_dict(data).max_commute_distance_km == 25.5


def test_commute_limit_roundtrip_when_disabled():
    """A disabled filter (null on disk) must not silently revert to the 40km default."""
    serialized = ScreeningPolicy(max_commute_distance_km=None).to_dict()

    assert serialized["max_commute_distance_km"] is None
    assert ScreeningPolicy.from_dict(serialized).max_commute_distance_km is None


def test_commute_limit_coerces_blank_and_string_values():
    assert (
        ScreeningPolicy.from_dict({"max_commute_distance_km": ""}).max_commute_distance_km is None
    )
    assert (
        ScreeningPolicy.from_dict({"max_commute_distance_km": "null"}).max_commute_distance_km
        is None
    )
    assert (
        ScreeningPolicy.from_dict({"max_commute_distance_km": "35.5"}).max_commute_distance_km
        == 35.5
    )


def test_commute_limit_save_and_load_roundtrip(tmp_path: Path | None = None):
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "settings.local.yaml"
        ScreeningPolicy(max_commute_distance_km=28.0, title_whitelist=["Agent"]).save_default(
            config_path=target
        )

        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert raw["max_commute_distance_km"] == 28.0

        loaded = ScreeningPolicy.load_default(config_path=target)
        assert loaded.max_commute_distance_km == 28.0
        assert loaded.title_whitelist == ["Agent"]


def test_commute_limit_loads_from_example_config():
    example_path = Path("config/settings.example.yaml")
    assert example_path.exists(), "config/settings.example.yaml must exist"

    policy = ScreeningPolicy.load_default(config_path=example_path)
    assert policy.max_commute_distance_km == 40.0
