"""
boss_agent.llm
==============
Backward-compatible facade over :mod:`boss_agent.llm_port` (the neutral contract) and
:mod:`boss_agent.adapters.llm_http` (the concrete HTTP clients).

New code should depend on the port directly: ``from boss_agent.llm_port import
LLMDecisionClient``. This module stays so existing imports of the concrete clients keep
working; importing it necessarily imports the HTTP adapter.
"""

from .adapters.llm_http import (
    ANTHROPIC_VERSION,
    AnthropicChatClient,
    OpenAIChatClient,
    configure_langsmith,
)
from .llm_port import (
    FRAMEWORK_DEFAULTS,
    MASK_MARKERS,
    PLACEHOLDER_API_KEY,
    LLMAuthError,
    LLMConfig,
    LLMDecisionClient,
    LLMError,
    LLMTimeoutError,
)

__all__ = [
    "ANTHROPIC_VERSION",
    "FRAMEWORK_DEFAULTS",
    "LLMAuthError",
    "LLMConfig",
    "LLMDecisionClient",
    "LLMError",
    "LLMTimeoutError",
    "MASK_MARKERS",
    "PLACEHOLDER_API_KEY",
    "AnthropicChatClient",
    "OpenAIChatClient",
    "configure_langsmith",
]
