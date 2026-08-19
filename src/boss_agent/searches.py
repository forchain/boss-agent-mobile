"""
boss_agent.searches
===================
Registry and manager for saved searches and query presets.
"""

from pathlib import Path
from typing import Any

import yaml

from .models import SavedSearch


class SavedSearchRegistry:
    """Registry managing preconfigured, persistent search & filter query presets."""

    def __init__(self, config_path: str | Path | None = None):
        self._searches: dict[str, SavedSearch] = {}
        self.config_path = Path(config_path) if config_path else self._find_default_config()
        if self.config_path and self.config_path.exists():
            self.load_from_yaml(self.config_path)

    def _find_default_config(self) -> Path | None:
        """Locate default config/searches.yaml relative to project root."""
        possible_paths = [
            Path(__file__).resolve().parent.parent.parent / "config" / "searches.yaml",
            Path("config/searches.yaml"),
        ]
        for p in possible_paths:
            if p.exists():
                return p
        return None

    def load_from_yaml(self, filepath: str | Path) -> None:
        """Load and parse saved searches from a YAML file."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Saved searches config file not found: {path}")

        content = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content) or {}
        self.load_from_dict(parsed)

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Populate registry from dictionary data."""
        searches_dict = data.get("searches", data)
        for search_id, item_data in searches_dict.items():
            if isinstance(item_data, dict):
                saved_search = SavedSearch.from_dict(search_id, item_data)
                self._searches[search_id] = saved_search

    def get(self, search_id: str) -> SavedSearch:
        """Retrieve a SavedSearch by its identifier.

        Raises KeyError if search_id does not exist.
        """
        if search_id not in self._searches:
            available = ", ".join(self._searches.keys()) or "none"
            raise KeyError(
                f"Saved search '{search_id}' not found. Available searches: [{available}]"
            )
        return self._searches[search_id]

    def list_all(self) -> list[SavedSearch]:
        """Return all registered SavedSearch instances in definition order."""
        return list(self._searches.values())

    def register(self, saved_search: SavedSearch) -> None:
        """Add or update a saved search preset in the registry."""
        self._searches[saved_search.id] = saved_search

    def get_default_search(self) -> SavedSearch:
        """Get the default startup search (default_agent_search or first available)."""
        if "default_agent_search" in self._searches:
            return self._searches["default_agent_search"]
        if self._searches:
            return next(iter(self._searches.values()))
        # Fallback default
        fallback = SavedSearch(id="default_agent_search", name="Default Agent Search")
        self._searches["default_agent_search"] = fallback
        return fallback


_global_search_registry: SavedSearchRegistry | None = None


def get_global_search_registry() -> SavedSearchRegistry:
    """Access the global singleton SavedSearchRegistry instance."""
    global _global_search_registry
    if _global_search_registry is None:
        _global_search_registry = SavedSearchRegistry()
    return _global_search_registry
