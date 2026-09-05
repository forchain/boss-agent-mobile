"""
boss_agent.searches
===================
Registry and manager for saved searches and query presets.
Loads and synchronizes searches directly from PocketBase database,
falling back to system defaults in memory if database is unreachable.
"""

from typing import Any

import requests

from .broker.provisioner import DEFAULT_INITIAL_SEARCHES
from .models import SavedSearch
from .settings import resolve_pocketbase_url


class SavedSearchRegistry:
    """Registry managing preconfigured, persistent search & filter query presets."""

    def __init__(
        self,
        pocketbase_url: str | None = None,
        prefer_database: bool = True,
        initial_searches: dict[str, Any] | None = None,
    ):
        self._searches: dict[str, SavedSearch] = {}
        self.pocketbase_url = pocketbase_url
        loaded_from_db = False
        if prefer_database:
            resolved_url = pocketbase_url or resolve_pocketbase_url()
            loaded_from_db = self.load_from_pocketbase(resolved_url)

        if not loaded_from_db:
            # Populate with defaults in memory
            defaults = (
                initial_searches if initial_searches is not None else DEFAULT_INITIAL_SEARCHES
            )
            self.load_from_dict(defaults)

    def load_from_pocketbase(self, pb_url: str | None = None) -> bool:
        """Fetch saved searches from PocketBase saved_searches collection."""
        target_url = (pb_url or resolve_pocketbase_url() or "").rstrip("/")
        if not target_url:
            return False
        endpoint = f"{target_url}/api/collections/saved_searches/records"
        try:
            resp = requests.get(
                endpoint,
                params={"perPage": 200, "sort": "-created"},
                timeout=1.5,
            )
            if resp.ok:
                items = resp.json().get("items", [])
                if items:
                    for item in items:
                        search_id = item.get("id")
                        if search_id:
                            self._searches[search_id] = SavedSearch.from_dict(search_id, item)
                    return True
        except Exception:
            pass
        return False

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
