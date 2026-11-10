from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from easytravel.backend.api.errors import ErrorResponse


class UncheckedDependencies(BaseModel):
    database: Literal["not_checked"] = "not_checked"
    model: Literal["not_checked"] = "not_checked"
    travel_data: Literal["not_checked"] = "not_checked"


class HealthResponse(BaseModel):
    status: Literal["alive"] = "alive"
    scope: Literal["http_application_liveness"] = "http_application_liveness"
    dependencies: UncheckedDependencies = UncheckedDependencies()


router = APIRouter(prefix="/api/v1", responses={
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
})


@router.get("/health", response_model=HealthResponse, tags=["health"],
            summary="HTTP application liveness only",
            description="Does not check database, model or travel-data readiness.")
async def health() -> HealthResponse:
    return HealthResponse()
