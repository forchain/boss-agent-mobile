"""
boss_agent.adapters.outbound.pocketbase.saved_search_store
==========================================================
PocketBase SavedSearch persistence, behind its own repository seam.

ADR 0013 confined ``BaseTaskBroker`` to task lifecycle, leases and realtime logs, but
four SavedSearch verbs stayed on the broker while a second component read the same
collection over raw, unauthenticated HTTP — so the domain had both an over-fat interface
and an unowned side-channel. This store is the seam both of those now pass through: one
read path, one owner, and a caller that names the seam it needs instead of reaching
around the broker for it.

Deleting or renaming a search also has to stop any live schedule pointing at it, which
is why the PocketBase adapter is the only adapter that carries a schedule hook: the
in-memory adapter has no scheduler to notify.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import requests

from boss_agent.adapters.memory.saved_search_store import InMemorySavedSearchStore
from boss_agent.adapters.outbound.config.settings import resolve_pocketbase_url
from boss_agent.core.search.domain.models import SavedSearch
from boss_agent.core.search.interfaces.store import SavedSearchStore, ScheduleRevoker

from .async_bridge import execute_broker_request
from .pocketbase_auth import pocketbase_headers

logger = logging.getLogger("boss_agent.adapters.outbound.pocketbase.saved_search_store")

#: Columns the domain interprets itself when they are absent, by falling back to a
#: sibling key. The schema's storage default must not be substituted for these before
#: that derivation runs, or e.g. an empty ``target_action`` would stop inheriting
#: ``target_task_type`` and quietly invert execution depth.
_DERIVED_ON_ABSENCE = ("name", "keyword", "enable_search", "enable_filter", "target_action")


def missing_saved_search_message(search_id: str, available_ids: Iterable[str]) -> str:
    """The one sentence a missed preset is reported in, whichever caller found it.

    Two callers report a miss to a human — the harness re-raises it as a ``KeyError``,
    the CLI prints it in red — and they used to assemble the text separately. Someone
    reading a stack trace and someone reading a terminal were then told subtly
    different things about the same miss, and the half naming the presets that *do*
    exist is the half that makes a typo fixable without reading the source.

    An empty store reports ``none`` rather than empty brackets: ``Available searches: []``
    reads as a formatting bug instead of as the answer it is.
    """
    available = ", ".join(available_ids) or "none"
    return f"Saved search '{search_id}' not found. Available searches: [{available}]"


def record_to_saved_search(search_id: str, record: Mapping[str, Any]) -> SavedSearch:
    """Map a raw ``saved_searches`` record to the domain type through the schema.

    Reading a collection through the schema rather than a private set of fallbacks is
    what keeps a stored preset from drifting away from the domain: a record written
    before a column existed returns without it, and an absent column has to land on the
    *declared* default rather than on whatever the reader happened to guess. The
    ``max_jobs`` 20-vs-30 drift was exactly that class of bug.

    The schema is imported inside the function so this store module does not depend on
    the collection schema at import time; the core domain model resolves its own schema
    default the same way.
    """
    from .collection_schema import SAVED_SEARCHES, normalize_record

    return SavedSearch.from_dict(
        search_id, normalize_record(SAVED_SEARCHES, record, exclude=_DERIVED_ON_ABSENCE)
    )


class PocketBaseSavedSearchStore(SavedSearchStore):
    """PocketBase-backed saved searches.

    This is the seam's production adapter, and it now owns the record→domain mapping
    too: reads go through the Collection Schema (``record_to_saved_search``) rather
    than the bare ``from_dict`` the adapter started with. That closes the gap a second
    reader used to cover — the field-spelling duplication this seam exists to remove is
    gone, and so is the only remaining reason for an unauthenticated read path to exist.
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
        resp = await execute_broker_request(
            lambda: self.session.get(
                url, params={"perPage": "200", "sort": "-created"}, headers=self._headers()
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix="PocketBase list_saved_searches failed",
        )
        if resp.status_code == 404:
            return []
        items = resp.json().get("items", [])
        return [record_to_saved_search(item["id"], item) for item in items]

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        url = f"{self._collection_url()}/{search_id}"
        resp = await execute_broker_request(
            lambda: self.session.get(url, headers=self._headers()),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix=f"PocketBase get_saved_search for {search_id} failed",
        )
        if resp.status_code == 404:
            return None
        data = resp.json()
        return record_to_saved_search(data["id"], data)

    async def save_saved_search(self, saved_search: SavedSearch) -> SavedSearch | None:
        url = self._collection_url()
        body = _wire_body(saved_search)
        existing = await self.get_saved_search(saved_search.id)
        if existing:
            resp = await execute_broker_request(
                lambda: self.session.patch(
                    f"{url}/{saved_search.id}", json=body, headers=self._headers()
                ),
                expected_statuses=(200,),
                error_prefix=f"PocketBase save_saved_search for {saved_search.id} failed",
            )
        else:
            resp = await execute_broker_request(
                lambda: self.session.post(url, json=body, headers=self._headers()),
                expected_statuses=(200, 201),
                error_prefix=f"PocketBase save_saved_search for {saved_search.id} failed",
            )
        data = resp.json()
        return record_to_saved_search(data["id"], data)

    async def delete_saved_search(self, search_id: str) -> bool:
        url = f"{self._collection_url()}/{search_id}"
        resp = await execute_broker_request(
            lambda: self.session.delete(url, headers=self._headers()),
            expected_statuses=(200, 204),
            allow_404=True,
            error_prefix=f"PocketBase delete_saved_search for {search_id} failed",
        )
        deleted = resp.status_code in (200, 204)
        if deleted and self._on_delete is not None:
            await self._on_delete(search_id)
        return deleted


def resolve_saved_search_store(prefer_database: bool = True) -> SavedSearchStore:
    """The one place a consumer asks for a saved-search store, so it cannot drift.

    Domain and CLI consumers used to each assemble their own store, which is how the
    same collection ended up being read with and without an auth token. This mirrors
    ``PocketBaseTaskBroker.__init__`` — same URL resolution, same
    ``POCKETBASE_AUTH_TOKEN``, same bearer header — so a harness started outside the
    broker talks to PocketBase exactly as one started inside it does.

    Construction deliberately performs no I/O. The retired registry probed the
    collection while being built and swallowed every failure, so a store that could
    not be built looked identical to one that was merely empty; here a transport
    failure surfaces from the read that actually needed it, where the caller can see
    which call and which URL produced it.

    ``prefer_database=False`` seeds ``DEFAULT_INITIAL_SEARCHES`` because that is where
    the "database unreachable, fall back to defaults in memory" behavior lived. Note
    the asymmetry: ``InMemorySavedSearchStore()`` composes empty (the in-memory broker
    relies on it), and the seeding is an explicit choice here.
    """
    from .provisioner import DEFAULT_INITIAL_SEARCHES

    if not prefer_database:
        return InMemorySavedSearchStore(DEFAULT_INITIAL_SEARCHES)

    auth_token = os.getenv("POCKETBASE_AUTH_TOKEN")

    def _headers() -> dict[str, str]:
        return pocketbase_headers(auth_token)

    return PocketBaseSavedSearchStore(
        base_url=resolve_pocketbase_url(),
        session=requests.Session(),
        headers=_headers,
    )


def _wire_body(saved_search: SavedSearch) -> dict[str, Any]:
    """The record body for a saved search, in the collection's own field spellings.

    ``filter`` is a whole-column replacement, not a merge: PocketBase stores one JSON
    value, so a key left out here is a key erased from the record. That makes this
    dict the single point where a ``FilterConfig`` field can silently disappear, and
    the Automation Scheduler amplifies it — it rewrites every dispatched strategy in
    full, purely to stamp ``last_run_at``, so any field missing here was reset by the
    next scheduled run rather than by anything the operator did.

    Hence the explicit list rather than a shorter body: ``channel_preference`` was
    exactly that field (issue #368), and it was dropped from here when the per-strategy
    channel shipped. ``''`` is written as ``''``, never omitted, because inherit is a
    decision the record has to carry to stay one.
    """
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
            "channel_preference": saved_search.filter.channel_preference,
        },
        "cron_expression": saved_search.cron_expression,
        "is_enabled": saved_search.is_enabled,
        "last_run_at": saved_search.last_run_at,
        "target_task_type": saved_search.target_task_type,
        "target_action": saved_search.target_action,
        "max_jobs": saved_search.max_jobs,
    }
