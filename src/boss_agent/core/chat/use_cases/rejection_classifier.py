"""
boss_agent.core.chat.use_cases.rejection_classifier
====================================================
LLM judge deciding whether an inbox message is an explicit rejection.

Fails safe: any transport, parsing, or schema problem yields a non-rejection verdict,
because the alternative -- auto-sending a closing message into a live opportunity --
is unrecoverable. The client is injected; resolving the realm's client is the edge's
job, not the classifier's.
"""

from typing import Any

from boss_agent.core.chat.domain.rejection import (
    RejectionVerdict,
    build_rejection_classification_messages,
)
from droid_agent_core.llm import LLMDecisionClient


class RejectionClassifier:
    """LLM judge deciding whether an inbox message is an explicit rejection."""

    def __init__(self, llm_client: LLMDecisionClient) -> None:
        self.llm_client = llm_client

    def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict:
        """Classify one inbox message into a :class:`RejectionVerdict`."""
        text = (message_text or "").strip()
        if not text:
            return RejectionVerdict(
                is_rejection=False,
                rationale="消息内容为空，跳过判定",
                error="empty_message",
            )

        messages = build_rejection_classification_messages(sender_name, text)
        try:
            payload = self.llm_client.chat_completion_json(messages)
        except Exception as exc:  # noqa: BLE001 - fail safe on any client failure
            return RejectionVerdict(
                is_rejection=False,
                rationale=f"判定失败，按非拒信保守处理: {exc}",
                error=str(exc),
            )

        if not isinstance(payload, dict) or not isinstance(payload.get("is_rejection"), bool):
            return RejectionVerdict(
                is_rejection=False,
                rationale="判定结果缺少合法的 is_rejection 字段，按非拒信保守处理",
                error="invalid_payload",
                raw=payload if isinstance(payload, dict) else {},
            )

        return RejectionVerdict(
            is_rejection=payload["is_rejection"],
            confidence=_coerce_float(payload.get("confidence"), default=0.0),
            rationale=str(payload.get("rationale") or "").strip(),
            raw=payload,
        )


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
