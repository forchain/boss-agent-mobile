"""
src/boss_agent/worker/daemon.py
===============================
Daemon loop for the out-of-process Automation Worker.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from typing import Any, cast

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.startup_cleanup import StartupCleanupGate
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger("boss_agent.worker")

# Acknowledgment marker emitted the instant a termination signal is received. Supervisors and
# test harnesses match on this line to prove the worker accepted the signal — never reword it
# without updating the callers that verify it (see boss_agent.services.teardown).
SHUTDOWN_ACK_MARKER = "[Worker] Received shutdown signal"

# Reason recorded in the State Stream Broker when a termination signal aborts an in-flight task.
SHUTDOWN_CANCEL_REASON = "Worker shutdown signal received"

#: Why a test-sourced task was cancelled. Says what happened, not who to blame.
TEST_RECLAIM_REASON = "Test-sourced task reclaimed at worker startup"

#: How many queued tasks the startup reclamation scan reads.
RECLAIM_SCAN_LIMIT = 100

# Upper bound for closing the Virtual Device Session. A wedged Appium server must not be
# able to hold worker shutdown (and therefore the E2E pre-test gate) hostage.
DEVICE_RELEASE_TIMEOUT_SEC = 3.0

# Statuses that mean the task's outcome is already decided and must not be overwritten by an
# abort racing the handler's own completion.
_TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


def _format_payload_for_logging(
    obj: Any,
    max_str_len: int = 120,
    max_list_items: int = 5,
    large_doc_keys: tuple[str, ...] = (
        "profile_document",
        "job_description",
        "raw_html",
        "resume_text",
    ),
) -> Any:
    """Recursively truncate payload structures for readable, non-bloated console logging."""
    if isinstance(obj, dict):
        truncated: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k) in large_doc_keys and isinstance(v, str) and len(v) > 50:
                truncated[k] = f"<{len(v)} chars: {v[:40].strip()}...>"
            else:
                truncated[k] = _format_payload_for_logging(
                    v, max_str_len, max_list_items, large_doc_keys
                )
        return truncated

    if isinstance(obj, list):
        if len(obj) > max_list_items:
            items = [
                _format_payload_for_logging(x, max_str_len, max_list_items, large_doc_keys)
                for x in obj[:max_list_items]
            ]
            items.append(f"... (+{len(obj) - max_list_items} more items)")
            return items
        return [
            _format_payload_for_logging(x, max_str_len, max_list_items, large_doc_keys) for x in obj
        ]

    if isinstance(obj, str):
        if len(obj) > max_str_len:
            return f"{obj[:max_str_len]}... (total {len(obj)} chars)"
        return obj

    return obj


class _TaskLoggingBrokerProxy:
    """Non-mutating proxy for BaseTaskBroker that mirrors handler progress logs to worker console."""

    def __init__(self, target: BaseTaskBroker) -> None:
        self._target = target

    async def append_log(self, task_id: str, log_line: str) -> bool:
        logger.info("📝 [Task %s] %s", task_id, log_line)
        return await self._target.append_log(task_id, log_line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class AutomationWorker:
    """Out-of-process task execution daemon bound 1:1 to a device session."""

    def __init__(
        self,
        config: WorkerConfig,
        broker: BaseTaskBroker,
        context: WorkerContext | None = None,
        handlers: Sequence[BaseTaskHandler] | None = None,
        driver: Any | None = None,
        startup_gate: StartupCleanupGate | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.context = context or WorkerContext(config=config, driver=driver)
        self.startup_gate = startup_gate or StartupCleanupGate(
            broker, enabled=config.run_cleanup_on_startup
        )
        self._handlers: dict[TaskType, BaseTaskHandler] = {}
        if handlers:
            for h in handlers:
                self.register_handler(h)

        self._running = False
        self._stop_event = asyncio.Event()
        self._shutting_down = False
        self._inflight_task_id: str | None = None
        self._inflight_exec_task: asyncio.Task[None] | None = None

    def register_handler(self, handler: BaseTaskHandler) -> None:
        """Register a polymorphic task handler."""
        self._handlers[handler.task_type] = handler

    async def _heartbeat_loop(self, task_id: str) -> None:
        """Background task renewing the heartbeat lease periodically."""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval_sec)
                await self.broker.update_heartbeat(task_id, worker_id=self.config.worker_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat update failed for task %s: %s", task_id, e)

    async def _finalize_task_outcome(
        self,
        task: AutomationTask,
        task_type_str: str,
        start_time: float,
        result: HandlerResult,
    ) -> None:
        """Evaluate task outcome, log completion status and update state in broker."""
        duration = time.monotonic() - start_time
        cur = await self.broker.get_task(task.id)
        if cur is None:
            logger.warning(
                "⚠️ Task %s record no longer exists (purged during execution?); "
                "outcome update skipped (%.2fs)",
                task.id,
                duration,
            )
            return
        if cur.status == TaskStatus.CANCELLED:
            logger.info(
                "⚠️ Task %s was cancelled during execution (%.2fs); preserving CANCELLED status",
                task.id,
                duration,
            )
        elif result.success:
            logger.info(
                "✅ Task %s [%s] completed successfully in %.2fs",
                task.id,
                task_type_str,
                duration,
            )
            await self.broker.update_task_status(
                task.id,
                status=TaskStatus.SUCCESS,
            )
        else:
            logger.error(
                "❌ Task %s [%s] failed in %.2fs: %s",
                task.id,
                task_type_str,
                duration,
                result.error_message,
            )
            await self.broker.update_task_status(
                task.id,
                status=TaskStatus.FAILED,
                error_message=result.error_message,
            )

    async def _execute_claimed_task(
        self,
        claimed_task: AutomationTask,
        handler: BaseTaskHandler,
        task_type_str: str,
    ) -> None:
        """Execute one claimed task with heartbeat renewal, then record its outcome."""
        start_time = time.monotonic()

        # Non-mutating broker proxy that mirrors handler append_log calls to worker console
        handler_broker = cast(BaseTaskBroker, _TaskLoggingBrokerProxy(self.broker))

        # Start heartbeat background renewal
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(claimed_task.id))
        try:
            result = await handler.handle(claimed_task, handler_broker, self.context)
            await self._finalize_task_outcome(claimed_task, task_type_str, start_time, result)
        except asyncio.CancelledError:
            # Shutdown aborted this task; broker bookkeeping is owned by the shutdown path.
            duration = time.monotonic() - start_time
            logger.warning(
                "⚠️ Task %s [%s] aborted after %.2fs (worker shutting down)",
                claimed_task.id,
                task_type_str,
                duration,
            )
            raise
        except Exception as e:
            duration = time.monotonic() - start_time
            cur = await self.broker.get_task(claimed_task.id)
            if cur is None:
                logger.warning(
                    "⚠️ Task %s record no longer exists; uncaught exception not "
                    "persisted: %s (%.2fs)",
                    claimed_task.id,
                    e,
                    duration,
                )
            elif cur.status == TaskStatus.CANCELLED:
                logger.info(
                    "⚠️ Task %s was cancelled, ignoring exception: %s (%.2fs)",
                    claimed_task.id,
                    e,
                    duration,
                )
            else:
                logger.exception(
                    "❌ Task %s [%s] execution raised uncaught exception in %.2fs: %s",
                    claimed_task.id,
                    task_type_str,
                    duration,
                    e,
                )
                await self.broker.append_log(claimed_task.id, f"Uncaught exception: {e}")
                await self.broker.update_task_status(
                    claimed_task.id,
                    status=TaskStatus.FAILED,
                    error_message=str(e),
                )
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def run_once(self) -> bool:
        """Attempt to claim and process one pending task.

        The startup 拒信清扫 is claimed ahead of anything queued before it, and every
        search task is held back until it settles (#230).

        The scan window is the gate's: a cleanup queued at startup sits behind any
        task left pending by an earlier run, so a narrower window could leave the
        cleanup unseen while its own barrier held every task in view.
        """
        pending_tasks = await self.broker.list_pending_tasks(
            limit=self.startup_gate.queue_scan_limit
        )
        if not pending_tasks:
            return False

        claimed_task: AutomationTask | None = None
        held = await self.startup_gate.held_types()
        for task in self.startup_gate.prioritize(pending_tasks):
            if task.task_type in held:
                logger.info(
                    "⏸️ Task %s [%s] held back until the startup 拒信清扫 settles",
                    task.id,
                    task.task_type.value,
                )
                continue
            claimed = await self.broker.claim_task(task.id, worker_id=self.config.worker_id)
            if claimed:
                claimed_task = claimed
                break

        if not claimed_task:
            return False

        task_type_str = claimed_task.task_type.value
        preview_payload = _format_payload_for_logging(claimed_task.payload or {})
        logger.info(
            "📥 Claimed task %s [type=%s] for device %s (payload=%s)",
            claimed_task.id,
            task_type_str,
            self.config.device_id,
            preview_payload,
        )

        handler = self._handlers.get(claimed_task.task_type)
        if not handler:
            logger.error(
                "❌ No handler registered for task type %s (task_id=%s)",
                task_type_str,
                claimed_task.id,
            )
            await self.broker.append_log(
                claimed_task.id, f"No handler registered for task type {claimed_task.task_type}"
            )
            await self.broker.update_task_status(
                claimed_task.id,
                status=TaskStatus.FAILED,
                error_message=f"Unsupported task type: {claimed_task.task_type}",
            )
            return True

        # Track the in-flight execution so a termination signal can abort it promptly.
        exec_task: asyncio.Task[None] = asyncio.create_task(
            self._execute_claimed_task(claimed_task, handler, task_type_str)
        )
        self._inflight_task_id = claimed_task.id
        self._inflight_exec_task = exec_task
        try:
            await exec_task
        finally:
            self._inflight_task_id = None
            self._inflight_exec_task = None

        return True

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep for `seconds`, waking early as soon as a shutdown has been requested."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    async def _release_device_session(self) -> None:
        """Close the active Appium session, bounded so a wedged server cannot stall shutdown."""
        if not self.context.has_device_session():
            return
        try:
            released = await asyncio.wait_for(
                asyncio.to_thread(self.context.release_device_session),
                timeout=DEVICE_RELEASE_TIMEOUT_SEC,
            )
        except TimeoutError:
            logger.warning(
                "⏱️ Device session release exceeded %.1fs; abandoning the session and continuing.",
                DEVICE_RELEASE_TIMEOUT_SEC,
            )
            return
        if released:
            logger.info("🔌 Released Appium device session for %s", self.config.device_id)

    async def _abort_in_flight_task(self) -> None:
        """Cancel the executing handler and record the cancellation in the State Stream Broker."""
        exec_task = self._inflight_exec_task
        task_id = self._inflight_task_id
        if exec_task is None or task_id is None:
            return

        logger.info("🛑 Aborting in-flight task %s", task_id)
        exec_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await exec_task

        # A handler that finished successfully in the window between its final broker write and
        # the in-flight handle being cleared must keep its terminal status.
        current = await self.broker.get_task(task_id)
        if current is not None and current.status in _TERMINAL_TASK_STATUSES:
            logger.info(
                "ℹ️ Task %s already reached %s; leaving its status untouched.",
                task_id,
                current.status.value,
            )
            return

        try:
            await self.broker.append_log(task_id, SHUTDOWN_CANCEL_REASON)
            await self.broker.update_task_status(
                task_id,
                status=TaskStatus.CANCELLED,
                error_message=SHUTDOWN_CANCEL_REASON,
            )
        except Exception as e:
            logger.error("Failed to mark task %s as cancelled during shutdown: %s", task_id, e)
            return

        logger.info("✅ Task %s marked CANCELLED (%s)", task_id, SHUTDOWN_CANCEL_REASON)

    async def shutdown(self, signal_name: str = "SIGTERM") -> None:
        """Handle a termination signal: stop polling, abort the in-flight task, release the device.

        The acknowledgment is logged before any cleanup, so a supervisor has immediate proof the
        signal was accepted. Cleanup itself is bounded only where it can be: the device session
        release is capped by `DEVICE_RELEASE_TIMEOUT_SEC`. Aborting a handler that is executing a
        *synchronous* Appium command cannot be bounded in-process — the command must return (and
        the event loop resumes) before the abort proceeds, so such a worker can exceed the usual
        exit bound by the duration of that one command. A supervisor should escalate rather than
        wait indefinitely.
        """
        logger.info("🛑 %s (%s), initiating graceful shutdown...", SHUTDOWN_ACK_MARKER, signal_name)
        if self._shutting_down:
            logger.info(
                "⏳ [Worker] Graceful shutdown already in progress; ignoring duplicate signal."
            )
            return
        self._shutting_down = True

        # Halt the polling loop and interrupt any pending poll sleep.
        self._running = False
        self._stop_event.set()

        await self._abort_in_flight_task()
        await self._release_device_session()

        logger.info("👋 [Worker] Shutdown complete.")

    async def _reclaim_test_sourced_tasks(self) -> list[str]:
        """Cancel queued tasks whose provenance is ``test``.

        CONTEXT.md defines Task Provenance precisely so an automated suite never
        contends with the live worker for the device: a test that queued work before
        the worker came up finds it already cancelled rather than racing the real run.
        Only *pending* work is reclaimed — a task already running is somebody's
        in-flight operation, and its own cancellation path owns it.
        """
        from boss_agent.task_launch import LaunchSource, coerce_source

        reclaimed: list[str] = []
        try:
            pending = await self.broker.list_pending_tasks(limit=RECLAIM_SCAN_LIMIT)
        except Exception as e:
            logger.warning("Could not scan for test-sourced tasks: %s", e)
            return reclaimed

        for task in pending:
            if coerce_source(getattr(task, "source", None)) is not LaunchSource.TEST:
                continue
            try:
                await self.broker.append_log(task.id, TEST_RECLAIM_REASON)
                await self.broker.update_task_status(
                    task.id,
                    status=TaskStatus.CANCELLED,
                    error_message=TEST_RECLAIM_REASON,
                )
                reclaimed.append(task.id)
            except Exception as e:
                logger.warning("Could not reclaim test-sourced task %s: %s", task.id, e)

        if reclaimed:
            logger.info(
                "🧹 Reclaimed %d test-sourced task(s) at startup: %s",
                len(reclaimed),
                ", ".join(reclaimed),
            )
        return reclaimed

    async def start(self, max_runs: int | None = None) -> None:
        """Start the worker execution polling loop."""
        self._running = True
        logger.info(
            "🚀 Worker daemon loop started for %s bound to device %s (poll_interval=%.1fs)",
            self.config.worker_id,
            self.config.device_id,
            self.config.poll_interval_sec,
        )
        # Reclaim before arming, so a cancelled test task is not the "first" thing the
        # cleanup barrier has to wait behind.
        await self._reclaim_test_sourced_tasks()
        # Queued before the loop so the first claim of this startup is the cleanup
        # rather than a search task that could re-apply to a rejecting employer.
        try:
            await self.startup_gate.arm()
        except Exception as e:
            logger.warning("Could not arm startup 拒信清扫 barrier: %s", e)
        runs = 0
        while self._running:
            try:
                did_work = await self.run_once()
                if did_work:
                    runs += 1
                    if max_runs is not None and runs >= max_runs:
                        break
                else:
                    await self._sleep_or_stop(self.config.poll_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in worker execution loop: %s", e)
                await self._sleep_or_stop(self.config.poll_interval_sec)
        logger.info("🛑 Worker daemon loop stopped for %s", self.config.worker_id)

    def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
        self._stop_event.set()
