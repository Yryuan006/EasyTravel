"""Only M1-01 creation and persisted reads; no generator or implicit migrations."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Response

from ...application.intakes import IntakeService
from ...domain.intake import CreateTripRequest, TripView
from ..errors import ErrorResponse, LocalApiError
from ..local_identity import trusted_actor
from fastapi import Request


def intake_service(request: Request) -> IntakeService:
    # Health, imports and OpenAPI must remain independent of database adapters.
    from ...infrastructure.database import load_database_settings
    from ...infrastructure.intakes import unit_of_work
    try:
        database = load_database_settings()
    except ValueError:
        raise LocalApiError(503, "business_dependency_unavailable") from None
    return IntakeService(unit_of_work(database))


router = APIRouter(prefix="/api/v1/trips", tags=["trips"], responses={
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 500, 503)
})
Actor = Annotated[str, Depends(trusted_actor)]
Service = Annotated[IntakeService, Depends(intake_service)]


@router.post("", response_model=TripView, status_code=201, operation_id="createTrip")
async def create_trip(body: CreateTripRequest, response: Response, actor: Actor,
                      service: Service, key: Annotated[UUID, Header(alias="Idempotency-Key")]):
    result = await service.create(body, key=str(key), actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{trip_id}", response_model=TripView, operation_id="getTrip")
async def get_trip(trip_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,80}$")],
                   response: Response, actor: Actor, service: Service):
    result = await service.get(trip_id, actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result
