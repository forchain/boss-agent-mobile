"""
boss_agent.adapters.llm_factory
===============================
Composition-root wiring for the LLM seam: protocol -> concrete client, and the
configuration realm assembled for it.

Concrete clients are instantiated here, at the adapter boundary, never inside the
application modules. Application code that needs a client should receive an
``LLMDecisionClient``; the CLI/worker entrypoints are the intended callers of
:func:`create_llm_client`.
"""

from __future__ import annotations

from pathlib import Path

from boss_agent import config_realm
from boss_agent.adapters.llm_http import AnthropicChatClient, OpenAIChatClient
from boss_agent.llm_port import LLMConfig, LLMDecisionClient

#: Wire protocol -> client. The keys are *protocols*, not vendors: a vendor is reached by
#: naming its protocol, and anything absent speaks the default one. That is what lets a
#: legacy `provider: minimax` or `provider: deepseek` keep working unchanged — those
#: name a model house, and both expose OpenAI-compatible Chat Completions.
PROTOCOL_CLIENTS: dict[str, type[LLMDecisionClient]] = {
    "anthropic": AnthropicChatClient,
    "openai": OpenAIChatClient,
}

#: The protocol a config lands on when its provider names none this layer speaks.
DEFAULT_PROTOCOL: str = "openai"

#: Pre-realm files this realm carries, in the position they have always occupied: just
#: above the shipped example template, below everything else in the canonical chain.
LLM_ONLY_FILES: tuple[Path, ...] = (
    Path("config/llm.local.yaml"),
    Path("config/llm.local.json"),
    Path("config/llm_config.yaml"),
)


def llm_config_chain() -> list[Path]:
    """This realm's chain: the canonical one with the LLM-only files above its floor."""
    chain = config_realm.resolve_chain()
    return chain[:-1] + list(LLM_ONLY_FILES) + chain[-1:]


def load_llm_config(config_path: str | Path | None = None) -> LLMConfig:
    """The LLM client's configuration, resolved from the realm.

    The realm owns the merge, the defaults and the masked-secret guard; the framework
    client only applies its own `LLM_*` environment layer on top.
    """
    if config_path:
        return LLMConfig.from_env_or_file(config_path=config_path)
    return LLMConfig.from_env_or_file(settings=config_realm.load_chain(llm_config_chain()))


def client_class_for_provider(provider: str | None) -> type[LLMDecisionClient]:
    """The client class speaking ``provider``'s protocol.

    Unrecognised values converge on :data:`DEFAULT_PROTOCOL` rather than failing: a
    config naming a vendor this build has no bespoke client for still gets a working
    client, because the vendor almost certainly speaks the default protocol.
    """
    return PROTOCOL_CLIENTS.get(
        (provider or "").strip().lower(), PROTOCOL_CLIENTS[DEFAULT_PROTOCOL]
    )


def create_llm_client(
    config_path: str | Path | None = None,
    config: LLMConfig | None = None,
) -> LLMDecisionClient:
    """Instantiate the client for the configured provider's protocol.

    If `config` is provided, it is used directly; otherwise configuration is loaded
    from the application's configuration realm. The return type is the framework's
    abstraction, so callers depend on the decision interface rather than on which
    protocol is currently configured.
    """
    cfg = config if config is not None else load_llm_config(config_path=config_path)
    return client_class_for_provider(cfg.provider)(cfg)


__all__ = [
    "DEFAULT_PROTOCOL",
    "LLM_ONLY_FILES",
    "PROTOCOL_CLIENTS",
    "client_class_for_provider",
    "create_llm_client",
    "llm_config_chain",
    "load_llm_config",
]
