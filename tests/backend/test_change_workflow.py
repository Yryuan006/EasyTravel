"""M1-06a real PostgreSQL change intent, exact authorization and atomic decision gates."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4
import httpx
import pytest
from test_generations import services, create, accept, step, run, headers, ACTOR_A, TOKEN_B
from test_generations import app as generation_app
from test_confirmations import generated, preview, request as confirm_request, confirm
from easytravel.backend.api.v1.trips import change_service, confirmation_service
from easytravel.backend.application.changes import ChangeService
from easytravel.backend.application.confirmations import ConfirmationService
from easytravel.backend.domain.changes import ApproveRequest
from easytravel.backend.infrastructure.changes import unit_of_work, PostgresChangeRepository
from easytravel.backend.infrastructure.confirmations import unit_of_work as confirmation_uow
from easytravel.backend.infrastructure.database import DatabaseSettings, connection
from easytravel.backend.infrastructure.migrate import upgrade
from fastapi.testclient import TestClient


@pytest.fixture(scope='module')
def db():
    assert os.environ['EASYTRAVEL_ISOLATED_TRANSPORT_PORT']=='55434'
    settings=DatabaseSettings(url=os.environ['EASYTRAVEL_M106_TEST_DATABASE_URL'])
    run(upgrade(settings));return settings


@pytest.fixture
def app(generation_app,db):
    generation_app.dependency_overrides[change_service]=lambda:ChangeService(unit_of_work(db))
    generation_app.dependency_overrides[confirmation_service]=lambda:ConfirmationService(confirmation_uow(db))
    return generation_app


@pytest.fixture
def http(app):
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',50000),raise_server_exceptions=False,
        backend_options={'loop_factory':asyncio.SelectorEventLoop} if sys.platform=='win32' else {}) as c:yield c


def intent(http,path,text='第二天安排轻松一点',key=None):
    t=http.get(path,headers=headers()).json()
    body=dict(expected_intake_revision=t['intake']['intake_revision'],expected_working_revision=t['working']['revision'],intent=text,retry_of=None)
    response=http.post(path+'/change-intents',headers=headers(key),json=body)
    return response,body


def proposal(http,db,path,text='第二天安排轻松一点'):
    r,_=intent(http,path,text);assert r.status_code==202,r.text
    run(step(db))
    result=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()
    assert result['status']=='SUCCEEDED',result
    pid=result['result']['proposal_id']
    r=http.get(path+'/proposals/'+pid,headers=headers());assert r.status_code==200,r.text
    return path+'/proposals/'+pid,r.json()


def decision(p,approve=True):
    body=dict(expected_intake_revision=p['intake_revision'],expected_working_revision=p['proposal']['output']['base_working_revision'],
        seen_candidate_hash=p['proposal']['candidate_hash'],decision='approve' if approve else 'reject')
    if approve:body.update(item_ids=p['proposal']['touched_item_ids'],explicit_authorization=p['proposal']['level']=='L3')
    return body


async def facts(db,tid):
    async with connection(db) as c:
        return {t:await(await c.execute(f'SELECT * FROM easytravel.{t} WHERE trip_id=%s ORDER BY 1',(tid,))).fetchall()
            for t in ('change_proposals','proposal_contexts','proposal_decisions','specific_authorizations','draft_versions','draft_contexts','operation_receipts','api_commands','confirmed_snapshots','confirmation_disclosures','working_drafts')}


def test_activity_applies_without_grant_preserves_snapshot(http,db):
    path=generated(http,db);p=preview(http,path);assert confirm(http,path,confirm_request(p)).status_code==201
    tid=path.split('/')[-1];before=run(facts(db,tid))
    pp,p=proposal(http,db,path);assert p['proposal']['level']=='L2' and p['state']=='PENDING'
    assert p['differences'] and p['unknown_costs'] and p['budget']['verification']!='VERIFIED'
    read_before=run(facts(db,tid));assert http.get(pp,headers=headers()).status_code==200;assert run(facts(db,tid))==read_before
    r=http.post(pp+'/decisions',headers=headers(),json=decision(p));assert r.status_code==200,r.text
    t=http.get(path,headers=headers()).json();assert t['working']['revision']==2 and t['plan_confirmation']=='UNCONFIRMED' and t['synchronization']=='CURRENT'
    after=run(facts(db,tid));assert after['confirmed_snapshots']==before['confirmed_snapshots'] and after['confirmation_disclosures']==before['confirmation_disclosures']
    assert after['specific_authorizations']==[] and len(after['draft_versions'])==2
    assert t['latest_confirmed_version_id']==before['working_drafts'][0]['latest_confirmed_version_id']
    assert http.get(pp,headers=headers()).json()['state']=='APPLIED'


def hotel_intent(http,path):
    t=http.get(path,headers=headers()).json()
    return '更换酒店 '+next(i['id'] for i in t['working']['payload']['items'] if i['kind']=='hotel')


@pytest.mark.parametrize('bad',['missing','less','more','hash','string','number'])
def test_l3_exact_scope_and_strict_intent(http,db,bad):
    path=generated(http,db);pp,p=proposal(http,db,path,hotel_intent(http,path));assert p['proposal']['level']=='L3'
    body=decision(p)
    if bad=='missing':body['explicit_authorization']=False
    if bad=='less':body['item_ids']=['absent']
    if bad=='more':body['item_ids']+=['extra']
    if bad=='hash':body['seen_candidate_hash']='0'*64
    if bad=='string':body['explicit_authorization']='true'
    if bad=='number':body['explicit_authorization']=1
    before=run(facts(db,path.split('/')[-1]))
    r=http.post(pp+'/decisions',headers=headers(),json=body);assert r.status_code==(422 if bad in ('string','number') else 409),r.text
    assert run(facts(db,path.split('/')[-1]))==before


def test_l3_atomic_apply_replay_and_reject(http,db):
    path=generated(http,db);pp,p=proposal(http,db,path,hotel_intent(http,path));body=decision(p);key=str(uuid4())
    r=http.post(pp+'/decisions',headers=headers(key),json=body);assert r.status_code==200,r.text
    assert http.post(pp+'/decisions',headers=headers(key),json=body).json()==r.json()
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p,False)).json()['error']['code']=='proposal_already_decided'
    data=run(facts(db,path.split('/')[-1]));assert len(data['specific_authorizations'])==1 and data['specific_authorizations'][0]['consumed_by_operation_id']==r.json()['operation_id']
    assert len(data['proposal_decisions'])==1 and len(data['draft_versions'])==2
    pp2,p2=proposal(http,db,path);body2=decision(p2,False);key2=str(uuid4())
    before=http.get(path,headers=headers()).json()
    r=http.post(pp2+'/decisions',headers=headers(key2),json=body2);assert r.status_code==200
    assert http.post(pp2+'/decisions',headers=headers(key2),json=body2).json()==r.json()
    assert http.get(path,headers=headers()).json()==before
    assert http.get(pp2,headers=headers()).json()['state']=='REJECTED'
    assert http.post(pp2+'/decisions',headers=headers(),json=decision(p2)).json()['error']['code']=='proposal_already_decided'
    assert http.post(pp2+'/decisions',headers=headers(),json=body2|dict(explicit_authorization=True)).status_code==422


def test_stale_input_revision_parent_and_owner(http,db):
    path=generated(http,db);pp,p=proposal(http,db,path);other=generated(http,db)
    assert http.get(pp,headers={}).status_code==401
    assert http.get(pp,headers=headers(token=TOKEN_B)).status_code==404
    assert http.post(pp+'/decisions',headers=headers(token=TOKEN_B),json=decision(p)).status_code==404
    assert http.post(path+'/change-intents',headers=headers(token=TOKEN_B),json=dict(expected_intake_revision=1,expected_working_revision=1,intent='第二天安排轻松一点',retry_of=None)).status_code==404
    wrong=other+'/proposals/'+p['proposal']['proposal_id']
    assert http.get(wrong,headers=headers()).status_code==404
    assert http.post(wrong+'/decisions',headers=headers(),json=decision(p)).status_code==404
    for field in ['expected_working_revision','expected_intake_revision']:
        r=http.post(pp+'/decisions',headers=headers(),json=decision(p)|{field:99});assert r.status_code==409
    t=http.get(path,headers=headers()).json();req=t['intake']['input'];req['destination']['value']='苏州'
    assert http.put(path+'/requirements',headers=headers(),json=dict(expected_intake_revision=1,expected_working_revision=1,requirements=req)).status_code==200
    assert http.get(pp,headers=headers()).json()['state']=='STALE'
    assert intent(http,path)[0].json()['error']['code']=='scope_replan_not_supported'
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==409


def test_unknown_intent_failed_no_proposal(http,db):
    path=generated(http,db);r,_=intent(http,path,'随便改一下明年的城市');assert r.status_code==202
    run(step(db));state=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()
    assert state['status']=='FAILED' and state['error']['code']=='unsupported_change_intent'
    assert not run(facts(db,path.split('/')[-1]))['change_proposals']


@pytest.mark.parametrize('phase',['save_decision','commit'])
def test_atomic_rollback(http,db,app,phase):
    path=generated(http,db);pp,p=proposal(http,db,path,hotel_intent(http,path));tid=path.split('/')[-1];before=run(facts(db,tid))
    if phase=='commit':
        @asynccontextmanager
        async def broken(actor):
            async with unit_of_work(db)(actor) as repo:
                # The real operation receipt fires this deferred PostgreSQL trigger at COMMIT.
                await repo.conn.execute("CREATE FUNCTION pg_temp.fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'test commit failure'; END$$")
                await repo.conn.execute('CREATE CONSTRAINT TRIGGER m106_fail_commit AFTER INSERT ON easytravel.operation_receipts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pg_temp.fail_commit()')
                yield repo
        app.dependency_overrides[change_service]=lambda:ChangeService(broken)
        response=http.post(pp+'/decisions',headers=headers(),json=decision(p))
    else:
        async def broken(*args,**kwargs):raise RuntimeError('private DSN')
        with patch.object(PostgresChangeRepository,'save_decision',broken):response=http.post(pp+'/decisions',headers=headers(),json=decision(p))
    assert response.status_code==500 and 'private' not in response.text
    assert run(facts(db,tid))==before
    app.dependency_overrides[change_service]=lambda:ChangeService(unit_of_work(db))
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==200


def test_concurrent_replay_and_changed_command(http,db,app):
    path=generated(http,db);key=str(uuid4());r,body=intent(http,path,key=key);assert r.status_code==202
    assert http.post(path+'/change-intents',headers=headers(key),json=body).json()==r.json()
    assert http.post(path+'/change-intents',headers=headers(key),json=body|dict(intent='different')).json()['error']['code']=='idempotency_key_reused'
    run(step(db));pid=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()['result']['proposal_id']
    pp=path+'/proposals/'+pid;p=http.get(pp,headers=headers()).json();body=decision(p);key=str(uuid4())
    async def concurrent():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',50001)),base_url='http://127.0.0.1:8000') as c:
            return await asyncio.gather(*[c.post(pp+'/decisions',headers=headers(key),json=body) for _ in range(4)])
    replies=run(concurrent());assert all(r.status_code==200 for r in replies);assert all(r.json()==replies[0].json() for r in replies)
    assert http.post(pp+'/decisions',headers=headers(key),json=decision(p,False)).json()['error']['code']=='idempotency_key_reused'
    data=run(facts(db,path.split('/')[-1]));assert len(data['draft_versions'])==2 and len(data['proposal_decisions'])==1


@pytest.mark.parametrize('command',['删除活动','替换活动','调整活动时间','添加活动','修改活动标题'])
def test_supported_activity_commands(http,db,command):
    path=generated(http,db);t=http.get(path,headers=headers()).json();target=next(i['id'] for i in t['working']['payload']['items'] if i['kind']=='activity')
    pp,p=proposal(http,db,path,command+' '+target)
    assert p['proposal']['level']==('L1' if command=='修改活动标题' else 'L2')
    r=http.post(pp+'/decisions',headers=headers(),json=decision(p));assert r.status_code==200,r.text
    assert http.get(path,headers=headers()).json()['working']['revision']==2


@pytest.mark.parametrize('mode',['invalid_json','over_budget','out_of_dates','hotel_with_activity'])
def test_untrusted_candidate_and_complete_l3_scope(http,db,mode):
    from easytravel.backend.infrastructure.mock_changes import mock_change_adapter
    from easytravel.model_adapter import MockAdapter,ModelReply
    path=generated(http,db);r,_=intent(http,path,hotel_intent(http,path));assert r.status_code==202
    def factory(w,text):
        data=json.loads(mock_change_adapter(w,text).responses[0].text)
        if mode=='invalid_json':return MockAdapter(ModelReply('{broken'),ModelReply('{broken'))
        if mode=='over_budget':data['operations'][0]['costs'][0].update(category='estimate',amount_cents=999999999,unknown_reason=None)
        if mode=='out_of_dates':
            data['operations'][0]['replacement']['start']='2026-01-01T15:00:00+08:00'
        if mode=='hotel_with_activity':
            target=next(i.id for i in w.payload.items if i.kind=='activity')
            extra=json.loads(mock_change_adapter(w,'删除活动 '+target).responses[0].text)
            data['operations']+=extra['operations']
        return MockAdapter(ModelReply(json.dumps(data)))
    with patch('easytravel.backend.infrastructure.mock_changes.mock_change_adapter',factory):run(step(db))
    state=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()
    if mode!='hotel_with_activity':
        assert state['status']=='FAILED'
        assert state['error']['code']==('known_costs_exceed_hard_limit' if mode=='over_budget' else 'invalid_output')
        assert not run(facts(db,path.split('/')[-1]))['change_proposals']
    else:
        pp=path+'/proposals/'+state['result']['proposal_id'];p=http.get(pp,headers=headers()).json()
        assert p['proposal']['level']=='L3' and len(p['proposal']['touched_item_ids'])==2
        before=run(facts(db,path.split('/')[-1]));body=decision(p);body['item_ids']=body['item_ids'][:1]
        assert http.post(pp+'/decisions',headers=headers(),json=body).json()['error']['code']=='authorization_scope_mismatch'
        assert run(facts(db,path.split('/')[-1]))==before
        assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==200


@pytest.mark.parametrize('approve',[True,False])
def test_process_replay_reads_decision_proposal_and_draft(http,db,approve):
    path=generated(http,db);pp,p=proposal(http,db,path,hotel_intent(http,path));body=decision(p,approve);key=str(uuid4())
    r=http.post(pp+'/decisions',headers=headers(key),json=body);assert r.status_code==200,r.text
    code="""import asyncio,json,os,sys
from easytravel.backend.infrastructure.database import DatabaseSettings
from easytravel.backend.infrastructure.changes import unit_of_work
from easytravel.backend.application.changes import ChangeService
from easytravel.backend.domain.changes import ApproveRequest,RejectRequest
async def main():
 data=json.loads(sys.stdin.read());db=DatabaseSettings(url=os.environ['EASYTRAVEL_M106_TEST_DATABASE_URL']);s=ChangeService(unit_of_work(db))
 request=(ApproveRequest if data['body']['decision']=='approve' else RejectRequest).model_validate(data['body'])
 replay=await s.decide(data['trip'],data['proposal'],request,key=data['key'],actor=data['actor'])
 proposal=await s.get(data['trip'],data['proposal'],actor=data['actor'])
 async with unit_of_work(db)(data['actor']) as repo:trip=await repo.read_trip(data['trip'])
 print(json.dumps(dict(replay=replay.model_dump(mode='json'),proposal=proposal.model_dump(mode='json'),trip=trip.model_dump(mode='json'))))
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(main())
"""
    data=dict(trip=path.split('/')[-1],proposal=p['proposal']['proposal_id'],body=body,key=key,actor=ACTOR_A)
    child=subprocess.run([sys.executable,'-c',code],input=json.dumps(data),capture_output=True,text=True,encoding='utf-8',env=os.environ.copy()|dict(PYTHONUTF8='1'))
    assert child.returncode==0,child.stderr
    result=json.loads(child.stdout);assert result['replay']==r.json() and result['proposal']['state']==('APPLIED' if approve else 'REJECTED') and result['trip']['working']['revision']==(2 if approve else 1)
    from pathlib import Path
    Path('Docs/M1-06a_独立进程_'+result['proposal']['state']+'.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


def test_interrupted_change_run_recovers_without_proposal(http,db):
    path=generated(http,db);r,_=intent(http,path);assert r.status_code==202
    code="""import asyncio,os,json,sys
from easytravel.backend.infrastructure.database import DatabaseSettings,connection
from easytravel.backend.generation_worker import GenerationWorker
async def main():
 db=DatabaseSettings(url=os.environ['EASYTRAVEL_M106_TEST_DATABASE_URL']);worker=GenerationWorker(db)
 async with connection(db) as c:
  if sys.argv[1]=='claim':
   claimed=await worker.claim(c);assert claimed[0].kind=='CHANGE_INTENT';os._exit(75)
  print(json.dumps(await worker.recover(c)))
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
"""
    child=subprocess.run([sys.executable,'-c',code,'claim'],capture_output=True,text=True,env=os.environ.copy()|dict(PYTHONUTF8='1'))
    assert child.returncode==75,child.stderr
    assert http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()['status']=='RUNNING'
    recovered=subprocess.run([sys.executable,'-c',code,'recover'],capture_output=True,text=True,env=os.environ.copy()|dict(PYTHONUTF8='1'))
    assert recovered.returncode==0,recovered.stderr
    assert r.json()['run_id'] in json.loads(recovered.stdout)
    state=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()
    assert state['status']=='FAILED' and state['error']['code']=='worker_interrupted'
    assert not run(facts(db,path.split('/')[-1]))['change_proposals']


def test_concurrent_intent_replays_one_frozen_run_and_survives_process_exit(http,db,app):
    path=generated(http,db);key=str(uuid4());body=dict(expected_intake_revision=1,expected_working_revision=1,intent='第二天安排轻松一点',retry_of=None)
    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',50001)),base_url='http://127.0.0.1:8000') as c:
            return await asyncio.gather(*[c.post(path+'/change-intents',headers=headers(key),json=body) for _ in range(4)])
    responses=run(race());assert all(r.status_code==202 for r in responses);assert len({r.json()['run_id'] for r in responses})==1
    run(step(db));accepted=responses[0].json()
    code="""import asyncio,json,os,sys
from easytravel.backend.application.changes import ChangeService
from easytravel.backend.domain.changes import ChangeIntentRequest
from easytravel.backend.infrastructure.changes import unit_of_work
from easytravel.backend.infrastructure.database import DatabaseSettings
async def main():
 d=json.loads(sys.stdin.read());s=ChangeService(unit_of_work(DatabaseSettings(url=os.environ['EASYTRAVEL_M106_TEST_DATABASE_URL'])))
 print((await s.accept(d['trip'],ChangeIntentRequest.model_validate(d['body']),key=d['key'],actor=d['actor'])).model_dump_json())
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
"""
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps(dict(trip=accepted['trip_id'],body=body,key=key,actor=ACTOR_A)),capture_output=True,text=True,encoding='utf-8',env=os.environ.copy()|dict(PYTHONUTF8='1'))
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==accepted
    data=run(facts(db,accepted['trip_id']));assert len(data['change_proposals'])==1


def test_protected_activity_is_l3_and_stale_proposal_cannot_overwrite(http,db):
    from easytravel.backend.domain.models import ItemConfirmation,WorkingDraft
    from easytravel.backend.domain.rules import item_hash
    from easytravel.backend.infrastructure.generations import PostgresGenerationRepository
    original=PostgresGenerationRepository.attach
    async def attach(repo,draft,context,generation):
        item=next(i for i in draft.payload.items if i.kind=='activity')
        draft=WorkingDraft(**draft.model_dump(exclude={'item_confirmations'}),item_confirmations=(ItemConfirmation(item_id=item.id,item_hash=item_hash(item)),))
        await original(repo,draft,context,generation)
    with patch.object(PostgresGenerationRepository,'attach',attach):path=generated(http,db)
    t=http.get(path,headers=headers()).json();target=t['working']['item_confirmations'][0]['item_id']
    pp,p=proposal(http,db,path,'删除活动 '+target);assert p['proposal']['level']=='L3'
    pp2,p2=proposal(http,db,path,hotel_intent(http,path))
    assert http.post(pp2+'/decisions',headers=headers(),json=decision(p2)).status_code==200
    assert http.get(pp,headers=headers()).json()['state']=='STALE'
    before=run(facts(db,path.split('/')[-1]))
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).json()['error']['code']=='stale_revision'
    assert run(facts(db,path.split('/')[-1]))==before


def test_generation_frozen_base_cannot_override_new_intake(http,db):
    from easytravel.backend.generation_worker import GenerationWorker
    from easytravel.backend.infrastructure.mock_changes import mock_change_adapter
    path=generated(http,db);r,_=intent(http,path)
    async def scenario():
        async with connection(db) as conn:run_record,actor=await GenerationWorker(db).claim(conn)
        t=http.get(path,headers=headers()).json();req=t['intake']['input'];req['destination']['value']='苏州'
        assert http.put(path+'/requirements',headers=headers(),json=dict(expected_intake_revision=1,expected_working_revision=1,requirements=req)).status_code==200
        await ChangeService(unit_of_work(db)).execute(run_record,actor=actor,adapter_factory=mock_change_adapter)
    run(scenario())
    state=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json()
    assert state['status']=='FAILED' and state['error']['code']=='stale_input'
    assert not run(facts(db,path.split('/')[-1]))['change_proposals']
