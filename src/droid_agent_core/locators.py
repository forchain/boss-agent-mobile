import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class By(str, Enum):
    ID = "id"
    XPATH = "xpath"
    ACCESSIBILITY_ID = "accessibility_id"
    CLASS_NAME = "class_name"
    ANDROID_UIAUTOMATOR = "-android uiautomator"


def wait_until(
    condition: Callable[[], T],
    timeout_sec: float = 10.0,
    poll_interval: float = 0.4,
    error_message: str = "Timed out waiting for condition",
) -> T:
    """Poll a callable condition until it returns a truthy value or timeout expires."""
    start_time = time.time()
    last_exception: Exception | None = None

    while time.time() - start_time < timeout_sec:
        try:
            res = condition()
            if res:
                return res
        except Exception as e:
            last_exception = e
        time.sleep(poll_interval)

    msg = f"{error_message} after {timeout_sec:.1f}s"
    if last_exception:
        msg += f" (last error: {last_exception})"
    raise TimeoutError(msg)


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

    def wait_until_present(self, driver: Any, timeout_sec: float = 10.0) -> bool:
        """Wait until all required selectors for this view are present."""
        return bool(
            wait_until(
                lambda: self.is_present(driver),
                timeout_sec=timeout_sec,
                error_message=f"Timed out waiting for view '{self.name}' to be present",
            )
        )

