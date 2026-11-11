"""Composition root. For Windows business APIs use backend.local_server."""

from fastapi import FastAPI

from easytravel.backend.api.errors import register_error_handlers
from easytravel.backend.api.v1.router import router
from easytravel.backend.api.v1.trips import router as trip_router
from easytravel.backend.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = load_settings() if settings is None else settings
    app = FastAPI(title="EasyTravel API", version="0.1.0", debug=False)
    app.state.settings = config
    register_error_handlers(app)
    app.include_router(router)
    app.include_router(trip_router)
    return app
