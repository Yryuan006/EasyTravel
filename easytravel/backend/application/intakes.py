"""Create/read a real Trip without manufacturing a WorkingDraft."""
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from ...contracts import ID
from ..domain.hashing import digest
from ..domain.intake import CreateTripRequest, TripView


class IntakeRepository(Protocol):
    async def lock_command(self, scope: str) -> None: ...
    async def command(self, key: str, request_hash: str) -> TripView | None: ...
    async def create_intake(self, trip_id: str, request: CreateTripRequest) -> TripView: ...
    async def save_command(self, key: str, request_hash: str, result: TripView,
                           command_id: str, operation_id: str) -> None: ...
    async def read_trip(self, trip_id: str) -> TripView: ...


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
