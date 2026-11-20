"""
boss_agent.automation
=====================
Platform-neutral automation primitives: the driver port, humanized gestures, selector
definitions, and dialog interceptors.

This package is free of platform clients, filesystem I/O and application configuration.
Platform adapters and the YAML-backed locator registry live under
``boss_agent.adapters``; a composition root that opens a device session names its
adapter directly (``boss_agent.adapters.appium_driver``).
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
    UISelector,
    ViewDescriptor,
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
    "Point",
    "SystemDialogInterceptor",
    "UISelector",
    "ViewDescriptor",
    "calculate_bounding_box_jitter",
    "calculate_probe_coordinate",
    "parse_selector",
    "wait_until",
]
