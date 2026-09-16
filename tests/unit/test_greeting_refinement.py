"""
tests.unit.test_greeting_refinement
===================================
Unit tests for GreetingStyleRule, rules persistence, critique refinement,
and condition-action rule distillation.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from boss_agent.greeting_rules import (
    GreetingStyleRule,
    load_greeting_rules,
    save_greeting_rules,
)
from boss_agent.matching import JobMatchGreetingService
from boss_agent.models import JobPosting


def test_greeting_style_rule_dataclass():
    rule = GreetingStyleRule(
        id="rule_123",
        condition="当 JD 强调英语能力时",
        instruction="突出海外经历与英文面试意愿",
        enabled=True,
        source_job="Google - Tech Lead",
    )
    d = rule.to_dict()
    assert d["id"] == "rule_123"
    assert d["condition"] == "当 JD 强调英语能力时"
    assert d["instruction"] == "突出海外经历与英文面试意愿"
    assert d["enabled"] is True
    assert d["source_job"] == "Google - Tech Lead"
    assert d["created_at"] != ""

    restored = GreetingStyleRule.from_dict(d)
    assert restored.id == rule.id
    assert restored.condition == rule.condition
    assert restored.instruction == rule.instruction
    assert restored.enabled is True
    assert restored.source_job == rule.source_job


def test_greeting_rules_load_from_example():
    example_path = Path("config/greeting_rules.example.yaml")
    assert example_path.exists()

    rules = load_greeting_rules(config_path=example_path)
    assert len(rules) >= 2
    assert any("英语" in r.condition for r in rules)
    assert any("Agent" in r.condition for r in rules)


def test_greeting_rules_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_config = Path(tmpdir) / "greeting_rules.local.yaml"
        sample_rules = [
            GreetingStyleRule(
                id="r1",
                condition="当职位要求精通Rust时",
                instruction="提及开源Rust项目与内存安全调优成果",
                enabled=True,
                source_job="某量化公司 - 架构师",
            ),
            GreetingStyleRule(
                id="r2",
                condition="当公司属于外包驻场时",
                instruction="礼貌询问是否接受远程",
                enabled=False,
            ),
        ]
        saved_path = save_greeting_rules(sample_rules, config_path=tmp_config)
        assert saved_path == tmp_config
        assert tmp_config.exists()

        loaded = load_greeting_rules(config_path=tmp_config)
        assert len(loaded) == 2
        assert loaded[0].id == "r1"
        assert loaded[0].condition == "当职位要求精通Rust时"
        assert loaded[0].instruction == "提及开源Rust项目与内存安全调优成果"
        assert loaded[0].enabled is True
        assert loaded[0].source_job == "某量化公司 - 架构师"
        assert loaded[1].id == "r2"
        assert loaded[1].enabled is False


def test_service_injects_active_rules_into_evaluate_prompt():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 90,
        "jd_key_requirements": ["英语口语流利，能跨国协同"],
        "match_reasons": ["具备海外留学经历，可全英文面试"],
        "greeting_message": "关注到贵司对英语流利沟通的明确要求，我具备海外留学背景，英语可作日常工作语言并随时接受英文面试，非常期待进一步交流！",
    }

    rule_active = GreetingStyleRule(
        id="r_eng",
        condition="当 JD 强调英语能力或跨国业务时",
        instruction="开门见山点出海外留学经历、英语可作为工作语言并主动提及可接受全英文面试",
        enabled=True,
    )
    rule_disabled = GreetingStyleRule(
        id="r_disabled",
        condition="当 JD 强调Flutter时",
        instruction="强调Flutter跨端经验",
        enabled=False,
    )

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior Android Engineer (Global)",
        company_name="International AI Corp",
        salary_range="40-60K",
        job_description="We are looking for a senior Android developer with strong English communication skills to collaborate with our overseas team.",
    )

    result = service.evaluate_and_draft_greeting(job=job, rules=[rule_active, rule_disabled])
    assert result.match_score == 90
    assert "海外留学" in result.greeting_message

    # Check prompt passed to LLM
    call_args = mock_llm.chat_completion_json.call_args[0][0]
    system_prompt = call_args[0]["content"]
    assert "【打招呼个性化长期偏好准则" in system_prompt
    assert "当 JD 强调英语能力或跨国业务时" in system_prompt
    assert "开门见山点出海外留学经历" in system_prompt
    # Disabled rule should NOT be present
    assert "强调Flutter跨端经验" not in system_prompt


def test_service_refine_with_critique():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "revised_greeting": "关注到贵司对海外协同与英语的硬性要求，我具备海外留学经历，英语可作为工作语言并乐意接受全英文面试。期待与您深入沟通！"
    }

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior AI Architect",
        company_name="Global Tech",
        salary_range="50-70K",
        job_description="Responsible for global AI Agent architecture, requiring fluent English communication with Silicon Valley HQ.",
    )
    current_greeting = "您好！我对贵司AI架构师职位非常感兴趣，曾主导过多个大模型智能体平台研发。"
    critique = "招呼语没有突出我的英语能力。请强调我有海外留学背景，英语可作工作语言，并主动提出可以接受全英文面试！"

    revised = service.refine_with_critique(
        job=job,
        current_greeting=current_greeting,
        critique=critique,
    )

    assert "海外留学" in revised
    assert "工作语言" in revised

    call_args = mock_llm.chat_completion_json.call_args[0][0]
    # Check messages passed
    user_prompt = call_args[-1]["content"]
    assert current_greeting in user_prompt
    assert critique in user_prompt
    assert "Senior AI Architect" in user_prompt


def test_service_distill_memory_rule():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "condition": "当 JD 明确要求英语能力、外企背景或跨国团队协同",
        "instruction": "突出海外留学背景，说明英语可作为工作语言，并主动表达可接受全英文面试",
    }

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior AI Architect",
        company_name="Global Tech",
        salary_range="50-70K",
        job_description="Responsible for global AI Agent architecture, requiring fluent English communication with Silicon Valley HQ.",
    )
    original_greeting = "您好！我对贵司AI架构师职位非常感兴趣，曾主导过多个大模型智能体平台研发。"
    revised_greeting = "关注到贵司对海外协同与英语的硬性要求，我具备海外留学经历，英语可作为工作语言并乐意接受全英文面试。期待与您深入沟通！"
    critique = "招呼语没有突出我的英语能力。请强调我有海外留学背景，英语可作工作语言，并主动提出可以接受全英文面试！"

    distilled_rule = service.distill_memory_rule(
        job=job,
        original_greeting=original_greeting,
        revised_greeting=revised_greeting,
        critique=critique,
    )

    assert isinstance(distilled_rule, GreetingStyleRule)
    assert distilled_rule.enabled is True
    assert "英语能力" in distilled_rule.condition
    assert "海外留学" in distilled_rule.instruction
    assert "Global Tech" in distilled_rule.source_job
    assert distilled_rule.id.startswith("rule_")


def test_service_refine_with_critique_passes_history():
    """Spec Fix #5: refine_with_critique(history) threads prior conversation turns into the prompt."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "revised_greeting": "根据对话历史修订后的招呼语。"
    }

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior AI Architect",
        company_name="Global Tech",
        salary_range="50-70K",
        job_description="Responsible for global AI Agent architecture.",
    )
    history = [
        {"role": "user", "content": "第一轮招呼：您好！我对贵司AI架构师职位非常感兴趣。"},
        {"role": "assistant", "content": "第一轮回复：感谢您的兴趣，能否详细说明您的技术栈？"},
        {"role": "user", "content": "第二轮反馈：我的技术栈包括Python、LangChain和LangGraph。"},
    ]

    service.refine_with_critique(
        job=job,
        current_greeting="您好！我对贵司AI架构师职位非常感兴趣，曾主导过多个大模型智能体平台研发。",
        critique="请根据之前的对话历史进行优化",
        history=history,
    )

    call_args = mock_llm.chat_completion_json.call_args[0][0]
    messages_content = " ".join(msg.get("content", "") for msg in call_args if msg.get("content"))
    # All history turns must appear in the prompt
    assert "第一轮招呼" in messages_content
    assert "第一轮回复" in messages_content
    assert "第二轮反馈" in messages_content


def test_persist_reload_then_evaluate_injects_rule():
    """Spec Fix #7: after save+load, evaluate_and_draft_greeting automatically applies the rule."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_config = Path(tmpdir) / "greeting_rules.local.yaml"

        # Step 1: save a rule
        rule = GreetingStyleRule(
            id="rule_persist_test",
            condition="当 JD 提及Rust或内存安全时",
            instruction="突出开源Rust项目与内存安全调优成果",
            enabled=True,
            source_job="TestCorp - Rust Engineer",
        )
        save_greeting_rules([rule], config_path=tmp_config)
        assert tmp_config.exists()

        # Step 2: reload from disk (simulating a fresh process / service restart)
        reloaded_rules = load_greeting_rules(config_path=tmp_config)
        assert len(reloaded_rules) == 1
        assert reloaded_rules[0].id == "rule_persist_test"
        assert reloaded_rules[0].instruction == "突出开源Rust项目与内存安全调优成果"

        # Step 3: pass reloaded rules to the service — rule must be injected into the prompt
        mock_llm = MagicMock()
        mock_llm.chat_completion_json.return_value = {
            "match_score": 85,
            "jd_key_requirements": ["Rust", "内存安全"],
            "match_reasons": ["有Rust开源经验"],
            "greeting_message": "注意到贵司强调Rust与内存安全，我有开源Rust项目经验。",
        }

        service = JobMatchGreetingService(llm_client=mock_llm)
        job = JobPosting(
            title="Systems Engineer",
            company_name="SafetyFirst Labs",
            salary_range="45-65K",
            job_description="Seeking a systems programmer with strong Rust experience and memory safety expertise.",
        )

        result = service.evaluate_and_draft_greeting(job=job, rules=reloaded_rules)
        assert result.match_score == 85

        # Verify the rule was injected into the system prompt
        call_args = mock_llm.chat_completion_json.call_args[0][0]
        system_prompt = call_args[0]["content"]
        assert "【打招呼个性化长期偏好准则" in system_prompt
        assert "当 JD 提及Rust或内存安全时" in system_prompt
        assert "突出开源Rust项目与内存安全调优成果" in system_prompt
