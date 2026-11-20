#!/usr/bin/env python3
"""
scripts/worker.py
=================
CLI entrypoint for running the out-of-process Automation Worker daemon.

The path is frozen: Cross-Worktree Service Preemption and System Doctor identify the
worker process by ``pgrep -f scripts/worker.py``. Composition lives in
:mod:`boss_agent.main`; this module keeps the flags and the process supervision.
"""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Coroutine, Sequence
from typing import Any

from boss_agent.adapters.inbound.worker.daemon import AutomationWorker
from boss_agent.main import build_supervised_services, build_worker

logger = logging.getLogger("worker_main")

TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGINT)


async def run_services(
    worker: AutomationWorker,
    extra_services: Sequence[Coroutine[Any, Any, None]] = (),
) -> None:
    """Supervise the worker loop and any auxiliary service loops until shutdown completes.

    Resolves on SIGTERM/SIGINT. It also resolves as soon as any supervised service loop has
    finished on its own, so the daemon can never wait forever for a signal that is not coming;
    the worker is still shut down through its normal protocol either way.
    """
    loop = asyncio.get_running_loop()
    stop_signal: asyncio.Future[str] = loop.create_future()

    def _request_shutdown(signame: str) -> None:
        # Signal-handler callbacks must stay trivial: just wake the supervisor.
        if not stop_signal.done():
            stop_signal.set_result(signame)

    registered_signals: list[signal.Signals] = []
    for sig in TERMINATION_SIGNALS:
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
            registered_signals.append(sig)
        except (NotImplementedError, RuntimeError) as e:
            logger.warning("Cannot install handler for %s: %s", sig.name, e)
    if registered_signals:
        logger.info(
            "🧷 Termination signal handlers installed: %s",
            ", ".join(s.name for s in registered_signals),
        )

    service_tasks = [asyncio.create_task(coro) for coro in (worker.start(), *extra_services)]

    async def supervise_shutdown() -> None:
        async def await_signal() -> None:
            await stop_signal

        # Resolve on whichever comes first: a termination signal, or a service loop ending.
        signal_task = asyncio.create_task(await_signal())
        await asyncio.wait([signal_task, *service_tasks], return_when=asyncio.FIRST_COMPLETED)
        if stop_signal.done():
            await worker.shutdown(stop_signal.result())
        else:
            logger.warning("⚠️ A supervised service loop exited; shutting down.")
            await worker.shutdown("service exit")
        for task in [*service_tasks, signal_task]:
            task.cancel()

    supervisor = asyncio.create_task(supervise_shutdown())
    try:
        results = await asyncio.gather(*service_tasks, supervisor, return_exceptions=True)
    finally:
        for sig in registered_signals:
            loop.remove_signal_handler(sig)

    for task, result in zip([*service_tasks, supervisor], results, strict=True):
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            logger.error("Service task '%s' terminated unexpectedly: %s", task.get_name(), result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Boss Agent Automation Worker Daemon")
    parser.add_argument("--worker-id", type=str, default=None, help="Unique worker identifier")
    parser.add_argument(
        "--device-id", type=str, default="emulator-5554", help="Android device/emulator ID"
    )
    parser.add_argument(
        "--appium-url",
        type=str,
        default=None,
        help="Appium server URL (overrides config/env setting, default: resolved from config)",
    )
    parser.add_argument(
        "--pb-url",
        "--pocketbase-url",
        type=str,
        default=None,
        help="PocketBase server URL (overrides config/env setting, default: resolved from config)",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=2.0, help="Polling interval in seconds"
    )
    parser.add_argument(
        "--enable-scheduler",
        dest="enable_scheduler",
        action="store_true",
        default=None,
        help="Enable integrated Cron scheduler daemon alongside the worker (default: enabled)",
    )
    parser.add_argument(
        "--no-scheduler",
        "--disable-scheduler",
        dest="enable_scheduler",
        action="store_false",
        help="Disable integrated Cron scheduler daemon alongside the worker",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Root logging level. DEBUG additionally enables low-level UI operation telemetry "
        "(logger 'droid_agent_core.ui').",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("worker_main")

    worker = build_worker(
        worker_id=args.worker_id,
        device_id=args.device_id,
        appium_url=args.appium_url,
        pocketbase_url=args.pb_url,
        poll_interval_sec=args.poll_interval,
        enable_scheduler=args.enable_scheduler,
    )
    config = worker.config

    logger.info(
        "Starting Automation Worker %s bound to device %s (PocketBase: %s)",
        config.worker_id,
        config.device_id,
        config.pocketbase_url,
    )

    service_coros = build_supervised_services(worker)
    if config.enable_scheduler:
        logger.info("Integrated Cron scheduler enabled")
    logger.info(
        "Lease sweeper enabled (lease_timeout=%.0fs, interval=%.0fs, max_retries=%d)",
        config.lease_timeout_sec,
        config.lease_sweep_interval_sec,
        config.max_task_retries,
    )

    try:
        asyncio.run(run_services(worker, service_coros))
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user, exiting gracefully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
