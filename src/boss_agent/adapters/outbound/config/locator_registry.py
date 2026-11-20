"""
boss_agent.adapters.outbound.config.locator_registry
====================================================
YAML-backed locator configuration behind ``LocatorStorePort``.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from boss_agent.core.automation.domain.locators import (
    UISelector,
    deep_merge_dict,
    parse_selector,
)

logger = logging.getLogger(__name__)


class YamlLocatorRegistry:
    """Loads base and local locator YAML, local overriding base."""

    def __init__(
        self,
        base_config_path: str | Path | None = None,
        local_config_path: str | Path | None = None,
    ) -> None:
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

    @staticmethod
    def _resolve_path(user_path: str | Path | None, default_path: str) -> Path | None:
        if user_path is not None:
            return Path(user_path)
        p = Path(default_path)
        if p.is_absolute() and p.exists():
            return p
        repo_root = Path(__file__).resolve().parents[5]
        candidate = repo_root / default_path
        if candidate.exists() or not p.exists():
            return candidate
        return p

    def reload(self) -> None:
        """Reload configuration from disk, applying local override on top of base."""
        merged: dict[str, Any] = {}

        if self.base_path and self.base_path.exists():
            try:
                base_dict = yaml.safe_load(self.base_path.read_text(encoding="utf-8")) or {}
                if isinstance(base_dict, dict):
                    merged = base_dict
            except Exception as e:
                logger.warning("Loading base locator config %s failed: %s", self.base_path, e)

        if self.local_path and self.local_path.exists():
            try:
                local_dict = yaml.safe_load(self.local_path.read_text(encoding="utf-8")) or {}
                if isinstance(local_dict, dict):
                    merged = deep_merge_dict(merged, local_dict)
            except Exception as e:
                logger.warning("Loading local locator config %s failed: %s", self.local_path, e)

        self._data = merged

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Retrieve a raw config node by dot-separated path (e.g. 'search.search_input')."""
        curr: Any = self._data
        for part in key.split("."):
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
                with contextlib.suppress(Exception):
                    item = item.format(**format_args)
            selectors.append(parse_selector(item, description=key))

        return selectors


#: Historical name.
LocatorRegistry = YamlLocatorRegistry
