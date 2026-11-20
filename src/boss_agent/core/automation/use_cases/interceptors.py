"""
boss_agent.core.automation.use_cases.interceptors
================================================
Prioritized orchestration of screen interceptors.
"""

from __future__ import annotations

from typing import Any

from ..interfaces.interceptor import ScreenInterceptorPort


class InterceptorRegistry:
    """Runs the interceptor chain, short-circuiting on the first match."""

    def __init__(self, interceptors: list[ScreenInterceptorPort] | None = None) -> None:
        self.interceptors: list[ScreenInterceptorPort] = list(interceptors or [])

    def register(self, interceptor: ScreenInterceptorPort) -> None:
        self.interceptors.append(interceptor)

    def process_all(self, driver: Any) -> bool:
        """Scan and trigger the first matching interceptor."""
        for interceptor in self.interceptors:
            if interceptor.can_handle(driver):
                return interceptor.handle(driver)
        return False
