"""M1-06b real PostgreSQL v2 replanning, retry and authorization regression."""
import os,json,asyncio,subprocess,sys
from uuid import uuid4
from unittest.mock import patch
from contextlib import asynccontextmanager
import pytest,httpx
from test_change_workflow import app as change_app,services,generation_app,http,proposal,decision,facts
from test_confirmations import generated,preview,request as confirmation_request,confirm
from test_generations import run,headers,step,ACTOR_A,TOKEN_B,create,accept
from easytravel.backend.infrastructure.database import DatabaseSettings,connection
from easytravel.backend.infrastructure.migrate import upgrade
from easytravel.backend.infrastructure.changes import unit_of_work,PostgresChangeRepository
from easytravel.backend.application.changes import ChangeService
from easytravel.backend.api.v1.trips import change_service


@pytest.fixture(scope='module')
def db():
    assert os.environ['EASYTRAVEL_ISOLATED_TRANSPORT_PORT']=='55434'
    db=DatabaseSettings(url=os.environ['EASYTRAVEL_M107_TEST_DATABASE_URL']);run(upgrade(db));return db


@pytest.fixture
def app(change_app):return change_app


def update(http,path,changes=None):
    t=http.get(path,headers=headers()).json();i=t['intake']['input']
    i.update(changes or dict(dates=dict(state='provided',value=dict(mode='fixed',start_date='2026-12-11',end_date='2026-12-13'))))
    r=http.put(path+'/requirements',headers=headers(),json=dict(expected_intake_revision=t['intake']['intake_revision'],expected_working_revision=t['working']['revision'],requirements=i))
    assert r.status_code==200,r.text
    return r.json()


def replan(http,db,path,*,execute=True,key=None,retry=None):
    t=http.get(path,headers=headers()).json();body=dict(expected_intake_revision=t['intake']['intake_revision'],expected_working_revision=t['working']['revision'],retry_of=retry)
    r=http.post(path+'/generations',headers=headers(key),json=body);assert r.status_code==202,r.text
    if not execute:return r.json(),body
    run(step(db));g=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json();assert g['status']=='SUCCEEDED',g
    pp=path+'/proposals/'+g['result']['proposal_id'];p=http.get(pp,headers=headers());assert p.status_code==200,p.text
    return pp,p.json()


def test_date_replan_preserves_confirmed_history(http,db):
    path=generated(http,db);p=preview(http,path);assert confirm(http,path,confirmation_request(p)).status_code==201
    before=run(facts(db,path.split('/')[-1]));old=before['draft_versions'][0]
    t=update(http,path);assert t['synchronization']=='STALE' and t['working']['revision']==1
    assert http.post(path+'/confirmations',headers=headers(),json=confirmation_request(p)).status_code==409
    pp,p=replan(http,db,path);v=p['context_v2']
    assert v['schema_version']==2 and v['base_intake_revision']==2 and p['proposal']['requirements_revision']==1
    assert v['candidate_requirements']['requirements_revision']==2 and v['candidate_requirements']['start_date']=='2026-12-11'
    assert p['proposal']['output']['kind']=='requirements_replan' and p['proposal']['level']=='L3' and p['differences']
    assert p['budget']['verification']!='VERIFIED' and p['unknown_costs']
    assert http.get(path,headers=headers()).json()['working']['revision']==1
    r=http.post(pp+'/decisions',headers=headers(),json=decision(p));assert r.status_code==200,r.text
    t=http.get(path,headers=headers()).json();assert t['working']['revision']==2 and t['synchronization']=='CURRENT' and t['plan_confirmation']=='UNCONFIRMED'
    assert t['working']['requirements']['requirements_revision']==2 and t['working']['context']['intake_revision']==2
    after=run(facts(db,t['trip_id']));assert old in after['draft_versions']
    assert after['confirmed_snapshots']==before['confirmed_snapshots'] and after['confirmation_disclosures']==before['confirmation_disclosures']
    assert len(after['specific_authorizations'])==1 and after['specific_authorizations'][0]['consumed_by_operation_id']==r.json()['operation_id']


@pytest.mark.parametrize('fault',['missing','less','more','hash','intake','working','string','number'])
def test_exact_l3_and_version_guards(http,db,fault):
    path=generated(http,db);update(http,path);pp,p=replan(http,db,path);b=decision(p)
    if fault=='missing':b['explicit_authorization']=False
    if fault=='less':b['item_ids']=b['item_ids'][:-1]
    if fault=='more':b['item_ids']+=['extra']
    if fault=='hash':b['seen_candidate_hash']='0'*64
    if fault=='intake':b['expected_intake_revision']=1
    if fault=='working':b['expected_working_revision']=2
    if fault=='string':b['explicit_authorization']='true'
    if fault=='number':b['explicit_authorization']=1
    before=run(facts(db,path.split('/')[-1]));r=http.post(pp+'/decisions',headers=headers(),json=b)
    assert r.status_code==(422 if fault in ('string','number') else 409),r.text
    assert run(facts(db,path.split('/')[-1]))==before


def test_preference_only_payload_no_change_preserves_protection(http,db):
    from easytravel.backend.infrastructure.generations import PostgresGenerationRepository
    from easytravel.backend.domain.models import WorkingDraft,ItemConfirmation
    from easytravel.backend.domain.rules import item_hash
    original=PostgresGenerationRepository.attach
    async def attach(repo,draft,context,g):
        i=draft.payload.items[0]
        draft=WorkingDraft(**draft.model_dump(exclude={'item_confirmations'}),item_confirmations=(ItemConfirmation(item_id=i.id,item_hash=item_hash(i)),))
        await original(repo,draft,context,g)
    with patch.object(PostgresGenerationRepository,'attach',attach):path=generated(http,db)
    old=http.get(path,headers=headers()).json()['working']
    update(http,path,dict(hotel=dict(state='provided',value='更安静的房间')))
    pp,p=replan(http,db,path)
    assert p['differences']==[] and p['proposal']['candidate']==old['payload']
    assert set(p['proposal']['touched_item_ids'])=={i['id'] for i in old['payload']['items']}
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==200
    t=http.get(path,headers=headers()).json();assert t['working']['item_confirmations']==old['item_confirmations']
    assert t['working']['context']['input']['hotel']['value']=='更安静的房间'


def test_reject_keeps_new_intake_and_replays(http,db):
    path=generated(http,db);update(http,path);pp,p=replan(http,db,path);key=str(uuid4());body=decision(p,False)
    r=http.post(pp+'/decisions',headers=headers(key),json=body);assert r.status_code==200
    assert http.post(pp+'/decisions',headers=headers(key),json=body).json()==r.json()
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).json()['error']['code']=='proposal_already_decided'
    t=http.get(path,headers=headers()).json();assert t['intake']['intake_revision']==2 and t['working']['revision']==1 and t['synchronization']=='STALE'
    assert not run(facts(db,t['trip_id']))['specific_authorizations']


def test_over_budget_and_explicit_retry_policy(http,db):
    path=generated(http,db);update(http,path,dict(hard_limit_cents=dict(state='provided',value=1),target_budget_cents=dict(state='no_preference',value=None)))
    r,body=replan(http,db,path,execute=False);run(step(db))
    g=http.get(path+'/generations/'+r['run_id'],headers=headers()).json();assert g['status']=='FAILED' and not g['error']['retryable']
    assert http.post(path+'/generations',headers=headers(),json=body).json()['error']['code']=='explicit_retry_required'
    assert http.post(path+'/generations',headers=headers(),json=body|dict(retry_of=r['run_id'])).json()['error']['code']=='invalid_retry'
    assert not run(facts(db,path.split('/')[-1]))['change_proposals']


def test_v1_and_v2_hash_parent_and_owner(http,db):
    from easytravel.backend.domain.hashing import digest
    path=generated(http,db);v1,p1=proposal(http,db,path)
    raw=dict(p1['proposal']);raw.pop('candidate_hash');assert digest(raw)==p1['proposal']['candidate_hash']
    update(http,path);pp,p=replan(http,db,path)
    raw=dict(p['proposal']);raw.pop('candidate_hash');assert digest(raw)!=p['proposal']['candidate_hash']
    assert http.post(v1+'/decisions',headers=headers(),json=decision(p1)).status_code==409
    assert http.get(pp,headers=headers(token=TOKEN_B)).status_code==404
    assert http.post(pp+'/decisions',headers=headers(token=TOKEN_B),json=decision(p)).status_code==404
    other=generated(http,db);wrong=other+'/proposals/'+p['proposal']['proposal_id']
    assert http.get(wrong,headers=headers()).status_code==404
    assert http.post(wrong+'/decisions',headers=headers(),json=decision(p)).status_code==404


def test_commit_failure_all_rolls_back(http,db,app):
    from easytravel.backend.infrastructure.database import connection
    path=generated(http,db);update(http,path);pp,p=replan(http,db,path);before=run(facts(db,path.split('/')[-1]))
    @asynccontextmanager
    async def broken(actor):
        async with unit_of_work(db)(actor) as repo:
            await repo.conn.execute("CREATE FUNCTION pg_temp.fail_replan() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'test commit failure'; END$$")
            await repo.conn.execute('CREATE CONSTRAINT TRIGGER fail_replan AFTER INSERT ON easytravel.operation_receipts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pg_temp.fail_replan()')
            yield repo
    app.dependency_overrides[change_service]=lambda:ChangeService(broken)
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==500
    assert run(facts(db,path.split('/')[-1]))==before
    app.dependency_overrides[change_service]=lambda:ChangeService(unit_of_work(db))
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==200


@pytest.mark.parametrize('kind',['INITIAL_PLAN','CHANGE_INTENT','REPLAN'])
def test_uniform_explicit_retry(http,db,kind):
    from easytravel.model_adapter import MockAdapter,ModelReply
    from easytravel.backend.generation_worker import GenerationWorker
    bad=lambda *args:MockAdapter(ModelReply('{broken'),ModelReply('{broken'))
    path=create(http) if kind=='INITIAL_PLAN' else generated(http,db)
    if kind=='REPLAN':update(http,path)
    t=http.get(path,headers=headers()).json()
    body=dict(expected_intake_revision=t['intake']['intake_revision'],expected_working_revision=t['working']['revision'] if t['working'] else None,retry_of=None)
    suffix='/change-intents' if kind=='CHANGE_INTENT' else '/generations'
    if kind=='CHANGE_INTENT':body['intent']='第二天安排轻松一点'
    key=str(uuid4());r=http.post(path+suffix,headers=headers(key),json=body);assert r.status_code==202
    async def execute():
        async with connection(db) as conn:return await GenerationWorker(db,adapter_factory=bad).step(conn)
    if kind=='CHANGE_INTENT':
        with patch('easytravel.backend.infrastructure.mock_changes.mock_change_adapter',bad):run(execute())
    else:run(execute())
    failed=http.get(path+'/generations/'+r.json()['run_id'],headers=headers()).json();assert failed['status']=='FAILED'
    assert http.post(path+suffix,headers=headers(key),json=body).json()==r.json()
    assert http.post(path+suffix,headers=headers(),json=body).json()['error']['code']=='explicit_retry_required'
    again=http.post(path+suffix,headers=headers(),json=body|dict(retry_of=r.json()['run_id']));assert again.status_code==202,again.text
    assert again.json()['attempt']==2;run(step(db))
    assert http.get(path+'/generations/'+again.json()['run_id'],headers=headers()).json()['status']=='SUCCEEDED'
    assert http.post(path+suffix,headers=headers(),json=body|dict(retry_of=again.json()['run_id'])).status_code==409
    assert http.post(path+suffix,headers=headers(key),json=body|dict(expected_intake_revision=99)).json()['error']['code']=='idempotency_key_reused'
    if kind!='INITIAL_PLAN':
        # A completed retry must not leave this source permanently poisoned by its older failure.
        fresh=http.post(path+suffix,headers=headers(),json=body);assert fresh.status_code==202,fresh.text
        run(step(db))


@pytest.mark.parametrize('mode',['accepted','running','committed'])
def test_real_replan_executor_exit_and_replay(http,db,tmp_path,mode):
    from pathlib import Path
    from test_generations import wait_file
    path=generated(http,db);update(http,path)
    env=os.environ|dict(EASYTRAVEL_DATABASE_URL=db.url.get_secret_value(),PROCESS_BARRIER=str(tmp_path),PROCESS_MODE=mode,PYTHONUTF8='1',PYTHONPATH=str(Path.cwd()))
    child=subprocess.Popen([sys.executable,'tests/backend/generation_process.py'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        wait_file(tmp_path/'ready',child);key=str(uuid4());accepted,body=replan(http,db,path,execute=False,key=key)
        if mode!='accepted':
            (tmp_path/'claim').touch();wait_file(tmp_path/('running' if mode=='running' else 'committed'),child)
        if child.poll() is None:child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
    recovered=subprocess.run([sys.executable,'-m','easytravel.backend.generation_worker','--once'],env=env,capture_output=True,text=True)
    assert recovered.returncode==0,recovered.stderr
    g=http.get(path+'/generations/'+accepted['run_id'],headers=headers()).json()
    assert g['status']==('SUCCEEDED' if mode=='committed' else 'FAILED')
    assert http.post(path+'/generations',headers=headers(key),json=body).json()==accepted
    if mode!='committed':assert g['error']['code']=='worker_interrupted'
    assert http.get(path,headers=headers()).json()['working']['revision']==1
    Path('Docs/M1-06b_进程退出_'+mode+'.json').write_text(json.dumps(g,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.parametrize('approve',[True,False])
def test_replan_concurrent_decision_and_new_process(http,db,app,approve):
    path=generated(http,db);update(http,path);pp,p=replan(http,db,path);key=str(uuid4());body=decision(p,approve)
    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',50001)),base_url='http://127.0.0.1:8000') as c:
            return await asyncio.gather(*[c.post(pp+'/decisions',headers=headers(key),json=body) for _ in range(4)])
    responses=run(race());assert all(r.status_code==200 for r in responses);assert all(r.json()==responses[0].json() for r in responses)
    code="""import os,sys,json,asyncio
from easytravel.backend.application.changes import ChangeService
from easytravel.backend.infrastructure.changes import unit_of_work
from easytravel.backend.infrastructure.database import DatabaseSettings
from easytravel.backend.domain.changes import ApproveRequest,RejectRequest
async def main():
 d=json.loads(sys.stdin.read());s=ChangeService(unit_of_work(DatabaseSettings(url=os.environ['EASYTRAVEL_M107_TEST_DATABASE_URL'])))
 b=(ApproveRequest if d['body']['decision']=='approve' else RejectRequest).model_validate(d['body'])
 r=await s.decide(d['trip'],d['pid'],b,key=d['key'],actor=d['actor'])
 p=await s.get(d['trip'],d['pid'],actor=d['actor'])
 print(json.dumps(dict(result=r.model_dump(mode='json'),proposal=p.model_dump(mode='json'))))
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(main())
"""
    r=subprocess.run([sys.executable,'-c',code],input=json.dumps(dict(trip=path.split('/')[-1],pid=p['proposal']['proposal_id'],body=body,key=key,actor=ACTOR_A)),env=os.environ|dict(PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8')
    assert r.returncode==0,r.stderr
    result=json.loads(r.stdout);assert result['result']==responses[0].json()
    from pathlib import Path
    Path('Docs/M1-06b_独立进程_'+str(approve)+'.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


def test_intake_and_requirements_revision_are_distinct(http,db):
    path=generated(http,db);update(http,path);update(http,path,dict(transport=dict(state='provided',value='步行优先')))
    pp,p=replan(http,db,path);assert p['context_v2']['base_intake_revision']==3 and p['context_v2']['candidate_requirements']['requirements_revision']==2
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==200
    t=http.get(path,headers=headers()).json();assert t['intake']['intake_revision']==3 and t['working']['revision']==2 and t['working']['requirements']['requirements_revision']==2


def test_v2_context_digest_immutable_and_retry_source_guards(http,db):
    from easytravel.backend.domain.replanning import ReplanProposal,ProposalContextV2,validate_v2,risk_hash
    from easytravel.backend.domain.rules import ChangeError
    from easytravel.backend.infrastructure.database import DatabaseUnavailable
    path=generated(http,db);update(http,path);pp,p=replan(http,db,path)
    model=ReplanProposal.model_validate(p['proposal']);ctx=ProposalContextV2.model_validate_json(json.dumps(p['context_v2']))
    data=ctx.model_dump(mode='json');data['candidate_input']['hotel']=dict(state='provided',value='不同偏好')
    altered=ProposalContextV2.model_validate_json(json.dumps(data))
    altered=ProposalContextV2(**altered.model_dump(exclude={'risk_hash'}),risk_hash=risk_hash(altered))
    with pytest.raises(ChangeError):validate_v2(model,altered)
    async def mutate():
        async with connection(db) as c,c.transaction():
            await c.execute('UPDATE easytravel.replan_contexts SET context_v2=context_v2 WHERE proposal_id=%s',(model.proposal_id,))
    with pytest.raises(DatabaseUnavailable):run(mutate())
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)|dict(context_v2=None)).status_code==422
    t=http.get(path,headers=headers()).json()
    body=dict(expected_intake_revision=2,expected_working_revision=1,retry_of=p['proposal']['proposal_id'])
    assert http.post(path+'/generations',headers=headers(),json=body).status_code==404
    latest=http.get(path+'/generation-state',headers=headers()).json()['latest_run']
    assert http.post(path+'/generations',headers=headers(),json=body|dict(retry_of=latest['run_id'])).json()['error']['code']=='invalid_retry'
    other=generated(http,db);update(http,other)
    assert http.post(other+'/generations',headers=headers(),json=body|dict(retry_of=latest['run_id'])).status_code==404
