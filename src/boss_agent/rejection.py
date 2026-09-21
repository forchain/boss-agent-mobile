"""
src/boss_agent/rejection.py
===========================
Rejection Auto-Acknowledgment domain: classifying explicit recruiter rejections
inside the New Greeting Inbox so they can be politely closed and marked as
disinterested (Issue #205).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The single, standardized disinterest feedback category. Never branched on:
#: "重复推荐" both prunes the conversation and suppresses future duplicate
#: recommendations, which is exactly the desired behaviour for a rejected role.
DISINTEREST_REASON: str = "重复推荐"

#: Polite closing message sent to a recruiter who explicitly rejected the candidate.
DEFAULT_REJECTION_REPLY_TEXT: str = "收到 谢谢"

#: Upper bound on how many inbox messages one CHECK_CHAT run may classify.
DEFAULT_MAX_SCAN_DEPTH: int = 30

REJECTION_CLASSIFIER_SYSTEM_PROMPT: str = (
    "你是求职者在 Boss 直聘上的消息助理。你的唯一任务是判断招聘者发来的消息"
    "是否属于【明确拒信】。\n\n"
    "【明确拒信】指招聘者已经明确表示不再推进该候选人的应聘流程，例如：\n"
    "- 明确表示不匹配/不合适（“抱歉，暂不匹配”“经历与岗位要求不符”）\n"
    "- 明确表示岗位已关闭/已招满/暂停招聘\n"
    "- 明确表示不再考虑/不推荐该候选人\n\n"
    "【不是拒信】的情形包括：\n"
    "- 面试邀约、约时间、索要简历或作品集\n"
    "- 对候选人经历的正常提问、追问或确认\n"
    "- 初次打招呼、群发模板消息、职位推荐等中性消息\n"
    "- 广告、推销、招聘外包等与本次求职无关的消息\n"
    "- 语义含糊、需要猜测才能判断为拒绝的消息\n\n"
    "只有当招聘者的意思**明确无误**为拒绝时才判定为拒信；任何不确定的情况都必须判定为非拒信。"
    "误判会造成真实机会的丢失，因此宁可漏判，不可错判。\n\n"
    "只输出 JSON，字段如下：\n"
    '- is_rejection: 布尔值，是否为明确拒信\n'
    "- confidence: 0 到 1 之间的置信度数字\n"
    "- rationale: 一句话中文说明判断依据\n"
)


@dataclass(frozen=True)
class ChatAcknowledgmentSettings:
    """Resolved configuration for the rejection auto-acknowledgment workflow."""

    rejection_reply_text: str = DEFAULT_REJECTION_REPLY_TEXT
    max_scan_depth: int = DEFAULT_MAX_SCAN_DEPTH

    def with_overrides(self, payload: Mapping[str, Any] | None) -> "ChatAcknowledgmentSettings":
        """Layer per-task payload overrides on top of the configured defaults.

        Malformed or blank overrides are ignored rather than raising, so a bad
        task payload degrades to the configured behaviour instead of aborting.
        """
        payload = payload or {}

        raw_reply = payload.get("rejection_reply_text")
        reply_text = str(raw_reply).strip() if raw_reply is not None else ""
        if not reply_text:
            reply_text = self.rejection_reply_text

        return ChatAcknowledgmentSettings(
            rejection_reply_text=reply_text,
            max_scan_depth=coerce_positive_int(
                payload.get("max_scan_depth"), self.max_scan_depth
            ),
        )


def coerce_positive_int(value: Any, default: int) -> int:
    """Parse `value` as a strictly positive int, falling back to `default`.

    Single clamp shared by the config resolver and the per-task payload overrides,
    so a corrupt or partial setting can never disable a scan bound.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class RejectionVerdict:
    """Outcome of classifying one inbox message as an explicit rejection."""

    is_rejection: bool
    confidence: float = 0.0
    rationale: str = ""
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def build_rejection_classification_messages(
    sender_name: str, message_text: str
) -> list[dict[str, str]]:
    """Build the chat messages asking the LLM to judge one inbox message."""
    sender = (sender_name or "").strip() or "未知招聘者"
    content = (
        f"[招聘者] {sender}\n"
        f"[消息内容] {message_text.strip()}\n\n"
        "请判断这条消息是否为【明确拒信】，并按要求输出 JSON。"
    )
    return [
        {"role": "system", "content": REJECTION_CLASSIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


class RejectionClassifier:
    """LLM judge deciding whether an inbox message is an explicit rejection.

    Fails safe: any transport, parsing, or schema problem yields a non-rejection
    verdict, because the alternative -- auto-sending a closing message into a
    live opportunity -- is unrecoverable.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client

    def _resolve_client(self) -> Any:
        if self.llm_client is None:
            from droid_agent_core.llm import OpenAIChatClient

            self.llm_client = OpenAIChatClient()
        return self.llm_client

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
            payload = self._resolve_client().chat_completion_json(messages)
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
