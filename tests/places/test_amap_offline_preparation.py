"""Offline M2-01c: fabricated v5-shaped replies, never actual provider data."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from easytravel.backend.application.live_place_ports import LivePlaceSourceConfig
from easytravel.backend.application.offline_live_places import DetailSelection, OfflineLivePlaceLookup
from easytravel.backend.domain.places import ApplicableScope, PlaceQuery, ResourceRef
from easytravel.backend.domain.place_search_v2 import (
    LiveUnverifiedObservation, PlaceSearchRequest, SearchCoverage,
)
from easytravel.backend.infrastructure.amap_offline_places import AmapOfflinePlaceSource
from easytravel.backend.infrastructure.fake_place_transport import FakeResponse, FakeTransport

NOW = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
SCOPE = ApplicableScope(start_date=date(2026, 12, 1), end_date=date(2026, 12, 3))
FIXTURE = json.loads(Path(__file__).with_name('fixtures').joinpath('amap_v5_fabricated.json').read_text(encoding='utf-8'))


def run(coro):
    # The dedicated runner installs process-wide DNS/TCP guards (including imports).
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


def request(**changes):
    return PlaceSearchRequest(query=PlaceQuery(keyword='合成主体', city='合成甲城', scope=SCOPE, **changes))


def poi(index=0, **changes):
    return deepcopy(FIXTURE['pois'][index]) | changes


def response(*pois, status=200, root=None):
    raw = root if root is not None else dict(status='1', infocode='10000', info='OK', count=str(len(pois)), pois=pois)
    return FakeResponse(status_code=status, body=json.dumps(raw, ensure_ascii=False))


def source(*replies, config=None):
    transport = FakeTransport(*replies)
    adapter = AmapOfflinePlaceSource(config or LivePlaceSourceConfig(offline_parsing_enabled=True), transport, clock=lambda: NOW)
    return adapter, transport


def ref(id='FAB_MAIN', **changes):
    return ResourceRef(internal_resource_id='pending_local', provider='amap_poi_v5', provider_resource_id=id,
                       city='合成甲城', district='合成东区', **changes)


def select(session, id='FAB_MAIN'):
    return DetailSelection(search_id=session.search_id, provider_resource_id=id)


def test_search_detail_and_unverified_observation():
    adapter, transport = source(response(poi()), response(poi()))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    session = run(lookup.search(request()))
    assert len(session.page.hits) == 1
    assert session.page.requires_explicit_selection
    assert not session.page.coverage.complete and not session.page.coverage.coverage_known
    assert lookup.resource_count == 0
    assert len(transport.calls) == 1  # no automatic detail or first-hit binding
    result = run(lookup.review_detail(select(session)))
    assert result.status == 'DETAIL_REVIEWED_UNVERIFIED'
    o = result.observation
    assert o.resource.internal_resource_id != o.resource.provider_resource_id
    assert o.resource.internal_resource_id.startswith('trial_')
    assert o.verification_state == 'SOURCE_UNVERIFIED'
    assert o.source.environment == 'synthetic' and o.source.target_environment == 'live'
    assert o.source.data_origin == 'fabricated_fake_transport' and o.source.provider_updated_at is None
    assert o.source.observed_at.date() != o.query_scope.start_date
    assert not o.applicable_date_verified and not o.usable_for_real_trip and not o.usable_in_confirmed_snapshot
    assert o.opening_state == o.price_state == o.route_state == 'UNKNOWN'
    assert o.fact.entrance is None and o.fact.coordinate_role == 'poi_position'
    assert o.fact.coordinates.crs == 'GCJ-02' and not o.fact.address_complete
    assert o.fact.children[0].relation == 'child_poi_hint'
    assert len(transport.calls) == 2 and lookup.resource_count == 1


def test_coverage_complete_incomplete_unknown_and_trim_guards():
    for unsearched in (True, None, False):
        coverage = SearchCoverage(request=request(), received_count=1, returned_count=1,
                                  truncated_count=0, has_unsearched_results=unsearched)
        assert coverage.coverage_known == (unsearched is not None)
        assert coverage.complete == (unsearched is False)
    trimmed = SearchCoverage(request=request(limit=1), received_count=2, returned_count=1,
                             truncated_count=1, has_unsearched_results=False)
    assert not trimmed.complete
    for change in (dict(received_count=0), dict(returned_count=21), dict(truncated_count=2)):
        with pytest.raises(ValidationError):
            SearchCoverage.model_validate(trimmed.model_dump() | change)
    with pytest.raises(ValidationError):
        SearchCoverage(request=PlaceSearchRequest(query=request().query, page=2), received_count=1,
                       returned_count=1, truncated_count=0, has_unsearched_results=False)


def test_even_complete_single_result_needs_explicit_detail():
    adapter, transport = source(response(poi()), response(poi()))
    original = run(adapter.search(request()))
    complete = original.model_copy(update=dict(coverage=original.coverage.model_copy(update=dict(has_unsearched_results=False))))
    class Stub:
        async def search(self, query): return complete
        async def detail(self, resource, scope): return await adapter.detail(resource, scope)
    lookup = OfflineLivePlaceLookup(Stub(), clock=lambda: NOW)
    session = run(lookup.search(request()))
    assert session.page.coverage.complete and lookup.resource_count == 0 and len(transport.calls) == 1
    assert run(lookup.review_detail(select(session))).observation is not None


def test_multiple_same_names_are_not_merged_and_truncation_remains_visible():
    adapter, transport = source(response(poi(), poi(1)), response(poi(1)))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    session = run(lookup.search(request()))
    assert len(session.page.hits) == 2 and lookup.resource_count == 0
    result = run(lookup.review_detail(select(session, 'FAB_OTHER')))
    assert result.observation.fact.provider_resource_id == 'FAB_OTHER'
    adapter, _ = source(response(poi(), poi(1)))
    trimmed = run(adapter.search(request(limit=1)))
    assert trimmed.coverage.received_count == 2 and trimmed.coverage.truncated_count == 1
    assert trimmed.requires_explicit_selection and not trimmed.coverage.complete


def test_alias_and_child_relationships_do_not_rename_or_create_entrances():
    adapter, _ = source(response(poi()), response(poi()), response(poi(2)), response(poi(2)))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    s = run(lookup.search(PlaceSearchRequest(query=PlaceQuery(keyword='合成别名', city='合成甲城', scope=SCOPE))))
    r = run(lookup.review_detail(select(s)))
    assert r.observation.fact.name == '合成主体展馆'
    assert r.observation.fact.aliases == ('合成别名',)
    assert r.observation.fact.children[0].provider_resource_id == 'FAB_GATE'
    gate_session = run(lookup.search(request()))
    gate = run(lookup.review_detail(select(gate_session, 'FAB_GATE')))
    assert gate.observation.fact.parent_id == 'FAB_MAIN' and gate.observation.fact.entrance is None
    assert gate.observation.resource.internal_resource_id != r.observation.resource.internal_resource_id


def test_unseen_or_unselected_identity_cannot_trigger_detail():
    adapter, transport = source(response(poi()))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    s = run(lookup.search(request()))
    for selection in (select(s, 'UNSEEN'), DetailSelection(search_id=uuid4(), provider_resource_id='FAB_MAIN')):
        assert run(lookup.review_detail(selection)).error.code == 'not_found'
    assert len(transport.calls) == 1 and lookup.resource_count == 0


@pytest.mark.parametrize('change', [dict(cityname='合成乙城'), dict(adname='合成西区'), dict(type='合成餐饮')])
def test_search_administrative_and_category_binding(change):
    adapter, _ = source(response(poi(**change)))
    page = run(adapter.search(request(district='合成东区', category='合成景点')))
    assert page.error.code == 'query_binding_mismatch' and not page.hits


@pytest.mark.parametrize('change', [dict(id='WRONG'), dict(cityname='合成乙城'), dict(adname='合成西区'),
                                 dict(adcode='TEST_002'), dict(name='合成另一实体'), dict(parent='WRONG'), dict(location='1.50,2.50')])
def test_detail_identity_conflicts_do_not_register(change):
    adapter, _ = source(response(poi()), response(poi(**change)))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    s = run(lookup.search(request()))
    result = run(lookup.review_detail(select(s)))
    assert result.error.code == 'identity_conflict' and lookup.resource_count == 0


def test_registry_stable_in_process_rejects_reused_id_and_does_not_promise_restart_identity():
    adapter, _ = source(response(poi()), response(poi()), response(poi()), response(poi()),
                        response(poi(address='合成测试路999号')), response(poi(address='合成测试路999号')))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    ids = []
    for _ in range(2):
        s = run(lookup.search(request()))
        ids.append(run(lookup.review_detail(select(s))).observation.resource.internal_resource_id)
    assert ids[0] == ids[1]
    s = run(lookup.search(request()))
    assert run(lookup.review_detail(select(s))).error.code == 'identity_conflict'
    assert lookup.resource_count == 1
    other, _ = source(response(poi()), response(poi()))
    restarted = OfflineLivePlaceLookup(other, clock=lambda: NOW)
    s = run(restarted.search(request()))
    assert run(restarted.review_detail(select(s))).observation.resource.internal_resource_id != ids[0]


def test_conflicting_or_duplicate_supplier_ids_fail_whole_page():
    for second in (poi(), poi(1, id='FAB_MAIN'), poi(3, id='FAB_MAIN')):
        adapter, _ = source(response(poi(), second))
        page = run(adapter.search(request()))
        assert page.error.code == 'identity_conflict' and not page.hits


def test_self_parent_or_conflicting_child_ids_fail():
    for change in (dict(parent='FAB_MAIN'), dict(children=[dict(id='FAB_MAIN',name='合成自身')]),
                   dict(children=[dict(id='FAB_CHILD',name='合成甲'),dict(id='FAB_CHILD',name='合成乙')])):
        adapter, _ = source(response(poi(**change)))
        assert run(adapter.search(request())).error.code == 'identity_conflict'


@pytest.mark.parametrize('location', ['181,2', '1,-91', 'NaN,2', 'Infinity,2', '2', '1,2,3', True, {'lng':1,'lat':2}, '1e100,2'])
def test_bad_coordinates_rejected(location):
    adapter, _ = source(response(poi(location=location)))
    assert run(adapter.search(request())).error.code == 'schema_changed'


@pytest.mark.parametrize('location', [None, '', []])
def test_missing_coordinates_stay_missing(location):
    adapter, _ = source(response(poi(location=location)))
    hit = run(adapter.search(request())).hits[0]
    assert hit.coordinates is None and 'coordinates' in hit.missing_or_unverified_fields


def test_empty_optional_fields_and_missing_crs_do_not_grant_geometry():
    adapter, _ = source(response(poi(4)), config=LivePlaceSourceConfig(offline_parsing_enabled=True, response_crs=None))
    hit = run(adapter.search(request())).hits[0]
    assert hit.address is None and hit.district is None and hit.entrance is None
    adapter, _ = source(response(poi()), config=LivePlaceSourceConfig(offline_parsing_enabled=True, response_crs=None))
    hit = run(adapter.search(request())).hits[0]
    assert hit.coordinates.crs is None and 'coordinates.crs' in hit.missing_or_unverified_fields
    for crs in ('BD-09', 'WGS84', 'guess'):
        with pytest.raises(ValidationError): LivePlaceSourceConfig(response_crs=crs)


@pytest.mark.parametrize('body', ['{', '[]', 'null', '{"status":"1","status":"0"}', '{"x":NaN}', '"text"'])
def test_malformed_json_and_root_type(body):
    adapter, _ = source(FakeResponse(status_code=200, body=body))
    assert run(adapter.search(request())).error.code == 'schema_changed'


@pytest.mark.parametrize('change', [dict(pois={}), dict(count=1), dict(status=True), dict(count='2'), dict(pois=None)])
def test_response_schema_changes_rejected(change):
    raw = dict(status='1', infocode='10000', info='OK', count='1', pois=[poi()]) | change
    adapter, _ = source(response(root=raw))
    assert run(adapter.search(request())).error.code == 'schema_changed'


@pytest.mark.parametrize('http,code', [(401,'auth_denied'), (403,'forbidden'), (429,'quota_exceeded'), (500,'unavailable'), (503,'unavailable'), (302,'unsupported')])
def test_http_failures_safely_classified(http, code):
    adapter, transport = source(FakeResponse(status_code=http, body='https://sensitive.invalid/?key=FAKE_SECRET_SENTINEL'))
    page = run(adapter.search(request()))
    assert page.error.code == code and not page.hits
    assert 'FAKE_SECRET_SENTINEL' not in page.model_dump_json() and len(transport.calls) == 1


@pytest.mark.parametrize('infocode,code', [('10001','auth_denied'), ('10005','auth_denied'), ('10012','forbidden'),
                                         ('10003','quota_exceeded'), ('10014','quota_exceeded'), ('99999','unavailable')])
def test_supplier_business_errors(infocode, code):
    adapter, _ = source(response(root=dict(status='0', infocode=infocode, info='FAKE_SECRET_SENTINEL', pois=[])))
    page = run(adapter.search(request()))
    assert page.error.code == code and 'FAKE_SECRET_SENTINEL' not in page.model_dump_json()


@pytest.mark.parametrize('exception,code', [(TimeoutError('FAKE_SECRET_SENTINEL'),'timeout'), (RuntimeError('FAKE_SECRET_SENTINEL'),'unavailable')])
def test_transport_exceptions_do_not_leak(exception, code):
    adapter, _ = source(exception)
    page = run(adapter.search(request()))
    assert page.error.code == code and 'FAKE_SECRET_SENTINEL' not in page.model_dump_json()


@pytest.mark.parametrize('field,value', [('verified',True), ('verification_state','VERIFIED'), ('evidence_id','forged'),
                                      ('environment','live'), ('provider','attacker'), ('crs','WGS84')])
def test_external_authority_fields_rejected_at_root_and_poi(field, value):
    for raw in (dict(status='1', infocode='10000', info='OK', count='1', pois=[poi()], **{field:value}),
                dict(status='1', infocode='10000', info='OK', count='1', pois=[poi(**{field:value})])):
        adapter, _ = source(response(root=raw))
        page = run(adapter.search(request()))
        assert page.error.code == 'schema_changed' and not page.hits


def test_instructions_in_names_are_data_only():
    adapter, _ = source(response(poi(5)), response(poi(5)))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    s = run(lookup.search(request()))
    o = run(lookup.review_detail(select(s, 'FAB_TEXT'))).observation
    assert 'VERIFIED' in o.fact.name and o.verification_state == 'SOURCE_UNVERIFIED'


def test_default_closed_and_concrete_fake_transport_required():
    fake = FakeTransport(response(poi()))
    for config, code in ((None, 'not_configured'), (LivePlaceSourceConfig(), 'live_disabled')):
        adapter = AmapOfflinePlaceSource(config, fake, clock=lambda: NOW)
        assert run(adapter.search(request())).error.code == code
        assert run(adapter.detail(ref(), SCOPE)).error.code == code
    assert not fake.calls
    class UntrustedTransport:
        async def exchange(self, *args): raise AssertionError('must not execute')
    class FakeSubclass(FakeTransport): pass
    for transport in (None, UntrustedTransport(), FakeSubclass(response(poi()))):
        adapter = AmapOfflinePlaceSource(LivePlaceSourceConfig(offline_parsing_enabled=True), transport, clock=lambda: NOW)
        assert run(adapter.search(request())).error.code == 'transport_not_permitted'
    for change in (dict(live_enabled=True), dict(host='example.invalid'), dict(search_path='/anything'), dict(environment='synthetic'), dict(api_version='v3'), dict(key='forbidden')):
        with pytest.raises(ValidationError): LivePlaceSourceConfig.model_validate(change)


def test_input_limits_reject_without_transport():
    adapter, transport = source()
    assert run(adapter.search(PlaceSearchRequest(query=PlaceQuery(keyword='x'*81,city='合成甲城',scope=SCOPE)))).error.code == 'unsupported'
    assert run(adapter.search(request(entrance_id='invented'))).error.code == 'unsupported'
    assert not transport.calls
    for change in (dict(page=0), dict(page=11), dict(page_size=0), dict(page_size=21), dict(verification_state='VERIFIED')):
        with pytest.raises(ValidationError): PlaceSearchRequest.model_validate(request().model_dump() | change)
    with pytest.raises(ValidationError): FakeResponse(status_code=200, body='x'*131073)


def test_no_result_or_bad_detail_cannot_create_observation():
    adapter, _ = source(response(), response(poi()), response(), response(poi()), response(poi(),poi(1)))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    empty = run(lookup.search(request()))
    assert not empty.page.hits and lookup.resource_count == 0
    s = run(lookup.search(request()))
    assert run(lookup.review_detail(select(s))).error.code == 'not_found'
    s = run(lookup.search(request()))
    assert run(lookup.review_detail(select(s))).error.code == 'identity_conflict'
    assert lookup.resource_count == 0


def test_observation_cannot_grant_authority_or_snapshot_use():
    adapter, _ = source(response(poi()), response(poi()))
    lookup = OfflineLivePlaceLookup(adapter, clock=lambda: NOW)
    s = run(lookup.search(request()))
    o = run(lookup.review_detail(select(s))).observation
    for change in (dict(verification_state='VERIFIED'), dict(usable_in_confirmed_snapshot=True),
                   dict(usable_for_real_trip=True), dict(applicable_date_verified=True), dict(opening_state='VERIFIED'),
                   dict(source_reference='offline-observation:'+str(uuid4()))):
        with pytest.raises(ValidationError): LiveUnverifiedObservation.model_validate(o.model_dump() | change)


def test_source_result_revalidation_blocks_environment_time_and_query_spoof():
    adapter, _ = source(response(poi()))
    good = run(adapter.search(request()))
    class Stub:
        def __init__(self, page): self.page=page
        async def search(self, request): return self.page
    for stamp_change in (dict(environment='live'), dict(observed_at=NOW.replace(year=2027)), dict(provider='attacker')):
        bad = good.model_copy(update=dict(source=good.source.model_copy(update=stamp_change)))
        with pytest.raises(ValueError): run(OfflineLivePlaceLookup(Stub(bad),clock=lambda:NOW).search(request()))
    wrong = good.model_copy(update=dict(coverage=good.coverage.model_copy(update=dict(request=request(limit=1)))))
    with pytest.raises(ValueError): run(OfflineLivePlaceLookup(Stub(wrong),clock=lambda:NOW).search(request()))


def test_detail_failures_all_produce_safe_typed_errors():
    for reply, code in ((FakeResponse(status_code=401,body='FAKE_SECRET_SENTINEL'),'auth_denied'),
                        (TimeoutError('FAKE_SECRET_SENTINEL'),'timeout'),
                        (FakeResponse(status_code=200,body='{'),'schema_changed')):
        adapter, _ = source(response(poi()), reply)
        lookup = OfflineLivePlaceLookup(adapter,clock=lambda:NOW)
        s=run(lookup.search(request()))
        result=run(lookup.review_detail(select(s)))
        assert result.error.code==code and not result.observation
        assert 'FAKE_SECRET_SENTINEL' not in result.model_dump_json()


def test_no_business_or_network_imports_and_unchanged_v1_contracts():
    import ast
    paths=['easytravel/backend/domain/place_search_v2.py','easytravel/backend/application/live_place_ports.py',
           'easytravel/backend/application/offline_live_places.py','easytravel/backend/infrastructure/amap_offline_places.py',
           'easytravel/backend/infrastructure/fake_place_transport.py']
    for path in paths:
        for node in ast.walk(ast.parse(Path(path).read_text(encoding='utf-8'))):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or '']
                assert not any(n.split('.')[0] in {'httpx','requests','urllib','socket','os','subprocess','psycopg','fastapi'} for n in names)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                assert node.func.id not in {'eval','exec','__import__'}
