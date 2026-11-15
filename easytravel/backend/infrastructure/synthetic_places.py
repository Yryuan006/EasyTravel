"""Entirely fabricated offline catalogue. No transport, keys or environment lookup."""
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, field_validator, model_validator

from ..domain.places import (
    ApplicableScope, Coordinates, EntranceIdentity, Identifier, PlaceCandidate,
    PlaceContract, PlaceFacts, PlaceQuery, ResourceRef, SourceError, SourceMetadata,
    SourceReference, SourceResult, Text,
)

PROVIDER = "easytravel_synthetic_places_v1"
FIXTURE = Path(__file__).with_name("synthetic_places.json")


class SyntheticPlaceRecord(PlaceContract):
    synthetic: Literal[True]
    # Local fixture keys are server catalogue keys, not supplier or model IDs.
    site_key: Identifier
    provider_resource_id: Identifier
    name: Text
    city: Text
    district: Text | None
    address: Text | None
    address_complete: bool
    category: Text
    coordinates: Coordinates | None
    entrance: EntranceIdentity | None

    @field_validator("name", "city")
    @classmethod
    def fabricated_label(cls, value):
        if not value.startswith("合成"):
            raise ValueError("synthetic labels required")
        return value

    @model_validator(mode="after")
    def address_presence(self):
        if self.address is None and self.address_complete:
            raise ValueError("missing address cannot be complete")
        return self


class SyntheticCatalog(PlaceContract):
    fixture_kind: Literal["fabricated_not_supplier_recording"]
    records: Annotated[tuple[SyntheticPlaceRecord, ...], Field(min_length=10, max_length=100)]

    @model_validator(mode="after")
    def stable_local_identity(self):
        sites, seen = {}, set()
        for record in self.records:
            location = (record.city, record.district, record.name)
            if record.site_key in sites and sites[record.site_key] != location:
                raise ValueError("local site cannot cross identity scope")
            sites[record.site_key] = location
            candidate_key = (record.site_key, record.provider_resource_id, record.entrance)
            if candidate_key in seen:
                raise ValueError("duplicate fixture candidate")
            seen.add(candidate_key)
        return self


class SyntheticPlaceSource:
    def __init__(self, catalogue: SyntheticCatalog | None = None, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 failure: Literal["timeout", "unavailable", "unsupported", "schema_changed"] | None = None):
        if failure not in (None, "timeout", "unavailable", "unsupported", "schema_changed"):
            raise ValueError("unsupported synthetic failure")
        if catalogue is None:
            raw = FIXTURE.read_bytes()
            if len(raw) > 65536:
                raise ValueError("catalogue too large")
            catalogue = SyntheticCatalog.model_validate_json(raw)
        # Revalidate even a model_copy/model_construct supplied by test code.
        self._catalogue = SyntheticCatalog.model_validate_json(catalogue.model_dump_json())
        self._clock, self._failure = clock, failure
        bindings: dict[str, set[tuple]] = {}
        for record in self._catalogue.records:
            bindings.setdefault(record.provider_resource_id, set()).add((record.site_key, record.city, record.district))
        self._collisions = {key for key, values in bindings.items() if len(values) > 1}

    def _source(self):
        return SourceMetadata(provider=PROVIDER, environment="synthetic", observed_at=self._clock())

    def _normalize(self, record: SyntheticPlaceRecord) -> PlaceCandidate:
        missing = []
        if record.district is None:
            missing.append("district")
        if not record.address_complete:
            missing.append("address")
        if record.coordinates is None:
            missing.append("coordinates")
        elif record.coordinates.crs is None:
            missing.append("coordinates.crs")
        if record.entrance is None:
            missing.append("entrance")
        return PlaceCandidate(
            resource=ResourceRef(internal_resource_id="syn_" + uuid5(NAMESPACE_URL, "easytravel-synthetic/" + record.site_key).hex,
                provider=PROVIDER, provider_resource_id=record.provider_resource_id, city=record.city, district=record.district),
            name=record.name, address=record.address, address_complete=record.address_complete,
            category=record.category, coordinates=record.coordinates, entrance=record.entrance,
            source_reference=SourceReference(reference="synthetic://places/" + record.site_key),
            missing_fields=tuple(missing), disambiguation=("合成测试数据，不能用于真实旅行",),
        )

    def _result(self, records, scope, limit, *, detail=False, error=None):
        schema = SourceResult[PlaceFacts] if detail else SourceResult[PlaceCandidate]
        if error is None:
            error = self._failure
        if error is None and any(r.provider_resource_id in self._collisions for r in records):
            error = "schema_changed"
        if error is not None:
            return schema(source=self._source(), scope=scope, outcome=error, items=(), total_matches=0,
                error=SourceError(code=error, retryable=error in ("timeout", "unavailable")))
        items = tuple(self._normalize(r) for r in records[:limit])
        if detail:
            items = tuple(PlaceFacts(**i.model_dump(), applicable_scope=scope) for i in items)
        outcome = "not_found" if not records else "ambiguous_resource" if len(records) > 1 else "partial" if items[0].missing_fields else "ok"
        return schema(source=self._source(), scope=scope, outcome=outcome, items=items, total_matches=len(records),
            error=SourceError(code=outcome, retryable=False) if outcome in ("not_found", "ambiguous_resource") else None)

    async def search(self, query: PlaceQuery) -> SourceResult[PlaceCandidate]:
        query = PlaceQuery.model_validate_json(query.model_dump_json())
        records = tuple(r for r in self._catalogue.records if query.keyword in r.name
            and r.city == query.city and (query.district is None or r.district == query.district)
            and (query.category is None or r.category == query.category)
            and (query.entrance_id is None or (r.entrance is not None and r.entrance.entrance_id == query.entrance_id)))
        return self._result(records, query.scope, query.limit)

    async def detail(self, resource: ResourceRef, scope: ApplicableScope) -> SourceResult[PlaceFacts]:
        resource = ResourceRef.model_validate_json(resource.model_dump_json())
        scope = ApplicableScope.model_validate_json(scope.model_dump_json())
        if resource.provider != PROVIDER:
            return self._result((), scope, 20, detail=True, error="unsupported")
        records = tuple(r for r in self._catalogue.records if self._normalize(r).resource == resource)
        return self._result(records, scope, 20, detail=True)
