"""
Architecture guards for the platform-neutral automation layer.

`boss_agent.automation` holds the device primitives; Appium survives only in
``appium_adapter`` behind the neutral ``port.DriverPort``. These tests read the tree by
AST, so they run with no device and no platform client installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "boss_agent"
AUTOMATION_ROOT = PACKAGE_ROOT / "automation"
ADAPTER = AUTOMATION_ROOT / "appium_adapter.py"

PLATFORM_CLIENTS = frozenset({"appium", "selenium"})


def _absolute_imports(path: Path) -> set[str]:
    """Every top-level or nested absolute import name in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_only_the_appium_adapter_imports_a_platform_client() -> None:
    """Every other module must speak the port, so a Web adapter can replace Appium."""
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if path == ADAPTER:
            continue
        clients = {
            name for name in _absolute_imports(path) if name.split(".")[0] in PLATFORM_CLIENTS
        }
        if clients:
            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(clients)}")

    assert not offenders, (
        "Appium/Selenium may only be imported by "
        f"{ADAPTER.relative_to(REPO_ROOT)}:\n  " + "\n  ".join(offenders)
    )


def test_neutral_automation_modules_do_not_import_the_platform_adapter() -> None:
    """The dependency arrow points adapter -> port, never back."""
    offenders = []
    for path in AUTOMATION_ROOT.glob("*.py"):
        if path == ADAPTER:
            continue
        if any(name.endswith("appium_adapter") for name in _absolute_imports(path)):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"neutral automation modules import the adapter: {offenders}"


def test_automation_imports_no_boss_domain_module() -> None:
    """The primitives stay reusable: no pages, worker, broker, or LLM reach-in."""
    offenders = []
    for path in AUTOMATION_ROOT.glob("*.py"):
        outside = [
            name
            for name in _absolute_imports(path)
            if name != "boss_agent.automation"
            and not name.startswith("boss_agent.automation.")
            and name.startswith("boss_agent")
        ]
        if outside:
            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(outside)}")

    assert not offenders, "automation modules reached into the domain layer:\n  " + "\n  ".join(
        offenders
    )
