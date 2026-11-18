"""
tests.unit.test_composition_confinement
=======================================
Where concrete adapters may be constructed (spec #443, ADR 0021).

``boss_agent/main.py`` is the single composition root: the only module that knows
every concrete adapter class. Adapters may of course construct each other inside
``adapters/``; everywhere else in production code -- the core packages and the CLI
scripts -- composition has to go through the root.

The CLI rule is sharper than "no adapter classes": a script may import a helper
function from an adapter (the verification runner composes one run from
``feed_verification``) but must not build an adapter, and must not construct an LLM
client itself. Tests are deliberately out of scope -- constructing the in-memory
doubles is the test seam.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
SRC_ROOT = REPO_ROOT / "src" / "boss_agent"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
ADAPTERS_ROOT = SRC_ROOT / "adapters"
COMPOSITION_ROOT = SRC_ROOT / "main.py"
ADAPTER_MODULE_PREFIX = "boss_agent.adapters"


def _adapter_class_names() -> set[str]:
    """Every class the adapter layer declares, so an import can be read as a class."""
    names: set[str] = set()
    for path in ADAPTERS_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return names


def _adapter_construction_violations(path: Path, adapter_classes: set[str]) -> list[str]:
    """Calls in `path` that construct a class imported from the adapter layer."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_classes: dict[str, str] = {}
    imported_modules: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == ADAPTER_MODULE_PREFIX or node.module.startswith(
                f"{ADAPTER_MODULE_PREFIX}."
            ):
                for alias in node.names:
                    if alias.name in adapter_classes:
                        imported_classes[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ADAPTER_MODULE_PREFIX or alias.name.startswith(
                    f"{ADAPTER_MODULE_PREFIX}."
                ):
                    imported_modules[alias.asname or alias.name.split(".")[-1]] = alias.name

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in imported_classes:
            violations.append(f"{func.id} (line {node.lineno})")
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in imported_modules
            and func.attr in adapter_classes
        ):
            violations.append(f"{func.value.id}.{func.attr} (line {node.lineno})")
    return violations


def _production_files() -> list[Path]:
    """Production modules outside the adapter layer and the composition root."""
    files = [
        path
        for path in sorted(SRC_ROOT.rglob("*.py"))
        if not path.is_relative_to(ADAPTERS_ROOT) and path != COMPOSITION_ROOT
    ]
    return files + sorted(SCRIPTS_ROOT.rglob("*.py"))


def test_concrete_adapters_are_built_only_in_adapters_and_main() -> None:
    """A core module or a CLI script that builds an adapter has bypassed the root."""
    adapter_classes = _adapter_class_names()
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {violation}"
        for path in _production_files()
        for violation in _adapter_construction_violations(path, adapter_classes)
    ]

    assert offenders == [], (
        "concrete adapters constructed outside adapters/ and main.py: " + "; ".join(offenders)
    )


def test_scripts_do_not_construct_the_llm_client_itself() -> None:
    """Only the composition root maps a configured protocol to a concrete client."""
    offenders: list[str] = []
    for path in sorted(SCRIPTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if called == "create_llm_client":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == [], "scripts construct an LLM client themselves: " + ", ".join(offenders)


def test_the_guard_sees_an_adapter_class_constructed_by_import(tmp_path: Path) -> None:
    """Prove the rule is reachable: an imported adapter class call is a violation."""
    adapter_classes = _adapter_class_names()
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from boss_agent.adapters.memory.task_broker import InMemoryTaskBroker\n"
        "broker = InMemoryTaskBroker()\n",
        encoding="utf-8",
    )

    assert "InMemoryTaskBroker" in adapter_classes
    assert "InMemoryTaskBroker (line 2)" in _adapter_construction_violations(probe, adapter_classes)
