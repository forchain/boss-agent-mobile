import tempfile
from pathlib import Path

import yaml

from boss_agent.models import JobRecord, ScreeningPolicy


def test_screening_policy_load_from_example():
    """Verify ScreeningPolicy loads default rules from settings.example.yaml."""
    example_path = Path("config/settings.example.yaml")
    assert example_path.exists(), "config/settings.example.yaml must exist"

    policy = ScreeningPolicy.load_default(config_path=example_path)
    assert policy.enable_screening is True
    assert isinstance(policy.title_blacklist, list)
    assert "销售" in policy.title_blacklist
    assert "外包" in policy.jd_blacklist


def test_screening_policy_save_and_load_roundtrip():
    """Verify saving policy writes clean YAML that loads identically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_config = Path(tmpdir) / "screening.local.yaml"
        policy = ScreeningPolicy(
            title_whitelist=["Python", "Agent"],
            title_blacklist=["销售", "实习"],
            company_blacklist=["避雷科技"],
            jd_blacklist=["驻场", "外包"],
            enable_screening=True,
        )
        saved_path = policy.save_default(config_path=tmp_config)
        assert saved_path == tmp_config
        assert tmp_config.exists()

        # Raw YAML check
        data = yaml.safe_load(tmp_config.read_text(encoding="utf-8"))
        assert data["title_whitelist"] == ["Python", "Agent"]
        assert data["company_blacklist"] == ["避雷科技"]

        # Loader check
        loaded = ScreeningPolicy.load_default(config_path=tmp_config)
        assert loaded.title_whitelist == ["Python", "Agent"]
        assert loaded.title_blacklist == ["销售", "实习"]
        assert loaded.company_blacklist == ["避雷科技"]
        assert loaded.jd_blacklist == ["驻场", "外包"]
        assert loaded.enable_screening is True


def test_job_record_screened_reason_field():
    """Verify JobRecord accepts and serializes screened_reason."""
    rec = JobRecord(
        title="高级测试工程师",
        company_name="某测试外包",
        recruiter_name="HR",
        status="ignored",
        screened_reason="命中岗位摘要/标签黑名单关键词: '外包'",
    )
    assert rec.screened_reason == "命中岗位摘要/标签黑名单关键词: '外包'"
    assert rec.status == "ignored"


# ----------------------------------------------------------------------------
# App-Enforced Filters & Whitelist Relaxation (Ticket #188, Spec #187)
# ----------------------------------------------------------------------------


def test_channel_preference_defaults_to_all():
    """A policy without explicit configuration targets all recruitment channels."""
    policy = ScreeningPolicy()
    assert policy.channel_preference == "all"


def test_channel_preference_roundtrip_and_normalization():
    """channel_preference survives dict roundtrip; unknown/blank values fall back to 'all'."""
    policy = ScreeningPolicy(channel_preference="direct_only")
    d = policy.to_dict()
    assert d["channel_preference"] == "direct_only"
    restored = ScreeningPolicy.from_dict(d)
    assert restored.channel_preference == "direct_only"

    assert ScreeningPolicy.from_dict({"channel_preference": "vip_only"}).channel_preference == "all"
    assert ScreeningPolicy.from_dict({}).channel_preference == "all"
    assert ScreeningPolicy.from_dict(None).channel_preference == "all"
    assert ScreeningPolicy(channel_preference="  HEADHUNTER_ONLY ").channel_preference == (
        "headhunter_only"
    )
    assert ScreeningPolicy(channel_preference=None).channel_preference == "all"


def test_app_filter_all_passes_both_channels():
    """Preference 'all' never produces App-Enforced Filter violations."""
    policy = ScreeningPolicy(channel_preference="all")
    passed_headhunter, violation_hh = policy.evaluate_app_enforced_filters(is_headhunter=True)
    passed_direct, violation_direct = policy.evaluate_app_enforced_filters(is_headhunter=False)
    assert passed_headhunter is True
    assert violation_hh == ""
    assert passed_direct is True
    assert violation_direct == ""


def test_app_filter_direct_only_flags_headhunter():
    """Headhunter posting violates the 'direct_only' channel preference."""
    policy = ScreeningPolicy(channel_preference="direct_only")
    passed, violation = policy.evaluate_app_enforced_filters(is_headhunter=True)
    assert passed is False
    assert "猎头" in violation
    assert "direct_only" in violation

    passed_direct, violation_direct = policy.evaluate_app_enforced_filters(is_headhunter=False)
    assert passed_direct is True
    assert violation_direct == ""


def test_app_filter_headhunter_only_flags_direct_posting():
    """Direct posting violates the 'headhunter_only' channel preference."""
    policy = ScreeningPolicy(channel_preference="headhunter_only")
    passed, violation = policy.evaluate_app_enforced_filters(is_headhunter=False)
    assert passed is False
    assert "headhunter_only" in violation

    passed_hh, violation_hh = policy.evaluate_app_enforced_filters(is_headhunter=True)
    assert passed_hh is True
    assert violation_hh == ""


def test_app_filter_bypassed_when_screening_disabled():
    """enable_screening=False disables App-Enforced Filter evaluation."""
    policy = ScreeningPolicy(channel_preference="direct_only", enable_screening=False)
    passed, violation = policy.evaluate_app_enforced_filters(is_headhunter=True)
    assert passed is True
    assert violation == ""


def test_relaxation_matches_original_token_in_title():
    """A whitelist token hitting the card title grants relaxation, returning the original token."""
    policy = ScreeningPolicy(title_whitelist=["游戏AI", "大模型"])
    is_relaxed, token = policy.evaluate_whitelist_relaxation(
        title="资深 大模型 Agent 架构研发", company_name="某独角兽公司"
    )
    assert is_relaxed is True
    assert token == "大模型"


def test_relaxation_inspects_all_card_facets():
    """Relaxation matches against title, tags, company and digest facets alike."""
    policy = ScreeningPolicy(title_whitelist=["RAG", "字节跳动"])
    # tags facet
    assert policy.evaluate_whitelist_relaxation(
        title="平台工程师", tags=["RAG", "Python"]
    ) == (True, "RAG")
    # company facet
    assert policy.evaluate_whitelist_relaxation(
        title="平台工程师", company_name="北京字节跳动科技有限公司"
    ) == (True, "字节跳动")
    # digest facet
    assert policy.evaluate_whitelist_relaxation(
        title="平台工程师", digest="负责企业知识库 RAG 检索增强生成"
    ) == (True, "RAG")


def test_relaxation_is_case_insensitive():
    """Token matching ignores ASCII case but preserves the configured token casing."""
    policy = ScreeningPolicy(title_whitelist=["LangGraph"])
    is_relaxed, token = policy.evaluate_whitelist_relaxation(title="资深 langgraph 工程师")
    assert is_relaxed is True
    assert token == "LangGraph"


def test_relaxation_requires_actual_facet_hit():
    """Without any whitelist token in the card facets, no relaxation is granted."""
    policy = ScreeningPolicy(title_whitelist=["Agent"])
    is_relaxed, token = policy.evaluate_whitelist_relaxation(
        title="Go语言云原生架构师",
        company_name="某科技公司",
        tags=["K8s", "Docker"],
        digest="负责容器平台建设",
    )
    assert is_relaxed is False
    assert token == ""


def test_empty_or_blank_whitelist_never_relaxes():
    """An empty (or whitespace-only) whitelist simply means nothing can be relaxed."""
    assert ScreeningPolicy(title_whitelist=[]).evaluate_whitelist_relaxation(
        title="AI Agent 架构师"
    ) == (False, "")
    assert ScreeningPolicy(title_whitelist=["", "  "]).evaluate_whitelist_relaxation(
        title="AI Agent 架构师"
    ) == (False, "")


def test_matches_card_keywords_whitelist_miss_no_longer_rejects():
    """The whitelist is no longer an inclusion gate: a miss passes the card stage."""
    policy = ScreeningPolicy(title_whitelist=["Agent", "大模型", "Python"])
    passed, reason = policy.matches_card_keywords(
        title="Go语言云原生架构师",
        company_name="某科技公司",
        tags=["K8s", "Docker"],
        digest="负责容器平台建设",
    )
    assert passed is True
    assert "白名单" not in reason


def test_matches_card_keywords_blacklists_still_reject_with_whitelist_configured():
    """Removing the whitelist gate must not weaken blacklist one-strike rejection."""
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "大模型"],
        title_blacklist=["销售"],
        jd_blacklist=["外包"],
    )
    passed_title, reason_title = policy.matches_card_keywords(
        title="Agent 技术销售经理", digest="负责智能体产品大客户销售"
    )
    assert passed_title is False
    assert "职位黑名单" in reason_title

    passed_digest, reason_digest = policy.matches_card_keywords(
        title="AI Agent 平台研发", digest="本项目属于人力外包性质"
    )
    assert passed_digest is False
    assert "外包" in reason_digest
