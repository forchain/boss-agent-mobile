"""
boss_agent.async_bridge
=======================
Reusable helper for synchronous-to-asynchronous bridging and broker request execution
(Spec #303 / #307, ADR 0013).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import requests

from .errors import BrokerError, ConflictError, RecordNotFoundError, TransportError, ValidationError

T = TypeVar("T")


def run_sync(coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
    """Execute a coroutine synchronously from any context.

    If an event loop is already running in the current thread (e.g. inside an async worker
    or LangGraph step), runs the coroutine in a dedicated single-thread worker pool
    with its own event loop to prevent event loop collision.
    Otherwise, executes directly via ``asyncio.run(coro)``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=timeout)


def execute_sync_broker_request(
    send_fn: Callable[[], requests.Response],
    *,
    expected_statuses: tuple[int, ...] = (200, 201),
    allow_404: bool = False,
    error_prefix: str = "Broker request failed",
) -> requests.Response:
    """Execute a synchronous broker HTTP call with typed exception handling.

    Maps:
    - requests.RequestException, ConnectionError, TimeoutError, OSError -> TransportError
    - 400 Bad Request -> ValidationError
    - 409 Conflict -> ConflictError
    - 404 (when allow_404=False) -> RecordNotFoundError
    - 5xx Server Error -> TransportError
    - Other unexpected statuses -> BrokerError
    """
    try:
        resp = send_fn()
    except (requests.RequestException, ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        raise TransportError(f"{error_prefix}: {e}") from e

    if resp.status_code in expected_statuses:
        return resp
    if allow_404 and resp.status_code == 404:
        return resp

    if resp.status_code == 400:
        raise ValidationError(f"{error_prefix} (400 Bad Request): {resp.text}")
    if resp.status_code == 404:
        raise RecordNotFoundError(f"{error_prefix} (404 Not Found): {resp.text}")
    if resp.status_code == 409:
        raise ConflictError(f"{error_prefix} (409 Conflict): {resp.text}")
    if resp.status_code >= 500:
        raise TransportError(f"{error_prefix} ({resp.status_code} Server Error): {resp.text}")

    raise BrokerError(f"{error_prefix} (HTTP {resp.status_code}): {resp.text}")


async def execute_broker_request(
    send_fn: Callable[[], requests.Response],
    *,
    expected_statuses: tuple[int, ...] = (200, 201),
    allow_404: bool = False,
    error_prefix: str = "Broker request failed",
) -> requests.Response:
    """Execute a synchronous broker HTTP call in an executor thread with typed exception handling."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: execute_sync_broker_request(
            send_fn,
            expected_statuses=expected_statuses,
            allow_404=allow_404,
            error_prefix=error_prefix,
        ),
    )
