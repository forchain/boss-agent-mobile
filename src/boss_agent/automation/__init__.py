"""
boss_agent.automation
=====================
Platform-neutral automation primitives: the driver port, humanized gestures, locator
configuration, and dialog interceptors.

Platform adapters are deliberately *not* re-exported here, so importing this facade never
imports Appium. A composition root that opens a device session names its adapter directly
(``boss_agent.automation.appium_adapter``).
"""

from .gestures import (
    BézierTouchSynthesizer,
    HumanizedGestureExecutor,
    Point,
    calculate_bounding_box_jitter,
    calculate_probe_coordinate,
)
from .interceptors import BaseInterceptor, InterceptorRegistry, SystemDialogInterceptor
from .locators import (
    LocatorRegistry,
    UISelector,
    ViewDescriptor,
    get_global_locator_registry,
    parse_selector,
    wait_until,
)
from .port import By, DriverPort, ElementHandle

__all__ = [
    "BaseInterceptor",
    "By",
    "BézierTouchSynthesizer",
    "DriverPort",
    "ElementHandle",
    "HumanizedGestureExecutor",
    "InterceptorRegistry",
    "LocatorRegistry",
    "Point",
    "SystemDialogInterceptor",
    "UISelector",
    "ViewDescriptor",
    "calculate_bounding_box_jitter",
    "calculate_probe_coordinate",
    "get_global_locator_registry",
    "parse_selector",
    "wait_until",
]
