"""Preparation ports/configuration; no live network implementation or credentials."""
from typing import Literal, Protocol

from ..domain.places import ApplicableScope, PlaceContract, ResourceRef
from ..domain.place_search_v2 import PlaceDetailResult, PlaceSearchPage, PlaceSearchRequest


class LivePlaceSourceConfig(PlaceContract):
    provider: Literal["amap_poi_v5"] = "amap_poi_v5"
    api_version: Literal["v5"] = "v5"
    environment: Literal["live"] = "live"
    host: Literal["restapi.amap.com"] = "restapi.amap.com"
    search_path: Literal["/v5/place/text"] = "/v5/place/text"
    detail_path: Literal["/v5/place/detail"] = "/v5/place/detail"
    response_crs: Literal["GCJ-02"] | None = "GCJ-02"
    offline_parsing_enabled: bool = False
    # No switch, key field or transport implementation can enable network here.
    live_enabled: Literal[False] = False
    execution_mode: Literal["offline_fake_only"] = "offline_fake_only"


class LivePlaceSource(Protocol):
    async def search(self, request: PlaceSearchRequest) -> PlaceSearchPage: ...
    async def detail(self, resource: ResourceRef, scope: ApplicableScope) -> PlaceDetailResult: ...
