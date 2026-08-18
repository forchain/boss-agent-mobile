"""
droid_agent_core
================
Universal, application-agnostic Android automation framework.
"""

from .driver import AppiumSession, DriverConfig
from .gestures import BézierTouchSynthesizer, HumanizedGestureExecutor, Point
from .interceptors import BaseInterceptor, InterceptorRegistry, SystemDialogInterceptor
from .locators import (
    By,
    LocatorRegistry,
    UISelector,
    ViewDescriptor,
    get_global_locator_registry,
    parse_selector,
    wait_until,
)

__all__ = [
    "AppiumSession",
    "BaseInterceptor",
    "By",
    "BézierTouchSynthesizer",
    "DriverConfig",
    "HumanizedGestureExecutor",
    "InterceptorRegistry",
    "LocatorRegistry",
    "Point",
    "SystemDialogInterceptor",
    "UISelector",
    "ViewDescriptor",
    "get_global_locator_registry",
    "parse_selector",
    "wait_until",
]
