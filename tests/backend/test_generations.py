"""M1-04a real PostgreSQL generation, transaction, concurrency and process recovery gates."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import pytest
import httpx
from test_intakes import run, input_data, headers, CONFIG, ACTOR_A, TOKEN_B, BASE
from easytravel.backend.main import create_app
from easytravel.backend.api.v1.trips import intake_service, generation_service
from easytravel.backend.application.intakes import IntakeService
from easytravel.backend.application.generations import GenerationService
from easytravel.backend.domain.intake import CreateTripRequest, UpdateRequirementsRequest
from easytravel.backend.domain.generation import GenerateRequest
from easytravel.backend.infrastructure.database import DatabaseSettings, connection
from easytravel.backend.infrastructure.intakes import unit_of_work as intake_uow
from easytravel.backend.infrastructure.generations import unit_of_work, PostgresGenerationRepository
from easytravel.backend.infrastructure.migrate import upgrade
from easytravel.backend.infrastructure.mock_planner import mock_adapter
from easytravel.backend.generation_worker import GenerationWorker
from easytravel.model_adapter import MockAdapter, ModelReply
from fastapi.testclient import TestClient

REQUEST = dict(expected_intake_revision=1,expected_working_revision=None,retry_of=None)

@pytest.fixture(scope='module')
def db():
    settings = DatabaseSettings(url=os.environ['EASYTRAVEL_M104_TEST_DATABASE_URL'])
    run(upgrade(settings))
    return settings

@pytest.fixture
def services(db):
    return IntakeService(intake_uow(db)),GenerationService(unit_of_work(db))

@pytest.fixture
def app(services):
    app = create_app(CONFIG)
    app.dependency_overrides[intake_service] = lambda: services[0]
    app.dependency_overrides[generation_service] = lambda: services[1]
    return app

@pytest.fixture
def http(app):
    with TestClient(app,base_url=BASE,client=('127.0.0.1',50000),raise_server_exceptions=False,
        backend_options={'loop_factory':asyncio.SelectorEventLoop} if sys.platform=='win32' else {}) as client:
        yield client

def create(http, complete=True):
    r=http.post('/api/v1/trips',json=input_data(complete),headers=headers())
    assert r.status_code==201,r.text
    return '/api/v1/trips/'+r.json()['trip_id']

async def step(db, factory=mock_adapter):
    async with connection(db) as conn:
        return await GenerationWorker(db,adapter_factory=factory).step(conn)

async def counts(db,tid):
    async with connection(db) as conn:
        return {t:(await (await conn.execute(f'SELECT count(*) n FROM easytravel.{t} WHERE trip_id=%s',(tid,))).fetchone())['n']
            for t in ('trips','trip_intake_versions','draft_versions','working_drafts','draft_contexts','confirmed_snapshots','generation_runs')}

def accept(http,path,body=None,key=None):
    r=http.post(path+'/generations',json=body or REQUEST,headers=headers(key))
    assert r.status_code==202,r.text
    assert r.json()['status']=='ACCEPTED' and r.json()['result'] is None
    return r.json()

def test_success_real_read_and_replay(http,db,services):
    path=create(http);key=str(uuid4());a=accept(http,path,key=key)
    assert http.get(path,headers=headers()).json()['active_run_id']==a['run_id']
    assert http.get(path+'/generations/'+a['run_id'],headers=headers()).json()['status']=='ACCEPTED'
    assert run(step(db))
    result=http.get(path+'/generations/'+a['run_id'],headers=headers()).json()
    assert result['status']=='SUCCEEDED' and result['result']==dict(working_revision=1,proposal_id=None)
    trip=http.get(path,headers=headers()).json();w=trip['working']
    assert trip['synchronization']=='CURRENT' and trip['active_run_id'] is None
    assert trip['plan_confirmation']=='UNCONFIRMED' and trip['latest_confirmed_version_id'] is None
    assert not trip['booked'] and trip['readiness']=='NOT_ASSESSED'
    assert w['revision']==w['requirements']['requirements_revision']==w['context']['intake_revision']==1
    assert w['context']['input']==input_data(True)['requirements'] and w['item_confirmations']==[]
    assert result['diagnostics_budget']['total_cents'] is None
    assert result['diagnostics_budget']['known_subtotal_cents']==60000
    assert result['diagnostics_budget']['verification']=='INDETERMINATE'
    assert result['diagnostics_budget']['verified_cost_ids']==[]
    assert any(r['code']=='unknown_costs' for r in w['context']['notes']['risks'])
    assert all(c['is_mock'] and c['evidence_id'] is None for c in w['payload']['costs'])
    assert accept(http,path,key=key)==a
    assert not run(step(db))
    c=run(counts(db,a['trip_id']));assert c['draft_versions']==c['working_drafts']==c['draft_contexts']==1 and c['confirmed_snapshots']==0
    # Independent Python process reads both run and complete Trip from the formal DB.
    code="""import json,asyncio,os;from easytravel.backend.infrastructure.database import DatabaseSettings;from easytravel.backend.infrastructure.intakes import unit_of_work as iu;from easytravel.backend.infrastructure.generations import unit_of_work as gu;from easytravel.backend.application.intakes import IntakeService;from easytravel.backend.application.generations import GenerationService
async def main():
 s=DatabaseSettings(url=os.environ['EASYTRAVEL_M104_TEST_DATABASE_URL'])
 t=await IntakeService(iu(s)).get(os.environ['READ_TRIP'],actor='m101_owner_a')
 r=await GenerationService(gu(s)).get(t.trip_id,os.environ['READ_RUN'],actor='m101_owner_a')
 print(json.dumps(dict(trip=t.model_dump(mode='json'),run=r.model_dump(mode='json')),ensure_ascii=False))
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(main())
"""
    child=subprocess.run([sys.executable,'-c',code],env=os.environ|dict(READ_TRIP=a['trip_id'],READ_RUN=a['run_id'],PYTHONUTF8='1'),capture_output=True,text=True,check=True)
    assert json.loads(child.stdout)==dict(trip=trip,run=result)
    Path('Docs/M1-04a_独立进程读取.json').write_text(child.stdout,encoding='utf-8')
    print('E04 E06 E12: 202 ACCEPTED -> SUCCEEDED; r1/context atomic; independent process matches; replay retains original 202')

@pytest.mark.parametrize('case',['incomplete','stale','replan','extra','missing_key'])
def test_rejections(http,db,case):
    path=create(http,case!='incomplete');body=REQUEST.copy();h=headers()
    if case=='stale':body['expected_intake_revision']=2
    if case=='replan':body['expected_working_revision']=1
    if case=='extra':body['kind']='CHANGE_INTENT'
    if case=='missing_key':h.pop('Idempotency-Key')
    r=http.post(path+'/generations',json=body,headers=h)
    assert r.status_code==(422 if case in ('extra','missing_key') else 409),r.text
    assert run(counts(db,path.split('/')[-1]))['generation_runs']==0

@pytest.mark.parametrize('mode',['invalid_json','duplicate_keys','dangling','hotel','overlap','outside','not_mock','quote','over_budget'])
def test_invalid_and_budget(http,db,mode):
    path=create(http);a=accept(http,path)
    frozen=run(IntakeService(intake_uow(db)).get(a['trip_id'],actor=ACTOR_A)).intake.input
    payload=json.loads(mock_adapter(frozen).responses[0].text)
    if mode=='dangling':payload['costs'][0]['item_id']='missing'
    if mode=='hotel':payload['items'][-1].pop('rooms')
    if mode=='overlap':payload['items'][1]['start']=payload['items'][0]['start'];payload['items'][1]['end']=payload['items'][0]['end']
    if mode=='outside':payload['items'][0]['start']='2026-12-03T11:00:00+08:00'
    if mode=='not_mock':payload['costs'][0]['is_mock']=False
    if mode=='quote':payload['costs'][-1]['category']='quote'
    if mode=='over_budget':payload['costs'][-1]['amount_cents']=230001
    raw='{"items": [], "items": []}' if mode=='duplicate_keys' else 'secret INVALID' if mode=='invalid_json' else json.dumps(payload)
    adapter=MockAdapter(ModelReply(raw),ModelReply(raw))
    assert run(step(db,lambda _:adapter))
    r=http.get(path+'/generations/'+a['run_id'],headers=headers()).json()
    assert r['status']=='FAILED' and r['error']['code']==('known_costs_exceed_hard_limit' if mode=='over_budget' else 'invalid_output'),r
    assert 'secret' not in json.dumps(r)
    c=run(counts(db,a['trip_id']));assert c['draft_versions']==c['working_drafts']==c['draft_contexts']==0 and c['trip_intake_versions']==1
    if mode in ('invalid_json','duplicate_keys','dangling','hotel'):assert adapter.calls==2
    if mode=='over_budget':assert r['diagnostics_budget']['constraint']=='OVER_LIMIT'


def test_owner_path_and_idempotency(http,db):
    path=create(http);other=create(http);key=str(uuid4());a=accept(http,path,key=key)
    url=path+'/generations/'+a['run_id']
    assert http.get(url,headers={'Authorization':'Bearer forged'}).status_code==401
    assert http.get(url,headers=headers(token=TOKEN_B)).status_code==404
    assert http.get(other+'/generations/'+a['run_id'],headers=headers()).status_code==404
    assert http.post(path+'/generations',json=REQUEST,headers=headers(token=TOKEN_B)).status_code==404
    assert http.post(path+'/generations',json=REQUEST|dict(expected_intake_revision=2),headers=headers(key)).json()['error']['code']=='idempotency_key_reused'
    assert http.post(path+'/generations',json=REQUEST,headers=headers()).json()['error']['code']=='generation_in_progress'
    run(step(db))
    assert http.post(path+'/generations',json=REQUEST,headers=headers()).json()['error']['code']=='scope_replan_not_supported'


def test_concurrent_idempotency(app,db,http):
    path=create(http);key=str(uuid4())
    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url=BASE) as client:
            return await asyncio.gather(*[client.post(path+'/generations',json=REQUEST,headers=headers(key)) for _ in range(6)])
    results=run(race());assert all(r.status_code==202 for r in results)
    assert len({r.json()['run_id'] for r in results})==1
    assert run(counts(db,path.split('/')[-1]))['generation_runs']==1
    run(step(db))


def test_stale_input_during_model_no_transaction_lock(http,db,services):
    path=create(http);a=accept(http,path)
    async def scenario():
        started,release=asyncio.Event(),asyncio.Event()
        class Blocked:
            async def generate(self,*,repair):
                started.set();await release.wait()
                return mock_adapter((await services[0].get(a['trip_id'],actor=ACTOR_A)).intake.input).responses[0]
        task=asyncio.create_task(step(db,lambda _:Blocked()))
        await started.wait()
        old=await services[0].get(a['trip_id'],actor=ACTOR_A)
        data=old.intake.input.model_dump(mode='json');data['destination']['value']='苏州'
        await services[0].update(a['trip_id'],UpdateRequirementsRequest.model_validate(dict(expected_intake_revision=1,expected_working_revision=None,requirements=data)),key=str(uuid4()),actor=ACTOR_A)
        release.set();await task
    run(scenario())
    r=http.get(path+'/generations/'+a['run_id'],headers=headers()).json()
    assert r['status']=='FAILED' and r['error']['code']=='stale_input'
    c=run(counts(db,a['trip_id']));assert c['trip_intake_versions']==2 and c['draft_versions']==0


@pytest.mark.parametrize('fault',['context','commit'])
def test_atomic_rollback(http,db,services,fault):
    path=create(http);a=accept(http,path)
    base=unit_of_work(db)
    @asynccontextmanager
    async def broken(actor):
        async with base(actor) as repo:
            original=repo.attach
            async def attach(*args):
                await original(*args)
                if fault=='context':
                    await repo.conn.execute('SELECT 1/0')
                else:
                    # Deferred real FK failure at COMMIT, after version/context/result writes.
                    await repo.conn.execute('SET CONSTRAINTS ALL DEFERRED')
                    await repo.conn.execute("UPDATE easytravel.working_drafts SET revision=999 WHERE trip_id=%s",(a['trip_id'],))
            repo.attach=attach
            yield repo
    worker=GenerationWorker(db);worker.service=GenerationService(broken)
    async def execute():
        async with connection(db) as conn:await worker.step(conn)
    run(execute())
    r=http.get(path+'/generations/'+a['run_id'],headers=headers()).json()
    assert r['status']=='FAILED' and r['error']['code']=='persistence_failed',r
    c=run(counts(db,a['trip_id']));assert c['draft_versions']==c['working_drafts']==c['draft_contexts']==0


def test_context_immutable_and_second_submit(http,db,services):
    path=create(http);a=accept(http,path);run(step(db))
    current=run(services[1].get(a['trip_id'],a['run_id'],actor=ACTOR_A))
    frozen=run(services[1].frozen_input(current,actor=ACTOR_A))
    from easytravel.model_adapter import parse_output
    from easytravel.contracts import PlanDraftOutput
    payload=parse_output(mock_adapter(frozen).responses[0].text,PlanDraftOutput)
    run(services[1].attach_initial_draft(current,payload,frozen,actor=ACTOR_A))
    assert run(counts(db,a['trip_id']))['draft_versions']==1
    async def mutate():
        async with connection(db) as conn,conn.transaction():
            await conn.execute('UPDATE easytravel.draft_contexts SET context=context WHERE trip_id=%s',(a['trip_id'],))
    with pytest.raises(Exception):run(mutate())
    assert http.get(path,headers=headers()).json()['synchronization']=='CURRENT'


def wait_file(path, process):
    deadline=time.monotonic()+8
    while not path.exists():
        assert process.poll() is None, process.stderr.read().decode('utf-8',errors='replace')
        assert time.monotonic()<deadline, 'process barrier timed out'
        time.sleep(.01)

@pytest.mark.parametrize('mode',['accepted','running','committed'])
def test_real_process_interruption_restart_replay(http,db,tmp_path,mode):
    env=os.environ|dict(EASYTRAVEL_DATABASE_URL=db.url.get_secret_value(),PROCESS_BARRIER=str(tmp_path),PROCESS_MODE=mode,PYTHONUTF8='1',PYTHONPATH=str(Path.cwd()))
    child=subprocess.Popen([sys.executable,'tests/backend/generation_process.py'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        wait_file(tmp_path/'ready',child)
        # A second actual executor cannot recover or claim while this session owns the lock.
        second=subprocess.run([sys.executable,'-m','easytravel.backend.generation_worker','--once'],env=env,capture_output=True,text=True)
        assert second.returncode!=0 and 'generation_executor_already_running' in second.stderr
        path=create(http);key=str(uuid4());a=accept(http,path,key=key)
        if mode!='accepted':
            (tmp_path/'claim').touch()
            wait_file(tmp_path/('running' if mode=='running' else 'committed'),child)
        before=http.get(path+'/generations/'+a['run_id'],headers=headers()).json()
        assert before['status']==dict(accepted='ACCEPTED',running='RUNNING',committed='SUCCEEDED')[mode]
        if mode=='committed':child.wait(timeout=5)
        else:child.kill();child.wait(timeout=5)
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
    restarted=subprocess.run([sys.executable,'-m','easytravel.backend.generation_worker','--once'],env=env,capture_output=True,text=True,check=True)
    after=http.get(path+'/generations/'+a['run_id'],headers=headers()).json()
    assert after['status']==('SUCCEEDED' if mode=='committed' else 'FAILED')
    assert accept(http,path,key=key)==a
    if mode!='committed':
        assert after['error']['code']=='worker_interrupted'
        assert http.post(path+'/generations',json=REQUEST,headers=headers()).json()['error']['code']=='explicit_retry_required'
        assert run(counts(db,a['trip_id']))['draft_versions']==0
        retry=accept(http,path,REQUEST|dict(retry_of=a['run_id']))
        assert retry['attempt']==2 and retry['run_id']!=a['run_id']
        run(step(db))
        assert http.get(path+'/generations/'+retry['run_id'],headers=headers()).json()['status']=='SUCCEEDED'
    assert run(counts(db,a['trip_id']))['draft_versions']==1
    evidence=dict(mode=mode,before=before,after=after,restart_stdout=restarted.stdout,counts=run(counts(db,a['trip_id'])))
    Path('Docs/M1-04a_恢复_'+mode+'.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print('E20 actual process kill/exit:',mode,'->',after['status'],'; replay and explicit retry verified')


def test_retry_requires_failed_same_input_and_parent(http,db,services):
    path=create(http);a=accept(http,path)
    # active retry rejected, then recover creates a persisted FAILED predecessor.
    assert http.post(path+'/generations',json=REQUEST|dict(retry_of=a['run_id']),headers=headers()).status_code==409
    async def recover():
        async with connection(db) as conn:await GenerationWorker(db).recover(conn)
    run(recover())
    other=create(http)
    assert http.post(other+'/generations',json=REQUEST|dict(retry_of=a['run_id']),headers=headers()).status_code==404
    t=http.get(path,headers=headers()).json()
    body=dict(expected_intake_revision=1,expected_working_revision=None,requirements=t['intake']['input'])
    assert http.put(path+'/requirements',json=body,headers=headers()).status_code==200
    assert http.post(path+'/generations',json=REQUEST|dict(expected_intake_revision=2,retry_of=a['run_id']),headers=headers()).json()['error']['code']=='invalid_retry'
    b=accept(http,path,REQUEST|dict(expected_intake_revision=2));run(step(db))
    trip=http.get(path,headers=headers()).json()
    assert trip['working']['context']['intake_revision']==2 and trip['working']['requirements']['requirements_revision']==1


@pytest.mark.parametrize('days',[1,5])
def test_supported_duration_boundaries(http,db,days):
    body=input_data(True);end='2026-12-04' if days==1 else '2026-12-08'
    body['requirements']['dates']['value']['end_date']=end
    body['requirements']['days']['value']=days
    r=http.post('/api/v1/trips',json=body,headers=headers());assert r.status_code==201
    path='/api/v1/trips/'+r.json()['trip_id'];a=accept(http,path);run(step(db))
    result=http.get(path+'/generations/'+a['run_id'],headers=headers()).json();assert result['status']=='SUCCEEDED'
    trip=http.get(path,headers=headers()).json();hotels=[i for i in trip['working']['payload']['items'] if i['kind']=='hotel']
    assert len(hotels)==(0 if days==1 else 1)
    assert result['diagnostics_budget']['known_subtotal_cents']==30000*(days-1)


def test_different_key_concurrency(app,http,db):
    path=create(http)
    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url=BASE) as client:
            return await asyncio.gather(*[client.post(path+'/generations',json=REQUEST,headers=headers()) for _ in range(5)])
    responses=run(race())
    assert [r.status_code for r in responses].count(202)==1
    assert [r.status_code for r in responses].count(409)==4
    assert run(counts(db,path.split('/')[-1]))['generation_runs']==1
    run(step(db))
