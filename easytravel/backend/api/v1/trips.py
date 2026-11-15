"""Trusted local Trip intake APIs; no generator or implicit migrations."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Response, Query

from ...application.intakes import IntakeService
from ...domain.intake import (CreateTripRequest, TripView, UpdateRequirementsRequest,
                              AnswerRequest, NextQuestionResponse)
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


TripId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
CommandKey = Annotated[UUID, Header(alias="Idempotency-Key")]


@router.put("/{trip_id}/requirements", response_model=TripView, operation_id="updateRequirements")
async def update_requirements(trip_id: TripId, body: UpdateRequirementsRequest,
                              response: Response, actor: Actor, service: Service, key: CommandKey):
    result = await service.update(trip_id,body,key=str(key),actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{trip_id}/clarifications/next", response_model=NextQuestionResponse, operation_id="getNextQuestion")
async def next_question(trip_id: TripId, response: Response, actor: Actor, service: Service):
    result = await service.next(trip_id,actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{trip_id}/clarifications/{question_id}/answers", response_model=TripView, operation_id="answerQuestion")
async def answer_question(trip_id: TripId, question_id: TripId, body: AnswerRequest,
                          response: Response, actor: Actor, service: Service, key: CommandKey):
    result = await service.update(trip_id,body,key=str(key),actor=actor,question_id=question_id)
    response.headers["Cache-Control"] = "no-store"
    return result


def generation_service(request: Request):
    from ...infrastructure.database import load_database_settings
    from ...infrastructure.generations import unit_of_work
    from ...application.generations import GenerationService
    try:
        database = load_database_settings()
    except ValueError:
        raise LocalApiError(503, "business_dependency_unavailable") from None
    return GenerationService(unit_of_work(database))


from ...domain.generation import GenerateRequest, GenerationRun
Generation = Annotated[object, Depends(generation_service)]


@router.post("/{trip_id}/generations", response_model=GenerationRun, status_code=202, operation_id="requestGeneration")
async def generate_plan(trip_id: TripId, body: GenerateRequest, response: Response,
                        actor: Actor, service: Generation, key: CommandKey):
    result = await service.accept(trip_id,body,key=str(key),actor=actor)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Location"] = f"/api/v1/trips/{trip_id}/generations/{result.run_id}"
    return result


@router.get("/{trip_id}/generations/{run_id}", response_model=GenerationRun, operation_id="getGeneration")
async def get_generation(trip_id: TripId, run_id: TripId, response: Response, actor: Actor, service: Generation):
    result = await service.get(trip_id,run_id,actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


from ...domain.generation import GenerationState

@router.get("/{trip_id}/generation-state", response_model=GenerationState, operation_id="getGenerationState")
async def generation_state(trip_id: TripId, response: Response, actor: Actor, service: Generation):
    result = await service.state(trip_id,actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


def confirmation_service(request: Request):
    from ...infrastructure.database import load_database_settings
    from ...infrastructure.confirmations import unit_of_work
    from ...application.confirmations import ConfirmationService
    try:
        database = load_database_settings()
    except ValueError:
        raise LocalApiError(503, "business_dependency_unavailable") from None
    return ConfirmationService(unit_of_work(database))


from ...domain.confirmation import ConfirmRequest, ConfirmResult, ConfirmationPreview
Confirmation = Annotated[object, Depends(confirmation_service)]


@router.get("/{trip_id}/confirmation-preview", response_model=ConfirmationPreview, operation_id="getConfirmationPreview")
async def confirmation_preview(trip_id: TripId, response: Response, actor: Actor, service: Confirmation):
    result = await service.preview(trip_id, actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{trip_id}/confirmations", response_model=ConfirmResult, status_code=201, operation_id="confirmTrip")
async def confirm_trip(trip_id: TripId, body: ConfirmRequest, response: Response,
                       actor: Actor, service: Confirmation, key: CommandKey):
    result = await service.confirm(trip_id, body, key=str(key), actor=actor)
    response.headers["Cache-Control"] = "no-store"
    return result


def change_service(request: Request):
    from ...infrastructure.database import load_database_settings
    from ...infrastructure.changes import unit_of_work
    from ...application.changes import ChangeService
    try:
        database = load_database_settings()
    except ValueError:
        raise LocalApiError(503, 'business_dependency_unavailable') from None
    return ChangeService(unit_of_work(database))


from ...domain.changes import ChangeIntentRequest, ProposalView, DecisionRequest, DecisionResult
Changes = Annotated[object, Depends(change_service)]


@router.post('/{trip_id}/change-intents', response_model=GenerationRun, status_code=202, operation_id='requestChange')
async def request_change(trip_id: TripId, body: ChangeIntentRequest, response: Response, actor: Actor, service: Changes, key: CommandKey):
    result = await service.accept(trip_id,body,key=str(key),actor=actor)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Location'] = f'/api/v1/trips/{trip_id}/generations/{result.run_id}'
    return result


@router.get('/{trip_id}/proposals/{proposal_id}', response_model=ProposalView, operation_id='getProposal')
async def get_proposal(trip_id: TripId, proposal_id: TripId, response: Response, actor: Actor, service: Changes):
    result = await service.get(trip_id,proposal_id,actor=actor)
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.post('/{trip_id}/proposals/{proposal_id}/decisions', response_model=DecisionResult, operation_id='decideProposal')
async def decide_proposal(trip_id: TripId, proposal_id: TripId, body: DecisionRequest, response: Response, actor: Actor, service: Changes, key: CommandKey):
    result = await service.decide(trip_id,proposal_id,body,key=str(key),actor=actor)
    response.headers['Cache-Control'] = 'no-store'
    return result


def snapshot_service():
    from ...infrastructure.database import load_database_settings
    from ...infrastructure.snapshots import unit_of_work
    from ...application.snapshots import SnapshotService
    try:
        database = load_database_settings()
    except ValueError:
        raise LocalApiError(503, 'business_dependency_unavailable') from None
    return SnapshotService(unit_of_work(database))


from ...domain.snapshots import SnapshotPage
from ...domain.confirmation import SnapshotView
from ...application.snapshots import InvalidCursor
Snapshots = Annotated[object, Depends(snapshot_service)]


@router.get('/{trip_id}/snapshots', response_model=SnapshotPage, operation_id='listSnapshots')
async def list_snapshots(trip_id: TripId, response: Response, actor: Actor, service: Snapshots,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None):
    try:
        result = await service.list(trip_id, actor=actor, limit=limit, cursor=cursor)
    except InvalidCursor:
        raise LocalApiError(422, 'invalid_request') from None
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.get('/{trip_id}/snapshots/{snapshot_id}', response_model=SnapshotView, operation_id='getSnapshot')
async def get_snapshot(trip_id: TripId, snapshot_id: TripId, response: Response, actor: Actor, service: Snapshots):
    result = await service.get(trip_id, snapshot_id, actor=actor)
    response.headers['Cache-Control'] = 'no-store'
    return result
