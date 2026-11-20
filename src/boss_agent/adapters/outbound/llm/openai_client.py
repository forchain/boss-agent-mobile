"""
boss_agent.adapters.outbound.llm.openai_client
==============================================
The OpenAI-compatible Chat Completions client.

Pure wire protocol: no tracing decorators and no environment side effects. Wrap it in
``TracedLLMDecisionClient`` where spans are wanted.
"""

from __future__ import annotations

from typing import Any

import requests

from boss_agent.core.shared.errors import LLMAuthError, LLMError, LLMTimeoutError
from boss_agent.core.shared.llm import LLMDecisionClient

from .config import LLMConfig, is_mask_placeholder
from .json_repair import JSONRepairMixin


class OpenAIChatClient(JSONRepairMixin, LLMDecisionClient):
    """Concrete OpenAI-compatible REST chat completion client.

    Carries the MiniMax brand sniff that disables extended thinking on structured
    tasks; it is knowledge about one provider's models.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env_or_file()
        #: The ``usage`` block of the last successful response, for tracing metadata.
        self.last_usage: dict[str, Any] | None = None

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.config.api_key
        if api_key:
            s_key = str(api_key).strip()
            if s_key and not is_mask_placeholder(s_key):
                headers["Authorization"] = f"Bearer {s_key}"
        return headers

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        url = f"{self.config.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        # Disable thinking for MiniMax on structured tasks to avoid long-running CoT.
        is_minimax = (
            "minimax" in self.config.base_url.lower() or "minimax" in self.config.model.lower()
        )
        if is_minimax and (not extra_payload or "thinking" not in extra_payload):
            payload["thinking"] = {"type": "disabled"}

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

        # Fallback if a provider rejects response_format or thinking with HTTP 400.
        if response.status_code == 400:
            try:
                fallback_payload = {
                    k: v for k, v in payload.items() if k not in ("response_format", "thinking")
                }
                response = requests.post(
                    url,
                    headers=self._get_headers(),
                    json=fallback_payload,
                    timeout=self.config.timeout_sec,
                )
            except Exception:
                pass

        if response.status_code in (401, 403):
            raise LLMAuthError(
                f"LLM authentication failed ({response.status_code}): {response.text}"
            )
        if response.status_code != 200:
            raise LLMError(f"LLM API returned HTTP {response.status_code}: {response.text}")

        try:
            resp_data = response.json()
            choices = resp_data.get("choices", [])
            if not choices:
                raise LLMError(f"LLM returned no choices in response: {resp_data}")
            content = choices[0].get("message", {}).get("content", "")
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
        target_format = response_format or {"type": "json_object"}
        raw_text = self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=target_format,
            extra_payload=extra_payload,
        )
        json_str = self._extract_json_block(raw_text)
        return self._robust_parse_json(json_str)
