"""Android/Appium/Selenium/ADB technology, isolated behind application ports."""

from .adb_client import AdbClient, SubprocessAdbClient
from .config import DriverConfig
from .driver_adapter import AppiumDriverAdapter
from .interceptors import AndroidSystemDialogInterceptor, SystemDialogInterceptor
from .session import AppiumSession

__all__ = [
    "AdbClient",
    "AndroidSystemDialogInterceptor",
    "AppiumDriverAdapter",
    "AppiumSession",
    "DriverConfig",
    "SubprocessAdbClient",
    "SystemDialogInterceptor",
]
