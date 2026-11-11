"""M1-03a: real PostgreSQL, migration compatibility, read purity and atomic races."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
import psycopg
import pytest

from test_intakes import (run, input_data, provided, headers, client_for, db_fingerprint,
                          CONFIG, BASE, ACTOR_A, TOKEN_B)
from easytravel.backend.api.v1.trips import intake_service
from easytravel.backend.application.intakes import IntakeService
from easytravel.backend.domain.intake import CreateTripRequest, next_field, RequirementsInput
from easytravel.backend.infrastructure.intakes import unit_of_work
from easytravel.backend.infrastructure.database import DatabaseSettings, connection
from easytravel.backend.infrastructure.migrate import MIGRATIONS, upgrade
from easytravel.backend.main import create_app


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    settings=DatabaseSettings(url=os.environ["EASYTRAVEL_M103_TEST_DATABASE_URL"])
    initial=tmp_path_factory.mktemp("m103_initial")
    for name in ("001_initial.sql","002_trip_intakes.sql"):
        (initial/name).write_bytes((MIGRATIONS/name).read_bytes())
    async def initialize():
        async with connection(settings) as conn:
            assert (await (await conn.execute("SELECT count(*) n FROM pg_tables WHERE schemaname='easytravel'")).fetchone())["n"]==0
        await upgrade(settings,directory=initial)
        service=IntakeService(unit_of_work(settings))
        legacy_input=input_data(True)
        legacy_input["requirements"]["hard_limit_cents"]={"state":"unanswered","value":None}
        old=await service.create(CreateTripRequest.model_validate(legacy_input),key=str(uuid4()),actor=ACTOR_A)
        before=await db_fingerprint(settings)
        assert await upgrade(settings)==["003_clarifications.sql"]
        assert await upgrade(settings)==[]
        after=await db_fingerprint(settings)
        assert all(after[k]==v for k,v in before.items() if k!="schema_migrations")
        assert await service.get(old.trip_id,actor=ACTOR_A)==old
        return old.trip_id
    old=run(initialize())
    print("M1-01 compatibility: genuine 001+002 Trip upgraded with unchanged facts/receipts; descriptors explicitly backfilled")
    return settings,old


@pytest.fixture
def http(db):
    with client_for(IntakeService(unit_of_work(db[0]))) as client:
        yield client


def create(http, data=None):
    r=http.post("/api/v1/trips",json=data or input_data(True),headers=headers())
    assert r.status_code==201,r.text
    return "/api/v1/trips/"+r.json()["trip_id"],r.json()


def update_body(trip, requirements):
    return dict(expected_intake_revision=trip["intake"]["intake_revision"],
                expected_working_revision=trip["working"]["revision"] if trip["working"] else None,
                requirements=requirements)


def question(http,path):
    r=http.get(path+"/clarifications/next",headers=headers());assert r.status_code==200,r.text
    return r.json()["question"]


def answer_body(q, value, state="provided"):
    return dict(expected_intake_revision=q["intake_revision"],expected_working_revision=None,
                field_key=q["field_key"],answer=dict(state=state,value=value))


def test_update_history_replay_and_restart(http,db):
    path,trip=create(http)
    req=trip["intake"]["input"]|dict(hard_limit_cents=dict(state="unanswered",value=None))
    body=update_body(trip,req);key=str(uuid4())
    r=http.put(path+"/requirements",json=body,headers=headers(key));assert r.status_code==200,r.text
    assert r.json()["intake"]["intake_revision"]==2
    q=question(http,path);assert q["field_key"]=="hard_limit_cents" and q["intake_revision"]==2
    before=run(db_fingerprint(db[0]))
    assert http.put(path+"/requirements",json=body,headers=headers(key)).json()==r.json()
    assert run(db_fingerprint(db[0]))==before
    assert http.put(path+"/requirements",json=body|dict(expected_intake_revision=2),headers=headers(key)).status_code==409
    async def verify():
        async with connection(db[0]) as conn:
            rows=await (await conn.execute("SELECT revision,input FROM easytravel.trip_intake_versions WHERE trip_id=%s ORDER BY revision",(trip["trip_id"],))).fetchall()
            assert [x["revision"] for x in rows]==[1,2]
            assert rows[0]["input"]==trip["intake"]["input"]
    run(verify())
    script="""
import asyncio,json,os,sys
from easytravel.backend.application.intakes import IntakeService
from easytravel.backend.infrastructure.intakes import unit_of_work
from easytravel.backend.infrastructure.database import DatabaseSettings
async def main():
    service=IntakeService(unit_of_work(DatabaseSettings(url=os.environ['EASYTRAVEL_M103_TEST_DATABASE_URL'])))
    data=json.load(sys.stdin)
    print((await service.get(data['id'],actor=data['actor'])).model_dump_json())
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as r:r.run(main())
"""
    result=subprocess.run([sys.executable,"-c",script],input=json.dumps(dict(id=trip["trip_id"],actor=ACTOR_A)),capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==r.json()


def test_get_pure_legacy_complete_and_no_inference(http,db):
    before=run(db_fingerprint(db[0]))
    assert question(http,"/api/v1/trips/"+db[1])["field_key"]=="hard_limit_cents"
    assert run(db_fingerprint(db[0]))==before
    path,trip=create(http,input_data())
    q=question(http,path)
    assert q["field_key"]=="dates"
    before=run(db_fingerprint(db[0]))
    assert question(http,path)==q
    assert http.get(path,headers=headers()).json()==trip
    assert run(db_fingerprint(db[0]))==before


def test_answer_versions_old_question_and_field_mismatch(http,db):
    data=input_data(True);data["requirements"]["hard_limit_cents"]=dict(state="unanswered",value=None)
    path,trip=create(http,data);q=question(http,path);route=path+"/clarifications/"+q["question_id"]+"/answers"
    body=answer_body(q,240000);key=str(uuid4())
    wrong=body|dict(field_key="travelers",answer=provided(2))
    assert http.post(route,json=wrong,headers=headers()).status_code==409
    r=http.post(route,json=body,headers=headers(key));assert r.status_code==200,r.text
    assert r.json()["intake"]["intake_revision"]==2 and question(http,path) is None
    assert http.post(route,json=body,headers=headers(key)).json()==r.json()
    assert http.post(route,json=body,headers=headers()).status_code==409
    # Date correction invalidates a different outstanding question.
    path2,t2=create(http,data);old=question(http,path2)
    req=t2["intake"]["input"]|dict(dates=provided(dict(mode="fixed",start_date="2026-12-07",end_date="2026-12-09")))
    assert http.put(path2+"/requirements",json=update_body(t2,req),headers=headers()).status_code==200
    assert http.post(path2+"/clarifications/"+old["question_id"]+"/answers",json=answer_body(old,240000),headers=headers()).status_code==409
    # Same-owner child resource binding is checked too.
    assert http.post(path2+"/clarifications/"+q["question_id"]+"/answers",json=answer_body(question(http,path2),240000),headers=headers()).status_code==409


@pytest.mark.parametrize("field,value",[("travelers",3),("travelers",True),("days",6),("days",0),
 ("hard_limit_cents",-1),("hard_limit_cents",1.5),("hard_limit_cents",10**12+1),("hard_limit_cents","100"),
 ("dates",dict(mode="fixed",start_date="2026-02-30",end_date="2026-03-01")),
 ("dates",dict(mode="fixed",start_date="2026-12-04T00:00:00Z",end_date="2026-12-06")),
 ("dates",dict(mode="fixed",start_date="2026-12-07",end_date="2026-12-06"))])
def test_invalid_answer_and_full_update(http,db,field,value):
    path,trip=create(http);before=run(db_fingerprint(db[0]))
    r=http.post(path+"/clarifications/anything/answers",json=dict(expected_intake_revision=1,expected_working_revision=None,field_key=field,answer=provided(value)),headers=headers())
    assert r.status_code==422,r.text
    req=trip["intake"]["input"]|{field:provided(value)}
    assert http.put(path+"/requirements",json=update_body(trip,req),headers=headers()).status_code==422
    assert run(db_fingerprint(db[0]))==before


@pytest.mark.parametrize("state",["unanswered","no_preference"])
def test_explicit_missing_state_and_correction(http,state):
    data=input_data(True);data["requirements"]["hard_limit_cents"]=dict(state="unanswered",value=None)
    path,trip=create(http,data);q=question(http,path);route=path+"/clarifications/"+q["question_id"]+"/answers"
    assert http.post(route,json=answer_body(q,1,state),headers=headers()).status_code==422
    r=http.post(route,json=answer_body(q,None,state),headers=headers());assert r.status_code==200,r.text
    assert r.json()["intake"]["input"]["hard_limit_cents"]==dict(state=state,value=None)
    q2=question(http,path);assert q2["field_key"]=="hard_limit_cents" and q2["question_id"]!=q["question_id"]
    assert http.post(path+"/clarifications/"+q2["question_id"]+"/answers",json=answer_body(q2,0),headers=headers()).status_code==200
    assert question(http,path)["field_key"]=="target_budget_cents"  # target now exceeds explicit zero limit


def test_semantic_rules_and_priority():
    data=input_data(True)["requirements"]
    cases=[({"dates":provided(dict(mode="window",earliest_start="2026-12-04",latest_end="2026-12-12"))},"dates"),
      ({"days":provided(2)},"dates"),({"origin":dict(state="no_preference",value=None)},"origin"),
      ({"target_budget_cents":provided(300000)},"target_budget_cents"),
      ({"transport":dict(state="no_preference",value=None)},None),
      ({"raw_text":"三人去北京，预算不限"},None)]
    for patch,expected in cases:assert next_field(RequirementsInput.model_validate(data|patch))==expected


def test_cross_owner_and_expected_working(http):
    data=input_data(True);data["requirements"]["hard_limit_cents"]=dict(state="unanswered",value=None)
    path,t=create(http,data);q=question(http,path)
    for method,route,body in [("get",path+"/clarifications/next",None),("put",path+"/requirements",update_body(t,t["intake"]["input"])),
      ("post",path+"/clarifications/"+q["question_id"]+"/answers",answer_body(q,230000))]:
        r=http.request(method,route,json=body,headers=headers(token=TOKEN_B));assert r.status_code==404,r.text
    body=update_body(t,t["intake"]["input"])|dict(expected_working_revision=1)
    assert http.put(path+"/requirements",json=body,headers=headers()).status_code==409


def test_concurrent_updates_and_answers(http,db):
    path,t=create(http);body=update_body(t,t["intake"]["input"])
    app=create_app(CONFIG);app.dependency_overrides[intake_service]=lambda:IntakeService(unit_of_work(db[0]))
    async def race(route,body,method,same_key=False):
        key=str(uuid4())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=("127.0.0.1",50000)),base_url=BASE) as c:
            results=await asyncio.gather(*[c.request(method,route,json=body,headers=headers(key if same_key else None)) for _ in range(5)])
            codes=sorted(r.status_code for r in results)
            assert codes==([200]*5 if same_key else [200,409,409,409,409]),[r.text for r in results]
            if same_key:assert all(r.json()==results[0].json() for r in results)
        print("Concurrent",method,"same-key" if same_key else "distinct-key",codes)
    run(race(path+"/requirements",body,"PUT"))
    path,t=create(http);body=update_body(t,t["intake"]["input"])
    run(race(path+"/requirements",body,"PUT",True))
    data=input_data(True);data["requirements"]["hard_limit_cents"]=dict(state="unanswered",value=None)
    for same in (False,True):
        path,t=create(http,data);q=question(http,path)
        run(race(path+"/clarifications/"+q["question_id"]+"/answers",answer_body(q,230000),"POST",same))
        assert http.get(path,headers=headers()).json()["intake"]["intake_revision"]==2


@pytest.mark.parametrize("kind",["update","answer"])
@pytest.mark.parametrize("fault",["statement","commit"])
def test_atomic_rollback(http,db,kind,fault):
    data=input_data(True);data["requirements"]["hard_limit_cents"]=dict(state="unanswered",value=None)
    path,t=create(http,data);q=question(http,path)
    @asynccontextmanager
    async def broken(actor):
        async with unit_of_work(db[0])(actor) as repo:
            class Failing:
                def __getattr__(self,name):return getattr(repo,name)
                async def save_command(self,*args):
                    await repo.save_command(*args)
                    if fault=="statement":await repo.conn.execute("SELECT 1/0")
                    else:
                        await repo.conn.execute("SET CONSTRAINTS ALL DEFERRED")
                        await repo.conn.execute("UPDATE easytravel.trip_intakes SET revision=revision+1 WHERE trip_id=%s",(t["trip_id"],))
            yield Failing()
    before=run(db_fingerprint(db[0]))
    with client_for(IntakeService(broken)) as client:
        if kind=="update":r=client.put(path+"/requirements",json=update_body(t,t["intake"]["input"]),headers=headers())
        else:r=client.post(path+"/clarifications/"+q["question_id"]+"/answers",json=answer_body(q,230000),headers=headers())
        assert r.status_code==500 and r.json()["error"]["code"]=="internal_error"
        assert "Traceback" not in r.text and "SELECT" not in r.text
    assert run(db_fingerprint(db[0]))==before
    print("Full rollback:",kind,fault,"versions, pointer, questions, answers, receipt unchanged")


def test_draft_preservation_and_stale_confirmation(http,db,payload,requirements):
    from easytravel.backend.application.plans import PlanService
    from easytravel.backend.infrastructure.repository import unit_of_work as plan_uow
    from easytravel.contracts import PlanDraftOutput
    from easytravel.backend.domain.errors import BusinessConflict
    svc=PlanService(plan_uow(db[0]));tid=str(uuid4())
    draft=run(svc.create_trip(tid,PlanDraftOutput.model_validate(payload),requirements,actor=ACTOR_A))
    preview=run(svc.confirmation_preview(tid,actor=ACTOR_A))
    snapshot=run(svc.confirm_plan(tid,actor=ACTOR_A,operation_id=str(uuid4()),expected_revision=1,seen_disclosure_hash=preview["disclosure_hash"],explicit_confirmation=True))
    # Explicit fixture for an existing draft with intake; no new product draft endpoint.
    async def attach():
        async with unit_of_work(db[0])(ACTOR_A) as repo:
            await repo.conn.execute("INSERT INTO easytravel.trip_intake_versions(trip_id,owner_id,revision,input) VALUES (%s,%s,1,%s)",
              (tid,ACTOR_A,psycopg.types.json.Jsonb(input_data(True)["requirements"])))
            await repo.conn.execute("INSERT INTO easytravel.trip_intakes VALUES (%s,%s,1)",(tid,ACTOR_A))
    run(attach());before=run(svc.get_working(tid,actor=ACTOR_A));old_snapshot=run(svc.get_snapshot(snapshot["latest_confirmed_version_id"],actor=ACTOR_A))
    path="/api/v1/trips/"+tid;t=http.get(path,headers=headers()).json()
    r=http.put(path+"/requirements",json=update_body(t,t["intake"]["input"]|dict(travelers=provided(1))),headers=headers())
    assert r.status_code==200,r.text
    assert r.json()["synchronization"]=="STALE" and r.json()["plan_confirmation"]=="UNCONFIRMED"
    assert run(svc.get_working(tid,actor=ACTOR_A))==before
    with pytest.raises(BusinessConflict):
        run(svc.confirm_plan(tid,actor=ACTOR_A,operation_id=str(uuid4()),expected_revision=1,seen_disclosure_hash=preview["disclosure_hash"],explicit_confirmation=True))
    assert run(svc.get_snapshot(snapshot["latest_confirmed_version_id"],actor=ACTOR_A))==old_snapshot


def test_read_only_transaction_and_legacy_answer(db):
    @asynccontextmanager
    async def read_only(actor):
        async with unit_of_work(db[0])(actor) as repo:
            await repo.conn.execute("SET TRANSACTION READ ONLY")
            yield repo
    with client_for(IntakeService(read_only)) as c:
        path="/api/v1/trips/"+db[1]
        q=question(c,path)
        assert c.get(path,headers=headers()).status_code==200
    with client_for(IntakeService(unit_of_work(db[0]))) as c:
        key=str(uuid4());route=path+"/clarifications/"+q["question_id"]+"/answers"
        body=answer_body(q,230000)
        r=c.post(route,json=body,headers=headers(key));assert r.status_code==200
        assert r.json()["intake"]["intake_revision"]==2
        assert c.post(route,json=body|dict(answer=provided(240000)),headers=headers(key)).status_code==409
    print("GET executes in PostgreSQL READ ONLY; pre-003 Trip successfully answered to revision 2")


@pytest.mark.parametrize("method,suffix,body",[("PUT","/requirements",dict(expected_intake_revision=1,expected_working_revision=None,requirements=input_data(True)["requirements"])),
    ("POST","/clarifications/q/answers",dict(expected_intake_revision=1,expected_working_revision=None,field_key="days",answer=provided(1)))])
def test_new_write_key_and_auth_contract(http,method,suffix,body):
    for h in ({"Idempotency-Key":str(uuid4())},headers()|{"Idempotency-Key":"bad"},
              {k:v for k,v in headers().items() if k!="Idempotency-Key"}):
        r=http.request(method,"/api/v1/trips/missing"+suffix,json=body,headers=h)
        assert r.status_code==(401 if "Authorization" not in h else 422),r.text


def test_question_answer_database_immutability(http,db):
    data=input_data(True);data["requirements"]["hard_limit_cents"]={"state":"unanswered","value":None}
    path,t=create(http,data);q=question(http,path)
    assert http.post(path+"/clarifications/"+q["question_id"]+"/answers",json=answer_body(q,230000),headers=headers()).status_code==200
    async def verify():
        async with connection(db[0]) as conn:
            for table in ("clarification_questions","clarification_answers"):
                for operation in (f"UPDATE easytravel.{table} SET field_key=field_key WHERE trip_id=%s",f"DELETE FROM easytravel.{table} WHERE trip_id=%s"):
                    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                        async with conn.transaction():await conn.execute(operation,(t["trip_id"],))
    run(verify())
