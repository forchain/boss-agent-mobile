"""
boss_agent.llm_config
=====================
Backward-compatible facade over :mod:`boss_agent.adapters.llm_factory`.

The wiring itself lives at the adapter boundary; this module stays so existing callers
and string-targeted patches (``patch("boss_agent.llm_config.create_llm_client")``) keep
resolving. New entrypoints should import from the adapter module directly.
"""

from .adapters.llm_factory import (
    DEFAULT_PROTOCOL,
    LLM_ONLY_FILES,
    PROTOCOL_CLIENTS,
    client_class_for_provider,
    create_llm_client,
    llm_config_chain,
    load_llm_config,
)

__all__ = [
    "DEFAULT_PROTOCOL",
    "LLM_ONLY_FILES",
    "PROTOCOL_CLIENTS",
    "client_class_for_provider",
    "create_llm_client",
    "llm_config_chain",
    "load_llm_config",
]
