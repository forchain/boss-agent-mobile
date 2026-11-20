"""
Architecture guards for the platform-neutral automation layer.

``boss_agent.automation`` holds the device primitives; Appium and every other concrete
platform client survive only under ``boss_agent.adapters``. These tests read the tree by
AST, so they run with no device and no platform client installed.

The guard resolves relative imports to absolute module names before inspecting them:
an earlier version only looked at ``from x import y`` and silently skipped
``from . import appium_adapter``, which made the boundary trivially bypassable.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "boss_agent"
AUTOMATION_ROOT = PACKAGE_ROOT / "automation"
ADAPTERS_ROOT = PACKAGE_ROOT / "adapters"

PLATFORM_CLIENTS = frozenset({"appium", "selenium"})


def _package_of(path: Path) -> str:
    """The package a module's relative imports resolve against."""
    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    parts.pop()  # drop the module (or ``__init__``) file name
    return "boss_agent" + ("." + ".".join(parts) if parts else "")


def _imports(path: Path) -> set[str]:
    """Every absolute module name ``path`` imports, with relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                target = node.module or ""
            else:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                target = f"{base}.{node.module}" if node.module else base
            names.add(target)
            if node.module is None:
                # ``from . import foo`` imports submodules of the current package.
                names.update(f"{target}.{alias.name}" for alias in node.names)
    return names


def _platform_imports(path: Path) -> set[str]:
    return {name for name in _imports(path) if name.split(".")[0] in PLATFORM_CLIENTS}


def _adapter_imports(path: Path) -> set[str]:
    return {
        name
        for name in _imports(path)
        if name == "boss_agent.adapters" or name.startswith("boss_agent.adapters.")
    }


def test_only_the_adapters_package_imports_a_platform_client() -> None:
    """Every other module must speak the port, so a Web adapter can replace Appium."""
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if ADAPTERS_ROOT in path.parents:
            continue
        clients = _platform_imports(path)
        if clients:
            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(clients)}")

    assert not offenders, (
        f"Appium/Selenium may only be imported under {ADAPTERS_ROOT.relative_to(REPO_ROOT)}:\n  "
        + "\n  ".join(offenders)
    )


def test_neutral_automation_modules_do_not_import_adapters() -> None:
    """The dependency arrow points adapter -> port, never back."""
    offenders = []
    for path in AUTOMATION_ROOT.rglob("*.py"):
        adapters = _adapter_imports(path)
        if adapters:
            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(adapters)}")

    assert not offenders, f"neutral automation modules import an adapter: {offenders}"


def test_automation_imports_no_boss_domain_module() -> None:
    """The primitives stay reusable: no pages, worker, broker, or LLM reach-in."""
    offenders = []
    for path in AUTOMATION_ROOT.rglob("*.py"):
        outside = [
            name
            for name in _imports(path)
            if name.startswith("boss_agent")
            and name != "boss_agent.automation"
            and not name.startswith("boss_agent.automation.")
        ]
        if outside:
            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(outside)}")

    assert not offenders, "automation modules reached into the domain layer:\n  " + "\n  ".join(
        offenders
    )


def test_the_guard_recognises_the_appium_adapter() -> None:
    """Guard the guard: the AST walk must actually see a platform import."""
    adapter = ADAPTERS_ROOT / "appium_driver.py"
    assert _platform_imports(adapter), f"{adapter} was expected to import a platform client"
