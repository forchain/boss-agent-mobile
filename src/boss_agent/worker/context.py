"""
src/boss_agent/worker/context.py
================================
Runtime execution context holding device driver and configurations for handlers.
"""

from collections.abc import Callable
from typing import Any

from boss_agent.worker.config import WorkerConfig


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
