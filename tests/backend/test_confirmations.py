"""M1-05a: real PostgreSQL confirmation, rollback, concurrency and restart gates."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from test_generations import services, create, accept, step, run, headers, ACTOR_A, TOKEN_B
from test_generations import app as generation_app
from easytravel.backend.api.v1.trips import confirmation_service
from easytravel.backend.application.confirmations import ConfirmationService
from easytravel.backend.domain.confirmation import ConfirmRequest
from easytravel.backend.domain.intake import DraftContext
from easytravel.backend.domain.models import WorkingDraft
from easytravel.backend.infrastructure.database import DatabaseSettings, connection
from easytravel.backend.infrastructure.confirmations import unit_of_work, PostgresConfirmationRepository
from easytravel.backend.infrastructure.generations import PostgresGenerationRepository
from easytravel.backend.infrastructure.migrate import upgrade


@pytest.fixture(scope='module')
def db():
    # No default .env fallback. Only the isolated provisioner supplies this resource.
    assert os.environ['EASYTRAVEL_ISOLATED_TRANSPORT_PORT'] == '55434'
    settings = DatabaseSettings(url=os.environ['EASYTRAVEL_M105_TEST_DATABASE_URL'])
    run(upgrade(settings))
    return settings


@pytest.fixture
def app(generation_app, db):
    generation_app.dependency_overrides[confirmation_service] = lambda: ConfirmationService(unit_of_work(db))
    return generation_app


@pytest.fixture
def http(app):
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1',50000),
        raise_server_exceptions=False, backend_options={'loop_factory':asyncio.SelectorEventLoop} if sys.platform=='win32' else {}) as c:
        yield c


def generated(http, db, kind=None):
    path = create(http)
    accept(http,path)
    original = PostgresGenerationRepository.attach
    async def attach(repo, draft, context, generation):
        if kind == 'over':
            data = draft.model_dump(mode='json')
            data['requirements']['hard_limit_cents'] = 1
            draft = WorkingDraft.model_validate(data)
        if kind == 'blocker':
            data = context.model_dump(mode='json')
            data['notes']['risks'].append(dict(code='unsafe_route',severity='blocker',message='需解决的路线风险',item_ids=[]))
            context = DraftContext.model_validate(data)
        if kind == 'no_agent_risks':
            context = DraftContext.model_validate(context.model_dump() | dict(notes=dict(transport_advice=[], risks=[])))
        await original(repo,draft,context,generation)
    with patch.object(PostgresGenerationRepository,'attach',attach):
        run(step(db))
    assert http.get(path,headers=headers()).json()['working'] is not None
    return path


def test_known_mock_costs_remain_estimated(http,db):
    from easytravel.backend.infrastructure.mock_planner import mock_adapter
    from easytravel.backend.application.intakes import IntakeService
    from easytravel.backend.infrastructure.intakes import unit_of_work as intake_uow
    from easytravel.model_adapter import MockAdapter, ModelReply
    path=create(http); accepted=accept(http,path)
    trip=run(IntakeService(intake_uow(db)).get(accepted['trip_id'],actor=ACTOR_A))
    data=json.loads(mock_adapter(trip.intake.input).responses[0].text)
    for cost in data['costs']:
        if cost['amount_cents'] is None:
            cost.update(amount_cents=0,category='estimate',unknown_reason=None)
    run(step(db,lambda _:MockAdapter(ModelReply(json.dumps(data)))))
    p=preview(http,path)
    assert p['budget']['verification']=='ESTIMATED' and p['budget']['constraint']=='WITHIN_LIMIT'
    assert p['budget']['total_cents']==60000 and not p['unknown_costs']
    r=confirm(http,path,request(p)); assert r.status_code==201
    assert r.json()['snapshot']['snapshot']['budget']['verification']=='ESTIMATED'


def preview(http,path):
    r = http.get(path+'/confirmation-preview',headers=headers())
    assert r.status_code == 200, r.text
    assert r.headers['cache-control'] == 'no-store'
    return r.json()


def request(p):
    return dict(expected_intake_revision=p['intake_revision'], expected_working_revision=p['working_revision'],
        seen_disclosure_hash=p['disclosure_hash'], seen_risk_hash=p['risk_hash'], explicit_confirmation=True)


def confirm(http,path,body,key=None):
    return http.post(path+'/confirmations',headers=headers(key),json=body)


async def facts(db,tid):
    async with connection(db) as conn:
        tables = ('confirmed_snapshots','confirmation_disclosures','operation_receipts','api_commands',
                  'draft_versions','draft_contexts','specific_authorizations')
        result = {t: (await (await conn.execute(f"SELECT count(*) AS n FROM easytravel.{t} WHERE trip_id=%s", (tid,))).fetchone())['n'] for t in tables}
        result['pointer'] = (await (await conn.execute('SELECT latest_confirmed_version_id FROM easytravel.working_drafts WHERE trip_id=%s',(tid,))).fetchone())['latest_confirmed_version_id']
        return result


def test_preview_confirmation_facts_immutable(http,db):
    path = generated(http,db,'no_agent_risks'); tid=path.split('/')[-1]
    before = run(facts(db,tid)); p=preview(http,path)
    assert run(facts(db,tid)) == before
    assert p['confirmation_allowed'] and not p['blocking_reasons']
    assert p['budget']['total_cents'] is None and p['budget']['known_subtotal_cents'] == 60000
    assert p['budget']['verification']=='INDETERMINATE' and p['budget']['constraint']=='UNDETERMINED'
    assert p['unknown_costs'] and all(x['reason'] for x in p['unknown_costs'])
    assert {r['code'] for r in p['risks']} >= {'not_booked','mock_data','route_unverified','prices_unverified','unknown_costs'}
    key=str(uuid4()); body=request(p); r=confirm(http,path,body,key)
    assert r.status_code==201,r.text
    result=r.json(); view=result['snapshot']; s=view['snapshot']; sid=s['snapshot_id']
    assert view['risk_disclosure']['risk_hash']==p['risk_hash'] and view['risk_disclosure']['risks']==p['risks']
    assert view['context']['intake_revision']==s['source_working_revision']==1
    assert s['budget']==p['budget'] and s['disclosure_hash']==p['disclosure_hash']
    assert s['item_confirmations']==[] and view['readiness']=='NOT_ASSESSED' and view['booked'] is False
    after=run(facts(db,tid))
    assert after['confirmed_snapshots']==after['confirmation_disclosures']==after['operation_receipts']==1
    assert after['pointer']==sid and after['specific_authorizations']==0 and after['draft_versions']==1
    assert http.get(path,headers=headers()).json()['plan_confirmation']=='CONFIRMED'
    assert confirm(http,path,body,key).json()==result
    other=confirm(http,path,body)
    assert other.status_code==201 and other.json()['snapshot']==view
    assert run(facts(db,tid))['confirmed_snapshots']==1
    # Actual DB triggers, including saved disclosures, reject UPDATE/DELETE/TRUNCATE.
    async def immutable():
        async with connection(db) as conn:
            for table in ('confirmed_snapshots','confirmation_disclosures','draft_versions','draft_contexts'):
                for query in (f'UPDATE easytravel.{table} SET trip_id=trip_id WHERE trip_id=%s',f'DELETE FROM easytravel.{table} WHERE trip_id=%s'):
                    with pytest.raises(psycopg.Error):
                        async with conn.transaction():
                            await conn.execute(query,(tid,))
            with pytest.raises(psycopg.Error):
                async with conn.transaction():
                    await conn.execute('TRUNCATE easytravel.confirmation_disclosures')
    run(immutable())
    Path('Docs/M1-05a_PostgreSQL证据.json').write_text(json.dumps(dict(preview=p,result=result,before=before,after=after,immutable=True),ensure_ascii=False,indent=2),encoding='utf-8')
    print('E06/E08 backend: unknown != zero; system disclosures survive empty Agent risks; snapshot/context/budget/pointer/receipts immutable and atomic; no hotel grant')


@pytest.mark.parametrize('value,status,code',[(False,409,'explicit_confirmation_required'),('true',422,'invalid_request'),(1,422,'invalid_request'),(None,422,'invalid_request')])
def test_strict_intent(http,db,value,status,code):
    path=generated(http,db); body=request(preview(http,path)) | dict(explicit_confirmation=value)
    r=confirm(http,path,body)
    assert r.status_code==status and r.json()['error']['code']==code
    assert run(facts(db,path.split('/')[-1]))['confirmed_snapshots']==0


@pytest.mark.parametrize('field,code', [('seen_disclosure_hash','disclosure_not_acknowledged'),('seen_risk_hash','risk_not_acknowledged'),('expected_intake_revision','stale_intake'),('expected_working_revision','stale_revision')])
def test_old_facts(http,db,field,code):
    path=generated(http,db); b=request(preview(http,path)); b[field]='0'*64 if 'hash' in field else 2
    r=confirm(http,path,b)
    assert r.status_code==409 and r.json()['error']['code']==code, r.text
    assert run(facts(db,path.split('/')[-1]))['confirmed_snapshots']==0


def test_missing_and_stale_pending_budget(http,db):
    path=create(http)
    r=http.get(path+'/confirmation-preview',headers=headers())
    assert r.status_code==409 and r.json()['error']['code']=='draft_not_ready'
    b=dict(expected_intake_revision=1,expected_working_revision=1,seen_disclosure_hash='0'*64,seen_risk_hash='0'*64,explicit_confirmation=True)
    assert confirm(http,path,b).json()['error']['code']=='draft_not_ready'
    path=generated(http,db); old=request(preview(http,path))
    t=http.get(path,headers=headers()).json(); inp=t['intake']['input']; inp['hard_limit_cents']['value']=1
    r=http.put(path+'/requirements',headers=headers(),json=dict(expected_intake_revision=1,expected_working_revision=1,requirements=inp))
    assert r.status_code==200
    p=preview(http,path)
    assert p['budget']['constraint']=='UNDETERMINED' and p['pending_requirements_budget']['constraint']=='OVER_LIMIT'
    assert not p['confirmation_allowed'] and {'stale_intake','known_costs_exceed_hard_limit'} <= set(p['blocking_reasons'])
    assert confirm(http,path,old).json()['error']['code']=='stale_intake'
    assert confirm(http,path,request(p)).json()['error']['code']=='stale_intake'
    print('E07/E13 backend: 200 STALE preview preserves original budget and pending OVER_LIMIT; both stale requests rejected')


@pytest.mark.parametrize('kind,code',[('over','known_costs_exceed_hard_limit'),('blocker','blocking_risks')])
def test_budget_and_blocker_cannot_be_waived(http,db,kind,code):
    path=generated(http,db,kind); p=preview(http,path)
    assert not p['confirmation_allowed'] and code in p['blocking_reasons']
    if kind=='over': assert p['budget']['constraint']=='OVER_LIMIT'
    r=confirm(http,path,request(p)); assert r.status_code==409 and r.json()['error']['code']==code


def test_security_and_validation(http,db):
    path=generated(http,db); body=request(preview(http,path))
    for suffix in ('/confirmation-preview','/confirmations'):
        action=lambda h: http.get(path+suffix,headers=h) if suffix.endswith('preview') else http.post(path+suffix,headers=h,json=body)
        assert action({}).status_code==401
        assert action(headers(token=TOKEN_B)).status_code==404
        assert action(headers() | {'Origin':'https://evil.example'}).status_code==403
    for extra in ({'actor':'other'}, {'explicit_authorization':True}, {'expected_working_revision':None}, {'expected_working_revision':True}):
        assert confirm(http,path,body|extra).status_code==422
    h=headers();h.pop('Idempotency-Key')
    assert http.post(path+'/confirmations',headers=h,json=body).status_code==422


def test_rules_change_and_same_source_conflict(http,db,app):
    path=generated(http,db); p=preview(http,path); old=request(p)
    newer=ConfirmationService(unit_of_work(db),rules_version='confirmation-v2-test')
    app.dependency_overrides[confirmation_service]=lambda:newer
    assert confirm(http,path,old).json()['error']['code']=='risk_not_acknowledged'
    q=preview(http,path); assert q['risk_hash']!=p['risk_hash']
    assert confirm(http,path,request(q)).status_code==201
    app.dependency_overrides[confirmation_service]=lambda:ConfirmationService(unit_of_work(db))
    blocked=preview(http,path)
    assert not blocked['confirmation_allowed'] and 'snapshot_disclosure_conflict' in blocked['blocking_reasons']
    assert confirm(http,path,old).json()['error']['code']=='snapshot_disclosure_conflict'
    print('E17 backend: same-version rule update rejects old risk hash; already-confirmed source cannot acquire different disclosure')


@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_http(app,http,db,same_key):
    path=generated(http,db); body=request(preview(http,path)); key=str(uuid4())
    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as c:
            return await asyncio.gather(*(c.post(path+'/confirmations',json=body,headers=headers(key if same_key else None)) for _ in range(6)))
    results=run(race()); assert all(r.status_code==201 for r in results),[r.text for r in results]
    assert len({r.json()['snapshot']['snapshot']['snapshot_id'] for r in results})==1
    assert len({r.json()['operation_id'] for r in results})==(1 if same_key else 6)
    assert run(facts(db,path.split('/')[-1]))['confirmed_snapshots']==1
    assert confirm(http,path,body|dict(explicit_confirmation=False),key).json()['error']['code']==('idempotency_key_reused' if same_key else 'explicit_confirmation_required')


@pytest.mark.parametrize('failure_at',['disclosure','pointer','operation','command','commit'])
def test_real_database_rollback(http,db,app,failure_at):
    path=generated(http,db); body=request(preview(http,path)); tid=path.split('/')[-1]; before=run(facts(db,tid))
    @asynccontextmanager
    async def broken(actor):
        async with connection(db) as conn, conn.transaction():
            repo=PostgresConfirmationRepository(conn,actor)
            name={'disclosure':'save_disclosure','pointer':'point_snapshot','operation':'save_receipt','command':'save_command','commit':'save_command'}[failure_at]
            original=getattr(repo,name)
            async def fail(*args,**kwargs):
                await original(*args,**kwargs)
                if failure_at=='commit':
                    # Real deferred FK violation, raised by PostgreSQL at COMMIT.
                    await conn.execute('CREATE TEMP TABLE m105_commit_failure (id text REFERENCES easytravel.trips(trip_id) DEFERRABLE INITIALLY DEFERRED)')
                    await conn.execute("INSERT INTO m105_commit_failure VALUES ('missing-parent-m105')")
                else:
                    await conn.execute('SELECT 1/0')
            setattr(repo,name,fail)
            yield repo
    app.dependency_overrides[confirmation_service]=lambda:ConfirmationService(broken)
    key=str(uuid4()); r=confirm(http,path,body,key)
    assert r.status_code==500 and r.json()=={'error':{'code':'internal_error','message':'Internal server error.'}}
    assert run(facts(db,tid))==before
    app.dependency_overrides[confirmation_service]=lambda:ConfirmationService(unit_of_work(db))
    assert confirm(http,path,body,key).status_code==201
    print('PostgreSQL failure at '+failure_at+': snapshot/disclosure/pointer/both receipts rolled back; same key retry succeeds')


def test_database_unavailable_and_internal_errors(http,app,db):
    from easytravel.backend.infrastructure.database import DatabaseUnavailable
    path=generated(http,db); body=request(preview(http,path))
    for error,status,code in [(DatabaseUnavailable('DSN-secret'),503,'business_dependency_unavailable'),(RuntimeError('SQL token-secret'),500,'internal_error')]:
        @asynccontextmanager
        async def broken(actor):
            raise error
            yield
        app.dependency_overrides[confirmation_service]=lambda:ConfirmationService(broken)
        for r in [http.get(path+'/confirmation-preview',headers=headers()),confirm(http,path,body)]:
            assert r.status_code==status and r.json()['error']['code']==code and 'secret' not in r.text


def test_cross_process_replay_after_intake_changed(http,db):
    path=generated(http,db); body=request(preview(http,path)); key=str(uuid4())
    result=confirm(http,path,body,key).json()
    t=http.get(path,headers=headers()).json()
    assert http.put(path+'/requirements',headers=headers(),json=dict(expected_intake_revision=1,expected_working_revision=1,requirements=t['intake']['input'])).status_code==200
    assert http.get(path,headers=headers()).json()['plan_confirmation']=='UNCONFIRMED'
    assert confirm(http,path,body,key).json()==result  # replay precedes stale intake
    code='''import asyncio,json,os,sys
from easytravel.backend.infrastructure.database import DatabaseSettings
from easytravel.backend.infrastructure.confirmations import unit_of_work
from easytravel.backend.application.confirmations import ConfirmationService
from easytravel.backend.domain.confirmation import ConfirmRequest
async def main():
 d=json.load(sys.stdin);s=ConfirmationService(unit_of_work(DatabaseSettings(url=os.environ['EASYTRAVEL_M105_TEST_DATABASE_URL'])))
 r=await s.confirm(d['trip_id'],ConfirmRequest.model_validate(d['body']),key=d['key'],actor='m101_owner_a')
 print(r.model_dump_json())
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(main())
'''
    payload=json.dumps(dict(trip_id=path.split('/')[-1],body=body,key=key))
    children=[subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8') for _ in range(2)]
    for child in children:
        out,err=child.communicate(payload,timeout=25)
        assert child.returncode==0,err
        assert json.loads(out)==result
    assert run(facts(db,path.split('/')[-1]))['confirmed_snapshots']==1
    assert confirm(http,path,body).json()['error']['code']=='stale_intake'
    Path('Docs/M1-05a_跨进程重放.json').write_text(json.dumps(dict(result=result,independentProcesses=2,replayBeforeRevisionCheck=True),ensure_ascii=False,indent=2),encoding='utf-8')


def test_legacy_snapshot_not_backfilled(http,db):
    from psycopg.types.json import Jsonb
    path=generated(http,db); p=preview(http,path); tid=path.split('/')[-1]
    async def seed_legacy():
        async with connection(db) as conn, conn.transaction():
            await conn.execute("""INSERT INTO easytravel.confirmed_snapshots
                (snapshot_id,trip_id,confirmed_by,source_working_revision,disclosure_hash,budget,confirmed_at)
                VALUES (%s,%s,%s,1,%s,%s,clock_timestamp())""",(str(uuid4()),tid,ACTOR_A,p['disclosure_hash'],Jsonb(p['budget'])))
    run(seed_legacy())
    blocked=preview(http,path)
    assert not blocked['confirmation_allowed'] and 'snapshot_disclosure_conflict' in blocked['blocking_reasons']
    r=confirm(http,path,request(p));assert r.status_code==409 and r.json()['error']['code']=='snapshot_disclosure_conflict'
    assert run(facts(db,tid))['confirmation_disclosures']==0


def test_old_revision_cannot_replace_new_pointer(http,db):
    path=generated(http,db); p=preview(http,path); tid=path.split('/')[-1]
    async def advance():
        async with connection(db) as conn, conn.transaction():
            await conn.execute("SELECT trip_id FROM easytravel.trips WHERE trip_id=%s FOR UPDATE",(tid,))
            await conn.execute("""INSERT INTO easytravel.draft_versions
                (trip_id,owner_id,revision,requirements_revision,requirements,payload,item_confirmations)
                SELECT trip_id,owner_id,2,requirements_revision,requirements,payload,item_confirmations
                FROM easytravel.draft_versions WHERE trip_id=%s AND revision=1""",(tid,))
            await conn.execute('UPDATE easytravel.working_drafts SET revision=2 WHERE trip_id=%s',(tid,))
    run(advance())
    assert confirm(http,path,request(p)).json()['error']['code']=='stale_revision'
    t=http.get(path,headers=headers()).json()
    assert t['working']['revision']==2 and t['latest_confirmed_version_id'] is None
    p=preview(http,path);assert not p['confirmation_allowed'] and 'draft_not_ready' in p['blocking_reasons']
