"""
tests.unit.test_rejection_classifier
====================================
Unit tests for LLM-driven explicit rejection classification (Issue #206).
"""

from unittest.mock import MagicMock

from boss_agent.rejection import (
    DEFAULT_REJECTION_REPLY_TEXT,
    DISINTEREST_REASON,
    RejectionClassifier,
    build_rejection_classification_messages,
)


def test_disinterest_reason_is_standardized_to_duplicate_recommendation():
    """The disinterest feedback category must be strictly '重复推荐' with no branching."""
    assert DISINTEREST_REASON == "重复推荐"


def test_default_rejection_reply_text_is_polite_closing():
    assert DEFAULT_REJECTION_REPLY_TEXT == "收到 谢谢"


def test_classification_prompt_carries_message_and_sender():
    messages = build_rejection_classification_messages(
        sender_name="李女士", message_text="抱歉，您的经历与岗位要求不太匹配。"
    )

    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    joined = "\n".join(m["content"] for m in messages)
    assert "抱歉，您的经历与岗位要求不太匹配。" in joined
    assert "李女士" in joined
    assert "is_rejection" in joined


def test_classify_marks_explicit_rejection():
    llm_client = MagicMock()
    llm_client.chat_completion_json.return_value = {
        "is_rejection": True,
        "confidence": 0.93,
        "rationale": "招聘者明确表示不再考虑该候选人",
    }

    verdict = RejectionClassifier(llm_client=llm_client).classify(
        message_text="抱歉，我们暂时没有适合您的岗位。", sender_name="王先生"
    )

    assert verdict.is_rejection is True
    assert verdict.confidence == 0.93
    assert verdict.rationale == "招聘者明确表示不再考虑该候选人"
    assert verdict.error is None
    assert llm_client.chat_completion_json.call_count == 1


def test_classify_preserves_positive_invitation():
    llm_client = MagicMock()
    llm_client.chat_completion_json.return_value = {
        "is_rejection": False,
        "confidence": 0.88,
        "rationale": "招聘者发出面试邀约",
    }

    verdict = RejectionClassifier(llm_client=llm_client).classify(
        message_text="您好，方便约个时间聊一下吗？", sender_name="张女士"
    )

    assert verdict.is_rejection is False
    assert verdict.error is None


def test_classify_fails_safe_when_llm_raises():
    """Any LLM failure must degrade to 'not a rejection' so no message is ever sent blindly."""
    llm_client = MagicMock()
    llm_client.chat_completion_json.side_effect = RuntimeError("upstream 503")

    verdict = RejectionClassifier(llm_client=llm_client).classify(
        message_text="抱歉，暂不匹配。", sender_name="赵先生"
    )

    assert verdict.is_rejection is False
    assert verdict.error is not None
    assert "503" in verdict.error


def test_classify_fails_safe_on_non_boolean_payload():
    llm_client = MagicMock()
    llm_client.chat_completion_json.return_value = {"confidence": 0.5}

    verdict = RejectionClassifier(llm_client=llm_client).classify(
        message_text="暂不匹配", sender_name=""
    )

    assert verdict.is_rejection is False


def test_classify_blank_message_short_circuits_without_llm_call():
    llm_client = MagicMock()

    verdict = RejectionClassifier(llm_client=llm_client).classify(message_text="   ")

    assert verdict.is_rejection is False
    assert llm_client.chat_completion_json.call_count == 0
