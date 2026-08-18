"""
droid_agent_core
================
Universal, application-agnostic Android automation framework.
"""

from .driver import AppiumSession, DriverConfig
from .gestures import BézierTouchSynthesizer, HumanizedGestureExecutor, Point
from .interceptors import BaseInterceptor, InterceptorRegistry, SystemDialogInterceptor
from .locators import By, UISelector, ViewDescriptor, wait_until

__all__ = [
    "AppiumSession",
    "BaseInterceptor",
    "By",
    "BézierTouchSynthesizer",
    "DriverConfig",
    "HumanizedGestureExecutor",
    "InterceptorRegistry",
    "Point",
    "SystemDialogInterceptor",
    "UISelector",
    "ViewDescriptor",
    "wait_until",
]

