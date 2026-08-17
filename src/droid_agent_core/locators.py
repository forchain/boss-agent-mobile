"""
droid_agent_core.locators
=========================
Declarative UI selectors, element locators, and view descriptors.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class By(str, Enum):
    ID = "id"
    XPATH = "xpath"
    ACCESSIBILITY_ID = "accessibility_id"
    CLASS_NAME = "class_name"
    ANDROID_UIAUTOMATOR = "-android uiautomator"


@dataclass
class UISelector:
    by: By
    value: str
    description: str | None = None
    timeout: float = 5.0


@dataclass
class ViewDescriptor:
    name: str
    required_selectors: list[UISelector] = field(default_factory=list)
    optional_selectors: list[UISelector] = field(default_factory=list)

    def is_present(self, driver: Any) -> bool:
        """Check if all required selectors for this view exist on current screen."""
        if not driver:
            return False
        for sel in self.required_selectors:
            try:
                elements = driver.find_elements(by=sel.by.value, value=sel.value)
                if not elements:
                    return False
            except Exception:
                return False
        return True
