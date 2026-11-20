"""
boss_agent.core.shared.llm
==========================
The LLM decision port every core use case depends on.

Pure abstraction: no HTTP client, no provider configuration, no observability SDK.
Transport and tracing implementations live in ``boss_agent.adapters.outbound.llm``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMDecisionClient(ABC):
    """Abstract interface for LLM-driven decision making and information parsing."""

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        """Send chat messages and return assistant text response."""

    @abstractmethod
    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send chat messages and return parsed JSON response."""


__all__ = ["LLMDecisionClient"]
