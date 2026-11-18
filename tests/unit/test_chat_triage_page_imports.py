"""
tests.unit.test_chat_triage_page_imports
=========================================
Import-graph guard for the 仅沟通 chat triage path (ticket #402, #455).

The core engine reads a conversation list and a chat, never a job feed or a search
dialog. After #455 it does not reach the device layer at all: the two ports live in
``core/chat/interfaces`` and production composition lives in
``boss_agent.pages.chat_adapters`` / ``boss_agent.pages.chat_bundle``. That promise is
only observable in the import graph, so it is asserted here — on the core module's own
imports, by AST — plus the two edge modules still naming the submodule that owns each
page object.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
PAGES_ROOT = REPO_ROOT / "src/boss_agent/pages"
CHAT_TRIAGE = REPO_ROOT / "src/boss_agent/core/chat/use_cases/triage.py"
CHAT_ADAPTERS = PAGES_ROOT / "chat_adapters.py"
CHAT_BUNDLE = PAGES_ROOT / "chat_bundle.py"
CHAT_TRIAGE_HARNESS = REPO_ROOT / "tests/unit/_chat_triage_harness.py"

#: The page-layer submodules on disk. Used to read an imported *name* as the submodule it
#: names -- `from boss_agent.pages import job_feed` binds the submodule, not the facade.
PAGE_SUBMODULES = frozenset(
    path.stem for path in PAGES_ROOT.glob("*.py") if path.stem != "__init__"
)

#: The page objects the chat edge drives, and the submodule that must own each one.
EXPECTED_EDGE_IMPORTS = {
    "boss_agent.pages.communication": (
        "CommunicationListPage",
        "ChatPage",
        "CommunicationCard",
    ),
    "boss_agent.pages.system": ("StartupDialogPage",),
}

#: The edge modules that may name a page object at all.
EDGE_MODULES = (CHAT_ADAPTERS, CHAT_BUNDLE)


def _page_imports(
    path: Path, package: str, *, relative_base: str | None = None
) -> list[tuple[str, str, int]]:
    """Every `boss_agent.pages` import in `path` as (module, symbol, lineno).

    Relative imports are resolved against ``relative_base`` (the package that owns the
    file), so a migration cannot hide behind `from .communication import ...` versus
    `from boss_agent.pages.communication import ...`. All three spellings that reach the
    layer are read: direct module imports, `ast.Import`, and facade imports whose bound
    name is itself a submodule.
    """
    base = relative_base or package
    found: list[tuple[str, str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{package}.pages"):
                    found.append((alias.name, alias.asname or alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = f"{base}.{node.module}" if node.level else node.module
            if not module.startswith(f"{package}.pages"):
                continue
            for alias in node.names:
                found.append((module, alias.name, node.lineno))
                submodule = f"{module}.{alias.name}"
                if module == f"{package}.pages" and alias.name in PAGE_SUBMODULES:
                    found.append((submodule, alias.name, node.lineno))
    return found


#: Every spelling that reaches job discovery, pinned against synthetic sources: if the parser
#: ever stops reading one of them, the guard below would go green on a violation it cannot see.
JOB_DISCOVERY_SPELLINGS = (
    "import boss_agent.pages.job_feed",
    "import boss_agent.pages.job_feed as feed",
    "from boss_agent.pages import job_feed",
    "from boss_agent.pages import job_feed as feed",
    "from .pages.job_feed import JobListPage",
    "from boss_agent.pages.job_detail import JobDetailPage",
    "from .communication import ChatPage",
)


@pytest.mark.parametrize("source", JOB_DISCOVERY_SPELLINGS, ids=JOB_DISCOVERY_SPELLINGS)
def test_the_guard_reads_every_spelling_that_reaches_the_page_layer(
    source: str, tmp_path: Path
) -> None:
    """Prove the forbidden-import rule is reachable, not just present."""
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")

    assert _page_imports(probe, "boss_agent", relative_base="boss_agent.pages"), (
        f"the parser cannot see {source!r}"
    )


def test_core_chat_triage_imports_no_page_objects() -> None:
    """The engine receives its device world as ports; it never reaches for a page."""
    imports = _page_imports(CHAT_TRIAGE, "boss_agent")

    assert imports == [], f"core chat triage imports page objects: {imports}"


@pytest.mark.parametrize(
    ("module", "symbol"),
    [(module, symbol) for module, symbols in EXPECTED_EDGE_IMPORTS.items() for symbol in symbols],
)
def test_the_chat_edge_imports_page_objects_from_their_feature_submodule(
    module: str, symbol: str
) -> None:
    """A triage run reads communication screens; the edge names those submodules."""
    imports = [
        record
        for path in EDGE_MODULES
        for record in _page_imports(path, "boss_agent", relative_base="boss_agent.pages")
    ]

    assert (module, symbol) in {(found_module, name) for found_module, name, _ in imports}, (
        f"the chat edge does not import {symbol} from {module}; "
        f"page imports are {sorted({(m, n) for m, n, _ in imports})}"
    )


def test_the_chat_edge_never_imports_the_job_discovery_page_objects() -> None:
    """仅沟通 triage must not load job feed or job detail adapters."""
    forbidden = ("boss_agent.pages.job_feed", "boss_agent.pages.job_detail")

    offenders = [
        (module, symbol, lineno)
        for path in EDGE_MODULES
        for module, symbol, lineno in _page_imports(
            path, "boss_agent", relative_base="boss_agent.pages"
        )
        if module.startswith(forbidden)
    ]

    assert not offenders, f"the chat edge imports job discovery page objects: {offenders}"


def test_chat_triage_harness_imports_its_card_from_the_communication_submodule() -> None:
    """The scripted device world stands in for the communication screens only."""
    imports = _page_imports(CHAT_TRIAGE_HARNESS, "boss_agent")

    assert ("boss_agent.pages.communication", "CommunicationCard") in {
        (module, symbol) for module, symbol, _ in imports
    }, f"harness page imports are {sorted({(m, n) for m, n, _ in imports})}"
