"""
tests.unit.test_memory_adapter_imports
======================================
The in-memory adapters' import graph (spec #443, ticket #463).

The InMemory task broker and its three stores are the fast tier's production doubles.
They used to import their record-shape and merge helpers from the PocketBase adapter,
which made every in-memory store pull ``requests`` and a peer outbound adapter into the
test import graph. The helpers now live in :mod:`boss_agent.adapters.shared.records`;
this guard keeps the memory package from reaching back across the adapter boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
MEMORY_ROOT = REPO_ROOT / "src" / "boss_agent" / "adapters" / "memory"

#: HTTP, transport, and device stacks. A store that speaks none of them needs none of
#: these in its import graph.
FORBIDDEN_TOP_LEVEL = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "http",
        "socket",
        "appium",
        "selenium",
    }
)

#: The peer outbound adapter the shared helpers were extracted from.
FORBIDDEN_PREFIX = "boss_agent.adapters.outbound.pocketbase"


def _imported_modules(path: Path) -> list[str]:
    """Every module named by an import in `path`, at any nesting depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_memory_adapters_import_no_transport_or_peer_adapter() -> None:
    """The in-memory stores are dependency-free doubles, not a second PocketBase client."""
    offenders: list[str] = []
    for path in sorted(MEMORY_ROOT.rglob("*.py")):
        for module in _imported_modules(path):
            top_level = module.split(".", 1)[0]
            if top_level in FORBIDDEN_TOP_LEVEL or module.startswith(FORBIDDEN_PREFIX):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")

    assert offenders == [], (
        "the in-memory adapters import a transport or the PocketBase adapter: "
        + "; ".join(offenders)
    )


def test_the_shared_record_helpers_live_outside_the_pocketbase_adapter() -> None:
    """Both stores import the one shared module; the helpers have a single home."""
    from boss_agent.adapters.shared import records

    memory_job_store = (MEMORY_ROOT / "job_store.py").read_text(encoding="utf-8")
    pocketbase_job_store = (
        REPO_ROOT / "src" / "boss_agent" / "adapters" / "outbound" / "pocketbase" / "job_store.py"
    ).read_text(encoding="utf-8")

    assert records.__file__ is not None
    assert "boss_agent.adapters.shared.records" in memory_job_store
    assert "boss_agent.adapters.shared.records" in pocketbase_job_store
