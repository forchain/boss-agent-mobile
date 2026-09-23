#!/usr/bin/env python3
"""
scripts/worker.py
=================
CLI entrypoint for running the out-of-process Automation Worker daemon.
"""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Coroutine, Sequence
from typing import Any

from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.settings import resolve_pocketbase_url, resolve_server_url
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.check_login import CheckLoginHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler
from droid_agent_core.driver import AppiumSession, DriverConfig

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
        action="store_true",
        help="Enable integrated Cron scheduler daemon alongside the worker",
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

    resolved_pb_url = resolve_pocketbase_url(explicit_url=args.pb_url)
    resolved_appium_url = resolve_server_url(explicit_url=args.appium_url)

    config = WorkerConfig(
        worker_id=args.worker_id or f"worker-{args.device_id}",
        device_id=args.device_id,
        appium_url=resolved_appium_url,
        pocketbase_url=resolved_pb_url,
        poll_interval_sec=args.poll_interval,
    )

    broker = PocketBaseTaskBroker(base_url=resolved_pb_url)

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
        ScrapeJobsHandler(),
        AutoApplyHandler(),
    ]

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=handlers,
    )

    logger.info(
        "Starting Automation Worker %s bound to device %s (PocketBase: %s)",
        config.worker_id,
        config.device_id,
        config.pocketbase_url,
    )

    service_coros: list[Coroutine[Any, Any, None]] = []
    if args.enable_scheduler:
        from boss_agent.scheduler import AutomationScheduler

        scheduler = AutomationScheduler(broker=broker, poll_interval_sec=30.0)
        logger.info("Integrated Cron scheduler enabled")
        service_coros.append(scheduler.run_forever())

    try:
        asyncio.run(run_services(worker, service_coros))
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user, exiting gracefully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
