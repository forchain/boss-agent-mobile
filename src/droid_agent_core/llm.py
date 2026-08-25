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
    timeout_sec: float = 120.0
    max_tokens: int = 262144

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
        timeout_sec = float(os.getenv("LLM_TIMEOUT_SEC") or data.get("timeout_sec") or 120.0)
        max_tokens = int(os.getenv("LLM_MAX_TOKENS") or data.get("max_tokens") or 262144)

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

    @staticmethod
    def _auto_close_json(raw: str) -> str:
        """Auto-close unclosed strings, arrays, and objects caused by token truncation."""
        s = raw.strip()
        # Strip trailing incomplete key or colon
        s = re.sub(r',\s*"[^"]*"\s*:\s*$', '', s)
        s = re.sub(r',\s*"[^"]*$', '', s)
        s = re.sub(r',\s*$', '', s)

        in_string = False
        escape = False
        stack: list[str] = []

        for c in s:
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c in '{[':
                    stack.append(c)
                elif c in '}]':
                    if stack:
                        top = stack[-1]
                        if (c == '}' and top == '{') or (c == ']' and top == '['):
                            stack.pop()

        if in_string:
            s += '"'

        while stack:
            top = stack.pop()
            if top == '{':
                s += '}'
            elif top == '[':
                s += ']'

        return s

    @classmethod
    def _robust_parse_json(cls, json_str: str) -> dict[str, Any]:
        """Parse JSON with multi-tier recovery for trailing commas, unescaped inner quotes, newlines, and truncation."""
        raw = json_str.strip()

        # 1. Direct standard parse
        try:
            return json.loads(raw, strict=False)
        except Exception:
            pass

        # 2. Clean trailing commas before closing braces/brackets
        cleaned = re.sub(r",\s*([\]}])", r"\1", raw)
        try:
            return json.loads(cleaned, strict=False)
        except Exception:
            pass

        # 2.5 Repair arrays improperly containing key-value pairs instead of objects: e.g. [ "key": [...] ] -> { "key": [...] }
        def fix_array_object_mixup(text: str) -> str:
            chars = list(text)
            n = len(chars)
            i = 0
            in_str = False
            escape = False
            stack: list[tuple[str, str]] = []

            while i < n:
                c = chars[i]
                if escape:
                    escape = False
                    i += 1
                    continue
                if c == '\\':
                    escape = True
                    i += 1
                    continue
                if c == '"':
                    in_str = not in_str
                    i += 1
                    continue
                if not in_str:
                    if c in '{[':
                        if c == '[':
                            j = i + 1
                            while j < n and chars[j] in ' \t\r\n':
                                j += 1
                            match = re.match(r'^"[^"]+"\s*:', text[j:])
                            if match:
                                chars[i] = '{'
                                stack.append(('converted', '{'))
                                i += 1
                                continue
                        stack.append(('normal', c))
                    elif c in '}]':
                        if stack:
                            kind, open_c = stack.pop()
                            if kind == 'converted' and c == ']':
                                chars[i] = '}'
                i += 1
            return ''.join(chars)

        cleaned = fix_array_object_mixup(cleaned)
        try:
            return json.loads(cleaned, strict=False)
        except Exception:
            pass

        # 3. Sanitize unescaped control characters (newlines/tabs) inside string literals
        def sanitize_string_control_chars(s: str) -> str:
            out: list[str] = []
            in_str = False
            escape = False
            for c in s:
                if escape:
                    out.append(c)
                    escape = False
                    continue
                if c == '\\':
                    out.append(c)
                    escape = True
                    continue
                if c == '"':
                    in_str = not in_str
                    out.append(c)
                    continue
                if in_str:
                    if c == '\n':
                        out.append('\\n')
                    elif c == '\r':
                        out.append('\\r')
                    elif c == '\t':
                        out.append('\\t')
                    else:
                        out.append(c)
                else:
                    out.append(c)
            return ''.join(out)

        sanitized = sanitize_string_control_chars(cleaned)
        try:
            return json.loads(sanitized, strict=False)
        except Exception:
            pass

        # 4. Tokenizer-level unescaped quote repair:
        def fix_unescaped_quotes_tokenizer(s: str) -> str:
            chars = list(s)
            i = 0
            in_string = False
            n = len(chars)
            res: list[str] = []
            while i < n:
                c = chars[i]
                if c == '\\':
                    res.append(c)
                    if i + 1 < n:
                        res.append(chars[i + 1])
                        i += 2
                        continue
                    i += 1
                    continue
                if c == '"':
                    if not in_string:
                        in_string = True
                        res.append(c)
                    else:
                        # Look ahead past whitespace to see if next non-whitespace char is structural (',', ':', '}', ']')
                        j = i + 1
                        while j < n and chars[j] in ' \t\r\n':
                            j += 1
                        if j < n and chars[j] in ',:}]':
                            in_string = False
                            res.append(c)
                        else:
                            # Inner unescaped double quote
                            res.append('\\"')
                    i += 1
                    continue
                res.append(c)
                i += 1
            return ''.join(res)

        fixed_quotes = fix_unescaped_quotes_tokenizer(sanitized)
        fixed_quotes = re.sub(r",\s*([\]}])", r"\1", fixed_quotes)
        try:
            return json.loads(fixed_quotes, strict=False)
        except Exception:
            pass

        # 5. Try Python AST literal eval (handles single quotes and Python boolean/None literals)
        try:
            import ast
            py_str = re.sub(r'\btrue\b', 'True', fixed_quotes)
            py_str = re.sub(r'\bfalse\b', 'False', py_str)
            py_str = re.sub(r'\bnull\b', 'None', py_str)
            eval_result = ast.literal_eval(py_str)
            if isinstance(eval_result, dict):
                return eval_result
        except Exception:
            pass

        # 6. Auto-close truncated JSON structures
        closed = cls._auto_close_json(fixed_quotes)
        try:
            return json.loads(closed, strict=False)
        except Exception:
            pass

        closed_orig = cls._auto_close_json(raw)
        try:
            return json.loads(closed_orig, strict=False)
        except Exception as e:
            raise LLMError(f"Failed to decode LLM response into JSON: {raw}") from e

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
        return self._robust_parse_json(json_str)

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
