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
SRC_ROOT = REPO_ROOT / "src" / "boss_agent"
MEMORY_ROOT = SRC_ROOT / "adapters" / "memory"
SHARED_ROOT = SRC_ROOT / "adapters" / "shared"

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

#: Outbound adapters (PocketBase, device, LLM, config, resume) are out of reach for
#: both in-memory doubles and shared adapter helpers.
FORBIDDEN_OUTBOUND_PREFIX = "boss_agent.adapters.outbound"


def _package_for_file(path: Path) -> str:
    """Containing Python package for a file under ``src/boss_agent``."""
    rel_parent = path.parent.relative_to(SRC_ROOT)
    return ".".join(("boss_agent", *rel_parent.parts))


def _resolve_import_from(node: ast.ImportFrom, package: str) -> list[str]:
    """Resolve an ``ast.ImportFrom`` (absolute or relative) to full module names."""
    if node.level == 0:
        return [node.module] if node.module is not None else []
    pkg_parts = package.split(".")
    up = node.level - 1
    if up >= len(pkg_parts):
        return [node.module or "__relative_escape__"]
    base = pkg_parts[: len(pkg_parts) - up]
    if node.module is not None:
        return [".".join((*base, node.module))]
    return [".".join((*base, alias.name)) for alias in node.names]


def _imported_modules(source: str, *, package: str) -> list[str]:
    """Every module named by an import in `source` (resolved if relative), at any depth."""
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.extend(_resolve_import_from(node, package))
    return modules


def _violations(source: str, *, package: str) -> list[str]:
    """Forbidden transport or outbound-adapter imports in `source`."""
    bad: list[str] = []
    for module in _imported_modules(source, package=package):
        top_level = module.split(".", 1)[0]
        if (
            top_level in FORBIDDEN_TOP_LEVEL
            or module == FORBIDDEN_OUTBOUND_PREFIX
            or module.startswith(f"{FORBIDDEN_OUTBOUND_PREFIX}.")
        ):
            bad.append(module)
    return bad


def test_memory_adapters_import_no_transport_or_peer_adapter() -> None:
    """The in-memory stores are dependency-free doubles, not a second PocketBase client."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {module}"
        for path in sorted(MEMORY_ROOT.rglob("*.py"))
        for module in _violations(path.read_text(encoding="utf-8"), package=_package_for_file(path))
    ]

    assert offenders == [], (
        "the in-memory adapters import a transport or an outbound adapter: " + "; ".join(offenders)
    )


def test_shared_adapters_import_no_transport_or_outbound_adapter() -> None:
    """Shared adapter helpers stay free of transport and outbound adapter dependencies."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {module}"
        for path in sorted(SHARED_ROOT.rglob("*.py"))
        for module in _violations(path.read_text(encoding="utf-8"), package=_package_for_file(path))
    ]

    assert offenders == [], (
        "the shared adapter package imports a transport or an outbound adapter: "
        + "; ".join(offenders)
    )


def test_relative_outbound_imports_are_caught_in_memory_and_shared() -> None:
    """Relative imports up into outbound adapters must fail the fence too."""
    assert _violations(
        "from ..outbound.pocketbase import collection_schema",
        package="boss_agent.adapters.memory",
    ) == ["boss_agent.adapters.outbound.pocketbase"]
    assert _violations(
        "from ..outbound import pocketbase",
        package="boss_agent.adapters.shared",
    ) == ["boss_agent.adapters.outbound"]
    assert _violations(
        "from .. import outbound",
        package="boss_agent.adapters.shared",
    ) == ["boss_agent.adapters.outbound"]
    assert (
        _violations(
            "from ..shared.records import _record_fields",
            package="boss_agent.adapters.memory",
        )
        == []
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
