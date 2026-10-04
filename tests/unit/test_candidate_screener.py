"""
tests/unit/test_candidate_screener.py
=====================================
Unit tests for the deep CandidateScreener module (Spec #231 / Ticket #232).

Screening is exercised through its two-method public interface over pure domain data,
with no Appium driver and no database involved.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.job_entities import JobCardBrief, JobPosting
from boss_agent.matching import MatchGreetingResult
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.screening import (
    CandidateScreener,
    CardFacets,
    CardVerdictStage,
    JobVerdictStage,
)
from boss_agent.screening_policy import ScreeningPolicy

GOOD_JD = (
    "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
    "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
)


def _card(**overrides) -> JobCardBrief:
    data = {
        "title": "AI Agent 平台工程师",
        "company_name": "智元创新",
        "recruiter_name": "周先生 · 技术总监",
        "tags": ["Python", "LangGraph"],
        "digest": "负责智能体编排平台研发",
    }
    data.update(overrides)
    return JobCardBrief(**data)


def _drafting_llm(**payload) -> MagicMock:
    llm = MagicMock()
    result = {
        "match_score": 88,
        "jd_key_requirements": ["LangGraph端侧落地"],
        "match_reasons": ["具备多智能体实战经验"],
        "greeting_message": "周总您好，看到贵司在招Agent平台工程师，我在LangGraph多智能体协同有成熟落地经验。",
    }
    result.update(payload)
    llm.chat_completion_json.return_value = result
    return llm


# ---------------------------------------------------------------------------
# evaluate_card — zero-token card screening
# ---------------------------------------------------------------------------


def test_evaluate_card_passes_clean_card_without_touching_llm():
    llm = MagicMock()
    screener = CandidateScreener(llm_client=llm)

    verdict = screener.evaluate_card(_card(), ScreeningPolicy())

    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.PASSED
    assert verdict.reason == "通过卡片初筛"
    llm.chat_completion_json.assert_not_called()


def test_evaluate_card_rejects_on_title_blacklist():
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(
        _card(title="高级 Java 后端开发工程师"),
        ScreeningPolicy(title_blacklist=["Java"]),
    )

    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD
    assert "Java" in verdict.reason


def test_evaluate_card_rejects_on_company_and_digest_blacklists():
    screener = CandidateScreener(llm_client=MagicMock())

    by_company = screener.evaluate_card(
        _card(company_name="北京软通动力信息技术有限公司"),
        ScreeningPolicy(company_blacklist=["软通动力"]),
    )
    assert by_company.passed is False
    assert "软通动力" in by_company.reason

    by_digest = screener.evaluate_card(
        _card(digest="需长期在客户现场驻场办公"),
        ScreeningPolicy(jd_blacklist=["驻场"]),
    )
    assert by_digest.passed is False
    assert "驻场" in by_digest.reason


def test_evaluate_card_rejects_headhunter_under_direct_only_channel():
    """A headhunter card must not reach a detail page when the candidate wants direct hires."""
    llm = MagicMock()
    screener = CandidateScreener(llm_client=llm)
    card = _card(
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
        tags=[],
    )
    assert card.is_headhunter is True

    verdict = screener.evaluate_card(card, ScreeningPolicy(channel_preference="direct_only"))

    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE
    assert "direct_only" in verdict.app_rule_violation
    assert verdict.relaxed_by_whitelist is False
    assert "direct_only" in verdict.screening_audit
    llm.chat_completion_json.assert_not_called()


def test_evaluate_card_rescues_app_rule_violation_through_whitelist():
    screener = CandidateScreener(llm_client=MagicMock())
    card = _card(
        title="大模型应用架构师",
        company_name="某人力资源服务公司",
        recruiter_name="林女士 · 资深猎头顾问",
    )

    verdict = screener.evaluate_card(
        card,
        ScreeningPolicy(channel_preference="direct_only", title_whitelist=["大模型"]),
    )

    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.RELAXED
    assert verdict.relaxed_by_whitelist is True
    assert "大模型" in verdict.relaxation_reason
    assert "direct_only" in verdict.screening_audit
    assert "大模型" in verdict.screening_audit


def test_evaluate_card_clean_card_bypasses_relaxation():
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(
        _card(),
        ScreeningPolicy(channel_preference="direct_only", title_whitelist=["量子计算"]),
    )

    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.PASSED
    assert verdict.app_rule_pass is True
    assert verdict.app_rule_violation == ""
    assert verdict.relaxed_by_whitelist is False
    assert verdict.screening_audit == ""


def test_evaluate_card_honours_disabled_screening_policy():
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(
        _card(title="Java架构师"),
        ScreeningPolicy(title_blacklist=["Java"], enable_screening=False),
    )
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# evaluate_job — full-JD evaluation and greeting drafting
# ---------------------------------------------------------------------------


def test_evaluate_job_rejects_unusable_jd_without_burning_tokens():
    llm = MagicMock()
    screener = CandidateScreener(llm_client=llm)

    result = screener.evaluate_job(_card(), "岗位职责：负责。", ScreeningPolicy())

    assert result.passed is False
    assert result.stage is JobVerdictStage.JD_UNAVAILABLE
    assert "missing or too short" in result.error_message
    llm.chat_completion_json.assert_not_called()


def test_evaluate_job_filters_jd_hitting_the_semantic_blacklist():
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "pass": False,
        "reason": "JD正文明确要求熟练掌握Java与微服务架构，触犯黑名单技术栈",
    }
    screener = CandidateScreener(llm_client=llm)
    jd = "【岗位职责】负责企业级电商结算系统与中台微服务建设；熟练掌握 Java、Spring Cloud、JVM 调优。"

    result = screener.evaluate_job(
        card=_card(),
        jd_text=jd,
        policy=ScreeningPolicy(jd_blacklist=["Java", "微服务"]),
    )

    assert result.passed is False
    assert result.stage is JobVerdictStage.FILTERED_BY_DEEP_SCREENER
    assert "Java" in result.reason
    assert result.greeting_message == ""
    # The semantic screen veto is final: no greeting is ever drafted for it.
    assert llm.chat_completion_json.call_count == 1


def test_evaluate_job_rescues_hallucinated_pass_false_when_reason_is_clearly_positive():
    """When LLM hallucinates pass=False but reason explicitly confirms no blacklist hit and pass, rescue it."""
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {
            "pass": False,
            "reason": "JD未涉及Java/产品/解决方案/微服务等黑名单关键词，岗位核心为Python+Agent开发，pass",
        },
        {
            "match_score": 90,
            "jd_key_requirements": ["Python", "AI Agent"],
            "match_reasons": ["技术匹配"],
            "greeting_message": "您好，我对该Agent岗位很感兴趣",
        },
    ]
    screener = CandidateScreener(llm_client=llm)
    result = screener.evaluate_job(
        card=_card(title="AI Agent 开发工程师（上海）", company_name="途游游戏"),
        jd_text="岗位职责：1. 负责素材AI智能体应用工程开发与架构设计。职位要求：熟练掌握Python语言，具备Agent智能体开发相关经验。",
        policy=ScreeningPolicy(jd_blacklist=["Java", "微服务", "产品", "解决方案"]),
    )
    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert result.match_score == 90


@pytest.mark.parametrize(
    "key,raw_val,reason_text,expected_passed",
    [
        ("approved", False, "触犯Java黑名单技术栈", False),
        ("approved", True, "岗位核心为Agent，未触犯黑名单", True),
        ("approved", "淘汰", "触犯Java黑名单技术栈", False),
        ("approved", "合格", "未触犯黑名单", True),
        ("qualified", False, "触犯Java黑名单技术栈", False),
        ("qualified", True, "未触犯黑名单", True),
        ("pass", "false", "触犯Java黑名单技术栈", False),
        ("pass", "0", "触犯Java黑名单技术栈", False),
        ("pass", "fail", "触犯Java黑名单技术栈", False),
        ("pass", "淘汰", "触犯Java黑名单技术栈", False),
        ("pass", "true", "未触犯黑名单", True),
        ("pass", "pass", "未触犯黑名单", True),
        ("pass", "通过", "未触犯黑名单", True),
        ("pass", "合格", "未触犯黑名单", True),
    ],
)
def test_screen_jd_semantics_coerces_string_pass_values(key, raw_val, reason_text, expected_passed):
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        key: raw_val,
        "reason": reason_text,
    }
    screener = CandidateScreener(llm_client=llm)
    passed, reason = screener._screen_jd_semantics(
        jd_text="岗位职责：...",
        policy=ScreeningPolicy(jd_blacklist=["Java"]),
    )
    assert passed is expected_passed


def test_evaluate_job_passes_and_drafts_tailored_greeting():
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "岗位契合Agent方向，未触犯任何黑名单"},
        {
            "match_score": 92,
            "jd_key_requirements": ["LangGraph", "Multi-Agent"],
            "match_reasons": ["丰富实战经验"],
            "greeting_message": "您好！看到贵司正在招聘AI Agent专家，我在LangGraph多智能体系统上有成熟落地经验...",
        },
    ]
    screener = CandidateScreener(llm_client=llm)
    profile = StructuredCandidateProfile(
        name="李华",
        years_of_experience=7,
        core_skills=["Python", "LangGraph"],
        target_positions=["AI Agent架构师"],
    )

    result = screener.evaluate_job(
        _card(),
        GOOD_JD,
        profile,
        ScreeningPolicy(jd_blacklist=["Java"]),
    )

    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert result.match_score == 92
    assert result.jd_key_requirements == ["LangGraph", "Multi-Agent"]
    assert "LangGraph" in result.greeting_message
    assert llm.chat_completion_json.call_count == 2


def test_evaluate_job_without_drafting_skips_the_greeting_call():
    llm = MagicMock()
    llm.chat_completion_json.return_value = {"pass": True, "reason": "未触犯黑名单"}
    screener = CandidateScreener(llm_client=llm)

    result = screener.evaluate_job(
        card=_card(),
        jd_text=GOOD_JD,
        policy=ScreeningPolicy(jd_blacklist=["Java"]),
        draft_greeting=False,
    )

    assert result.passed is True
    assert result.greeting_message == ""
    assert llm.chat_completion_json.call_count == 1


def test_evaluate_job_without_blacklist_passes_deterministically():
    """No blacklist means zero veto material: screening must not spend an LLM call."""
    llm = _drafting_llm()
    screener = CandidateScreener(llm_client=llm)

    result = screener.evaluate_job(
        card=_card(),
        jd_text=GOOD_JD,
        policy=ScreeningPolicy(title_whitelist=["Agent"]),
    )

    assert result.passed is True
    assert result.match_score == 88
    assert llm.chat_completion_json.call_count == 1


def test_evaluate_job_degrades_gracefully_when_screening_llm_fails():
    llm = MagicMock()
    llm.chat_completion_json.side_effect = RuntimeError("Rate limit exceeded")
    screener = CandidateScreener(llm_client=llm)

    result = screener.evaluate_job(
        card=_card(), jd_text=GOOD_JD, policy=ScreeningPolicy(jd_blacklist=["Java"])
    )

    assert result.passed is True
    assert "降级放行" in result.reason


def test_evaluate_job_accepts_dict_card_and_dict_profile():
    llm = MagicMock()
    llm.chat_completion_json.return_value = {"pass": True, "reason": "未触犯黑名单"}
    screener = CandidateScreener(llm_client=llm)

    result = screener.evaluate_job(
        {"title": "AI Agent 工程师", "company_name": "智元创新", "tags": ["Python"]},
        GOOD_JD,
        {"name": "李华", "core_skills": ["Python"]},
        {"jd_blacklist": ["Java"]},
        draft_greeting=False,
    )

    assert result.passed is True


def test_evaluate_job_accepts_job_posting_card():
    llm = _drafting_llm()
    screener = CandidateScreener(llm_client=llm)
    posting = JobPosting(
        title="移动端自动化专家",
        company_name="前沿智能",
        salary_range="40-60K",
        job_description=GOOD_JD,
    )

    result = screener.evaluate_job(posting, GOOD_JD, None, ScreeningPolicy())

    assert result.passed is True
    assert result.match_score == 88


def test_screener_uses_injected_matching_service():
    service = MagicMock()
    service.evaluate_and_draft_greeting.return_value = MatchGreetingResult(
        match_score=77,
        greeting_message="注入服务生成的招呼语",
    )
    screener = CandidateScreener(llm_client=MagicMock(), matching_service=service)

    result = screener.evaluate_job(_card(), GOOD_JD, None, ScreeningPolicy())

    assert result.match_score == 77
    assert result.greeting_message == "注入服务生成的招呼语"
    service.evaluate_and_draft_greeting.assert_called_once()


def test_evaluate_job_injects_blacklists_into_the_greeting_prompt():
    """The drafter must be told what the candidate refuses, so it never praises it."""
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "pass": True,
        "reason": "黑名单仅为协作提及，岗位主体是Agent平台",
    }
    screener = CandidateScreener(llm_client=llm)
    policy = ScreeningPolicy(jd_blacklist=["Java", "微服务"], title_blacklist=["外包"])
    jd = (
        "岗位职责：主导多智能体协同平台建设；任职要求：精通Python与LangGraph，"
        "配合Java中台团队交付接口，具备微服务治理经验。"
    )

    screener.evaluate_job(_card(), jd, None, policy)

    drafter_prompt = llm.chat_completion_json.call_args_list[1].args[0]
    system_prompt = next(m["content"] for m in drafter_prompt if m["role"] == "system")
    for token in ("Java", "微服务", "外包"):
        assert token in system_prompt, f"blacklist token '{token}' missing from drafter prompt"
    assert "严禁" in system_prompt


def test_jd_screen_prompt_carries_no_whitelist_veto():
    """Whitelist tokens are relaxation tokens only: they never judge a JD."""
    llm = MagicMock()
    llm.chat_completion_json.return_value = {"pass": True, "reason": "未触犯黑名单"}
    screener = CandidateScreener(llm_client=llm)
    policy = ScreeningPolicy(title_whitelist=["Agent", "量子计算"], jd_blacklist=["Java"])

    screener.evaluate_job(
        _card(),
        "岗位职责：负责Agent编排平台建设，配合Java数据团队提供接口支持。",
        None,
        policy,
        draft_greeting=False,
    )

    messages = llm.chat_completion_json.call_args_list[0].args[0]
    system_prompt = next(m["content"] for m in messages if m["role"] == "system")
    assert "白名单目标关键词" not in system_prompt
    assert "量子计算" not in system_prompt, "whitelist tokens must not leak into the JD prompt"
    assert "Java" in system_prompt, "blacklist criteria must remain"
    assert "核心职责" in system_prompt or "主技术栈" in system_prompt


def test_evaluate_job_greeting_formats_salutation_from_card_recruiter():
    """evaluate_job must pass card's recruiter through to JobPosting and enforce dynamic salutation."""
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "通过筛选"},
        {
            "match_score": 95,
            "jd_key_requirements": ["多智能体"],
            "match_reasons": ["经验匹配"],
            "greeting_message": "您好！看到贵司正在招聘，希望能进一步交流！",
        },
    ]
    screener = CandidateScreener(llm_client=llm)
    card = _card(recruiter_name="李女士 · HRBP")

    result = screener.evaluate_job(card, GOOD_JD, None)
    assert result.passed is True
    assert result.greeting_message.startswith("李女士您好,幸会!")


def test_evaluate_job_greeting_formats_compound_and_single_surname_recruiters():
    """Real name recruiters on card are converted to x总您好,幸会!."""
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "通过"},
        {"match_score": 90, "greeting_message": "针对岗位核心挑战，我具备相关经验。"},
        {"pass": True, "reason": "通过"},
        {"match_score": 90, "greeting_message": "针对岗位核心挑战，我具备相关经验。"},
    ]
    screener = CandidateScreener(llm_client=llm)

    # 1. Compound surname
    res1 = screener.evaluate_job(_card(recruiter_name="诸葛孔明"), GOOD_JD, None)
    assert res1.greeting_message.startswith("诸葛总您好,幸会!")

    # 2. Single surname
    res2 = screener.evaluate_job(_card(recruiter_name="张伟"), GOOD_JD, None)
    assert res2.greeting_message.startswith("张总您好,幸会!")


def test_evaluate_card_rejects_on_business_district_blacklist_administrative_district():
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(
        _card(location="上海 崇明区 城桥"),
        ScreeningPolicy(business_district_blacklist=["崇明区"]),
    )

    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD
    assert (
        verdict.reason
        == "【商圈黑名单过滤】岗位所在区域/商圈 '上海 崇明区 城桥' 命中黑名单 '崇明区'"
    )


def test_evaluate_card_rejects_on_business_district_blacklist_commercial_quarter():
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(
        _card(location="上海 浦东新区 临港"),
        ScreeningPolicy(business_district_blacklist=["临港"]),
    )

    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD
    assert (
        verdict.reason
        == "【商圈黑名单过滤】岗位所在区域/商圈 '上海 浦东新区 临港' 命中黑名单 '临港'"
    )


def test_evaluate_card_rejects_both_direct_and_headhunter_on_business_district_blacklist():
    screener = CandidateScreener(llm_client=MagicMock())
    policy = ScreeningPolicy(business_district_blacklist=["崇明区"])

    direct_card = _card(location="上海 崇明区 城桥", is_headhunter=False)
    hh_card = _card(
        location="上海 崇明区 城桥",
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
        is_headhunter=True,
    )

    direct_verdict = screener.evaluate_card(direct_card, policy)
    hh_verdict = screener.evaluate_card(hh_card, policy)

    assert direct_verdict.passed is False
    assert (
        direct_verdict.reason
        == "【商圈黑名单过滤】岗位所在区域/商圈 '上海 崇明区 城桥' 命中黑名单 '崇明区'"
    )
    assert hh_verdict.passed is False
    assert (
        hh_verdict.reason
        == "【商圈黑名单过滤】岗位所在区域/商圈 '上海 崇明区 城桥' 命中黑名单 '崇明区'"
    )


def test_evaluate_card_business_district_blacklist_not_exempted_by_whitelist():
    """Whitelist relaxation tokens never rescue a business district blacklist rejection."""
    screener = CandidateScreener(llm_client=MagicMock())
    card = _card(
        title="AI 架构专家",
        location="上海 浦东新区 临港",
    )
    policy = ScreeningPolicy(
        title_whitelist=["AI"],
        business_district_blacklist=["临港"],
    )

    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is False
    assert verdict.relaxed_by_whitelist is False
    assert (
        verdict.reason
        == "【商圈黑名单过滤】岗位所在区域/商圈 '上海 浦东新区 临港' 命中黑名单 '临港'"
    )


def test_evaluate_card_business_district_blacklist_clean_locations_pass():
    screener = CandidateScreener(llm_client=MagicMock())
    policy = ScreeningPolicy(business_district_blacklist=["崇明区", "临港"])

    # Clean location
    verdict1 = screener.evaluate_card(_card(location="上海 静安区 静安寺"), policy)
    assert verdict1.passed is True

    # Empty location
    verdict2 = screener.evaluate_card(_card(location=""), policy)
    assert verdict2.passed is True


def test_evaluate_card_business_district_blacklist_case_insensitive_and_stripped():
    screener = CandidateScreener(llm_client=MagicMock())
    policy = ScreeningPolicy(business_district_blacklist=[" Pudong "])

    verdict = screener.evaluate_card(_card(location="Shanghai Pudong Area"), policy)
    assert verdict.passed is False
    assert (
        verdict.reason
        == "【商圈黑名单过滤】岗位所在区域/商圈 'Shanghai Pudong Area' 命中黑名单 'Pudong'"
    )


def test_evaluate_card_business_district_blacklist_disabled_when_enable_screening_false():
    screener = CandidateScreener(llm_client=MagicMock())
    policy = ScreeningPolicy(
        business_district_blacklist=["崇明区"],
        enable_screening=False,
    )

    verdict = screener.evaluate_card(_card(location="上海 崇明区"), policy)
    assert verdict.passed is True


def test_card_facets_to_job_posting_retains_digest():
    """CardFacets.to_job_posting must preserve digest (issue #352)."""
    facets = CardFacets.from_card(
        {
            "title": "大模型架构师",
            "company_name": "创新工场",
            "digest": "急聘算法工程专家，硕士优先",
            "tags": ["硕士优先", "Python"],
        }
    )
    posting = facets.to_job_posting("岗位职责：负责智能体系统设计研发，要求本科及以上。")
    assert posting.digest == "急聘算法工程专家，硕士优先"
    assert posting.tags == ["硕士优先", "Python"]


def test_evaluate_job_forwards_search_filter_to_greeting_service():
    """evaluate_job passes search_filter into JobPosting and down to greeting service."""
    screener = CandidateScreener(llm_client=MagicMock())
    mock_greeting = MagicMock()
    mock_greeting.evaluate_and_draft_greeting.return_value = MagicMock(
        match_score=90,
        match_reasons=["硕士学历与岗位强偏好吻合"],
        jd_key_requirements=["多Agent系统研发"],
        greeting_message="张总您好,幸会!我具备硕士背景与工程落地经验。",
    )
    screener._greeting_service = MagicMock(return_value=mock_greeting)

    card = _card(
        title="Agent 架构师",
        company_name="智能未来",
        digest="大模型端侧团队直招，硕士优先",
        tags=["硕士优先"],
    )
    search_filter = {"education": "硕士", "salary": "5万元以上"}
    res = screener.evaluate_job(
        card=card,
        jd_text=GOOD_JD,
        search_filter=search_filter,
    )
    assert res.passed is True
    assert res.match_score == 90

    passed_job = mock_greeting.evaluate_and_draft_greeting.call_args.kwargs["job"]
    assert passed_job.digest == "大模型端侧团队直招，硕士优先"
    assert passed_job.search_filter == search_filter
