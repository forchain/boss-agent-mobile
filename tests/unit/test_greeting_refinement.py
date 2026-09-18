"""
tests.unit.test_greeting_refinement
===================================
Unit tests for GreetingStyleRule, rules persistence, critique refinement,
and condition-action rule distillation.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def test_service_distill_fallback_rewrites_raw_critique():
    """When LLM distillation fails, the fallback must still produce an agent-friendly
    instruction rather than memorize the user's raw words verbatim."""

    mock_llm = MagicMock()
    # Force the JSON call to fail so the fallback path runs.
    mock_llm.chat_completion_json.side_effect = RuntimeError("simulated distillation failure")

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior Backend Engineer",
        company_name="Acme Corp",
        salary_range="30-50K",
        job_description="We need a senior backend engineer.",
    )
    # A real user-typed critique that is verbose / unclear.
    raw_critique = "我英语其实还行，工作这么多年了，开会也没啥问题，要不你也帮我提一下这个？还有就是我是党员这个事也可以说说"

    rule = service.distill_memory_rule(
        job=job,
        original_greeting="您好，我对贵司的职位感兴趣。",
        revised_greeting="您好，作为多年经验的后端工程师，我对贵司的职位非常感兴趣。",
        critique=raw_critique,
    )

    # Fallback must NOT be the raw user text dumped into instruction.
    assert rule.instruction != raw_critique
    # Fallback reframes the raw critique into an agent-friendly directive.
    assert "求职者偏好" in rule.instruction
    assert raw_critique in rule.instruction  # still references the user's intent
    # Raw critique is trimmed to a sane length to keep the rule readable.
    assert len(rule.instruction) <= 280


def test_service_refine_with_critique_raises_on_llm_failure():
    """When LLM refinement fails, the method MUST raise — not silently
    return a fake 'refined' greeting that is just the original text with the
    critique concatenated in parentheses. This bug previously caused the UI
    to show '优化后: <original>（结合建议补充：...）' as a real refinement.
    Also asserts the raised message includes the underlying error info
    (not the literal 'unknown' string from a scoping bug)."""

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("simulated refine failure")

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior AI Architect",
        company_name="Global Tech",
        salary_range="50-70K",
        job_description="Global AI Agent role.",
    )
    original = "您好，我对贵司AI架构师职位非常感兴趣。"

    with pytest.raises(RuntimeError) as exc_info:
        service.refine_with_critique(
            job=job,
            current_greeting=original,
            critique="请强调我的英语能力",
        )

    msg = str(exc_info.value)
    assert "LLM 微调失败" in msg
    # The message must propagate the underlying error, not the literal 'unknown'
    # placeholder produced by a scoping bug where 'e' was checked outside the except block.
    assert "unknown" not in msg
    assert "simulated refine failure" in msg


def test_service_refine_with_critique_raises_when_llm_returns_empty():
    """When the LLM call succeeds but returns no revised_greeting, the method
    must still raise — same root contract as a hard exception."""

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {}  # missing revised_greeting

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Senior AI Architect",
        company_name="Global Tech",
        salary_range="50-70K",
        job_description="Global AI Agent role.",
    )

    with pytest.raises(RuntimeError, match="LLM 微调失败"):
        service.refine_with_critique(
            job=job,
            current_greeting="您好，我对贵司AI架构师职位非常感兴趣。",
            critique="请强调我的英语能力",
        )
