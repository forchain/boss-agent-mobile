"""
boss_agent.llm_config
=====================
The Configuration Realm's LLM section, assembled for the framework's client.

`droid_agent_core` is an app-agnostic framework layer: it must not read this
application's configuration, and it must not contain this application's name. So the
LLM realm is resolved *here* and handed over as data.

That inversion is what removed the last second defaults table. The framework's client
used to start from the example *template* and ship `max_tokens=16384` /
`timeout_sec=300.0` while every other realm shipped `262144` / `120.0`, so a
config-less run silently used a sixteenth of the documented token ceiling. Now there is
one table (:mod:`boss_agent.config_realm`), one chain, and one place that joins them.
"""

from __future__ import annotations

from pathlib import Path

from droid_agent_core.llm import LLMConfig

from . import config_realm

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


__all__ = ["LLM_ONLY_FILES", "llm_config_chain", "load_llm_config"]
