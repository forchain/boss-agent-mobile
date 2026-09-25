"""
src/boss_agent/worker/context.py
================================
Runtime execution context holding device driver and configurations for handlers.
"""

import logging
from collections.abc import Callable
from typing import Any

from boss_agent.worker.config import WorkerConfig

logger = logging.getLogger("boss_agent.worker")

# Appium's WebDriver entrypoint first, then the AppiumSession wrapper's own teardown.
_SESSION_CLOSERS: tuple[str, ...] = ("quit", "stop")


class WorkerContext:
    """Provides access to the active device session and configurations."""

    def __init__(
        self,
        config: WorkerConfig,
        driver: Any | None = None,
        driver_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config
        self._driver = driver
        self._driver_factory = driver_factory

    @property
    def driver(self) -> Any:
        if self._driver is None and self._driver_factory is not None:
            self._driver = self._driver_factory()
        return self._driver

    def set_driver(self, driver: Any) -> None:
        self._driver = driver

    def has_device_session(self) -> bool:
        """Report whether a Virtual Device Session has already been established.

        Deliberately does not resolve the lazy `driver` property: shutdown must never
        open a device session purely to close it.
        """
        return self._driver is not None

    def release_device_session(self) -> bool:
        """Close the active Virtual Device Session and drop the reference.

        Returns True when a session was handed to a closer, False when there was nothing to
        release (or the driver exposed no teardown entrypoint at all). Never raises — a
        failing teardown must not be able to block worker shutdown.
        """
        driver = self._driver
        self._driver = None
        if driver is None:
            return False

        for closer_name in _SESSION_CLOSERS:
            closer = getattr(driver, closer_name, None)
            if not callable(closer):
                continue
            try:
                closer()
            except Exception as e:
                logger.warning("Error while releasing device session via %s(): %s", closer_name, e)
            return True

        logger.warning(
            "Device session object exposes no closer (%s); dropping the reference only.",
            _SESSION_CLOSERS,
        )
        return False
