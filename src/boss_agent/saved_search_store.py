"""
boss_agent.saved_search_store
=============================
SavedSearch persistence, behind its own repository seam.

ADR 0013 confined ``BaseTaskBroker`` to task lifecycle, leases and realtime logs, but
four SavedSearch verbs stayed on the broker *and* the registry read the collection
anyway, over raw HTTP — so the domain had both an over-fat interface and an unowned
side-channel. This store is the owner the registry's read was missing.

Deleting or renaming a search also has to stop any live schedule pointing at it, which
is why the PocketBase adapter is the only adapter that carries a schedule hook: the
in-memory adapter has no scheduler to notify.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from .models import SavedSearch

logger = logging.getLogger("boss_agent.saved_search_store")

#: Called with a deleted search id so a running scheduler can drop its schedule.
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
    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        """Create or update a saved search preset."""

    @abstractmethod
    async def delete_saved_search(self, search_id: str) -> bool:
        """Delete a saved search preset by ID."""


class InMemorySavedSearchStore(SavedSearchStore):
    """Volatile saved-search registry for tests and local development."""

    def __init__(
        self,
        initial: dict[str, SavedSearch] | None = None,
        on_delete: ScheduleRevoker | None = None,
    ) -> None:
        self._searches: dict[str, SavedSearch] = dict(initial or {})
        self._on_delete = on_delete

    async def list_saved_searches(self) -> list[SavedSearch]:
        return list(self._searches.values())

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        return self._searches.get(search_id)

    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        self._searches[saved_search.id] = saved_search
        return saved_search

    async def delete_saved_search(self, search_id: str) -> bool:
        if search_id not in self._searches:
            return False
        del self._searches[search_id]
        if self._on_delete is not None:
            await self._on_delete(search_id)
        return True


class PocketBaseSavedSearchStore(SavedSearchStore):
    """PocketBase-backed saved searches.

    This is the seam's production adapter; the `SavedSearchRegistry`'s own read is
    still its own. That read is synchronous (it runs during a page load, with a 1.5s
    timeout and no auth headers) and this adapter is async, so folding it in means
    either making the registry async or duplicating a sync path here. Neither was worth
    doing in this pass: the registry already maps its records through the Collection
    Schema, so the field-spelling duplication this seam exists to remove is gone even
    though the transport duplication remains.
    """

    def __init__(
        self,
        *,
        base_url: str,
        session: Any,
        headers: Callable[[], dict[str, str]],
        on_delete: ScheduleRevoker | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self._headers = headers
        self._on_delete = on_delete

    def _collection_url(self) -> str:
        return f"{self.base_url}/api/collections/saved_searches/records"

    async def list_saved_searches(self) -> list[SavedSearch]:
        url = self._collection_url()
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url, params={"perPage": "200", "sort": "-created"}, headers=self._headers()
                ),
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [SavedSearch.from_dict(item["id"], item) for item in items]
        except Exception as e:
            logger.warning("PocketBase list_saved_searches failed: %s", e)
            return []

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        url = f"{self._collection_url()}/{search_id}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self.session.get(url, headers=self._headers())
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return SavedSearch.from_dict(data["id"], data)
        except Exception as e:
            logger.warning("PocketBase get_saved_search for %s failed: %s", search_id, e)
            return None

    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        url = self._collection_url()
        loop = asyncio.get_running_loop()
        body = _wire_body(saved_search)
        try:
            existing = await self.get_saved_search(saved_search.id)
            if existing:
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.patch(
                        f"{url}/{saved_search.id}", json=body, headers=self._headers()
                    ),
                )
            else:
                resp = await loop.run_in_executor(
                    None, lambda: self.session.post(url, json=body, headers=self._headers())
                )
            if resp.ok:
                data = resp.json()
                return SavedSearch.from_dict(data["id"], data)
        except Exception as e:
            logger.warning("PocketBase save_saved_search failed: %s", e)
        return saved_search

    async def delete_saved_search(self, search_id: str) -> bool:
        url = f"{self._collection_url()}/{search_id}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self.session.delete(url, headers=self._headers())
            )
            deleted = resp.status_code in (200, 204)
        except Exception as e:
            logger.warning("PocketBase delete_saved_search failed: %s", e)
            return False
        if deleted and self._on_delete is not None:
            await self._on_delete(search_id)
        return deleted


def _wire_body(saved_search: SavedSearch) -> dict[str, Any]:
    """The record body for a saved search, in the collection's own field spellings."""
    return {
        "id": saved_search.id,
        "name": saved_search.name,
        "description": saved_search.description,
        "keyword": saved_search.search.keyword,
        "enable_search": saved_search.enable_search,
        "enable_filter": saved_search.enable_filter,
        "filter": {
            "education": saved_search.filter.education,
            "salary": saved_search.filter.salary,
            "experience": saved_search.filter.experience,
            "activity": saved_search.filter.activity,
            "company_scales": saved_search.filter.company_scales,
            "industries": saved_search.filter.industries,
            "enable_filter": saved_search.enable_filter,
        },
        "cron_expression": saved_search.cron_expression,
        "is_enabled": saved_search.is_enabled,
        "last_run_at": saved_search.last_run_at,
        "target_task_type": saved_search.target_task_type,
        "target_action": saved_search.target_action,
        "max_jobs": saved_search.max_jobs,
    }
