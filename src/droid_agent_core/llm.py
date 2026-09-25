"""
droid_agent_core.llm
====================
Provider-agnostic LLM reasoning interface and OpenAI-compatible client.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

#: Characters a masked secret is rendered with, and the template's placeholder. Kept
#: local because this layer must not know which application is using it; the
#: application's own predicate is asserted equal to these by
#: `test_framework_defaults_match_the_realm`, so the two cannot drift apart.
MASK_MARKERS: tuple[str, ...] = ("•", "****")
PLACEHOLDER_API_KEY: str = "your-api-key-here"


def _is_mask_placeholder(value: Any) -> bool:
    """Whether ``value`` is a masked display string rather than a usable secret."""
    if value is None:
        return False
    text = str(value)
    return not text or text == PLACEHOLDER_API_KEY or any(m in text for m in MASK_MARKERS)


def _read_config_file(path: Path) -> dict[str, Any]:
    """Parse one YAML/JSON config file, or return ``{}`` when it is absent or unreadable."""
    if not path.is_file():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        loaded = json.loads(content) if path.suffix == ".json" else (yaml.safe_load(content) or {})
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        k: v
        for k, v in loaded.items()
        if v is not None and not (k == "api_key" and _is_mask_placeholder(v))
    }


class LLMError(Exception):
    """Base exception for LLM operations."""


class LLMAuthError(LLMError):
    """Authentication or authorization failure when invoking LLM API."""


class LLMTimeoutError(LLMError):
    """Request timed out while waiting for LLM response."""


#: Framework-neutral fallbacks, used only when a host supplies no configuration. A host
#: owns its defaults table and passes it as ``settings``; these exist so the framework
#: is usable standalone, and `test_framework_defaults_match_the_realm` keeps them
#: agreeing with the host's table so the fallback can never be a second opinion.
FRAMEWORK_DEFAULTS: dict[str, Any] = {
    "provider": "openai",
    "base_url": "https://api.minimaxi.com/v1",
    "model": "MiniMax-M3",
    "temperature": 0.2,
    "timeout_sec": 120.0,
    "max_tokens": 262144,
    "langsmith_project": "boss-agent-mobile",
}


@dataclass
class LLMConfig:
    provider: str = FRAMEWORK_DEFAULTS["provider"]
    base_url: str = FRAMEWORK_DEFAULTS["base_url"]
    api_key: str | None = None
    model: str = FRAMEWORK_DEFAULTS["model"]
    temperature: float = FRAMEWORK_DEFAULTS["temperature"]
    timeout_sec: float = FRAMEWORK_DEFAULTS["timeout_sec"]
    max_tokens: int = FRAMEWORK_DEFAULTS["max_tokens"]
    extra_params: dict[str, Any] = field(default_factory=dict)
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None
    langsmith_endpoint: str | None = None

    @classmethod
    def from_env_or_file(
        cls,
        config_path: str | Path | None = None,
        *,
        settings: Mapping[str, Any] | None = None,
    ) -> "LLMConfig":
        """Build a config from a host-supplied baseline, one optional file, and the env.

        Priority: ``settings`` / ``config_path`` -> LLM-specific environment variables
        -> :data:`FRAMEWORK_DEFAULTS`.

        The host owns the defaults and the file chain: this layer is deliberately
        app-agnostic and does not read the host's configuration realm itself. A host
        that resolved its realm passes the result as ``settings`` — that is the whole
        delegation, and it is what stops a second defaults table from existing here.
        """
        data: dict[str, Any] = {k: v for k, v in (settings or {}).items() if k != "chat"}
        if config_path:
            data.update(_read_config_file(Path(config_path)))

        # Environment sits above every file, as it does for the rest of the realm.
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
            or FRAMEWORK_DEFAULTS["base_url"]
        )
        model = os.getenv("LLM_MODEL") or data.get("model") or FRAMEWORK_DEFAULTS["model"]
        provider = (
            os.getenv("LLM_PROVIDER") or data.get("provider") or FRAMEWORK_DEFAULTS["provider"]
        )

        temperature = float(
            os.getenv("LLM_TEMPERATURE") or data.get("temperature") or FRAMEWORK_DEFAULTS["temperature"]
        )
        timeout_sec = float(
            os.getenv("LLM_TIMEOUT_SEC") or data.get("timeout_sec") or FRAMEWORK_DEFAULTS["timeout_sec"]
        )
        max_tokens = int(
            os.getenv("LLM_MAX_TOKENS") or data.get("max_tokens") or FRAMEWORK_DEFAULTS["max_tokens"]
        )
        extra_params = data.get("extra_params") or {}

        # 3. LangSmith Tracing Configuration
        langsmith_tracing_env = os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2")
        if langsmith_tracing_env is not None:
            langsmith_tracing = langsmith_tracing_env.lower() in ("true", "1", "yes")
        else:
            langsmith_tracing = bool(data.get("langsmith_tracing", False))

        langsmith_api_key = (
            os.getenv("LANGSMITH_API_KEY")
            or os.getenv("LANGCHAIN_API_KEY")
            or data.get("langsmith_api_key")
        )
        langsmith_project = (
            os.getenv("LANGSMITH_PROJECT")
            or os.getenv("LANGCHAIN_PROJECT")
            or data.get("langsmith_project")
            or "boss-agent-mobile"
        )
        langsmith_endpoint = (
            os.getenv("LANGSMITH_ENDPOINT")
            or os.getenv("LANGCHAIN_ENDPOINT")
            or data.get("langsmith_endpoint")
        )

        return cls(
            provider=provider,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout_sec=timeout_sec,
            max_tokens=max_tokens,
            extra_params=extra_params,
            langsmith_tracing=langsmith_tracing,
            langsmith_api_key=langsmith_api_key,
            langsmith_project=langsmith_project,
            langsmith_endpoint=langsmith_endpoint,
        )


def configure_langsmith(config: LLMConfig | None = None) -> None:
    """Configure LangSmith tracing environment variables from config or existing env.

    Sets LANGSMITH_TRACING, LANGCHAIN_TRACING_V2, LANGSMITH_API_KEY, LANGSMITH_PROJECT,
    and LANGSMITH_ENDPOINT when tracing is enabled.
    """
    cfg = config or LLMConfig.from_env_or_file()
    tracing_enabled = (
        cfg.langsmith_tracing
        or os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
        or os.getenv("LANGCHAIN_TRACING_V2", "").lower() in ("true", "1", "yes")
    )
    if tracing_enabled:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        if cfg.langsmith_api_key and not os.getenv("LANGSMITH_API_KEY"):
            os.environ["LANGSMITH_API_KEY"] = cfg.langsmith_api_key
        if cfg.langsmith_project and not os.getenv("LANGSMITH_PROJECT"):
            os.environ["LANGSMITH_PROJECT"] = cfg.langsmith_project
        if cfg.langsmith_endpoint and not os.getenv("LANGSMITH_ENDPOINT"):
            os.environ["LANGSMITH_ENDPOINT"] = cfg.langsmith_endpoint


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

    @abstractmethod
    def evaluate_text_match(self, candidate_resume: str, job_description: str) -> dict[str, Any]:
        """Evaluate match score between resume and job description."""


class OpenAIChatClient(LLMDecisionClient):
    """Concrete OpenAI-compatible REST chat completion client."""

    def __init__(self, config: LLMConfig | None = None):
        cfg = config or LLMConfig.from_env_or_file()
        configure_langsmith(cfg)
        super().__init__(cfg)

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        api_key = self.config.api_key
        if api_key:
            # A masked display value must never be sent as a bearer token.
            s_key = str(api_key).strip()
            if s_key and not _is_mask_placeholder(s_key):
                headers["Authorization"] = f"Bearer {s_key}"
        return headers

    @traceable(name="OpenAIChatClient.chat_completion", run_type="llm")
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

        # Default: disable thinking for MiniMax models to prevent long-running CoT timeouts on structured tasks
        is_minimax = (
            "minimax" in self.config.base_url.lower()
            or "minimax" in self.config.model.lower()
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

        # Handle fallback if response_format or thinking is not supported by a specific OpenAI-compatible provider
        if response.status_code == 400:
            try:
                fallback_payload = {
                    k: v
                    for k, v in payload.items()
                    if k not in ("response_format", "thinking")
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

            run_tree = get_current_run_tree()
            if run_tree:
                run_tree.metadata["model"] = self.config.model
                run_tree.metadata["base_url"] = self.config.base_url
                if "usage" in resp_data and isinstance(resp_data["usage"], dict):
                    run_tree.metadata["usage"] = resp_data["usage"]

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
        s = re.sub(r',\s*"[^"]*"\s*:\s*$', "", s)
        s = re.sub(r',\s*"[^"]*$', "", s)
        s = re.sub(r",\s*$", "", s)

        in_string = False
        escape = False
        stack: list[str] = []

        for c in s:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c in "{[":
                    stack.append(c)
                elif c in "}]" and stack:
                    top = stack[-1]
                    if (c == "}" and top == "{") or (c == "]" and top == "["):
                        stack.pop()

        if in_string:
            s += '"'

        while stack:
            top = stack.pop()
            if top == "{":
                s += "}"
            elif top == "[":
                s += "]"

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
                if c == "\\":
                    escape = True
                    i += 1
                    continue
                if c == '"':
                    in_str = not in_str
                    i += 1
                    continue
                if not in_str:
                    if c in "{[":
                        if c == "[":
                            j = i + 1
                            while j < n and chars[j] in " \t\r\n":
                                j += 1
                            match = re.match(r'^"[^"]+"\s*:', text[j:])
                            if match:
                                chars[i] = "{"
                                stack.append(("converted", "{"))
                                i += 1
                                continue
                        stack.append(("normal", c))
                    elif c in "}]":
                        if stack:
                            kind, open_c = stack.pop()
                            if kind == "converted" and c == "]":
                                chars[i] = "}"
                i += 1
            return "".join(chars)

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
                if c == "\\":
                    out.append(c)
                    escape = True
                    continue
                if c == '"':
                    in_str = not in_str
                    out.append(c)
                    continue
                if in_str:
                    if c == "\n":
                        out.append("\\n")
                    elif c == "\r":
                        out.append("\\r")
                    elif c == "\t":
                        out.append("\\t")
                    else:
                        out.append(c)
                else:
                    out.append(c)
            return "".join(out)

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
                if c == "\\":
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
                        while j < n and chars[j] in " \t\r\n":
                            j += 1
                        if j < n and chars[j] in ",:}]":
                            in_string = False
                            res.append(c)
                        else:
                            # Inner unescaped double quote
                            res.append('\\"')
                    i += 1
                    continue
                res.append(c)
                i += 1
            return "".join(res)

        fixed_quotes = fix_unescaped_quotes_tokenizer(sanitized)
        fixed_quotes = re.sub(r",\s*([\]}])", r"\1", fixed_quotes)
        try:
            return json.loads(fixed_quotes, strict=False)
        except Exception:
            pass

        # 5. Try Python AST literal eval (handles single quotes and Python boolean/None literals)
        try:
            import ast

            py_str = re.sub(r"\btrue\b", "True", fixed_quotes)
            py_str = re.sub(r"\bfalse\b", "False", py_str)
            py_str = re.sub(r"\bnull\b", "None", py_str)
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

    @traceable(name="OpenAIChatClient.chat_completion_json", run_type="chain")
    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Default to OpenAI standard JSON mode format: {"type": "json_object"}
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

    @traceable(name="OpenAIChatClient.evaluate_text_match", run_type="chain")
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
