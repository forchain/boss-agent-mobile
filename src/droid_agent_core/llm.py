"""
droid_agent_core.llm
====================
Provider-agnostic LLM reasoning interface and OpenAI-compatible client.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml


class LLMError(Exception):
    """Base exception for LLM operations."""


class LLMAuthError(LLMError):
    """Authentication or authorization failure when invoking LLM API."""


class LLMTimeoutError(LLMError):
    """Request timed out while waiting for LLM response."""


@dataclass
class LLMConfig:
    provider: str = "openai"
    base_url: str = "https://api.minimaxi.com/v1"
    api_key: str | None = None
    model: str = "MiniMax-M3"
    temperature: float = 0.2
    timeout_sec: float = 30.0
    max_tokens: int = 2048

    @classmethod
    def from_env_or_file(cls, config_path: str | Path | None = None) -> "LLMConfig":
        """Load LLM configuration with priority: config_path -> config/llm.local.yaml -> env vars -> defaults."""
        data: dict[str, Any] = {}

        # 1. Search for local config files
        search_paths: list[Path] = []
        if config_path:
            search_paths.append(Path(config_path))
        else:
            search_paths.extend(
                [
                    Path("config/llm.local.yaml"),
                    Path("config/llm.local.json"),
                    Path("config/llm_config.yaml"),
                ]
            )

        for p in search_paths:
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8")
                    if p.suffix in [".yaml", ".yml"]:
                        loaded = yaml.safe_load(content) or {}
                    else:
                        loaded = json.loads(content) or {}
                    if isinstance(loaded, dict):
                        data.update(loaded)
                        break
                except Exception:
                    pass

        # 2. Check environment variables (highest priority over config file)
        api_key = (
            os.getenv("LLM_API_KEY")
            or os.getenv("MINIMAX_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or data.get("api_key")
        )
        base_url = (
            os.getenv("LLM_BASE_URL")
            or os.getenv("MINIMAX_BASE_URL")
            or data.get("base_url")
            or "https://api.minimaxi.com/v1"
        )
        model = os.getenv("LLM_MODEL") or data.get("model") or "MiniMax-M3"
        provider = os.getenv("LLM_PROVIDER") or data.get("provider") or "openai"

        temperature = float(os.getenv("LLM_TEMPERATURE") or data.get("temperature") or 0.2)
        timeout_sec = float(os.getenv("LLM_TIMEOUT_SEC") or data.get("timeout_sec") or 30.0)
        max_tokens = int(os.getenv("LLM_MAX_TOKENS") or data.get("max_tokens") or 2048)

        return cls(
            provider=provider,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout_sec=timeout_sec,
            max_tokens=max_tokens,
        )


class LLMDecisionClient(ABC):
    """Abstract interface for LLM-driven UI decision making and information parsing."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Send chat messages and return assistant text response."""

    @abstractmethod
    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send chat messages and return parsed JSON response."""

    @abstractmethod
    def evaluate_text_match(self, candidate_resume: str, job_description: str) -> dict[str, Any]:
        """Evaluate match score between resume and job description."""


class OpenAIChatClient(LLMDecisionClient):
    """Concrete OpenAI-compatible REST chat completion client."""

    def __init__(self, config: LLMConfig | None = None):
        super().__init__(config or LLMConfig.from_env_or_file())

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
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

        # Handle fallback if response_format is not supported by a specific OpenAI-compatible provider
        if response.status_code == 400 and response_format is not None:
            try:
                fallback_payload = {k: v for k, v in payload.items() if k != "response_format"}
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
            return content
        except Exception as e:
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Failed to parse LLM response JSON: {e}") from e

    @staticmethod
    def _extract_json_block(text: str) -> str:
        """Extract JSON substring from potentially markdown-wrapped LLM output."""
        text = text.strip()
        # Look for markdown ```json ... ``` or ``` ... ```
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # If wrapped in braces
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            return text[brace_start : brace_end + 1]
        return text

    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Default to OpenAI standard JSON mode format: {"type": "json_object"}
        target_format = response_format or {"type": "json_object"}
        raw_text = self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=target_format,
        )
        json_str = self._extract_json_block(raw_text)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            raise LLMError(f"Failed to decode LLM response into JSON: {json_str}") from e

    def evaluate_text_match(self, candidate_resume: str, job_description: str) -> dict[str, Any]:
        prompt = (
            "请评估以下求职者简历与招聘岗位(JD)的匹配度：\n\n"
            f"[求职者简历]\n{candidate_resume}\n\n"
            f"[招聘岗位要求]\n{job_description}\n\n"
            "请以 JSON 格式输出以下字段：\n"
            "- match_score: 匹配度打分 (0 到 100 整数)\n"
            "- match_reasons: 匹配核心亮点列表 (list of string)\n"
            "- greeting_message: 适合发给招聘者的礼貌且突显匹配亮点的简短打招呼文案\n"
        )
        messages = [
            {
                "role": "system",
                "content": "You are a professional HR and recruitment assistant. Output valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ]
        return self.chat_completion_json(messages)
