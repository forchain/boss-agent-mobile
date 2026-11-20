"""
boss_agent.adapters.locator_registry
====================================
Filesystem-backed locator configuration.

The neutral selector vocabulary and parser live in :mod:`boss_agent.automation.locators`;
reading YAML files, resolving repository paths and owning the process-wide registry are
adapter concerns and live here, so the automation package stays free of filesystem I/O.
"""

import os
from pathlib import Path
from typing import Any

import yaml

from boss_agent.automation.locators import UISelector, parse_selector


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dictionary into base dictionary."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge_dict(result[k], v)
        else:
            result[k] = v
    return result


class LocatorRegistry:
    """Loads and manages locator configurations with local override priority."""

    def __init__(
        self,
        base_config_path: str | Path | None = None,
        local_config_path: str | Path | None = None,
    ):
        self._data: dict[str, Any] = {}
        self.base_path = self._resolve_path(
            base_config_path,
            os.environ.get("LOCATORS_CONFIG_PATH", "config/locators.yaml"),
        )
        self.local_path = self._resolve_path(
            local_config_path,
            os.environ.get("LOCATORS_LOCAL_CONFIG_PATH", "config/locators.local.yaml"),
        )
        self.reload()

    def _resolve_path(self, user_path: str | Path | None, default_path: str) -> Path | None:
        if user_path is not None:
            return Path(user_path)
        p = Path(default_path)
        if p.is_absolute() and p.exists():
            return p
        repo_root = Path(__file__).resolve().parents[3]
        candidate = repo_root / default_path
        if candidate.exists() or not p.exists():
            return candidate
        return p

    def reload(self) -> None:
        """Reload configuration from disk, applying local override on top of base."""
        merged: dict[str, Any] = {}

        # 1. Load base configuration
        if self.base_path and self.base_path.exists():
            try:
                content = self.base_path.read_text(encoding="utf-8")
                base_dict = yaml.safe_load(content) or {}
                if isinstance(base_dict, dict):
                    merged = base_dict
            except Exception as e:
                print(f"[LocatorRegistry] Warning loading base config {self.base_path}: {e}")

        # 2. Load local override configuration (Higher priority, git-ignored)
        if self.local_path and self.local_path.exists():
            try:
                content = self.local_path.read_text(encoding="utf-8")
                local_dict = yaml.safe_load(content) or {}
                if isinstance(local_dict, dict):
                    merged = _deep_merge_dict(merged, local_dict)
            except Exception as e:
                print(f"[LocatorRegistry] Warning loading local config {self.local_path}: {e}")

        self._data = merged

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Retrieve raw config node by dot-separated path (e.g. 'search.search_input')."""
        parts = key.split(".")
        curr = self._data
        for part in parts:
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            else:
                return default
        return curr

    def get_selectors(
        self,
        key: str,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ) -> list[UISelector]:
        """Resolve a locator key to a list of UISelectors with automatic strategy detection."""
        raw_val = self.get_raw(key, default)
        if raw_val is None:
            return []

        items = raw_val if isinstance(raw_val, list) else [raw_val]
        selectors: list[UISelector] = []

        for item in items:
            if isinstance(item, str) and format_args:
                try:
                    formatted_val = item.format(**format_args)
                except Exception:
                    formatted_val = item
                selectors.append(parse_selector(formatted_val, description=key))
            else:
                selectors.append(parse_selector(item, description=key))

        return selectors


_GLOBAL_REGISTRY: LocatorRegistry | None = None


def get_global_locator_registry() -> LocatorRegistry:
    """Get or instantiate the global LocatorRegistry instance."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = LocatorRegistry()
    return _GLOBAL_REGISTRY


__all__ = ["LocatorRegistry", "get_global_locator_registry"]
