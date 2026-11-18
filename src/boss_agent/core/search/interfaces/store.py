"""
boss_agent.core.search.interfaces.store
=======================================
SavedSearch persistence, behind its own repository seam.

ADR 0013 confined ``BaseTaskBroker`` to task lifecycle, leases and realtime logs, but
four SavedSearch verbs stayed on the broker while a second component read the same
collection over raw, unauthenticated HTTP — so the domain had both an over-fat interface
and an unowned side-channel. This store is the seam both of those now pass through: one
read path, one owner, and a caller that names the seam it needs instead of reaching
around the broker for it.

The port lives in core; its PocketBase and InMemory implementations stay in the adapter
module until the adapters ticket moves them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from boss_agent.core.search.domain.models import SavedSearch

#: The id the default-search resolver looks for, and the id it synthesizes.
DEFAULT_SEARCH_ID = "default_agent_search"

#: Called with a deleted search id so a running scheduler can drop its schedule. Declared
#: here because both store adapters accept it, and neither owns the other's module.
ScheduleRevoker = Callable[[str], Awaitable[None]]


class SavedSearchStore(ABC):
    """Repository interface for SavedSearch presets.

    Deliberately broker-free: the Automation Scheduler receives one of these rather
    than a broker, so scheduling logic is testable without lease machinery.
    """

    @abstractmethod
    async def list_saved_searches(self) -> list[SavedSearch]:
        """List all saved search presets."""

    @abstractmethod
    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        """Fetch a saved search preset by ID."""

    @abstractmethod
    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch | None:
        """Create or update a saved search preset.

        ``None`` means the preset did not land. A write cannot degrade the way a read
        can: returning the input unchanged would let a caller believe it persisted a
        record that PocketBase never saw, and the Automation Scheduler's same-minute
        guard reads the ``last_run_at`` this write is supposed to store.
        """

    @abstractmethod
    async def delete_saved_search(self, search_id: str) -> bool:
        """Delete a saved search preset by ID."""

    async def get_default_search(self) -> SavedSearch:
        """Resolve the search a caller should start on when it names none.

        ``default_agent_search`` wins when the store carries it; otherwise the first
        search that exists is a better answer than none; and an empty store still
        yields a synthesized default so a fresh install has something runnable.

        That precedence is the seam's own rather than inherited. It exists so a consumer
        that names no preset has one documented way to choose, and this is it: answering
        the question per adapter instead would make "the default" mean something slightly
        different in each, which is the drift this seam exists to remove. Hence concrete
        on the interface — the adapters differ in where presets come from, not in how one
        is picked.

        The synthesized default is returned but *not* stored: caching it would
        manufacture a preset the collection never held, and callers could not tell a
        real record from one this method invented.
        """
        searches = await self.list_saved_searches()
        for search in searches:
            if search.id == DEFAULT_SEARCH_ID:
                return search
        if searches:
            return searches[0]
        return SavedSearch(id=DEFAULT_SEARCH_ID, name="Default Agent Search")
