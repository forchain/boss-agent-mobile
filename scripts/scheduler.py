#!/usr/bin/env python3
"""
scripts/scheduler.py
====================
CLI entrypoint for running the Boss Agent Cron Scheduler daemon.
"""

import argparse
import asyncio
import logging
import sys

from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.scheduler import AutomationScheduler
from boss_agent.settings import resolve_pocketbase_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Boss Agent Cron Scheduler Daemon")
    parser.add_argument(
        "--pb-url",
        "--pocketbase-url",
        type=str,
        default=None,
        help="PocketBase server URL (default: resolved from config/env)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=30.0,
        help="Scheduler evaluation polling interval in seconds (default: 30.0)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger = logging.getLogger("scheduler_main")

    resolved_pb_url = resolve_pocketbase_url(explicit_url=args.pb_url)
    broker = PocketBaseTaskBroker(base_url=resolved_pb_url)
    scheduler = AutomationScheduler(broker=broker, poll_interval_sec=args.poll_interval)

    logger.info(
        "Starting Boss Agent Cron Scheduler (PocketBase: %s, Poll interval: %.1fs)",
        resolved_pb_url,
        args.poll_interval,
    )

    try:
        asyncio.run(scheduler.run_forever())
    except KeyboardInterrupt:
        logger.info("Scheduler interrupted by user, exiting gracefully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
