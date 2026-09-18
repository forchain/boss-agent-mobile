"""
tests.unit.test_greeting_refinement
===================================
Unit tests for critique-driven greeting refinement (never-fabricate contract).

Greeting long-term memory tests live in test_greeting_prompt.py; the retired
condition-action rule system (ADR 0010) and its distillation tests were
removed with the subsystem.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.matching import JobMatchGreetingService
from boss_agent.models import JobPosting


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
