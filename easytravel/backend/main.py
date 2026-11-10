"""Composition root. Run with uvicorn's --factory option."""

from fastapi import FastAPI

from easytravel.backend.api.errors import register_error_handlers
from easytravel.backend.api.v1.router import router
from easytravel.backend.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = load_settings() if settings is None else settings
    app = FastAPI(title="EasyTravel API", version="0.1.0", debug=False)
    app.state.settings = config
    register_error_handlers(app)
    app.include_router(router)
    return app
