"""
tests/unit/test_pages_submodule_isolation.py
============================================
The layer boundaries of the `boss_agent.pages` package (spec #395, ticket #404).

Ticket #398 split the 2,015-line `pages.py` monolith into five feature submodules behind a
backward-compatible facade, and tickets #400 and #402 moved two consumers onto the narrow
imports. Nothing yet stopped the split from quietly rotting back: a `job_feed` edit reaching
for a chat screen, or a decision module importing Appium to read a card, would have passed
the tier. These tests are the architecture-wide guard, read from the source tree by AST --
nothing under test is imported, so the suite says nothing about a machine with no device.

The boundaries enforced here, one rule each:

1. `base` and `system` are leaves. They may use the shared `base` layer and nothing else --
   no feature screen, and not the facade, which imports them (so a facade import is also the
   shortest possible import cycle).
2. No submodule imports the facade. `boss_agent.pages/__init__.py` imports every submodule,
   so an edge back into it is always a cycle.
3. `communication` never imports `job_feed` or `job_detail`: 仅沟通 chat reads conversations,
   never job discovery.
4. `job_feed` never imports `communication`, for the same reason in the other direction.
5. The five submodules form no import cycle at all.
6. Only the screen drivers may import page objects. Every other module under `src/boss_agent/`
   is a decision, entity, store, prompt or broker module that has no business paying Appium's
   import cost -- see `SCREEN_DRIVERS` for the accepted exception.

The accepted cross-feature exception, stated once so it is not mistaken for an oversight:
`src/boss_agent/feed_pipeline.py` imports `ChatPage` from `boss_agent.pages.communication`.
`JobFeedPipeline` opens the chat screen to send the greeting it just evaluated, so that is a
real runtime dependency and not a leftover import. Ticket #400's criterion was amended to
"no broad facade import" rather than "zero communication imports" for exactly this edge.
`feed_pipeline` is a screen driver, so rules 1-5 never see it -- and rule 6 permits the edge
without caring whether the name arrives through the submodule or the facade.

Resolution is real, not string-matched: `from .pages.communication import ChatPage` inside
`src/boss_agent/chat_triage.py` is `boss_agent.pages.communication`, and `from ..job_entities
import JobCardBrief` inside `src/boss_agent/pages/__init__.py` is `boss_agent.job_entities`.
A guard that only read `node.module` would pass on every relative import in the tree and
would be worthless -- which is why `test_the_relative_import_resolver_...` below pins the
resolver against synthetic sources first.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = REPO_ROOT / "src"
#: This repo's own package. `src/` also holds `droid_agent_core`, the bundled Appium driver,
#: whose modules are not this layer's business and are never scanned.
BOSS_AGENT_ROOT = SOURCE_ROOT / "boss_agent"

PAGES_PACKAGE = "boss_agent.pages"

#: The five feature submodules the split produced, and the nodes the cycle rule walks.
FEATURE_SUBMODULES = ("base", "system", "job_feed", "job_detail", "communication")

#: The only page-layer module `base` and `system` may depend on: the shared screen base they
#: both subclass. `base` is the root of the layer, so even `system` is out of reach from it.
ONLY_BASE_IS_ALLOWED = (f"{PAGES_PACKAGE}.base",)


class ImportRecord(NamedTuple):
    """One `import`/`from ... import` edge, resolved to an absolute module path."""

    module: str
    symbol: str
    lineno: int


def _dotted_names(path: Path) -> tuple[str, str]:
    """`(module, package)` for a source file under `src/`.

    A package `__init__` *is* its package -- `boss_agent/pages/__init__.py` is the module
    `boss_agent.pages` -- so it must drop the `__init__` segment. Naming it
    `boss_agent.pages.__init__` instead would resolve its relative imports against a package
    that does not exist, and the facade's edges would vanish from the cycle rule exactly when
    they matter most.
    """
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts), ".".join(parts if path.name == "__init__.py" else parts[:-1])


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """Apply a relative import's `level` dots to `package`, then append `module`."""
    parts = package.split(".") if package else []
    parts = parts[: max(0, len(parts) - (level - 1))]
    if module:
        parts = [*parts, *module.split(".")]
    return ".".join(parts)


def _records_from_source(source: str, package: str) -> list[ImportRecord]:
    """Every import edge in `source`, as absolute dotted modules, in source order.

    `from . import pages` targets the *submodule* `package.pages`, not the package itself --
    the one shape where the module field alone would understate what the file reaches.
    """
    records: list[ImportRecord] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            records += [
                ImportRecord(alias.name, alias.asname or alias.name, node.lineno)
                for alias in node.names
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(package, node.level, node.module)
                # A bare `from . import x` names the submodule x; a `from .a import x` names a.
                targets = (
                    [(f"{base}.{alias.name}", alias.name) for alias in node.names]
                    if node.module is None
                    else [(base, alias.name) for alias in node.names]
                )
            else:
                targets = [(node.module or "", alias.name) for alias in node.names]
            records += [ImportRecord(module, symbol, node.lineno) for module, symbol in targets]
    return sorted(records, key=lambda record: record.lineno)


def _import_records(path: Path) -> list[ImportRecord]:
    _, package = _dotted_names(path)
    return _records_from_source(path.read_text(encoding="utf-8"), package)


def _page_imports(path: Path) -> list[ImportRecord]:
    """The imports in `path` that land anywhere in the page layer, facade included."""
    return [
        record
        for record in _import_records(path)
        if record.module == PAGES_PACKAGE or record.module.startswith(f"{PAGES_PACKAGE}.")
    ]


def _describe(path: Path, record: ImportRecord) -> str:
    return (
        f"{path.relative_to(REPO_ROOT)}:{record.lineno} imports {record.module} "
        f"(binds {record.symbol!r})"
    )


def _module_path(dotted: str) -> Path:
    """The source file for a dotted module under `src/`, package `__init__` included.

    `boss_agent.pages` names a directory, and reading `pages.py` there is the file the split
    deleted -- so the facade has to resolve to its `__init__.py` for the cycle rule to see it.
    """
    package_root = SOURCE_ROOT.joinpath(*dotted.split("."))
    if package_root.is_dir():
        return package_root / "__init__.py"
    return package_root.with_suffix(".py")


# --------------------------------------------------------------------------- #
# The resolver is the guard: prove it resolves before trusting what it reports
# --------------------------------------------------------------------------- #


class ResolverCase(NamedTuple):
    """One spelling of an import, and the absolute module it must resolve to."""

    package: str
    source: str
    module: str
    symbol: str


#: Every spelling of a page import this tree uses, plus the two that are easy to get wrong:
#: a `from . import pages`, which names the submodule rather than its package, and a
#: `from ..pages.base import X` from inside the layer, which reaches *up* and back *down*.
RESOLVER_CASES = [
    ResolverCase(
        "boss_agent",
        "from .pages.communication import ChatPage",
        "boss_agent.pages.communication",
        "ChatPage",
    ),
    ResolverCase("boss_agent", "from .pages import JobListPage", "boss_agent.pages", "JobListPage"),
    ResolverCase(
        "boss_agent",
        "from boss_agent.pages.system import LoginPage",
        "boss_agent.pages.system",
        "LoginPage",
    ),
    ResolverCase(
        "boss_agent",
        "import boss_agent.pages.job_feed",
        "boss_agent.pages.job_feed",
        "boss_agent.pages.job_feed",
    ),
    ResolverCase("boss_agent", "from . import pages", "boss_agent.pages", "pages"),
    ResolverCase(
        "boss_agent.pages",
        "from .base import BaseBossPage",
        "boss_agent.pages.base",
        "BaseBossPage",
    ),
    ResolverCase(
        "boss_agent.pages",
        "from ..job_entities import JobCardBrief",
        "boss_agent.job_entities",
        "JobCardBrief",
    ),
    ResolverCase(
        "boss_agent.pages",
        "from ..pages.base import BaseBossPage",
        "boss_agent.pages.base",
        "BaseBossPage",
    ),
]


@pytest.mark.parametrize("case", RESOLVER_CASES, ids=lambda case: case.source)
def test_the_relative_import_resolver_reads_every_spelling_of_a_page_import(
    case: ResolverCase,
) -> None:
    """A boundary guard that cannot resolve a relative import cannot fail.

    Every rule below reads `ImportRecord.module`, so if the resolver mistook `from .pages.X
    import Y` for a bare `X`, the whole suite would pass on the violations it exists to catch.
    These cases pin each spelling against synthetic sources, so the property holds for
    imports that are not in the tree today as well as the ones that are.
    """
    assert _records_from_source(case.source, case.package) == [
        ImportRecord(case.module, case.symbol, 1)
    ]


# --------------------------------------------------------------------------- #
# Rule 1 -- base and system are leaves of the layer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("submodule", ("base", "system"))
def test_the_shared_base_and_startup_screens_depend_on_no_other_screen(submodule: str) -> None:
    """A login dialog and a selector helper must not drag a job feed in behind them.

    `system.py` clears the startup dialog and waits for login; `base.py` holds the selector
    telemetry and the hardware-Back cadence. Both are reached by every feature, so an import
    of `job_feed`, `job_detail` or `communication` here would make every screen -- including
    the ones those features do not care about -- load job discovery.
    """
    path = _module_path(f"{PAGES_PACKAGE}.{submodule}")
    offenders = [
        record for record in _page_imports(path) if record.module not in ONLY_BASE_IS_ALLOWED
    ]

    assert not offenders, (
        f"{submodule}.py must depend on {ONLY_BASE_IS_ALLOWED[0]} and nothing else in the page "
        f"layer, but:\n  " + "\n  ".join(_describe(path, record) for record in offenders)
    )


# --------------------------------------------------------------------------- #
# Rule 2 -- the facade is downstream of every submodule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("submodule", FEATURE_SUBMODULES)
def test_no_submodule_imports_the_backwards_compatible_facade(submodule: str) -> None:
    """`boss_agent.pages/__init__.py` imports all five submodules, so they must not import it.

    The facade is where every legacy caller still lands, and it is the *broadest* import in
    the layer: a submodule reaching back for it would both form an import cycle and undo the
    point of the split by re-acquiring its siblings.
    """
    path = _module_path(f"{PAGES_PACKAGE}.{submodule}")
    offenders = [record for record in _page_imports(path) if record.module == PAGES_PACKAGE]

    assert not offenders, (
        f"{submodule}.py imports the facade it is re-exported by:\n  "
        + "\n  ".join(_describe(path, record) for record in offenders)
    )


# --------------------------------------------------------------------------- #
# Rules 3 and 4 -- features do not reach across
# --------------------------------------------------------------------------- #


def test_communication_never_imports_the_job_discovery_screens() -> None:
    """A 仅沟通 chat reads conversations; it never sees a job feed, a search dialog or a commute probe.

    This is the promise that made the split worth doing, so it is asserted where it can
    actually be broken -- the import graph -- rather than only in prose.
    """
    path = _module_path(f"{PAGES_PACKAGE}.communication")
    forbidden = (f"{PAGES_PACKAGE}.job_feed", f"{PAGES_PACKAGE}.job_detail")
    offenders = [record for record in _page_imports(path) if record.module in forbidden]

    assert not offenders, "communication.py reached for job discovery:\n  " + "\n  ".join(
        _describe(path, record) for record in offenders
    )


def test_job_feed_never_imports_the_communication_screens() -> None:
    """The mirror of the rule above: job discovery must not acquire a chat screen.

    Job search, filter dialogs and pagination have nothing to say about conversations; the
    one place the two features genuinely meet is the greeting dispatch in `feed_pipeline`,
    which is outside this package on purpose.
    """
    path = _module_path(f"{PAGES_PACKAGE}.job_feed")
    offenders = [
        record
        for record in _page_imports(path)
        if record.module == f"{PAGES_PACKAGE}.communication"
    ]

    assert not offenders, "job_feed.py reached for the communication screens:\n  " + "\n  ".join(
        _describe(path, record) for record in offenders
    )


def test_the_feed_engine_keeps_its_accepted_chat_screen_dependency() -> None:
    """The one deliberate cross-feature edge, stated so nobody deletes it as a mistake.

    `JobFeedPipeline` opens `ChatPage` to send the greeting draft it just evaluated. That is
    a real runtime dependency, so it is permitted -- but it is permitted *here*, in the
    orchestrator, not by relaxing the rules above. This test deliberately checks for the
    dependency and not for the module it is spelled on: re-reading `ChatPage` through the
    facade is still a violation of ticket #400, and a live greeting dispatch must keep
    working.
    """
    path = BOSS_AGENT_ROOT / "feed_pipeline.py"
    chat_imports = [record for record in _page_imports(path) if record.symbol == "ChatPage"]

    assert chat_imports, (
        "feed_pipeline no longer imports ChatPage. The greeting dispatch opened the chat "
        "screen to send the draft it evaluated; if that moved, re-decide it deliberately."
    )


# --------------------------------------------------------------------------- #
# Rule 5 -- no cycle inside the layer, whatever the individual edges say
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LayerEdge:
    source: str
    target: str
    lineno: int


def _layer_edges() -> dict[str, list[LayerEdge]]:
    """The page layer's own import graph, facade included, edges carrying their line numbers."""
    graph: dict[str, list[LayerEdge]] = {
        f"{PAGES_PACKAGE}.{name}": [] for name in FEATURE_SUBMODULES
    }
    graph[PAGES_PACKAGE] = []
    for node in graph:
        for record in _page_imports(_module_path(node)):
            edge = LayerEdge(node, record.module, record.lineno)
            if record.module in graph and edge not in graph[node]:
                graph[node].append(edge)
    return graph


def _find_cycle(graph: dict[str, list[LayerEdge]]) -> list[str] | None:
    """Depth-first search for the first cycle, returned as the node path that closes it."""
    visiting, done = {node: False for node in graph}, {node: False for node in graph}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        visiting[node], done[node] = True, False
        path.append(node)
        for edge in graph[node]:
            if visiting[edge.target]:
                return [*path[path.index(edge.target) :], edge.target]
            if not done[edge.target]:
                found = visit(edge.target)
                if found:
                    return found
        path.pop()
        visiting[node], done[node] = False, True
        return None

    for node in sorted(graph):
        if not done[node] and (found := visit(node)):
            return found
    return None


def test_the_page_submodules_form_no_import_cycle() -> None:
    """Rules 3 and 4 name today's edges; this one holds whatever the next edit writes.

    Python resolves a cycle by leaving one module's namespace half-initialised at import
    time, which surfaces as an `AttributeError` at first use rather than as an import error --
    the failure mode that is hardest to trace back here. The rule is structural, so it
    catches the two-module version (a screens <-> base handshake) as readily as the long one.
    """
    cycle = _find_cycle(_layer_edges())

    assert cycle is None, "the page layer has an import cycle: " + " -> ".join(cycle)


# --------------------------------------------------------------------------- #
# Rule 6 -- only the screen drivers may import page objects
# --------------------------------------------------------------------------- #

#: Modules outside the page layer that drive screens, and so may import it.
#:
#: `feed_pipeline` is here for the accepted greeting-dispatch edge documented at the top of
#: this file; the other three are the screen-driving orchestrations, and `boss_agent` itself
#: re-exports the page classes as its own public surface. Everything else under
#: `src/boss_agent/` is a decision, entity, prompt, store or broker module, and an import of
#: `boss_agent.pages` in one of those is the regression rule 6 exists to catch.
SCREEN_DRIVERS = frozenset(
    {
        "boss_agent",
        "boss_agent.chat_triage",
        "boss_agent.feed_pipeline",
        "boss_agent.workflows",
    }
)

#: Subtrees excluded from the domain sweep: `pages` is the layer under guard, and `worker` is
#: the automation driver whose handlers exist to press the screens `pages` models.
NOT_DOMAIN = ("boss_agent.pages", "boss_agent.worker")

#: The modules the ticket names as the minimum the domain sweep must reach. Discovery below
#: is derived from the tree rather than pinned by hand, so a rename that quietly drops a
#: required module out of coverage fails here instead of shrinking the guard in silence.
REQUIRED_DOMAIN_MODULES = (
    "boss_agent.candidate_entities",
    "boss_agent.card_parser",
    "boss_agent.enums",
    "boss_agent.feed_records",
    "boss_agent.identifier_helpers",
    "boss_agent.job_entities",
    "boss_agent.keyword_constants",
    "boss_agent.matching",
    "boss_agent.rejection",
    "boss_agent.screening",
    "boss_agent.search_entities",
)


def _iter_source_modules() -> list[str]:
    """Every dotted module name under `src/boss_agent/`, sorted."""
    return sorted(
        {
            dotted
            for dotted, _package in (_dotted_names(path) for path in BOSS_AGENT_ROOT.rglob("*.py"))
        }
    )


def _domain_modules() -> tuple[str, ...]:
    """The modules that must never reach for a page object: everything outside the drivers."""
    return tuple(
        name
        for name in _iter_source_modules()
        if name not in SCREEN_DRIVERS and not name.startswith(NOT_DOMAIN)
    )


@pytest.mark.parametrize("module", _domain_modules())
def test_a_domain_module_never_imports_the_page_objects(module: str) -> None:
    """A decision module must not pull Appium in to read one field off a card.

    `JobCardBrief` used to live in `pages.py`, so the Candidate Screener imported the
    device-side module to read text off a job card. The entities moved out in spec #303; this
    is what keeps them from creeping back, across the whole domain surface rather than the
    eleven modules the card-parser suite happens to list.
    """
    offenders = _page_imports(_module_path(module))

    assert not offenders, (
        f"{module} imports the page objects; it is a decision module, not a screen driver:\n  "
        + "\n  ".join(_describe(_module_path(module), record) for record in offenders)
    )


def test_the_domain_sweep_still_covers_every_module_the_spec_named() -> None:
    """Discovery must not quietly shrink the guard as the tree moves."""
    covered = set(_domain_modules())
    missing = sorted(set(REQUIRED_DOMAIN_MODULES) - covered)

    assert not missing, (
        "these modules are no longer classified as domain, so the rule above no longer "
        f"protects them: {missing}"
    )


def test_only_the_screen_drivers_import_the_page_objects() -> None:
    """The complement of the rule above, in one line: name any new importer of the layer.

    Per-module failures say *where* a rule broke; this one says *who* is allowed to hold page
    objects at all, so a brand-new orchestrator has to be added here deliberately instead of
    arriving unnoticed with a `from .pages import ...` on top.
    """
    importers = {
        name
        for name in _iter_source_modules()
        if name not in SCREEN_DRIVERS
        and not name.startswith(NOT_DOMAIN)
        and _page_imports(_module_path(name))
    }

    assert not importers, (
        f"only {sorted(SCREEN_DRIVERS)} may import page objects, but these do: {sorted(importers)}"
    )
