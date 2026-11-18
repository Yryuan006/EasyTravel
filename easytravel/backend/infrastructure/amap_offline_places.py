"""Amap v5 field-shape parser, exercised with FABRICATED FakeTransport only.

There is intentionally no HTTP request construction, credential loading or real transport.
"""
from collections.abc import Callable
from datetime import datetime, timezone
import json
import re
from typing import Annotated

from pydantic import BeforeValidator, Field, ValidationError

from ..application.live_place_ports import LivePlaceSourceConfig
from ..domain.places import ApplicableScope, Coordinates, Identifier, PlaceContract, ResourceRef, Text
from ..domain.place_search_v2 import (
    ChildPlaceHint, OfflineSourceStamp, PlaceDetailResult, PlaceHit, PlaceSearchPage,
    PlaceSearchRequest, PlaceSourceFailure, SearchCoverage,
)
from .fake_place_transport import FakeTransport


def empty(value):
    return None if value is None or value == "" or value == [] else value


OptionalText = Annotated[Text | None, BeforeValidator(empty)]
OptionalID = Annotated[Identifier | None, BeforeValidator(empty)]


class RawBusiness(PlaceContract):
    alias: OptionalText = None


class RawChild(PlaceContract):
    id: Identifier
    name: Text
    location: OptionalText = None
    address: OptionalText = None
    subtype: OptionalText = None
    typecode: OptionalID = None


class RawPoi(PlaceContract):
    id: Identifier
    name: Text
    cityname: Text
    adname: OptionalText = None
    adcode: OptionalID = None
    address: OptionalText = None
    location: OptionalText = None
    type: Text
    typecode: OptionalID = None
    pname: OptionalText = None
    pcode: OptionalID = None
    citycode: OptionalID = None
    parent: OptionalID = None
    children: Annotated[tuple[RawChild, ...], Field(max_length=20)] = ()
    business: RawBusiness | None = None


class RawReply(PlaceContract):
    status: Annotated[str, Field(pattern=r"^[01]$")]
    infocode: Annotated[str, Field(pattern=r"^[0-9]{5}$")]
    info: Annotated[str, Field(max_length=200)]
    count: Annotated[str, Field(pattern=r"^[0-9]{1,4}$")] | None = None
    pois: Annotated[tuple[RawPoi, ...], Field(max_length=25)] = ()


class ParseFault(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_reply(body: str) -> RawReply:
    # Reject duplicate JSON keys/nonfinite values before Pydantic's JSON parsing.
    json.loads(body, object_pairs_hook=unique_object,
               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    reply = RawReply.model_validate_json(body)
    if reply.status != "1" or reply.infocode != "10000":
        if reply.infocode in {"10003", "10004", "10010", "10014", "10015"}:
            raise ParseFault("quota_exceeded")
        if reply.infocode in {"10001", "10002", "10005", "10006", "10007", "10008", "10009", "10013"}:
            raise ParseFault("auth_denied")
        if reply.infocode == "10012":
            raise ParseFault("forbidden")
        raise ParseFault("unavailable")
    return reply


def normalize(poi: RawPoi, config: LivePlaceSourceConfig) -> PlaceHit:
    child_ids = [child.id for child in poi.children]
    if poi.parent == poi.id or poi.id in child_ids or len(set(child_ids)) != len(child_ids):
        raise ParseFault("identity_conflict")
    coordinates = None
    if poi.location is not None:
        if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?,[+-]?[0-9]+(?:\.[0-9]+)?", poi.location):
            raise ValueError("invalid coordinate shape")
        longitude, latitude = (float(v) for v in poi.location.split(","))
        coordinates = Coordinates(longitude=longitude, latitude=latitude, crs=config.response_crs)
    return PlaceHit(provider_resource_id=poi.id, name=poi.name,
        aliases=(poi.business.alias,) if poi.business and poi.business.alias else (),
        city=poi.cityname, district=poi.adname, administrative_code=poi.adcode,
        address=poi.address, category=poi.type, coordinates=coordinates, parent_id=poi.parent,
        children=tuple(ChildPlaceHint(provider_resource_id=c.id, name=c.name) for c in poi.children))


class AmapOfflinePlaceSource:
    def __init__(self, config: LivePlaceSourceConfig | None = None, transport: FakeTransport | None = None,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._config = LivePlaceSourceConfig.model_validate_json(config.model_dump_json()) if config is not None else None
        self._transport, self._clock = transport, clock

    def _stamp(self):
        return OfflineSourceStamp(observed_at=self._clock())

    def _guard(self):
        if self._config is None:
            return PlaceSourceFailure(code="not_configured")
        if not self._config.offline_parsing_enabled:
            return PlaceSourceFailure(code="live_disabled")
        # Protocol conformance alone is NOT permission to plug in a networking client.
        if type(self._transport) is not FakeTransport:
            return PlaceSourceFailure(code="transport_not_permitted")
        return None

    async def _read(self, action, supplier_id=None):
        try:
            response = await self._transport.exchange(action, supplier_id)
        except TimeoutError:
            raise ParseFault("timeout") from None
        except Exception:
            raise ParseFault("unavailable") from None
        if response.status_code == 401:
            raise ParseFault("auth_denied")
        if response.status_code == 403:
            raise ParseFault("forbidden")
        if response.status_code == 429:
            raise ParseFault("quota_exceeded")
        if response.status_code >= 500:
            raise ParseFault("unavailable")
        if response.status_code != 200:
            raise ParseFault("unsupported")
        return parse_reply(response.body)

    async def search(self, request: PlaceSearchRequest) -> PlaceSearchPage:
        request = PlaceSearchRequest.model_validate_json(request.model_dump_json())
        error, hits, received = self._guard(), (), 0
        if error is None and (len(request.query.keyword) > 80 or request.query.entrance_id is not None):
            error = PlaceSourceFailure(code="unsupported")
        if error is None:
            try:
                reply = await self._read("search")
                if reply.count is None or int(reply.count) != len(reply.pois) or len(reply.pois) > request.page_size:
                    raise ValueError("page count mismatch")
                parsed = tuple(normalize(p, self._config) for p in reply.pois)
                if len({p.provider_resource_id for p in parsed}) != len(parsed):
                    raise ParseFault("identity_conflict")
                if any(p.city != request.query.city
                       or (request.query.district is not None and p.district != request.query.district)
                       or (request.query.category is not None and p.category != request.query.category)
                       for p in parsed):
                    raise ParseFault("query_binding_mismatch")
                received, hits = len(parsed), parsed[:request.query.limit]
            except ParseFault as fault:
                error = PlaceSourceFailure(code=str(fault), retryable=str(fault) in {"timeout", "unavailable"})
            except (ValueError, TypeError, RecursionError, ValidationError):
                error = PlaceSourceFailure(code="schema_changed")
        coverage = SearchCoverage(request=request, received_count=received, returned_count=len(hits),
            truncated_count=received-len(hits), has_unsearched_results=None)
        # Amap count is a page count. Even an empty/one-item page has unknown exhaustive coverage.
        return PlaceSearchPage(source=self._stamp(), coverage=coverage, hits=hits, error=error)

    async def detail(self, resource: ResourceRef, scope: ApplicableScope) -> PlaceDetailResult:
        resource = ResourceRef.model_validate_json(resource.model_dump_json())
        scope = ApplicableScope.model_validate_json(scope.model_dump_json())
        error, hit = self._guard(), None
        if error is None and resource.provider != self._config.provider:
            error = PlaceSourceFailure(code="query_binding_mismatch")
        if error is None:
            try:
                reply = await self._read("detail", resource.provider_resource_id)
                if not reply.pois:
                    raise ParseFault("not_found")
                if len(reply.pois) != 1:
                    raise ParseFault("identity_conflict")
                parsed = normalize(reply.pois[0], self._config)
                if (parsed.provider_resource_id != resource.provider_resource_id or parsed.city != resource.city
                        or parsed.district != resource.district):
                    raise ParseFault("identity_conflict")
                hit = parsed
            except ParseFault as fault:
                error = PlaceSourceFailure(code=str(fault), retryable=str(fault) in {"timeout", "unavailable"})
            except (ValueError, TypeError, RecursionError, ValidationError):
                error = PlaceSourceFailure(code="schema_changed")
        return PlaceDetailResult(source=self._stamp(), scope=scope, requested_id=resource.provider_resource_id, hit=hit, error=error)
