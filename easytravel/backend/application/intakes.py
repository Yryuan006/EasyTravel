"""Create/read a real Trip without manufacturing a WorkingDraft."""
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from ...contracts import ID
from ..domain.hashing import digest
from ..domain.intake import CreateTripRequest, TripView, RequirementsInput, NextQuestionResponse
from ..domain.errors import BusinessConflict


class IntakeRepository(Protocol):
    async def lock_command(self, scope: str) -> None: ...
    async def command(self, key: str, request_hash: str, method="POST", path="/api/v1/trips") -> TripView | None: ...
    async def create_intake(self, trip_id: str, request: CreateTripRequest) -> TripView: ...
    async def save_command(self, key: str, request_hash: str, result: TripView,
                           command_id: str, operation_id: str, method="POST", path="/api/v1/trips", status=201) -> None: ...
    async def read_trip(self, trip_id: str) -> TripView: ...
    async def lock_trip(self, trip_id: str) -> None: ...
    async def next_question(self, trip: TripView): ...
    async def advance_intake(self, trip: TripView, requirements: RequirementsInput) -> TripView: ...
    async def save_answer(self, question, trip_id: str, answer, command_id: str) -> None: ...


IntakeUoW = Callable[[str], AbstractAsyncContextManager[IntakeRepository]]


class IntakeService:
    def __init__(self, uow: IntakeUoW):
        self.uow = uow

    async def create(self, request: CreateTripRequest, *, key: str, actor: str) -> TripView:
        TypeAdapter(ID).validate_python(actor)
        request_hash = digest(request.model_dump(mode="json"))
        scope = digest(dict(actor=actor, method="POST", path="/api/v1/trips", key=key))
        async with self.uow(actor) as repo:
            await repo.lock_command(scope)
            replay = await repo.command(key, request_hash)
            if replay is not None:
                return replay
            result = await repo.create_intake(str(uuid4()), request)
            await repo.save_command(key, request_hash, result, str(uuid4()), str(uuid4()))
            return result

    async def get(self, trip_id: str, *, actor: str) -> TripView:
        TypeAdapter(ID).validate_python(actor)
        async with self.uow(actor) as repo:
            return await repo.read_trip(trip_id)


    async def next(self, trip_id, *, actor):
        TypeAdapter(ID).validate_python(actor)
        async with self.uow(actor) as repo:
            trip = await repo.read_trip(trip_id)
            question = await repo.next_question(trip)
            return NextQuestionResponse(intake_revision=trip.intake.intake_revision,
                question=question,blocking_fields=trip.intake.blocking_fields)

    async def update(self, trip_id, request, *, key, actor, question_id=None):
        TypeAdapter(ID).validate_python(actor)
        method = "PUT" if question_id is None else "POST"
        path = f"/api/v1/trips/{trip_id}/requirements" if question_id is None else f"/api/v1/trips/{trip_id}/clarifications/{question_id}/answers"
        request_hash = digest(request.model_dump(mode="json"))
        scope = digest(dict(actor=actor,method=method,path=path,key=key))
        async with self.uow(actor) as repo:
            # All intake mutations: command lock -> owned Trip lock -> CAS -> receipt.
            await repo.lock_command(scope)
            replay = await repo.command(key,request_hash,method,path)
            if replay is not None:
                return replay
            await repo.lock_trip(trip_id)
            trip = await repo.read_trip(trip_id)
            working_revision = trip.working.revision if trip.working else None
            if (trip.intake.intake_revision != request.expected_intake_revision
                    or working_revision != request.expected_working_revision):
                raise BusinessConflict()
            question = None
            if question_id is None:
                requirements = request.requirements
            else:
                question = await repo.next_question(trip)
                if question is None or question.question_id != question_id or question.field_key != request.field_key:
                    raise BusinessConflict()
                data = trip.intake.input.model_dump(mode="json")
                data[question.field_key] = request.answer.model_dump(mode="json")
                requirements = RequirementsInput.model_validate(data)
            command_id = str(uuid4())
            result = await repo.advance_intake(trip,requirements)
            if question is not None:
                await repo.save_answer(question,trip_id,request.answer,command_id)
            await repo.save_command(key,request_hash,result,command_id,str(uuid4()),method,path,200)
            return result
