"""
boss_agent.core.automation.interfaces.locator_store
==================================================
The locator configuration source a page object reads through.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..domain.locators import UISelector


class LocatorStorePort(Protocol):
    """Raw and parsed selector lookup by dot-separated key."""

    def get_raw(self, key: str, default: Any = None) -> Any: ...

    def get_selectors(
        self,
        key: str,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ) -> list[UISelector]: ...

    def reload(self) -> None: ...
