"""Offline fabricated identity contract tests, never business evidence or DB facts."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import socket
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import ValidationError

from easytravel.backend.application.place_lookup import PlaceBoundaryError, PlaceLookup
from easytravel.backend.domain.places import (
    ApplicableScope, CoordinateBoundaryError, Coordinates, PlaceCandidate, PlaceFacts,
    PlaceQuery, ResourceRef, SourceMetadata, SourceResult, assess_identity, require_comparable_coordinates,
)
from easytravel.backend.infrastructure.synthetic_places import (
    FIXTURE, PROVIDER, SyntheticCatalog, SyntheticPlaceSource,
)

NOW = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
SCOPE = ApplicableScope(start_date=date(2026, 12, 1), end_date=date(2026, 12, 3))


def run(coro):
    async def guarded():
        # Enter after asyncio's local wakeup sockets are created; no DNS/TCP during source execution.
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
             patch.object(socket.socket, "connect_ex", side_effect=AssertionError("network forbidden")), \
             patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            return await coro
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(guarded())


def query(keyword="合成唯一展馆", **kwargs):
    return PlaceQuery(keyword=keyword, city="合成甲城", scope=SCOPE, **kwargs)


@pytest.fixture
def source(monkeypatch):
    monkeypatch.delenv("EASYTRAVEL_DATABASE_URL", raising=False)
    monkeypatch.setenv("AMAP_API_KEY", "must-never-be-read")
    return SyntheticPlaceSource(clock=lambda: NOW)


def search(source, keyword="合成唯一展馆", **kwargs):
    return run(source.search(query(keyword, **kwargs)))


def test_all_fifteen_fabricated_records_normalize(source):
    catalogue = SyntheticCatalog.model_validate_json(FIXTURE.read_bytes())
    assert len(catalogue.records) == 15
    assert len({r.site_key for r in catalogue.records}) == 14
    for record in catalogue.records:
        result = search(source, record.name, district=record.district)
        assert result.source.environment == "synthetic"
        assert result.source.provider == PROVIDER
        assert result.source.observed_at == NOW
        assert result.scope == SCOPE
        if record.provider_resource_id == "fixture_collision":
            assert result.outcome == "schema_changed" and not result.items
        else:
            assert any(i.name == record.name for i in result.items)
            for item in result.items:
                assert item.resource.internal_resource_id != item.resource.provider_resource_id
                assert item.source_reference.reference.startswith("synthetic://")
                assert "verification_state" not in item.model_dump()
    print("15 fabricated records / 14 sites normalized or explicitly rejected; no supplier recordings")


def test_unique_identity_and_detail_are_date_scoped(source):
    found = search(source)
    assert found.outcome == "ok"
    selected = assess_identity(query(), found)
    assert selected.status == "SELECTED" and not selected.usable_for_real_trip
    assert selected.selected_entrance.entrance_id == "main"
    detail = run(source.detail(found.items[0].resource, SCOPE))
    assert isinstance(detail.items[0], PlaceFacts)
    assert detail.items[0].applicable_scope == SCOPE
    assert detail.source.observed_at.date() != SCOPE.start_date
    assert set(PlaceFacts.model_fields).isdisjoint({"opening_hours", "price", "route_verified", "verification_state"})


def test_same_name_district_ambiguity_does_not_select_first(source):
    result = search(source, "合成同名花园")
    assert result.outcome == "ambiguous_resource" and result.total_matches == 2
    assert assess_identity(query("合成同名花园"), result).selected is None
    narrowed = search(source, "合成同名花园", district="合成西区")
    assert narrowed.outcome == "ok" and narrowed.items[0].resource.district == "合成西区"
    assert narrowed.items[0].resource != result.items[0].resource


def test_same_name_entrances_need_explicit_choice_and_share_site(source):
    result = search(source, "合成双门展馆")
    assert result.outcome == "ambiguous_resource"
    assert result.items[0].resource.internal_resource_id == result.items[1].resource.internal_resource_id
    assert assess_identity(query("合成双门展馆"), result).status == "AMBIGUOUS"
    chosen = search(source, "合成双门展馆", entrance_id="west")
    assessment = assess_identity(query("合成双门展馆", entrance_id="west"), chosen)
    assert assessment.status == "SELECTED" and assessment.selected_entrance.entrance_id == "west"


def test_truncated_ambiguity_is_not_unique(source):
    result = search(source, "合成同名花园", limit=1)
    assert len(result.items) == 1 and result.total_matches == 2
    assert assess_identity(query("合成同名花园", limit=1), result).status == "AMBIGUOUS"


@pytest.mark.parametrize("change", [dict(keyword="合成另一个馆"), dict(city="合成乙城"), dict(district="合成西区"), dict(category="合成餐饮"), dict(entrance_id="wrong")])
def test_external_result_cannot_cross_query_binding(source, change):
    raw = query().model_dump() | change
    wrong = PlaceQuery.model_validate(raw)
    assert assess_identity(wrong, search(source)).status == "REJECTED"


def test_city_and_full_resource_binding(source):
    first = search(source).items[0]
    other = run(source.search(PlaceQuery(keyword=first.name, city="合成乙城", scope=SCOPE))).items[0]
    assert first.resource.internal_resource_id != other.resource.internal_resource_id
    with pytest.raises(CoordinateBoundaryError, match="administrative_scope_mismatch"):
        require_comparable_coordinates(first, other)
    for change in (dict(city="合成乙城"), dict(internal_resource_id="invented"), dict(district="合成西区")):
        ref = ResourceRef.model_validate(first.resource.model_dump() | change)
        result = run(source.detail(ref, SCOPE))
        assert result.outcome == "not_found" and not result.items
    ref = ResourceRef.model_validate(first.resource.model_dump() | dict(provider="another_provider"))
    assert run(source.detail(ref, SCOPE)).outcome == "unsupported"


@pytest.mark.parametrize("name,field", [
    ("合成残缺地址馆", "address"), ("合成无坐标馆", "coordinates"),
    ("合成无坐标系馆", "coordinates.crs"), ("合成无入口馆", "entrance"), ("合成无区县馆", "district"),
])
def test_partial_is_explicit_and_does_not_select(source, name, field):
    result = search(source, name)
    assert result.outcome == "partial" and field in result.items[0].missing_fields
    assessment = assess_identity(query(name), result)
    assert assessment.status == "INCOMPLETE" and assessment.selected is None


@pytest.mark.parametrize("name,reason", [
    ("合成异坐标馆", "crs_mismatch"), ("合成无坐标系馆", "crs_unknown"),
    ("合成无坐标馆", "coordinates_missing"), ("合成无入口馆", "entrance_unknown"),
])
def test_coordinate_and_entrance_guards_do_not_convert(source, name, reason):
    first, second = search(source).items[0], search(source, name).items[0]
    before = second.model_dump_json()
    with pytest.raises(CoordinateBoundaryError, match=reason):
        require_comparable_coordinates(first, second)
    assert second.model_dump_json() == before


def test_same_crs_precondition_is_not_a_route_result(source):
    first = search(source).items[0]
    second = search(source, "合成双门展馆", entrance_id="east").items[0]
    assert require_comparable_coordinates(first, second) == (first.coordinates, second.coordinates)
    west = search(source, "合成同名花园", district="合成西区").items[0]
    with pytest.raises(CoordinateBoundaryError, match="administrative_scope_mismatch"):
        require_comparable_coordinates(first, west)


@pytest.mark.parametrize("coordinates", [
    dict(longitude=181.0, latitude=2.0, crs="WGS84"),
    dict(longitude=1.0, latitude=-91.0, crs="WGS84"),
    dict(longitude=float("nan"), latitude=2.0, crs="WGS84"),
    dict(longitude=1.0, latitude=float("inf"), crs="WGS84"),
    dict(longitude="1.0", latitude=2.0, crs="WGS84"),
    dict(longitude=True, latitude=2.0, crs="WGS84"),
    dict(longitude=1.0, latitude=2.0, crs="guess"),
])
def test_illegal_coordinates_rejected(coordinates):
    with pytest.raises(ValidationError):
        Coordinates.model_validate(coordinates)


def test_provider_id_conflict_rejected_not_used_as_internal_id(source):
    result = search(source, "合成冲突")
    assert result.outcome == "schema_changed" and result.error.code == "schema_changed"
    assert not result.items and result.total_matches == 0


@pytest.mark.parametrize("failure", ["timeout", "unavailable", "unsupported", "schema_changed"])
def test_failures_and_not_found_remain_distinct(failure, source):
    empty = search(source, "合成不存在馆")
    assert empty.outcome == "not_found" and empty.error.retryable is False
    broken = SyntheticPlaceSource(clock=lambda: NOW, failure=failure)
    result = search(broken)
    assert result.outcome == failure and result.error.code == failure
    assert result.error.retryable == (failure in ("timeout", "unavailable"))
    assert not result.items
    assert assess_identity(query(), result).status == "UNAVAILABLE"


@pytest.mark.parametrize("change", [dict(keyword=""), dict(keyword=" "*2), dict(keyword="x"*101), dict(city=""), dict(limit=0), dict(limit=21), dict(limit=True), dict(limit="2"), dict(verification_state="VERIFIED"), dict(environment="live"), dict(evidence_id="forged")])
def test_query_bounds_and_authority_fields_rejected(change):
    with pytest.raises(ValidationError):
        PlaceQuery.model_validate(query().model_dump() | change)


def test_catalogue_rejects_extra_authority_and_bad_types():
    original = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for changes in (dict(verification_state="VERIFIED"), dict(environment="live"), dict(evidence_id="forged"), dict(synthetic=False), dict(name="真实供应商地点"), dict(address_complete="true")):
        raw = deepcopy(original)
        raw["records"][0].update(changes)
        with pytest.raises(ValidationError):
            SyntheticCatalog.model_validate_json(json.dumps(raw))
    for count in (9, 101):
        with pytest.raises(ValidationError):
            SyntheticCatalog.model_validate_json(json.dumps(original | dict(records=(original["records"] * 7)[:count])))
    for field in ("name", "provider_resource_id", "city", "coordinates"):
        raw = deepcopy(original)
        del raw["records"][0][field]
        with pytest.raises(ValidationError):
            SyntheticCatalog.model_validate_json(json.dumps(raw))
    for changes in (dict(site_key="unique", city="合成乙城"), dict(address=None, address_complete=True)):
        raw = deepcopy(original)
        raw["records"][1].update(changes)
        with pytest.raises(ValidationError):
            SyntheticCatalog.model_validate_json(json.dumps(raw))


def test_normalized_schema_cannot_hide_gaps_or_add_authority(source):
    item = search(source, "合成无坐标馆").items[0]
    for change in (dict(missing_fields=()), dict(route_verified=True), dict(opening_hours="always"), dict(verification_state="VERIFIED")):
        with pytest.raises(ValidationError):
            PlaceCandidate.model_validate(item.model_dump() | change)
    with pytest.raises(ValidationError):
        item.name = "mutated"


def test_server_observation_ids_and_non_live_boundary(source):
    lookup = PlaceLookup(source, clock=lambda: NOW)
    first, second = run(lookup.search(query())), run(lookup.search(query()))
    observation = first.observations[0]
    assert isinstance(observation.evidence_id, UUID)
    assert observation.evidence_id != second.observations[0].evidence_id
    assert observation.claim_digest == second.observations[0].claim_digest
    assert observation.verification_state == "NON_LIVE"
    assert not observation.usable_for_real_trip
    assert observation.source.environment == "synthetic"
    for change in (dict(verification_state="VERIFIED"), dict(usable_for_real_trip=True)):
        with pytest.raises(ValidationError):
            type(observation).model_validate(observation.model_dump() | change)
    with pytest.raises(ValidationError):
        type(observation).model_validate(observation.model_dump() | dict(source=observation.source.model_copy(update=dict(environment="live"))))


class FakeSource:
    def __init__(self, result):
        self.result = result

    async def search(self, query):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("environment", ["live", "sandbox", "replay"])
def test_adapter_cannot_spoof_environment(source, environment):
    result = search(source)
    altered = result.model_copy(update=dict(source=result.source.model_copy(update=dict(environment=environment))))
    with pytest.raises(PlaceBoundaryError, match="source_binding_mismatch"):
        run(PlaceLookup(FakeSource(altered), clock=lambda: NOW).search(query()))


def test_boundary_revalidates_bypass_provider_future_scope_and_count(source):
    result = search(source)
    forged = result.model_copy(update=dict(outcome="VERIFIED"))
    with pytest.raises(PlaceBoundaryError, match="schema_changed"):
        run(PlaceLookup(FakeSource(forged), clock=lambda: NOW).search(query()))
    for changed in (dict(provider="forged"), dict(observed_at=NOW.replace(year=2027))):
        altered = result.model_copy(update=dict(source=result.source.model_copy(update=changed)))
        with pytest.raises(PlaceBoundaryError):
            run(PlaceLookup(FakeSource(altered), clock=lambda: NOW).search(query()))
    broad = search(source, "合成同名花园")
    with pytest.raises(PlaceBoundaryError, match="result_limit_exceeded"):
        run(PlaceLookup(FakeSource(broad), clock=lambda: NOW).search(query("合成同名花园", limit=1)))
    altered = result.model_copy(update=dict(scope=ApplicableScope(start_date=date(2027,1,1), end_date=date(2027,1,2))))
    rejected = run(PlaceLookup(FakeSource(altered), clock=lambda: NOW).search(query()))
    assert rejected.identity.status == "REJECTED" and not rejected.observations


@pytest.mark.parametrize("failure,code", [(TimeoutError("do-not-echo"), "timeout"), (RuntimeError("do-not-echo"), "unavailable")])
def test_raised_source_failure_does_not_fabricate_or_leak(failure, code):
    with pytest.raises(PlaceBoundaryError) as error:
        run(PlaceLookup(FakeSource(failure), clock=lambda: NOW).search(query()))
    assert str(error.value) == code


def test_supplier_instructions_are_inert_text_and_never_authority(source):
    result = run(PlaceLookup(source, clock=lambda: NOW).search(query("合成文本展馆")))
    assert "VERIFIED" in result.source_result.items[0].name
    assert result.observations[0].verification_state == "NON_LIVE"
    assert not result.identity.usable_for_real_trip
    # A failure never gets an observation, including an unrecognized place.
    for adapter, keyword in ((source, "合成不存在馆"), (SyntheticPlaceSource(clock=lambda: NOW, failure="timeout"), "合成唯一展馆")):
        value = run(PlaceLookup(adapter, clock=lambda: NOW).search(query(keyword)))
        assert not value.observations and not value.source_result.items


def test_observation_time_is_aware_and_not_applicability():
    with pytest.raises(ValidationError):
        SourceMetadata(provider=PROVIDER, environment="synthetic", observed_at=datetime(2026,1,1))
    with pytest.raises(ValidationError):
        ApplicableScope(start_date=SCOPE.end_date, end_date=SCOPE.start_date)


def test_typed_result_rejects_incoherent_or_authority_payload(source):
    result = search(source)
    for change in (dict(outcome="partial"), dict(total_matches=0), dict(verification_state="VERIFIED"), dict(items=()), dict(error=dict(code="timeout", retryable=True))):
        raw = result.model_dump(mode="json") | change
        with pytest.raises(ValidationError):
            SourceResult[PlaceCandidate].model_validate_json(json.dumps(raw))


def test_result_rejects_conflicting_provider_or_internal_bindings(source):
    first = search(source).items[0]
    second = search(source, "合成同名花园", district="合成西区").items[0]
    for change in (dict(provider_resource_id=first.resource.provider_resource_id), dict(internal_resource_id=first.resource.internal_resource_id)):
        raw = search(source, "合成同名花园").model_dump(mode="json")
        item = second.model_dump(mode="json")
        item["resource"].update(change)
        raw["items"] = [first.model_dump(mode="json"), item]
        with pytest.raises(ValidationError):
            SourceResult[PlaceCandidate].model_validate_json(json.dumps(raw))


def test_detail_never_silently_selects_shared_poi_entrance(source):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Some future providers identify a site rather than each entrance.
    raw["records"][4]["provider_resource_id"] = raw["records"][3]["provider_resource_id"]
    same_poi = SyntheticPlaceSource(SyntheticCatalog.model_validate_json(json.dumps(raw)), clock=lambda: NOW)
    result = search(same_poi, "合成双门展馆", entrance_id="west")
    detail = run(same_poi.detail(result.items[0].resource, SCOPE))
    assert detail.outcome == "ambiguous_resource" and len(detail.items) == 2


def test_library_has_no_business_or_transport_dependencies():
    import ast
    from pathlib import Path
    files = [
        "easytravel/backend/domain/places.py", "easytravel/backend/application/place_sources.py",
        "easytravel/backend/application/place_lookup.py", "easytravel/backend/infrastructure/synthetic_places.py",
    ]
    forbidden = {"httpx", "requests", "urllib", "socket", "psycopg", "fastapi", "langgraph", "os", "subprocess"}
    for name in files:
        for node in ast.walk(ast.parse(Path(name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(n.name.split('.')[0] not in forbidden for n in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or '').split('.')[0] not in forbidden
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile", "__import__"}
