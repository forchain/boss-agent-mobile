"""
src/boss_agent/worker/config.py
===============================
Configuration settings for the out-of-process Automation Worker daemon.
"""

import uuid

from pydantic import BaseModel, Field


class WorkerConfig(BaseModel):
    """Configuration options for an Automation Worker instance."""

    worker_id: str = Field(default_factory=lambda: f"worker-{uuid.uuid4().hex[:6]}")
    device_id: str = "emulator-5554"
    avd_name: str = "boss_avd_arm64"
    appium_url: str = "http://127.0.0.1:4723"
    poll_interval_sec: float = 2.0
    heartbeat_interval_sec: float = 15.0
    lease_timeout_sec: float = 60.0
