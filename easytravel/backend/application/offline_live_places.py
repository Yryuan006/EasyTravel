"""Explicit search selection + detail review. In-process, fabricated preparation only."""
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import UUID4

from .live_place_ports import LivePlaceSource
from ..domain.places import Identifier, PlaceContract, ResourceRef
from ..domain.place_search_v2 import (
    DetailReview, LiveUnverifiedObservation, PlaceDetailResult, PlaceHit, PlaceSearchPage,
    PlaceSearchRequest, PlaceSourceFailure, SearchSession,
)


class DetailSelection(PlaceContract):
    search_id: UUID4
    provider_resource_id: Identifier


class ResourceConflict(ValueError):
    pass


class TrialResourceRegistry:
    """Local experiment mappings, no supplier ID/name-derived internal identity."""
    def __init__(self):
        self._entries: dict[tuple[str, str, str], tuple[PlaceHit, ResourceRef]] = {}

    @property
    def size(self):
        return len(self._entries)

    def register(self, hit: PlaceHit) -> ResourceRef:
        key = ("amap_poi_v5", "v5", hit.provider_resource_id)
        previous = self._entries.get(key)
        if previous:
            if previous[0] != hit:
                raise ResourceConflict("identity_conflict")
            return previous[1]
        if len(self._entries) >= 100:
            raise ResourceConflict("unsupported")
        resource = ResourceRef(internal_resource_id="trial_"+uuid4().hex, provider=key[0],
            provider_resource_id=key[2], city=hit.city, district=hit.district)
        self._entries[key] = (hit, resource)
        return resource


def reject(code: str) -> DetailReview:
    return DetailReview(status="REJECTED", observation=None, error=PlaceSourceFailure(code=code))


class OfflineLivePlaceLookup:
    def __init__(self, source: LivePlaceSource, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._source, self._clock = source, clock
        self._sessions: dict[UUID4, SearchSession] = {}
        self._registry = TrialResourceRegistry()

    @property
    def resource_count(self):
        return self._registry.size

    def _time_valid(self, stamp):
        now = self._clock()
        return now.utcoffset() is not None and stamp.observed_at <= now

    async def search(self, request: PlaceSearchRequest) -> SearchSession:
        request = PlaceSearchRequest.model_validate_json(request.model_dump_json())
        # Source exceptions contain no safe facts. Do not expose supplier text.
        try:
            returned = await self._source.search(request)
            page = PlaceSearchPage.model_validate_json(returned.model_dump_json())
        except Exception:
            raise ValueError("invalid_source_result") from None
        if page.coverage.request != request or not self._time_valid(page.source):
            raise ValueError("source_binding_mismatch")
        if any(h.city != request.query.city
               or (request.query.district is not None and h.district != request.query.district)
               or (request.query.category is not None and h.category != request.query.category)
               for h in page.hits):
            raise ValueError("query_binding_mismatch")
        session = SearchSession(search_id=uuid4(), page=page)
        if len(self._sessions) >= 100:
            raise ValueError("search_session_limit")
        # Never infer uniqueness/selection, including complete one-item coverage.
        self._sessions[session.search_id] = session
        return session

    async def review_detail(self, selection: DetailSelection) -> DetailReview:
        selection = DetailSelection.model_validate_json(selection.model_dump_json())
        session = self._sessions.get(selection.search_id)
        if session is None or session.page.error is not None:
            return reject("not_found")
        chosen = next((h for h in session.page.hits if h.provider_resource_id == selection.provider_resource_id), None)
        if chosen is None:
            return reject("not_found")
        provisional = ResourceRef(internal_resource_id="pending_"+uuid4().hex,
            provider=session.page.source.provider, provider_resource_id=chosen.provider_resource_id,
            city=chosen.city, district=chosen.district)
        try:
            returned = await self._source.detail(provisional, session.page.coverage.request.query.scope)
            detail = PlaceDetailResult.model_validate_json(returned.model_dump_json())
        except Exception:
            return reject("schema_changed")
        if (detail.scope != session.page.coverage.request.query.scope or not self._time_valid(detail.source)
                or detail.source.provider != session.page.source.provider
                or detail.source.api_version != session.page.source.api_version
                or detail.source.environment != session.page.source.environment
                or detail.requested_id != chosen.provider_resource_id):
            return reject("query_binding_mismatch")
        if detail.error:
            return DetailReview(status="REJECTED", observation=None, error=detail.error)
        hit = detail.hit
        # An ID is a locator, not proof that the entity has not changed.
        if (hit.city != chosen.city or hit.district != chosen.district or hit.administrative_code != chosen.administrative_code
                or hit.parent_id != chosen.parent_id
                or (hit.name != chosen.name and hit.name not in chosen.aliases and chosen.name not in hit.aliases)
                or (chosen.coordinates is not None and hit.coordinates is not None and chosen.coordinates != hit.coordinates)):
            return reject("identity_conflict")
        try:
            resource = self._registry.register(hit)
        except ResourceConflict as error:
            return reject(str(error))
        evidence_id = uuid4()
        observation = LiveUnverifiedObservation(evidence_id=evidence_id, source=detail.source,
            resource=resource, query_scope=detail.scope, fact=hit,
            source_reference="offline-observation:"+str(evidence_id))
        return DetailReview(status="DETAIL_REVIEWED_UNVERIFIED", observation=observation, error=None)
