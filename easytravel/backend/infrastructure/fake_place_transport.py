"""In-memory scripted replies only. No URL, key, HTTP client or socket interface."""
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from ..domain.places import PlaceContract


class FakeResponse(PlaceContract):
    status_code: Annotated[int, Field(ge=100, le=599)]
    body: Annotated[str, Field(max_length=131072)]


@dataclass(frozen=True)
class FakeCall:
    action: Literal["search", "detail"]
    supplier_id: str | None = None


class FakeTransport:
    def __init__(self, *replies: FakeResponse | Exception):
        if len(replies) > 100:
            raise ValueError("too many fake replies")
        self._replies = tuple(replies)
        self._calls: list[FakeCall] = []

    @property
    def calls(self) -> tuple[FakeCall, ...]:
        return tuple(self._calls)

    async def exchange(self, action: Literal["search", "detail"], supplier_id: str | None = None) -> FakeResponse:
        index = len(self._calls)
        if index >= 100:
            raise RuntimeError("fake call limit")
        self._calls.append(FakeCall(action, supplier_id))
        if index >= len(self._replies):
            raise RuntimeError("fake replies exhausted")
        reply = self._replies[index]
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse.model_validate_json(reply.model_dump_json())
