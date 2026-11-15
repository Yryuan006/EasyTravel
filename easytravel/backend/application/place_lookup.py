"""Offline boundary: check registered provenance and issue server-owned test receipts.

Not a business evidence repository and not registered in the composition root.
Only the server-selected synthetic binding is supported in M2-01a.
"""
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, UUID4, ValidationError, model_validator

from .place_sources import PlaceSource
from ..domain.places import (
    ApplicableScope, IdentityAssessment, PlaceCandidate, PlaceContract, PlaceQuery,
    ResourceRef, SourceMetadata, SourceReference, SourceResult, assess_identity,
)

# Server configuration, never obtained from adapter text or request JSON.
SYNTHETIC_PROVIDER = "easytravel_synthetic_places_v1"


class PlaceObservation(PlaceContract):
    evidence_id: UUID4
    schema_version: Literal["place-observation-v1"] = "place-observation-v1"
    fact_kind: Literal["PLACE_IDENTITY"] = "PLACE_IDENTITY"
    source: SourceMetadata
    resource: ResourceRef
    applicable_scope: ApplicableScope
    source_reference: SourceReference
    claim_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    verification_state: Literal["NON_LIVE"] = "NON_LIVE"
    usable_for_real_trip: Literal[False] = False

    @model_validator(mode="after")
    def offline_only(self):
        if (self.source.environment != "synthetic" or self.source.provider != SYNTHETIC_PROVIDER
                or self.resource.provider != SYNTHETIC_PROVIDER):
            raise ValueError("only registered synthetic observations supported")
        return self


class PlaceLookupResult(PlaceContract):
    source_result: SourceResult[PlaceCandidate]
    identity: IdentityAssessment
    observations: Annotated[tuple[PlaceObservation, ...], Field(max_length=20)]


class PlaceBoundaryError(ValueError):
    """Safe diagnostic, never echo supplier payloads or exception strings."""


class PlaceLookup:
    def __init__(self, source: PlaceSource, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._source, self._clock = source, clock

    async def search(self, query: PlaceQuery) -> PlaceLookupResult:
        query = PlaceQuery.model_validate_json(query.model_dump_json())
        # Transport exceptions are classified, not converted into made-up candidates.
        try:
            result = await self._source.search(query)
        except TimeoutError:
            raise PlaceBoundaryError("timeout") from None
        except Exception:
            raise PlaceBoundaryError("unavailable") from None
        try:
            result = SourceResult[PlaceCandidate].model_validate_json(result.model_dump_json())
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise PlaceBoundaryError("schema_changed") from None
        now = self._clock()
        if now.utcoffset() is None:
            raise PlaceBoundaryError("aware_clock_required")
        if (result.source.provider != SYNTHETIC_PROVIDER or result.source.environment != "synthetic"
                or result.source.observed_at > now):
            raise PlaceBoundaryError("source_binding_mismatch")
        if len(result.items) > query.limit:
            raise PlaceBoundaryError("result_limit_exceeded")
        if any(not i.name.startswith("合成") or not i.resource.city.startswith("合成")
               or not i.source_reference.reference.startswith("synthetic://places/") for i in result.items):
            raise PlaceBoundaryError("source_binding_mismatch")
        identity = assess_identity(query, result)
        observations = () if identity.status == "REJECTED" else tuple(
            PlaceObservation(evidence_id=uuid4(), source=result.source, resource=item.resource,
                applicable_scope=result.scope, source_reference=item.source_reference,
                claim_digest=hashlib.sha256(item.model_dump_json().encode()).hexdigest())
            for item in result.items
        )
        return PlaceLookupResult(source_result=result, identity=identity, observations=observations)
