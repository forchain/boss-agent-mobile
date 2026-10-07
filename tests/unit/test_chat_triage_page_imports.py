"""
tests.unit.test_chat_triage_page_imports
=========================================
Import-graph guard for the 仅沟通 chat triage path (ticket #402).

The spec's promise is that a rejection-triage run does not load job discovery
adapters: 仅沟通 triage reads a conversation list and a chat, never a job feed,
a search dialog or a commute probe. That promise is only observable in the
import graph, so it is asserted here — on ``chat_triage``'s own imports, by AST.

Scoped to the one module this ticket migrates. The whole-package boundary suite
belongs to ticket #404 and is deliberately not duplicated here — but that suite
exempts `chat_triage` as a screen driver, so the resolver here is the only thing
reading this file's imports. It therefore reads every spelling that reaches the
layer (``import boss_agent.pages.job_feed`` as well as
``from .pages.job_feed import X`` and ``from boss_agent.pages import job_feed``),
and pins them against synthetic sources so a blind spot cannot pass unnoticed.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
PAGES_ROOT = REPO_ROOT / "src/boss_agent/pages"
CHAT_TRIAGE = REPO_ROOT / "src/boss_agent/chat_triage.py"
CHAT_TRIAGE_HARNESS = REPO_ROOT / "tests/unit/_chat_triage_harness.py"

#: The page-layer submodules on disk. Used to read an imported *name* as the submodule it
#: names -- `from boss_agent.pages import job_feed` binds the submodule, not the facade.
PAGE_SUBMODULES = frozenset(
    path.stem for path in PAGES_ROOT.glob("*.py") if path.stem != "__init__"
)

#: Page objects chat triage drives, and the submodule that must own each one.
#: A triage run reads conversations, so its screen models are communication
#: screens plus the startup dialog it clears on the way in.
EXPECTED_IMPORTS = {
    "boss_agent.pages.communication": (
        "CommunicationListPage",
        "ChatPage",
        "CommunicationCard",
        "OUTBOUND_STATUS_MARKERS",
    ),
    "boss_agent.pages.system": ("StartupDialogPage",),
}


def _page_imports(path: Path, package: str) -> list[tuple[str, str, int]]:
    """Every `boss_agent.pages` import in `path` as (module, symbol, lineno).

    Relative imports are resolved against `package`, so a migration cannot hide
    behind `from .pages import ...` versus `from .pages.communication import ...`.

    All three spellings that reach the layer are read, because `chat_triage` is on the
    screen-driver allowlist and so is excluded from the fully general resolver in
    ``test_pages_submodule_isolation`` -- this narrower parser is its only guard, so an
    evasion it cannot see is an evasion nobody sees:

    * ``from .pages.job_feed import JobListPage`` -- the module is named directly;
    * ``import boss_agent.pages.job_feed`` -- an ``ast.Import``, which is not an
      ``ImportFrom`` at all and was previously skipped wholesale;
    * ``from boss_agent.pages import job_feed`` -- ``node.module`` is the *facade* here and
      the submodule is the imported name, so the record is qualified with it.
    """
    found: list[tuple[str, str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{package}.pages"):
                    found.append((alias.name, alias.asname or alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = f"{package}.{node.module}" if node.level else node.module
            if not module.startswith(f"{package}.pages"):
                continue
            for alias in node.names:
                found.append((module, alias.name, node.lineno))
                # A name imported from the facade may be a submodule rather than a page
                # object; record that edge too, so `from boss_agent.pages import job_feed`
                # is judged as the job-discovery import it is.
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
)


@pytest.mark.parametrize("source", JOB_DISCOVERY_SPELLINGS, ids=JOB_DISCOVERY_SPELLINGS)
def test_the_guard_reads_every_spelling_that_reaches_job_discovery(
    source: str, tmp_path: Path
) -> None:
    """Prove the forbidden-import rule is reachable, not just present.

    `chat_triage` sits on the screen-driver allowlist, so the fully general resolver in
    `test_pages_submodule_isolation` deliberately skips it and this file is the only thing
    standing between a triage run and the job-discovery adapters. A parser blind to one
    spelling is a guard that cannot fail, so each spelling is checked here rather than left
    to a future import to reveal.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    forbidden = ("boss_agent.pages.job_feed", "boss_agent.pages.job_detail")

    offenders = [
        record
        for record in _page_imports(probe, package="boss_agent")
        if record[0].startswith(forbidden)
    ]

    assert offenders, f"the parser cannot see {source!r}, so the guard below cannot fail on it"


@pytest.mark.parametrize(
    ("module", "symbol"),
    [(module, symbol) for module, symbols in EXPECTED_IMPORTS.items() for symbol in symbols],
)
def test_chat_triage_imports_page_objects_from_their_feature_submodule(
    module: str, symbol: str
) -> None:
    """A triage run reads communication screens, so it names those submodules.

    Reading them off the broad `pages` facade would still work, but it is what
    kept job discovery adapters inside the 仅沟通 import path.
    """
    imports = _page_imports(CHAT_TRIAGE, package="boss_agent")

    assert (module, symbol) in {(found_module, name) for found_module, name, _ in imports}, (
        f"chat_triage does not import {symbol} from {module}; "
        f"page imports are {sorted({(m, n) for m, n, _ in imports})}"
    )


def test_chat_triage_never_imports_the_job_discovery_page_objects() -> None:
    """仅沟通 triage must not load job feed or job detail adapters.

    Job search, filter dialogs and commute probes are a different feature domain;
    a triage run that imported them would pay for them on every dispatch.
    """
    forbidden = ("boss_agent.pages.job_feed", "boss_agent.pages.job_detail")

    offenders = [
        (module, symbol, lineno)
        for module, symbol, lineno in _page_imports(CHAT_TRIAGE, package="boss_agent")
        if module.startswith(forbidden)
    ]

    assert not offenders, f"chat_triage imports job discovery page objects: {offenders}"


def test_chat_triage_harness_imports_its_card_from_the_communication_submodule() -> None:
    """The scripted device world stands in for the communication screens only."""
    imports = _page_imports(CHAT_TRIAGE_HARNESS, package="boss_agent")

    assert ("boss_agent.pages.communication", "CommunicationCard") in {
        (module, symbol) for module, symbol, _ in imports
    }, f"harness page imports are {sorted({(m, n) for m, n, _ in imports})}"
