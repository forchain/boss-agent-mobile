"""
boss_agent.adapters.outbound.llm.anthropic_client
================================================
The native Anthropic Messages protocol client.

Pure wire protocol: no tracing decorators and no environment side effects.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from boss_agent.core.shared.errors import LLMAuthError, LLMError, LLMTimeoutError
from boss_agent.core.shared.llm import LLMDecisionClient

from .config import LLMConfig, is_mask_placeholder
from .json_repair import JSONRepairMixin

#: The Messages API version this client speaks. Sent verbatim as ``anthropic-version``.
ANTHROPIC_VERSION: str = "2023-06-01"

#: Anthropic has no provider-native JSON mode, so the prompt is the only lever.
_JSON_ONLY_INSTRUCTION: str = (
    "Respond with a single valid JSON object for your answer and nothing else. "
    "Do not wrap it in markdown fences or add commentary."
)


class AnthropicChatClient(JSONRepairMixin, LLMDecisionClient):
    """Client for the native Anthropic Messages protocol."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env_or_file()
        #: The ``usage`` block of the last successful response, for tracing metadata.
        self.last_usage: dict[str, Any] | None = None

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        api_key = self.config.api_key
        if api_key:
            s_key = str(api_key).strip()
            if s_key and not is_mask_placeholder(s_key):
                headers["x-api-key"] = s_key
        return headers

    def _messages_url(self) -> str:
        """Resolve the Messages endpoint from a base URL with or without a version suffix."""
        base = self.config.base_url.rstrip("/")
        if re.search(r"/v\d+[a-zA-Z]*$", base):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    @staticmethod
    def _split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
        """Separate the top-level ``system`` parameter from the conversation turns."""
        system_parts: list[str] = []
        turns: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(str(message.get("content", "")))
            elif role in ("user", "assistant"):
                turns.append({"role": role, "content": message.get("content", "")})
        return "\n\n".join(part for part in system_parts if part), turns

    @staticmethod
    def _join_content_blocks(resp_data: dict[str, Any]) -> str:
        """Concatenate the ``text`` blocks of a Messages response."""
        blocks = resp_data.get("content")
        if isinstance(blocks, str):
            return blocks
        if not isinstance(blocks, list):
            return ""
        return "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type", "text") == "text"
        )

    @classmethod
    def _with_json_instruction(cls, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Fold the JSON-only instruction into the system turn, ahead of the turns."""
        system, turns = cls._split_system(messages)
        if _JSON_ONLY_INSTRUCTION not in system:
            system = f"{system}\n\n{_JSON_ONLY_INSTRUCTION}" if system else _JSON_ONLY_INSTRUCTION
        return ([{"role": "system", "content": system}] if system else []) + turns

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        """Send messages and return the assistant's text."""
        del response_format  # No native JSON mode on this protocol.

        url = self._messages_url()
        system, turns = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": turns,
            # Required by the Messages API, unlike the OpenAI one.
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if system:
            payload["system"] = system

        if self.config.extra_params:
            payload.update(self.config.extra_params)
        if extra_payload:
            payload.update(extra_payload)

        try:
            response = requests.post(
                url,
                headers=self._get_headers(),
                json=payload,
                timeout=self.config.timeout_sec,
            )
        except requests.exceptions.Timeout as e:
            raise LLMTimeoutError(f"Request to LLM at {url} timed out: {e}") from e
        except requests.exceptions.RequestException as e:
            raise LLMError(f"LLM connection error: {e}") from e

        if response.status_code in (401, 403):
            raise LLMAuthError(
                f"LLM authentication failed ({response.status_code}): {response.text}"
            )
        if response.status_code != 200:
            raise LLMError(f"LLM API returned HTTP {response.status_code}: {response.text}")

        try:
            resp_data = response.json()
            content = self._join_content_blocks(resp_data)
            usage = resp_data.get("usage")
            self.last_usage = usage if isinstance(usage, dict) else None
            return content
        except Exception as e:
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Failed to parse LLM response JSON: {e}") from e

    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del response_format  # No native JSON mode; the prompt carries the instruction.
        raw_text = self.chat_completion(
            messages=self._with_json_instruction(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            extra_payload=extra_payload,
        )
        json_str = self._extract_json_block(raw_text)
        return self._robust_parse_json(json_str)
