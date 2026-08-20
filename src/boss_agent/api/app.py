"""
src/boss_agent/api/app.py
=========================
FastAPI application factory for the Modular Monolith Backend Application Service.
"""

from fastapi import FastAPI

from boss_agent.api.routes import get_task_broker
from boss_agent.api.routes import router as tasks_router
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker, InMemoryTaskBroker


def create_app(broker: BaseTaskBroker | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="Boss Agent Mobile API",
        description="Modular Monolith Backend Application Service for Boss Mobile Automation",
        version="0.1.0",
    )

    actual_broker = broker or InMemoryTaskBroker()
    app.dependency_overrides[get_task_broker] = lambda: actual_broker

    app.include_router(tasks_router)

    @app.get("/healthz")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app
