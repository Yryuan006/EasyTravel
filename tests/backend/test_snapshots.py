"""M1-07 real PostgreSQL history, owner/parent boundaries and read-only facts."""
import os, json, subprocess, sys
from pathlib import Path
from uuid import uuid4
import pytest
from test_replanning import app as replan_app, db, update, replan
from test_change_workflow import app as change_app
from test_change_workflow import services, generation_app, http, decision
from test_confirmations import generated, preview, request, confirm
from test_generations import run, headers, ACTOR_A, TOKEN_B, create
from easytravel.backend.api.v1.trips import snapshot_service
from easytravel.backend.application.snapshots import SnapshotService
from easytravel.backend.infrastructure.snapshots import unit_of_work
from easytravel.backend.infrastructure.database import connection


@pytest.fixture
def app(replan_app, db):
    replan_app.dependency_overrides[snapshot_service] = lambda: SnapshotService(unit_of_work(db))
    return replan_app


def get(http, path, suffix='', **kwargs):
    return http.get(path+'/snapshots'+suffix, headers=headers(), **kwargs)


async def fingerprint(db):
    async with connection(db) as conn:
        await conn.execute('SET TRANSACTION READ ONLY')
        tables = await (await conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='easytravel' ORDER BY 1")).fetchall()
        return {r['tablename']: await (await conn.execute('SELECT count(*) AS n,md5(COALESCE(string_agg(h,\'\' ORDER BY h),\'\')) AS hash FROM (SELECT md5(row_to_json(t)::text) h FROM easytravel.'+r['tablename']+' t)x')).fetchone() for r in tables}


def two(http, db):
    path=generated(http,db)
    s1=confirm(http,path,request(preview(http,path))).json()['snapshot']
    update(http,path)
    pp,p=replan(http,db,path)
    assert http.post(pp+'/decisions',headers=headers(),json=decision(p)).status_code==200
    assert http.get(path,headers=headers()).json()['plan_confirmation']=='UNCONFIRMED'
    assert get(http,path,'/'+s1['snapshot']['snapshot_id']).json()==s1
    s2=confirm(http,path,request(preview(http,path))).json()['snapshot']
    return path,s1,s2


def test_empty_then_two_versions_and_read_only_restart(http,db):
    empty=create(http);assert get(http,empty).json()==dict(items=[],next_cursor=None)
    path,s1,s2=two(http,db);before=run(fingerprint(db))
    page=get(http,path);assert page.status_code==200 and page.headers['cache-control']=='no-store'
    assert [x['snapshot_id'] for x in page.json()['items']]==[s2['snapshot']['snapshot_id'],s1['snapshot']['snapshot_id']]
    for s in [s1,s2]:
        r=get(http,path,'/'+s['snapshot']['snapshot_id']);assert r.status_code==200 and r.json()==s
        assert r.headers['cache-control']=='no-store'
    assert s1['snapshot']['requirements']['start_date']!='2026-12-11'
    assert s2['snapshot']['requirements']['start_date']=='2026-12-11'
    assert s1['snapshot']['payload']!=s2['snapshot']['payload']
    assert s1['risk_disclosure']!=s2['risk_disclosure']
    code="""import asyncio,json,sys
from easytravel.backend.infrastructure.database import load_database_settings
from easytravel.backend.infrastructure.snapshots import unit_of_work
from easytravel.backend.application.snapshots import SnapshotService
async def main():
 s=await SnapshotService(unit_of_work(load_database_settings())).get(sys.argv[1],sys.argv[2],actor=sys.argv[3]);print(s.model_dump_json())
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as r:r.run(main())
"""
    env=os.environ.copy()|dict(EASYTRAVEL_DATABASE_URL=db.url.get_secret_value())
    result=subprocess.run([sys.executable,'-c',code,path.split('/')[-1],s1['snapshot']['snapshot_id'],ACTOR_A],env=env,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==s1
    after=run(fingerprint(db));assert before==after
    Path('Docs/M1-07_HTTP与PostgreSQL证据.json').write_text(json.dumps(dict(path=path,page=page.json(),s1=s1,s2=s2,before=before,after=after,restart=json.loads(result.stdout),readOnly=True),ensure_ascii=False,indent=2),encoding='utf-8')


def test_pagination_ties_binding_and_mutation(http,db):
    path,s1,s2=two(http,db)
    first=get(http,path,params={'limit':1}).json();cursor=first['next_cursor'];assert cursor
    second=get(http,path,params={'limit':1,'cursor':cursor}).json()
    assert [first['items'][0]['snapshot_id'],second['items'][0]['snapshot_id']]==[s2['snapshot']['snapshot_id'],s1['snapshot']['snapshot_id']]
    assert second['next_cursor'] is None
    assert get(http,path,params={'limit':1,'cursor':cursor}).json()==second
    for bad in [cursor[:-1]+'!',cursor+'=',cursor.replace(cursor[4], 'A' if cursor[4]!='A' else 'B',1),'garbage']:
        assert get(http,path,params={'cursor':bad}).status_code==422
    other=create(http)
    assert get(http,other,params={'cursor':cursor}).status_code==422
    assert http.get(path+'/snapshots',params={'cursor':cursor},headers=headers(token=TOKEN_B)).status_code==422


@pytest.mark.parametrize('query',['limit=0','limit=101','limit=-1','limit=x','limit=1.5','cursor=','limit=1&limit=2','actor=a','cursor=x&cursor=y'])
def test_bad_queries(http,db,query):
    path=create(http);assert get(http,path,'?'+query).status_code==422


def test_owner_parent_auth_and_missing(http,db):
    path,s1,_=two(http,db);sid=s1['snapshot']['snapshot_id'];other=create(http)
    before=run(fingerprint(db))
    for suffix in ['', '/'+sid]:
        assert http.get(path+'/snapshots'+suffix,headers=headers(token=TOKEN_B)).status_code==404
        assert http.get(path+'/snapshots'+suffix).status_code==401
        assert http.get(path+'/snapshots'+suffix,headers=headers()|{'Origin':'https://evil.example'}).status_code==403
    assert get(http,other,'/'+sid).status_code==404
    assert get(http,'/api/v1/trips/missing','/'+sid).status_code==404
    assert get(http,path,'/missing').status_code==404
    assert run(fingerprint(db))==before


def test_legacy_no_context_no_disclosure(http,db,payload,requirements):
    from easytravel.backend.application.plans import PlanService
    from easytravel.backend.infrastructure.repository import unit_of_work as plan_uow
    from easytravel.contracts import PlanDraftOutput
    from test_persistence import confirm as legacy_confirm
    service=PlanService(plan_uow(db));tid=str(uuid4())
    draft=run(service.create_trip(tid,PlanDraftOutput.model_validate(payload),requirements,actor=ACTOR_A))
    result=run(legacy_confirm(service,ACTOR_A,draft));sid=result['latest_confirmed_version_id']
    before=run(fingerprint(db));r=get(http,'/api/v1/trips/'+tid,'/'+sid)
    assert r.status_code==200,r.text
    assert r.json()['context'] is None and r.json()['risk_disclosure'] is None
    assert get(http,'/api/v1/trips/'+tid).json()['items'][0]['snapshot_id']==sid
    assert run(fingerprint(db))==before


def test_default_maximum_and_equal_time_keyset(http,db):
    from psycopg.types.json import Jsonb
    path=generated(http,db);s=confirm(http,path,request(preview(http,path))).json()['snapshot']['snapshot']
    tid=path.split('/')[-1]
    async def seed():
        async with connection(db) as conn:
            for revision in range(2,106):
                # Controlled immutable historical fixtures with tied confirmation times.
                await conn.execute('''INSERT INTO easytravel.draft_versions
                    (trip_id,owner_id,revision,requirements_revision,requirements,payload,item_confirmations)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',(tid,ACTOR_A,revision,1,Jsonb(s['requirements']),Jsonb(s['payload']),Jsonb([])))
                await conn.execute('''INSERT INTO easytravel.confirmed_snapshots
                    (snapshot_id,trip_id,confirmed_by,source_working_revision,disclosure_hash,budget,confirmed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',(f'tied-{tid}-{revision:03}',tid,ACTOR_A,revision,s['disclosure_hash'],Jsonb(s['budget']),s['confirmed_at']))
    run(seed());before=run(fingerprint(db))
    assert len(get(http,path).json()['items'])==20
    assert len(get(http,path,params={'limit':100}).json()['items'])==100
    expected=sorted([s['snapshot_id']]+[f'tied-{tid}-{r:03}' for r in range(2,106)],reverse=True)
    seen=[];cursor=None
    while True:
        r=get(http,path,params={'limit':7,**({'cursor':cursor} if cursor else {})});assert r.status_code==200,r.text
        data=r.json();seen.extend(x['snapshot_id'] for x in data['items']);cursor=data['next_cursor']
        if cursor is None:break
    assert seen==expected and len(set(seen))==105
    assert run(fingerprint(db))==before


def test_context_without_disclosure_stays_unacknowledged(http,db):
    from psycopg.types.json import Jsonb
    path=generated(http,db);t=http.get(path,headers=headers()).json();w=t['working'];sid=str(uuid4());budget=preview(http,path)['budget']
    # Legacy confirmation fact references a generated revision but never recorded risk review.
    async def seed():
        async with connection(db) as conn:
            await conn.execute('''INSERT INTO easytravel.confirmed_snapshots
                (snapshot_id,trip_id,confirmed_by,source_working_revision,disclosure_hash,budget,confirmed_at)
                VALUES (%s,%s,%s,1,%s,%s,now())''',(sid,t['trip_id'],ACTOR_A,'0'*64,Jsonb(budget)))
    run(seed());before=run(fingerprint(db));data=get(http,path,'/'+sid).json()
    assert data['context']==w['context'] and data['risk_disclosure'] is None
    assert run(fingerprint(db))==before


@pytest.mark.parametrize('suffix',['','/missing'])
def test_unavailable_database_is_503(http,app,suffix):
    from easytravel.backend.infrastructure.database import DatabaseSettings
    unavailable=DatabaseSettings(url='postgresql://unavailable:unavailable@127.0.0.1:1/easytravel_m002_test_unavailable')
    app.dependency_overrides[snapshot_service]=lambda:SnapshotService(unit_of_work(unavailable))
    r=get(http,'/api/v1/trips/missing',suffix)
    assert r.status_code==503 and r.json()['error']['code']=='business_dependency_unavailable'
