"""
boss_agent.adapters.memory.llm_client
=====================================
A programmable in-memory ``LLMDecisionClient`` for fast unit tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from boss_agent.core.shared.llm import LLMDecisionClient


class StubLLMDecisionClient(LLMDecisionClient):
    """Returns scripted answers and records every request."""

    def __init__(
        self,
        *,
        text: str = "",
        data: dict[str, Any] | None = None,
        on_chat: Callable[..., str] | None = None,
        on_json: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.text = text
        self.data = data if data is not None else {}
        self._on_chat = on_chat
        self._on_json = on_json
        self.chat_calls: list[dict[str, Any]] = []
        self.json_calls: list[dict[str, Any]] = []

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        self.chat_calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "extra_payload": extra_payload,
            }
        )
        if self._on_chat is not None:
            return self._on_chat(messages=messages, response_format=response_format)
        return self.text

    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.json_calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "extra_payload": extra_payload,
            }
        )
        if self._on_json is not None:
            return self._on_json(messages=messages, response_format=response_format)
        return dict(self.data)
