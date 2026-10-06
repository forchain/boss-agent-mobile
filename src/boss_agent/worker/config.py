"""
src/boss_agent/worker/config.py
===============================
Configuration settings for the out-of-process Automation Worker daemon.
"""

import uuid

from pydantic import BaseModel, Field

from boss_agent.settings import (
    resolve_enable_scheduler,
    resolve_pocketbase_url,
    resolve_run_cleanup_on_startup,
    resolve_server_url,
)


class WorkerConfig(BaseModel):
    """Configuration options for an Automation Worker instance."""

    worker_id: str = Field(default_factory=lambda: f"worker-{uuid.uuid4().hex[:6]}")
    device_id: str = "emulator-5554"
    avd_name: str = "boss_avd_arm64"
    appium_url: str = Field(default_factory=resolve_server_url)
    pocketbase_url: str = Field(default_factory=resolve_pocketbase_url)
    poll_interval_sec: float = 2.0
    heartbeat_interval_sec: float = 15.0
    #: How long a running task's lease may go unrenewed before it is treated as an
    #: orphan. This must exceed the longest the event loop can be blocked by a single
    #: synchronous Appium command, because that stall also freezes the heartbeat
    #: coroutine: a lease shorter than the stall would let the lease sweeper re-queue a
    #: task that is still being worked on, and AUTO_APPLY would greet employers twice.
    #: The floor is the Appium `new_command_timeout` (see `DriverConfig`), rounded up.
    lease_timeout_sec: float = 420.0
    #: How often the lease sweeper inspects running tasks for an expired lease.
    lease_sweep_interval_sec: float = 30.0
    #: Retries an orphan task gets before its lease recovery fails it terminally.
    max_task_retries: int = 2
    #: Run the 拒信清扫 before the first search of a fresh service startup (#230).
    run_cleanup_on_startup: bool = Field(default_factory=resolve_run_cleanup_on_startup)
    #: Run the integrated Cron scheduler alongside the worker (#85, #230).
    enable_scheduler: bool = Field(default_factory=resolve_enable_scheduler)
