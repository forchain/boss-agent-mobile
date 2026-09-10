"""
tests/unit/test_masked_company_guardrail.py
===========================================
Unit tests verifying the Masked Company Guardrail (is_masked_company_name)
and direct-only rejection blacklist protection in ScreeningPolicy.
"""

from boss_agent.models import ScreeningPolicy, is_masked_company_name


def test_is_masked_company_name_positive_cases():
    """Verify is_masked_company_name flags confidential, masked, or placeholder names."""
    masked_names = [
        "某中型人工智能公司",
        "成都某中型...智能公司",
        "某知名互联网公司",
        "某大型国企",
        "某上市公司",
        "某独角兽科技公司",
        "某AI初创团队",
        "某外资企业",
        "***公司",
        "***",
        "保密公司",
        "保密",
        "知名外企",
        "头部互联网公司",
    ]
    for name in masked_names:
        assert is_masked_company_name(name) is True, f"Expected '{name}' to be identified as masked."


def test_is_masked_company_name_negative_cases():
    """Verify is_masked_company_name preserves authentic registered enterprise names."""
    authentic_names = [
        "深至科技",
        "游族网络",
        "腾讯科技（深圳）有限公司",
        "北京字节跳动网络技术有限公司",
        "阿里巴巴（中国）网络技术有限公司",
        "Google LLC",
        "美团",
        "小红书",
        "商汤科技",
    ]
    for name in authentic_names:
        assert is_masked_company_name(name) is False, f"Expected '{name}' to NOT be flagged as masked."


def test_is_masked_company_name_empty_or_invalid():
    """Verify is_masked_company_name handles empty and invalid inputs gracefully."""
    assert is_masked_company_name("") is False
    assert is_masked_company_name("   ") is False
    assert is_masked_company_name(None) is False  # type: ignore[arg-type]


def test_guardrail_blocks_masked_company_blacklisting():
    """Verify attempting to add a masked company to company_blacklist is blocked with notice."""
    policy = ScreeningPolicy()

    allowed, notice = policy.validate_can_blacklist_company("某中型人工智能公司", is_headhunter=False)
    assert allowed is False
    assert "黑名单保护生效" in notice
    assert "保密/占位公司名称" in notice

    # Attempting to add via add_company_to_blacklist
    success, add_notice = policy.add_company_to_blacklist("某中型人工智能公司", is_headhunter=False)
    assert success is False
    assert "某中型人工智能公司" not in policy.company_blacklist


def test_guardrail_blocks_headhunter_company_blacklisting():
    """Verify attempting to blacklist a headhunter posting's company is blocked even if name looks authentic."""
    policy = ScreeningPolicy()

    # Even with an authentic-looking name, headhunter channel must not be company-blacklisted
    allowed, notice = policy.validate_can_blacklist_company("深至科技", is_headhunter=True)
    assert allowed is False
    assert "猎头代招岗位" in notice

    success, add_notice = policy.add_company_to_blacklist("深至科技", is_headhunter=True)
    assert success is False
    assert "深至科技" not in policy.company_blacklist


def test_guardrail_allows_authentic_direct_company_blacklisting():
    """Verify authentic direct company names can safely be blacklisted and removed."""
    policy = ScreeningPolicy()

    allowed, notice = policy.validate_can_blacklist_company("深至科技", is_headhunter=False)
    assert allowed is True
    assert "真实直招企业" in notice

    # Add to blacklist
    success, add_notice = policy.add_company_to_blacklist("深至科技", is_headhunter=False)
    assert success is True
    assert "深至科技" in policy.company_blacklist
    assert "已成功将直招企业" in add_notice

    # Idempotent re-add
    success2, notice2 = policy.add_company_to_blacklist("深至科技", is_headhunter=False)
    assert success2 is True
    assert policy.company_blacklist.count("深至科技") == 1
    assert "已在公司黑名单中" in notice2

    # Remove
    removed = policy.remove_company_from_blacklist("深至科技")
    assert removed is True
    assert "深至科技" not in policy.company_blacklist


def test_quota_conservation_and_false_positive_avoidance():
    """Verify blacklisting a direct employer skips its subsequent postings while sparing unrelated postings."""
    policy = ScreeningPolicy()

    # Blacklist direct company '深至科技' after rejection/ignore
    success, _ = policy.add_company_to_blacklist("深至科技", is_headhunter=False)
    assert success is True

    # 1. Direct posting from '深至科技' is rejected, saving quota
    passed, reason = policy.matches_card_keywords(
        title="医疗AI技术负责人",
        company_name="深至科技",
        tags=["10年以上", "硕士"],
        digest="核心算法团队管理",
    )
    assert passed is False
    assert "命中公司黑名单关键词: '深至科技'" in reason

    # 2. Suffix or full name matching
    passed_full, reason_full = policy.matches_card_keywords(
        title="图像算法专家",
        company_name="上海深至科技有限公司",
        tags=["5-10年", "博士"],
        digest="医学影像分析",
    )
    assert passed_full is False
    assert "命中公司黑名单关键词: '深至科技'" in reason_full

    # 3. Unrelated direct company '游族网络' passes
    passed_other, _ = policy.matches_card_keywords(
        title="游戏AI架构师",
        company_name="游族网络",
        tags=["5-10年", "本科"],
        digest="NPC智能驱动系统",
    )
    assert passed_other is True

    # 4. Headhunter posting with placeholder '某中型人工智能公司' was blocked from blacklist,
    # so another unrelated agency role with that placeholder still passes keyword screening
    passed_hh, _ = policy.matches_card_keywords(
        title="大模型平台负责人",
        company_name="某中型人工智能公司",
        tags=["10年以上", "硕士"],
        digest="核心研发与平台建设",
    )
    assert passed_hh is True
