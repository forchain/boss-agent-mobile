#!/usr/bin/env python3
"""
scripts/worker.py
=================
CLI entrypoint for running the out-of-process Automation Worker daemon.
"""

import argparse
import asyncio
import logging
import sys

from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.check_login import CheckLoginHandler
from droid_agent_core.driver import AppiumSession, DriverConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Boss Agent Automation Worker Daemon")
    parser.add_argument("--worker-id", type=str, default=None, help="Unique worker identifier")
    parser.add_argument(
        "--device-id", type=str, default="emulator-5554", help="Android device/emulator ID"
    )
    parser.add_argument(
        "--appium-url", type=str, default="http://127.0.0.1:4723", help="Appium server URL"
    )
    parser.add_argument(
        "--pb-url", type=str, default="http://127.0.0.1:8090", help="PocketBase server URL"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=2.0, help="Polling interval in seconds"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger = logging.getLogger("worker_main")

    config = WorkerConfig(
        worker_id=args.worker_id or f"worker-{args.device_id}",
        device_id=args.device_id,
        appium_url=args.appium_url,
        poll_interval_sec=args.poll_interval,
    )

    broker = PocketBaseTaskBroker(base_url=args.pb_url)

    def driver_factory():
        logger.info("Initializing Appium driver session for device %s", config.device_id)
        driver_cfg = DriverConfig(
            server_url=config.appium_url,
            app_package="com.hpbr.bosszhipin",
            extra_capabilities={"udid": config.device_id},
        )
        session = AppiumSession(driver_cfg)
        return session.start()

    context = WorkerContext(config=config, driver_factory=driver_factory)

    handlers = [
        CheckLoginHandler(),
    ]

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=handlers,
    )

    logger.info(
        "Starting Automation Worker %s bound to device %s", config.worker_id, config.device_id
    )
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user, exiting gracefully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
