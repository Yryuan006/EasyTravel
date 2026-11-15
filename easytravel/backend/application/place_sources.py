"""Read-only place port; not wired into generation, confirmation or HTTP."""
from typing import Protocol

from ..domain.places import ApplicableScope, PlaceCandidate, PlaceFacts, PlaceQuery, ResourceRef, SourceResult


class PlaceSource(Protocol):
    async def search(self, query: PlaceQuery) -> SourceResult[PlaceCandidate]: ...
    async def detail(self, resource: ResourceRef, scope: ApplicableScope) -> SourceResult[PlaceFacts]: ...
