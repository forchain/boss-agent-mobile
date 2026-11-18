"""
boss_agent.adapters.memory.saved_search_store
=============================================
Volatile SavedSearch store for tests and local development.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from boss_agent.core.search.domain.models import SavedSearch
from boss_agent.core.search.interfaces.store import SavedSearchStore

if TYPE_CHECKING:
    from boss_agent.adapters.outbound.pocketbase.saved_search_store import ScheduleRevoker


def _hydrate_fixture(
    fixture: Mapping[str, SavedSearch | dict[str, Any]],
) -> dict[str, SavedSearch]:
    """Coerce a saved-search fixture into domain objects, keyed by id.

    Fixtures reach this seam in two shapes: the domain type, and the raw
    record-shaped dicts the provisioner ships as ``DEFAULT_INITIAL_SEARCHES``. Both
    are accepted so ``resolve_saved_search_store(prefer_database=False)`` hands the
    defaults over verbatim instead of rebuilding them into domain objects first.

    They are hydrated through bare ``from_dict`` rather than the schema: a fixture is
    authored in the domain's own vocabulary, not the collection's, so normalizing one
    first would resolve columns the author never wrote.

    The mapping is taken as it comes — a ``{"searches": {...}}`` wrapper is not
    unwrapped. Nothing produces one: the loader that accepted it is gone with the
    registry, so tolerating it would be support for a shape no caller has, and a
    wrapper handed over by mistake would arrive looking like one preset named
    ``searches`` rather than like an empty store.
    """
    return {
        search_id: value
        if isinstance(value, SavedSearch)
        else SavedSearch.from_dict(search_id, value)
        for search_id, value in fixture.items()
    }


class InMemorySavedSearchStore(SavedSearchStore):
    """Volatile saved-search store for tests and local development."""

    def __init__(
        self,
        initial: Mapping[str, SavedSearch | dict[str, Any]] | None = None,
        on_delete: ScheduleRevoker | None = None,
    ) -> None:
        self._searches: dict[str, SavedSearch] = _hydrate_fixture(initial or {})
        self._on_delete = on_delete

    async def list_saved_searches(self) -> list[SavedSearch]:
        return list(self._searches.values())

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        return self._searches.get(search_id)

    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch | None:
        self._searches[saved_search.id] = saved_search
        return saved_search

    async def delete_saved_search(self, search_id: str) -> bool:
        if search_id not in self._searches:
            return False
        del self._searches[search_id]
        if self._on_delete is not None:
            await self._on_delete(search_id)
        return True
