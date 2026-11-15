"""Place identity only. These DTOs confer no opening, price or route authority."""
from datetime import date, datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


class PlaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


def nonblank(value: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError("nonblank trimmed text required")
    return value


Text = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(nonblank)]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Environment = Literal["synthetic", "sandbox", "replay", "live"]
CRS = Literal["GCJ-02", "BD-09", "WGS84"]
ErrorCode = Literal["not_found", "ambiguous_resource", "unsupported", "unavailable", "timeout", "schema_changed"]


class ApplicableScope(PlaceContract):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("invalid applicable dates")
        return self


class PlaceQuery(PlaceContract):
    keyword: Annotated[str, Field(min_length=1, max_length=100), AfterValidator(nonblank)]
    city: Text
    district: Text | None = None
    category: Text | None = None
    entrance_id: Identifier | None = None
    scope: ApplicableScope
    limit: Annotated[int, Field(ge=1, le=20)] = 10


class ResourceRef(PlaceContract):
    internal_resource_id: Identifier
    provider: Identifier
    provider_resource_id: Identifier
    city: Text
    district: Text | None


class Coordinates(PlaceContract):
    # None represents a missing CRS, never permission to infer/convert it.
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    crs: CRS | None

    @field_validator("longitude", "latitude", mode="before")
    @classmethod
    def numeric_only(cls, value):
        if type(value) not in (int, float):
            raise ValueError("numeric coordinates required")
        return value


class EntranceIdentity(PlaceContract):
    entrance_id: Identifier
    label: Text


class SourceReference(PlaceContract):
    reference: Annotated[str, Field(min_length=1, max_length=300), AfterValidator(nonblank)]


class SourceMetadata(PlaceContract):
    provider: Identifier
    environment: Environment
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("aware observation required")
        return value


MissingField = Literal["district", "address", "coordinates", "coordinates.crs", "entrance"]


class PlaceCandidate(PlaceContract):
    resource: ResourceRef
    name: Text
    address: Text | None
    address_complete: bool
    category: Text
    coordinates: Coordinates | None
    entrance: EntranceIdentity | None
    source_reference: SourceReference
    missing_fields: Annotated[tuple[MissingField, ...], Field(max_length=5)]
    disambiguation: Annotated[tuple[Text, ...], Field(max_length=5)] = ()

    @model_validator(mode="after")
    def explicit_missing(self):
        expected = []
        if self.resource.district is None:
            expected.append("district")
        if not self.address_complete:
            expected.append("address")
        if self.address is None and self.address_complete:
            raise ValueError("missing address cannot be complete")
        if self.coordinates is None:
            expected.append("coordinates")
        elif self.coordinates.crs is None:
            expected.append("coordinates.crs")
        if self.entrance is None:
            expected.append("entrance")
        if tuple(expected) != self.missing_fields:
            raise ValueError("missing fields must describe actual gaps")
        return self


class PlaceFacts(PlaceCandidate):
    fact_kind: Literal["PLACE_IDENTITY"] = "PLACE_IDENTITY"
    applicable_scope: ApplicableScope


class SourceError(PlaceContract):
    code: ErrorCode
    retryable: bool


T = TypeVar("T", bound=PlaceCandidate)


class SourceResult(PlaceContract, Generic[T]):
    source: SourceMetadata
    scope: ApplicableScope
    outcome: Literal["ok", "partial", "not_found", "ambiguous_resource", "unsupported", "unavailable", "timeout", "schema_changed"]
    items: Annotated[tuple[T, ...], Field(max_length=20)]
    total_matches: Annotated[int, Field(ge=0, le=1000)]
    error: SourceError | None

    @model_validator(mode="after")
    def coherent(self):
        if self.total_matches < len(self.items):
            raise ValueError("invalid match count")
        if self.outcome in ("ok", "partial"):
            if not self.items or self.total_matches != 1 or self.error is not None:
                raise ValueError("single result required")
            if (self.outcome == "partial") != bool(self.items[0].missing_fields):
                raise ValueError("partial outcome must match gaps")
        else:
            if self.error is None or self.error.code != self.outcome:
                raise ValueError("explicit matching error required")
            if self.outcome == "ambiguous_resource":
                if self.total_matches < 2 or not self.items:
                    raise ValueError("ambiguity requires multiple matches")
            elif self.items or self.total_matches:
                raise ValueError("failure cannot contain invented facts")
        if any(i.resource.provider != self.source.provider for i in self.items):
            raise ValueError("provider binding mismatch")
        if any(isinstance(i, PlaceFacts) and i.applicable_scope != self.scope for i in self.items):
            raise ValueError("fact scope mismatch")
        keys = [(i.resource, i.entrance) for i in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate candidates")
        bindings = {}
        internal_bindings = {}
        for item in self.items:
            key = (item.resource.provider, item.resource.provider_resource_id)
            if key in bindings and bindings[key] != item.resource:
                raise ValueError("provider resource collision")
            bindings[key] = item.resource
            internal_key = item.resource.internal_resource_id
            location = (item.resource.city, item.resource.district, item.name)
            if internal_key in internal_bindings and internal_bindings[internal_key] != location:
                raise ValueError("internal resource binding mismatch")
            internal_bindings[internal_key] = location
        return self


class IdentityAssessment(PlaceContract):
    status: Literal["SELECTED", "INCOMPLETE", "AMBIGUOUS", "NOT_FOUND", "UNAVAILABLE", "REJECTED"]
    selected: ResourceRef | None
    selected_entrance: EntranceIdentity | None = None
    issues: Annotated[tuple[Text, ...], Field(max_length=10)]
    # This entire slice is offline. Matching identity never proves a real trip fact.
    usable_for_real_trip: Literal[False] = False


def assess_identity(query: PlaceQuery, result: SourceResult[PlaceCandidate]) -> IdentityAssessment:
    if result.scope != query.scope or any(
        query.keyword not in i.name or i.resource.city != query.city
        or (query.district is not None and i.resource.district != query.district)
        or (query.category is not None and i.category != query.category)
        or (query.entrance_id is not None and (i.entrance is None or i.entrance.entrance_id != query.entrance_id))
        for i in result.items
    ):
        return IdentityAssessment(status="REJECTED", selected=None, issues=("query_binding_mismatch",))
    if result.outcome == "not_found":
        return IdentityAssessment(status="NOT_FOUND", selected=None, issues=("not_found",))
    if result.outcome == "ambiguous_resource":
        return IdentityAssessment(status="AMBIGUOUS", selected=None, issues=("explicit_selection_required",))
    if result.error:
        return IdentityAssessment(status="UNAVAILABLE", selected=None, issues=(result.error.code,))
    item = result.items[0]
    if item.missing_fields:
        return IdentityAssessment(status="INCOMPLETE", selected=None, issues=item.missing_fields)
    return IdentityAssessment(status="SELECTED", selected=item.resource, selected_entrance=item.entrance, issues=())


class CoordinateBoundaryError(ValueError):
    pass


def require_comparable_coordinates(first: PlaceCandidate, second: PlaceCandidate) -> tuple[Coordinates, Coordinates]:
    """Only a precondition check, no distance, conversion or route verification."""
    if first.resource.city != second.resource.city or first.resource.district != second.resource.district:
        raise CoordinateBoundaryError("administrative_scope_mismatch")
    if first.resource.district is None:
        raise CoordinateBoundaryError("district_missing")
    if first.entrance is None or second.entrance is None:
        raise CoordinateBoundaryError("entrance_unknown")
    if first.coordinates is None or second.coordinates is None:
        raise CoordinateBoundaryError("coordinates_missing")
    if first.coordinates.crs is None or second.coordinates.crs is None:
        raise CoordinateBoundaryError("crs_unknown")
    if first.coordinates.crs != second.coordinates.crs:
        raise CoordinateBoundaryError("crs_mismatch")
    return first.coordinates, second.coordinates
