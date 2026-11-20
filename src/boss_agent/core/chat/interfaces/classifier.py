"""The rejection-classifier port triage depends on."""

from typing import Protocol

from boss_agent.core.chat.domain.rejection import RejectionVerdict


class RejectionClassifierPort(Protocol):
    """Judge one inbox message for an explicit rejection."""

    def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict: ...
