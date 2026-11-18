"""V2 search/observation contracts. Independent of the complete synthetic V1 catalogue."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, UUID4, field_validator, model_validator

from .places import ApplicableScope, Coordinates, Identifier, PlaceContract, PlaceQuery, ResourceRef, Text

Count = Annotated[int, Field(ge=0, le=1000)]


class PlaceSearchRequest(PlaceContract):
    schema_version: Literal["place-search-v2"] = "place-search-v2"
    query: PlaceQuery
    page: Annotated[int, Field(ge=1, le=10)] = 1
    page_size: Annotated[int, Field(ge=1, le=20)] = 10


class SearchCoverage(PlaceContract):
    schema_version: Literal["search-coverage-v1"] = "search-coverage-v1"
    request: PlaceSearchRequest
    received_count: Count
    returned_count: Count
    has_unsearched_results: bool | None
    truncated_count: Count

    @model_validator(mode="after")
    def consistent(self):
        if (self.received_count > self.request.page_size or self.returned_count > self.request.query.limit
                or self.returned_count + self.truncated_count != self.received_count):
            raise ValueError("inconsistent search coverage")
        if self.request.page > 1 and self.has_unsearched_results is False:
            raise ValueError("earlier pages are not covered")
        return self

    @property
    def coverage_known(self) -> bool:
        return self.has_unsearched_results is not None

    @property
    def complete(self) -> bool:
        return self.has_unsearched_results is False and self.truncated_count == 0 and self.request.page == 1


class ChildPlaceHint(PlaceContract):
    provider_resource_id: Identifier
    name: Text
    # A parent/child relation is not an entrance authorization or geometry.
    relation: Literal["child_poi_hint"] = "child_poi_hint"


class PlaceHit(PlaceContract):
    provider_resource_id: Identifier
    name: Text
    aliases: Annotated[tuple[Text, ...], Field(max_length=5)] = ()
    city: Text
    district: Text | None
    administrative_code: Identifier | None
    address: Text | None
    address_complete: Literal[False] = False
    category: Text
    coordinates: Coordinates | None
    coordinate_role: Literal["poi_position"] = "poi_position"
    entrance: None = None
    parent_id: Identifier | None
    children: Annotated[tuple[ChildPlaceHint, ...], Field(max_length=20)] = ()

    @property
    def missing_or_unverified_fields(self) -> tuple[str, ...]:
        fields = ["address_completeness", "entrance"]
        if self.address is None:
            fields.append("address")
        if self.district is None:
            fields.append("district")
        if self.coordinates is None:
            fields.append("coordinates")
        elif self.coordinates.crs is None:
            fields.append("coordinates.crs")
        return tuple(fields)


class OfflineSourceStamp(PlaceContract):
    provider: Literal["amap_poi_v5"] = "amap_poi_v5"
    api_version: Literal["v5"] = "v5"
    target_environment: Literal["live"] = "live"
    environment: Literal["synthetic"] = "synthetic"
    data_origin: Literal["fabricated_fake_transport"] = "fabricated_fake_transport"
    observed_at: datetime
    provider_updated_at: None = None

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("aware observation time required")
        return value


FailureCode = Literal[
    "not_configured", "live_disabled", "transport_not_permitted", "unsupported",
    "schema_changed", "auth_denied", "forbidden", "quota_exceeded", "timeout",
    "unavailable", "identity_conflict", "query_binding_mismatch", "not_found",
]


class PlaceSourceFailure(PlaceContract):
    code: FailureCode
    retryable: bool = False


class PlaceSearchPage(PlaceContract):
    schema_version: Literal["place-search-page-v2"] = "place-search-page-v2"
    source: OfflineSourceStamp
    coverage: SearchCoverage
    hits: Annotated[tuple[PlaceHit, ...], Field(max_length=20)]
    error: PlaceSourceFailure | None
    requires_explicit_selection: Literal[True] = True

    @model_validator(mode="after")
    def consistent(self):
        if self.coverage.returned_count != len(self.hits):
            raise ValueError("returned count mismatch")
        if self.error and (self.hits or self.coverage.received_count):
            raise ValueError("failure cannot contain candidates")
        if len({h.provider_resource_id for h in self.hits}) != len(self.hits):
            raise ValueError("duplicate supplier identity")
        return self


class PlaceDetailResult(PlaceContract):
    schema_version: Literal["place-detail-v2"] = "place-detail-v2"
    source: OfflineSourceStamp
    scope: ApplicableScope
    requested_id: Identifier
    hit: PlaceHit | None
    error: PlaceSourceFailure | None

    @model_validator(mode="after")
    def consistent(self):
        if (self.hit is None) == (self.error is None):
            raise ValueError("detail requires one fact or an error")
        if self.hit and self.hit.provider_resource_id != self.requested_id:
            raise ValueError("detail identity mismatch")
        return self


class SearchSession(PlaceContract):
    search_id: UUID4
    page: PlaceSearchPage


class LiveUnverifiedObservation(PlaceContract):
    """Live-shaped boundary exercised ONLY with fabricated transport in this phase."""
    schema_version: Literal["live-place-observation-v1"] = "live-place-observation-v1"
    evidence_id: UUID4
    source: OfflineSourceStamp
    resource: ResourceRef
    query_scope: ApplicableScope
    fact: PlaceHit
    source_reference: Annotated[str, Field(pattern=r"^offline-observation:[0-9a-f-]{36}$")]
    verification_state: Literal["SOURCE_UNVERIFIED"] = "SOURCE_UNVERIFIED"
    applicable_date_verified: Literal[False] = False
    opening_state: Literal["UNKNOWN"] = "UNKNOWN"
    price_state: Literal["UNKNOWN"] = "UNKNOWN"
    route_state: Literal["UNKNOWN"] = "UNKNOWN"
    usable_for_real_trip: Literal[False] = False
    usable_in_confirmed_snapshot: Literal[False] = False

    @model_validator(mode="after")
    def binding(self):
        if (self.resource.provider != self.source.provider
                or self.resource.provider_resource_id != self.fact.provider_resource_id
                or self.resource.city != self.fact.city or self.resource.district != self.fact.district):
            raise ValueError("observation identity mismatch")
        if self.source_reference != "offline-observation:" + str(self.evidence_id):
            raise ValueError("observation reference mismatch")
        return self


class DetailReview(PlaceContract):
    status: Literal["DETAIL_REVIEWED_UNVERIFIED", "REJECTED"]
    observation: LiveUnverifiedObservation | None
    error: PlaceSourceFailure | None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "REJECTED":
            if self.observation is not None or self.error is None:
                raise ValueError("invalid rejected review")
        elif self.observation is None or self.error is not None:
            raise ValueError("invalid reviewed observation")
        return self
