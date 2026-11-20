"""
src/boss_agent/adapters/inbound/worker/handlers/check_login.py
==============================================================
Handler for CHECK_LOGIN task: verifies session persistence and dismisses startup popups.
"""

from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.inbound.worker.handlers.base import BaseTaskHandler, HandlerResult
from boss_agent.adapters.outbound.device.pages.job_feed import JobListPage
from boss_agent.adapters.outbound.device.pages.system import LoginPage, StartupDialogPage
from boss_agent.core.automation.interfaces.locator_store import LocatorStorePort
from boss_agent.core.shared.enums import AuthStatus
from boss_agent.core.task.domain.models import AutomationTask, TaskType
from boss_agent.core.task.interfaces.broker import BaseTaskBroker


class CheckLoginHandler(BaseTaskHandler):
    """Executes login readiness and session persistence check."""

    def __init__(self, locator_store: LocatorStorePort) -> None:
        self._locator_store = locator_store

    @property
    def task_type(self) -> TaskType:
        return TaskType.CHECK_LOGIN

    async def handle(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        context: WorkerContext,
    ) -> HandlerResult:
        driver = context.driver
        if not driver:
            await broker.append_log(task.id, "Error: No driver session initialized")
            return HandlerResult(success=False, error_message="Driver session is unavailable")

        await broker.append_log(
            task.id, f"Checking session readiness on device {context.config.device_id}"
        )

        startup_page = StartupDialogPage(driver, locator_registry=self._locator_store)
        if startup_page.is_dialog_present():
            await broker.append_log(task.id, "Startup dialog detected, dismissing...")
            startup_page.dismiss_dialog()

        login_page = LoginPage(driver, locator_registry=self._locator_store)
        auth_status = login_page.get_auth_status()

        if auth_status == AuthStatus.AUTHENTICATED:
            job_list_page = JobListPage(driver, locator_registry=self._locator_store)
            job_list_page.navigate_to_home()
            await broker.append_log(task.id, "Session authenticated successfully")
            return HandlerResult(success=True)

        if auth_status == AuthStatus.UNAUTHENTICATED:
            await broker.append_log(task.id, "User is not logged in to Boss App")
            return HandlerResult(
                success=False,
                error_message="User is not logged in. Manual login required on device.",
            )

        # CHALLENGE_REQUIRED
        await broker.append_log(task.id, "Security challenge (captcha/SMS) detected")
        return HandlerResult(
            success=False,
            error_message="Security challenge detected. Takeover required.",
        )
