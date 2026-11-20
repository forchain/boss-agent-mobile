"""
boss_agent.adapters.outbound.config.initial_searches
====================================================
Loader for the initial SavedSearch seed presets in the Configuration Realm.

Operator-specific default search presets belong in ``config/`` alongside the rest of
the Configuration Realm (settings, prompts, locators) rather than hardcoded inside the
PocketBase infrastructure provisioner.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .settings import resolve_git_common_root

LOCAL_FILENAME = "initial_searches.local.json"
SEED_FILENAME = "initial_searches.example.json"


def candidate_paths(config_path: str | Path | None = None) -> list[Path]:
    """Resolve the load order: explicit path, shared-root local override, then shipped seed."""
    if config_path is not None:
        return [Path(config_path)]

    root = resolve_git_common_root()
    cwd = Path.cwd()
    return [
        root / "config" / LOCAL_FILENAME,
        root / "config" / SEED_FILENAME,
        cwd / "config" / SEED_FILENAME,
    ]


def load_initial_searches(config_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the default initial SavedSearch seed dictionary from Configuration Realm."""
    checked = candidate_paths(config_path)
    for path in checked:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Initial searches file must contain a JSON object: {path}")
            return copy.deepcopy(raw)
    raise FileNotFoundError(
        "Initial searches seed document not found. Checked: " + ", ".join(str(p) for p in checked)
    )
