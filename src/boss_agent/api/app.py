from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from boss_agent.api.routes import (
    candidate_router,
    get_task_broker,
    match_router,
    settings_router,
)
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
    app.include_router(candidate_router)
    app.include_router(settings_router)
    app.include_router(match_router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", response_class=HTMLResponse)
        @app.get("/dashboard", response_class=HTMLResponse)
        def get_dashboard() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    @app.get("/healthz")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app

