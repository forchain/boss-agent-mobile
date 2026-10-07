"""
tests.unit.test_chat_triage_page_imports
=========================================
Import-graph guard for the 仅沟通 chat triage path (ticket #402).

The spec's promise is that a rejection-triage run does not load job discovery
adapters: 仅沟通 triage reads a conversation list and a chat, never a job feed,
a search dialog or a commute probe. That promise is only observable in the
import graph, so it is asserted here — on ``chat_triage``'s own imports, by AST.

Scoped to the one module this ticket migrates. The whole-package boundary suite
belongs to ticket #404 and is deliberately not duplicated here.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
CHAT_TRIAGE = REPO_ROOT / "src/boss_agent/chat_triage.py"
CHAT_TRIAGE_HARNESS = REPO_ROOT / "tests/unit/_chat_triage_harness.py"

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
    """
    found: list[tuple[str, str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = f"{package}.{node.module}" if node.level else node.module
            if not module.startswith(f"{package}.pages"):
                continue
            for alias in node.names:
                found.append((module, alias.name, node.lineno))
    return found


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
