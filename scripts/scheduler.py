#!/usr/bin/env python3
"""
scripts/scheduler.py
====================
CLI entrypoint for running the Boss Agent Cron Scheduler daemon.

Composition lives in :mod:`boss_agent.main`; this module keeps the flags and the loop.
"""

import argparse
import asyncio
import logging
import sys

from boss_agent.main import build_scheduler


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

    scheduler = build_scheduler(
        pocketbase_url=args.pb_url,
        poll_interval_sec=args.poll_interval,
    )

    logger.info(
        "Starting Boss Agent Cron Scheduler (PocketBase: %s, Poll interval: %.1fs)",
        scheduler.broker.base_url,
        args.poll_interval,
    )

    try:
        asyncio.run(scheduler.run_forever())
    except KeyboardInterrupt:
        logger.info("Scheduler interrupted by user, exiting gracefully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
