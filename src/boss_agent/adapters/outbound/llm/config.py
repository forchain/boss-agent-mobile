"""
boss_agent.adapters.outbound.llm.config
=======================================
LLM client configuration and the LangSmith environment wiring.

Configuration data and environment side effects belong to the adapter layer; the
core only sees the ``LLMDecisionClient`` port.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Characters a masked secret is rendered with, and the template's placeholder.
MASK_MARKERS: tuple[str, ...] = ("•", "****")
PLACEHOLDER_API_KEY: str = "your-api-key-here"


def is_mask_placeholder(value: Any) -> bool:
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
        if v is not None and not (k == "api_key" and is_mask_placeholder(v))
    }


#: Framework-neutral fallbacks, used only when a host supplies no configuration.
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
    ) -> LLMConfig:
        """Build a config from a host-supplied baseline, one optional file, and the env.

        Priority: ``settings`` / ``config_path`` -> LLM-specific environment variables
        -> :data:`FRAMEWORK_DEFAULTS`.
        """
        data: dict[str, Any] = {k: v for k, v in (settings or {}).items() if k != "chat"}
        if config_path:
            data.update(_read_config_file(Path(config_path)))

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
            os.getenv("LLM_TEMPERATURE")
            or data.get("temperature")
            or FRAMEWORK_DEFAULTS["temperature"]
        )
        timeout_sec = float(
            os.getenv("LLM_TIMEOUT_SEC")
            or data.get("timeout_sec")
            or FRAMEWORK_DEFAULTS["timeout_sec"]
        )
        max_tokens = int(
            os.getenv("LLM_MAX_TOKENS")
            or data.get("max_tokens")
            or FRAMEWORK_DEFAULTS["max_tokens"]
        )
        extra_params = data.get("extra_params") or {}

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
    """Configure LangSmith tracing environment variables from config or existing env."""
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
