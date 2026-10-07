"""
tests.unit.test_run_live_test_preset_loading
============================================
How ``scripts/run_live_test.py`` resolves saved-search presets through the
``SavedSearchStore`` seam (issue #394).

The script is a synchronous caller of an async seam, so the behaviour worth pinning is
not the plumbing but the two judgement calls the migration forced:

  * the registry answered an unreachable PocketBase with "no data" and the built-in
    defaults, and the CLI still needs to be runnable on a laptop with no database;
  * a preset that simply is not there is not a transport failure, and substituting the
    defaults for it would run a preset the operator never asked for.
"""

import sys
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from scripts import run_live_test as live_test

from boss_agent.errors import TransportError
from boss_agent.saved_search_store import (
    InMemorySavedSearchStore,
    SavedSearchStore,
    resolve_saved_search_store,
)
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig

_ALPHA = SavedSearch(
    id="alpha_preset",
    name="Alpha Preset",
    search=SearchConfig(keyword="alpha"),
    filter=FilterConfig(education="本科"),
)


class _FakeSession:
    """An Appium stand-in: no server is contacted and no process is started."""

    def __init__(self, config: object) -> None:
        self.config = config

    def start(self) -> MagicMock:
        return MagicMock()

    def stop(self) -> None:
        return None


class _FakePath:
    """``pathlib.Path`` stand-in, so a unit run never writes ``~/.boss_agent``."""

    def __init__(self, *parts: object) -> None:
        self.parts = parts

    @classmethod
    def home(cls) -> "_FakePath":
        return cls("home")

    def __truediv__(self, other: object) -> "_FakePath":
        return _FakePath(*self.parts, other)

    def mkdir(self, **kwargs: object) -> None:
        return None

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        return None

    def __str__(self) -> str:
        return "/fake/artifact"


class _NoSleepTime:
    """The runner pauses between Appium steps; a unit run must not spend them."""

    def sleep(self, _seconds: float) -> None:
        return None


@dataclass
class _FakeJob:
    title: str = "资深 Agent"
    company_name: str = "示例公司"
    salary_range: str = "40-60K"


def _stub_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace everything ``run_live_test`` needs a device for, with local stand-ins."""
    monkeypatch.setattr(live_test, "AppiumSession", _FakeSession)
    monkeypatch.setattr(live_test, "TakeoverHandler", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr(live_test, "Path", _FakePath)
    monkeypatch.setattr(live_test, "time", _NoSleepTime())


class _UnreachableStore(InMemorySavedSearchStore):
    """A store whose PocketBase is down, the way ``execute_broker_request`` reports it."""

    async def get_saved_search(self, search_id: str) -> SavedSearch | None:
        raise TransportError("PocketBase get_saved_search failed: connection refused")

    async def list_saved_searches(self) -> list[SavedSearch]:
        raise TransportError("PocketBase list_saved_searches failed: connection refused")


def _default_store() -> SavedSearchStore:
    return resolve_saved_search_store(prefer_database=False)


def test_read_saved_searches_reads_the_presets_from_the_store():
    """The listing comes off the seam, so the table is whatever the store actually holds."""
    searches = live_test.read_saved_searches(
        store=InMemorySavedSearchStore({"alpha_preset": _ALPHA})
    )

    assert [search.id for search in searches] == ["alpha_preset"]


def test_list_saved_searches_renders_what_the_store_holds(capsys):
    live_test.list_saved_searches(store=InMemorySavedSearchStore({"alpha_preset": _ALPHA}))

    printed = capsys.readouterr().out
    assert "alpha_preset" in printed
    assert "Alpha Preset" in printed


def test_read_saved_search_resolves_a_known_preset():
    loaded = live_test.read_saved_search(
        "alpha_preset", store=InMemorySavedSearchStore({"alpha_preset": _ALPHA})
    )

    assert loaded is not None
    assert loaded.search.keyword == "alpha"


def test_read_saved_search_reports_a_miss_as_none():
    """A store answers a miss with ``None``; the registry's ``KeyError`` is gone."""
    loaded = live_test.read_saved_search(
        "absent", store=InMemorySavedSearchStore({"alpha_preset": _ALPHA})
    )

    assert loaded is None


def test_read_saved_search_falls_back_to_defaults_when_pocketbase_is_unreachable(
    monkeypatch, capsys
):
    """A database-less run must still find the built-in presets — but it has to say so.

    The registry probed PocketBase while it was being built and swallowed every failure,
    so an unreachable database was indistinguishable from an empty one. The store does no
    I/O at construction and raises a typed ``TransportError`` from the read instead, which
    means the fallback has to be written down somewhere. This is that place: the operator
    is told the database was skipped before the run proceeds on defaults.
    """
    requested: list[bool] = []

    def fake_resolver(prefer_database: bool = True) -> SavedSearchStore:
        requested.append(prefer_database)
        return _UnreachableStore() if prefer_database else _default_store()

    monkeypatch.setattr(live_test, "resolve_saved_search_store", fake_resolver)

    loaded = live_test.read_saved_search("default_agent_search")

    assert loaded is not None
    assert loaded.search.keyword == "agent"
    assert requested == [True, False]
    assert "connection refused" in capsys.readouterr().out


def test_a_miss_does_not_substitute_the_default_presets():
    """A store that answered is not a store that failed — the difference is the run."""
    assert live_test.read_saved_search("absent", store=InMemorySavedSearchStore()) is None


def test_read_saved_searches_falls_back_to_defaults_when_pocketbase_is_unreachable(monkeypatch):
    requested: list[bool] = []

    def fake_resolver(prefer_database: bool = True) -> SavedSearchStore:
        requested.append(prefer_database)
        return _UnreachableStore() if prefer_database else _default_store()

    monkeypatch.setattr(live_test, "resolve_saved_search_store", fake_resolver)

    searches = live_test.read_saved_searches()

    assert [search.id for search in searches] == ["default_agent_search", "ai_llm_engineer"]
    assert requested == [True, False]


def test_run_live_test_stops_and_names_the_preset_when_it_is_unknown(capsys):
    """The operator has to learn *which* preset was missing, and which ones exist."""
    assert (
        live_test.run_live_test(
            search_id="absent",
            saved_search_store=InMemorySavedSearchStore({"alpha_preset": _ALPHA}),
        )
        is False
    )

    printed = capsys.readouterr().out
    assert "absent" in printed
    assert "alpha_preset" in printed


def test_run_live_test_hands_the_injected_store_to_the_harness(monkeypatch):
    """The injected store has to reach the harness, or the parameter is a lie.

    ``run_live_test`` resolves the preset itself, so the store it is handed is not
    consulted on this path today — which is exactly why the omission survived. The
    next caller who injects a store and watches the harness build its own authenticated
    one has been handed a parameter that does nothing, with no failure to notice it.
    """
    captured: dict[str, object] = {}
    store = InMemorySavedSearchStore({"alpha_preset": _ALPHA})

    class _FakeHarness:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_smoke_test(self) -> _FakeJob:
            return _FakeJob()

    _stub_the_device(monkeypatch)
    monkeypatch.setattr(live_test, "SmokeHarness", _FakeHarness)

    assert live_test.run_live_test(search_id="alpha_preset", saved_search_store=store) is True

    assert captured["saved_search_store"] is store


def test_run_live_test_names_a_bracketed_preset_it_could_have_run(capsys):
    """Rich reads ``[we[ird]]`` as a markup tag, so the id list has to be escaped.

    Without the escape the one line telling the operator what to type instead comes out
    with the id swallowed — a message that names the miss and not the fix.
    """
    store = InMemorySavedSearchStore(
        {"we[ird]": SavedSearch(id="we[ird]", name="Weird", search=SearchConfig(keyword="alpha"))}
    )

    assert live_test.run_live_test(search_id="absent", saved_search_store=store) is False

    assert "we[ird]" in capsys.readouterr().out


def test_main_degrades_to_cli_defaults_when_the_preset_is_unknown(monkeypatch, capsys):
    """``main`` must keep going without a preset rather than dying on a typed error."""
    captured: dict[str, object] = {}

    def fake_run_live_test(**kwargs: object) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(live_test, "run_live_test", fake_run_live_test)
    monkeypatch.setattr(
        live_test,
        "resolve_saved_search_store",
        lambda prefer_database=True: InMemorySavedSearchStore(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_live_test.py", "--config", "/non/existent/path.yaml", "--search-id", "absent"],
    )

    with pytest.raises(SystemExit) as excinfo:
        live_test.main()

    assert excinfo.value.code == 0
    assert captured["keyword"] is None
    assert "absent" in capsys.readouterr().out


def test_main_uses_a_resolved_preset_when_the_database_answers(monkeypatch):
    """The happy path still takes the preset's own keyword and flags, not the CLI's."""
    captured: dict[str, object] = {}

    def fake_run_live_test(**kwargs: object) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(live_test, "run_live_test", fake_run_live_test)
    monkeypatch.setattr(
        live_test, "resolve_saved_search_store", lambda prefer_database=True: _default_store()
    )
    monkeypatch.setattr(sys, "argv", ["run_live_test.py", "--config", "/non/existent/path.yaml"])

    with pytest.raises(SystemExit) as excinfo:
        live_test.main()

    assert excinfo.value.code == 0
    assert captured["keyword"] == "agent"
    assert captured["search_id"] == "default_agent_search"
