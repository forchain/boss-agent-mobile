"""
boss_agent.adapters.shared
============================
Helpers shared between adapter implementations, with no transport dependency.
"""

from __future__ import annotations

from boss_agent.adapters.shared.prompts import load_prompt_or_default

__all__ = ["load_prompt_or_default"]
