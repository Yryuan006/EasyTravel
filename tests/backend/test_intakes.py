"""M1-01 HTTP authority, strict input and real PostgreSQL transaction tests."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import secrets
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import ValidationError
import pytest
from fastapi.testclient import TestClient

from easytravel.backend.api.v1.trips import intake_service
from easytravel.backend.application.intakes import IntakeService
from easytravel.backend.config import ConfigurationError, LocalIdentity, Settings, load_settings
from easytravel.backend.domain.hashing import digest
from easytravel.backend.infrastructure.database import DatabaseSettings, connection
from easytravel.backend.infrastructure.intakes import unit_of_work
from easytravel.backend.infrastructure.migrate import MIGRATIONS, upgrade
from easytravel.backend.main import create_app


TOKEN_A, TOKEN_B = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
ACTOR_A, ACTOR_B = "m101_owner_a", "m101_owner_b"
CONFIG = Settings(environment="test", local_business_enabled=True, local_identities=(
    LocalIdentity(actor=ACTOR_A, token=TOKEN_A), LocalIdentity(actor=ACTOR_B, token=TOKEN_B)))
BASE = "http://127.0.0.1:8000"


def run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(asyncio.wait_for(coro, 50))


def provided(value):
    return {"state": "provided", "value": value}


def input_data(complete=False):
    data = {field: {"state": "unanswered", "value": None} for field in (
        "origin", "destination", "dates", "days", "travelers", "target_budget_cents",
        "hard_limit_cents", "transport", "hotel", "restrictions")}
    data["destination"] = provided("杭州")
    data["raw_text"] = None
    if complete:
        data.update(origin=provided("上海"), dates=provided({"mode": "fixed", "start_date": "2026-12-04", "end_date": "2026-12-06"}),
            days=provided(3), travelers=provided(2), target_budget_cents=provided(200000), hard_limit_cents=provided(230000),
            transport=provided("公共交通优先"), hotel=provided("安静、干净，一间双人房"), restrictions=provided("不爬山"))
    return {"requirements": data}


def headers(key=None, token=TOKEN_A):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key or str(uuid4())}


def client_for(service=None, config=CONFIG, peer="127.0.0.1"):
    app = create_app(config)
    if service is not None:
        app.dependency_overrides[intake_service] = lambda: service
    return TestClient(app, base_url=BASE, client=(peer, 50000), raise_server_exceptions=False,
                      backend_options={"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {})


def test_default_disabled_and_health_without_database(monkeypatch):
    monkeypatch.delenv("EASYTRAVEL_DATABASE_URL", raising=False)
    with client_for(config=Settings(environment="test")) as client:
        r = client.post("/api/v1/trips", json=input_data(), headers=headers())
        assert r.status_code == 403 and r.json()["error"]["code"] == "local_only_api"
        health = client.get("/api/v1/health")
        assert health.status_code == 200 and health.json()["dependencies"]["database"] == "not_checked"


@pytest.mark.parametrize("change", [
    {"local_business_enabled": True},
    {"local_allowed_hosts": ("evil.example:8000",)},
    {"local_allowed_origins": ("https://evil.example:443",)},
    {"local_allowed_origins": ("http://localhost:8000/path",)},
    {"local_allowed_hosts": ("user@127.0.0.1:8000",)},
    {"local_identities": (LocalIdentity(actor=ACTOR_A, token=TOKEN_A), LocalIdentity(actor=ACTOR_B, token=TOKEN_A))},
])
def test_unsafe_local_configuration_rejected(change):
    with pytest.raises(ValidationError) as error:
        Settings.model_validate({"environment": "test"} | change)
    assert TOKEN_A not in str(error.value)


@pytest.mark.parametrize("value", ["short", "not a token", "x" * 129])
def test_invalid_token_configuration(value):
    with pytest.raises(ConfigurationError) as error:
        load_settings({"EASYTRAVEL_APP_ENVIRONMENT": "test", "EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED": "true",
            "EASYTRAVEL_APP_LOCAL_IDENTITIES": json.dumps([{"actor": ACTOR_A, "token": value}])})
    assert value not in str(error.value)


def test_local_config_load_and_secret_repr():
    config = load_settings({"EASYTRAVEL_APP_ENVIRONMENT": "local", "EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED": "true",
        "EASYTRAVEL_APP_LOCAL_IDENTITIES": json.dumps([{"actor": ACTOR_A, "token": TOKEN_A}])})
    assert config.local_identities[0].actor == ACTOR_A and config.local_business_enabled
    assert TOKEN_A not in repr(config)
    with pytest.raises(ConfigurationError):
        load_settings({"EASYTRAVEL_APP_ENVIRONMENT": "test", "EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED": "yes"})


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Basic anything", "Bearer", "Bearer " + "z" * 200])
def test_missing_or_invalid_identity_fails_before_database(auth, monkeypatch):
    monkeypatch.delenv("EASYTRAVEL_DATABASE_URL", raising=False)
    with client_for() as client:
        r = client.post("/api/v1/trips", json=input_data(), headers={} if auth is None else {"Authorization": auth})
        assert r.status_code == 401 and r.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("extra,status,code", [
    ({"Origin": "https://evil.example"},403,"origin_not_allowed"),
    ({"Origin": "null"},403,"origin_not_allowed"),
    ({"Host": "evil.example:8000"},403,"local_only_api"),
    ({"X-Actor": ACTOR_B},422,"invalid_request"),
])
def test_origin_host_and_actor_header(extra,status,code):
    with client_for() as client:
        r = client.post("/api/v1/trips", json=input_data(), headers=headers() | extra)
        assert r.status_code == status and r.json()["error"]["code"] == code
        assert TOKEN_A not in r.text


def test_non_loopback_peer_cannot_spoof_forwarding():
    with client_for(peer="203.0.113.10") as client:
        r = client.get("/api/v1/trips/anything", headers=headers() | {"X-Forwarded-For":"127.0.0.1"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "local_only_api"


def test_missing_database_is_safe_503(monkeypatch):
    monkeypatch.delenv("EASYTRAVEL_DATABASE_URL", raising=False)
    with client_for() as client:
        r=client.post("/api/v1/trips",json=input_data(),headers=headers())
        assert r.status_code==503 and r.json()["error"]["code"]=="business_dependency_unavailable"


async def db_fingerprint(settings, *, trip_id=None):
    result={}
    async with connection(settings) as conn:
        tables=await (await conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='easytravel' ORDER BY tablename")).fetchall()
        for table in tables:
            name=table["tablename"]
            if trip_id is not None and name == "schema_migrations":
                continue
            query=sql.SQL("SELECT count(*) AS n, md5(COALESCE(string_agg(h,'' ORDER BY h),'')) AS hash FROM "
                "(SELECT md5(row_to_json(t)::text) AS h FROM easytravel.{} t{} ) rows").format(sql.Identifier(name),
                    sql.SQL(" WHERE trip_id=%s") if trip_id is not None else sql.SQL(""))
            # All formal business tables, including new ones, carry trip_id.
            result[name]=await (await conn.execute(query, (trip_id,) if trip_id is not None else ())).fetchone()
    return result


@pytest.fixture(scope="module")
def m101db(tmp_path_factory):
    settings=DatabaseSettings(url=os.environ["EASYTRAVEL_M101_TEST_DATABASE_URL"])
    target=conninfo_to_dict(settings.url.get_secret_value())
    assert target["host"]=="127.0.0.1" and target["port"]=="55432" and target["dbname"].startswith("easytravel_m002_test_")
    assert target["dbname"] != conninfo_to_dict(os.environ["EASYTRAVEL_DATABASE_URL"])["dbname"]
    # Read-only guards over existing experimental and retained formal data.
    from easytravel.spike02.database import business_connection
    async def experiments():
        result={}
        async with business_connection() as conn:
            tables=await (await conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2")).fetchall()
            for row in tables:
                q=sql.SQL("SELECT count(*) AS n, md5(COALESCE(string_agg(h,'' ORDER BY h),'')) AS hash FROM "
                    "(SELECT md5(row_to_json(t)::text) AS h FROM {}.{} t) x").format(sql.Identifier(row["schemaname"]),sql.Identifier(row["tablename"]))
                result[row["schemaname"]+'.'+row["tablename"]]=await (await conn.execute(q)).fetchone()
        return result
    before_experiment=run(experiments())
    retained=DatabaseSettings(url=os.environ["EASYTRAVEL_M101_RETAINED_DATABASE_URL"])
    before_retained=run(db_fingerprint(retained))
    initial=tmp_path_factory.mktemp("initial")
    (initial/"001_initial.sql").write_bytes((MIGRATIONS/"001_initial.sql").read_bytes())
    async def initialize():
        async with connection(settings) as conn:
            assert (await (await conn.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')")).fetchone())["n"]==0
        assert await upgrade(settings,directory=initial)==["001_initial.sql"]
        # A genuine M0 formal draft/snapshot, before applying the new migration.
        from easytravel.backend.application.plans import PlanService
        from easytravel.backend.infrastructure.repository import unit_of_work as plan_uow
        from easytravel.contracts import PlanDraftOutput, TripRequirements
        legacy_id="legacy_"+uuid4().hex
        payload=PlanDraftOutput.model_validate({"items":[{"id":"a","kind":"activity","resource_id":"r","title":"旧正式项目",
            "start":"2026-12-04T09:00:00+08:00","end":"2026-12-04T10:00:00+08:00"}],"costs":[{
            "id":"c","item_id":"a","amount_cents":0,"category":"estimate","unknown_reason":None,"evidence_id":None,"is_mock":True,
            "query":{"resource_id":"r","start_date":"2026-12-04","end_date":"2026-12-04","currency":"CNY","room_type":None,"rooms":None}}]})
        req=TripRequirements.model_validate_json('{"destination":"杭州","start_date":"2026-12-04","end_date":"2026-12-04","timezone":"Asia/Shanghai","currency":"CNY","hard_limit_cents":100,"requirements_revision":1}')
        svc=PlanService(plan_uow(settings))
        await svc.create_trip(legacy_id,payload,req,actor=ACTOR_A)
        preview=await svc.confirmation_preview(legacy_id,actor=ACTOR_A)
        await svc.confirm_plan(legacy_id,actor=ACTOR_A,operation_id=str(uuid4()),expected_revision=1,
            seen_disclosure_hash=preview["disclosure_hash"],explicit_confirmation=True)
        old=await db_fingerprint(settings,trip_id=legacy_id)
        assert await upgrade(settings)==["002_trip_intakes.sql", "003_clarifications.sql", "004_generations.sql", "005_confirmation_disclosures.sql", "006_changes.sql", "007_replanning.sql"]
        assert await upgrade(settings)==[]
        after=await db_fingerprint(settings,trip_id=legacy_id)
        assert all(after[k]==v for k,v in old.items())
        return legacy_id,after
    legacy_id,old=run(initialize())
    print("M1-01 dedicated database initialized: 001 legacy snapshot preserved, 002 applied once; no reset")
    yield settings,legacy_id
    assert run(db_fingerprint(settings,trip_id=legacy_id))==old
    assert run(experiments())==before_experiment
    assert run(db_fingerprint(retained))==before_retained
    print(f"Isolation verified: {len(before_experiment)} experiment/Checkpoint tables + {len(before_retained)} retained formal tables + seeded legacy snapshot unchanged")


@pytest.fixture
def http(m101db):
    with client_for(IntakeService(unit_of_work(m101db[0]))) as client:
        yield client


@pytest.mark.parametrize("mode", ["complete","destination","raw"])
def test_create_read_input_and_no_draft(http,m101db,mode):
    body=input_data(mode=="complete")
    if mode=="raw":
        body["requirements"]["destination"]={"state":"unanswered","value":None}
        body["requirements"]["raw_text"]="想在国内玩三天，暂未确定目的地"
    r=http.post("/api/v1/trips",json=body,headers=headers() | {"Origin":"http://127.0.0.1:5173"})
    assert r.status_code==201,r.text
    value=r.json()
    assert value["intake"]["input"]==body["requirements"] and value["intake"]["intake_revision"]==1
    assert value["working"] is value["latest_confirmed_version_id"] is value["active_run_id"] is None
    assert value["synchronization"]=="NO_DRAFT" and value["plan_confirmation"]=="UNCONFIRMED"
    assert value["readiness"]=="NOT_ASSESSED" and value["booked"] is False
    assert value["intake"]["collection_status"]==("READY_TO_GENERATE" if mode=="complete" else "COLLECTING")
    assert value["intake"]["suggested_values"]==[]
    assert (not value["intake"]["blocking_fields"])==(mode=="complete")
    before=run(db_fingerprint(m101db[0]))
    read=http.get("/api/v1/trips/"+value["trip_id"],headers=headers())
    assert read.status_code==200 and read.json()==value
    assert r.headers["cache-control"]==read.headers["cache-control"]=="no-store"
    assert run(db_fingerprint(m101db[0]))==before
    print(f"HTTP {mode}: POST 201 / GET 200; input persisted, no draft, GET fingerprint unchanged")


@pytest.mark.parametrize("case", ["empty","missing","money","money_bool","travelers","extra_owner","extra_actor",
    "answer_extra","provided_null","unanswered_value","blank_destination","blank_raw","bad_date","extra_status"])
def test_reject_invalid_input_without_side_effects(http,m101db,case):
    body=input_data()
    data=body["requirements"]
    if case=="empty": data["destination"]={"state":"unanswered","value":None}
    elif case=="missing": del data["hotel"]
    elif case=="money": data["hard_limit_cents"]=provided(-1)
    elif case=="money_bool": data["hard_limit_cents"]=provided(True)
    elif case=="travelers": data["travelers"]=provided(3)
    elif case=="extra_owner": body["owner_id"]=ACTOR_B
    elif case=="extra_actor": body["actor"]=ACTOR_B
    elif case=="answer_extra": data["destination"]["owner_id"]=ACTOR_B
    elif case=="provided_null": data["destination"]=provided(None)
    elif case=="unanswered_value": data["hotel"]={"state":"unanswered","value":"安静"}
    elif case=="blank_destination": data["destination"]=provided("  ")
    elif case=="blank_raw": data["raw_text"]="  "
    elif case=="bad_date": data["dates"]=provided({"mode":"fixed","start_date":"2026-02-30","end_date":"2026-03-01"})
    elif case=="extra_status": body["collection_status"]="READY_TO_GENERATE"
    before=run(db_fingerprint(m101db[0]))
    r=http.post("/api/v1/trips",json=body,headers=headers())
    assert r.status_code==422 and r.json()["error"]["code"]=="invalid_request"
    assert run(db_fingerprint(m101db[0]))==before


@pytest.mark.parametrize("key", [None,"invalid-uuid"])
def test_idempotency_header_required(http,key):
    h={"Authorization":"Bearer "+TOKEN_A}
    if key is not None: h["Idempotency-Key"]=key
    r=http.post("/api/v1/trips",json=input_data(),headers=h)
    assert r.status_code==422 and r.json()["error"]["code"]=="invalid_request"


def test_owner_scope_and_forged_query(http):
    r=http.post("/api/v1/trips",json=input_data(),headers=headers())
    path="/api/v1/trips/"+r.json()["trip_id"]
    other=http.get(path,headers=headers(token=TOKEN_B))
    missing=http.get("/api/v1/trips/missing",headers=headers(token=TOKEN_B))
    assert other.status_code==missing.status_code==404 and other.json()==missing.json()
    assert http.get(path+"?actor="+ACTOR_A,headers=headers(token=TOKEN_B)).status_code==422
    assert http.post("/api/v1/trips?owner_id="+ACTOR_B,json=input_data(),headers=headers()).status_code==422


def test_sequential_replay_conflict_and_actor_key_scope(http,m101db):
    key=str(uuid4()); body=input_data()
    first=http.post("/api/v1/trips",json=body,headers=headers(key))
    before=run(db_fingerprint(m101db[0]))
    for _ in range(3):
        r=http.post("/api/v1/trips",json=body,headers=headers(key))
        assert r.status_code==201 and r.json()==first.json()
    assert run(db_fingerprint(m101db[0]))==before
    changed=input_data(); changed["requirements"]["destination"]=provided("苏州")
    conflict=http.post("/api/v1/trips",json=changed,headers=headers(key))
    assert conflict.status_code==409 and conflict.json()["error"]["code"]=="idempotency_key_reused"
    second=http.post("/api/v1/trips",json=body,headers=headers(key,token=TOKEN_B))
    assert second.status_code==201 and second.json()["trip_id"]!=first.json()["trip_id"]


def test_concurrent_creates_and_committed_timeout_replay(m101db):
    import httpx
    key=str(uuid4()); body=input_data()
    app=create_app(CONFIG)
    app.dependency_overrides[intake_service]=lambda:IntakeService(unit_of_work(m101db[0]))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=("127.0.0.1",50000)),base_url=BASE) as client:
            results=await asyncio.gather(*[client.post("/api/v1/trips",json=body,headers=headers(key)) for _ in range(5)])
            assert all(r.status_code==201 for r in results),[r.text for r in results]
            assert all(r.json()==results[0].json() for r in results)
            # Simulate a lost response: discard the first response after commit,
            # create a new application/client and replay the same command below.
            return results[0].json()
    first=run(scenario())
    with client_for(IntakeService(unit_of_work(m101db[0]))) as restarted:
        r=restarted.post("/api/v1/trips",json=body,headers=headers(key))
        assert r.status_code==201 and r.json()==first
    async def count():
        async with connection(m101db[0]) as conn:
            for table in ("trips","trip_intakes","trip_intake_versions","api_commands"):
                row=await (await conn.execute(sql.SQL("SELECT count(*) AS n FROM easytravel.{} WHERE trip_id=%s").format(sql.Identifier(table)),(first["trip_id"],))).fetchone()
                assert row["n"]==1
            assert (await (await conn.execute("SELECT count(*) AS n FROM easytravel.working_drafts WHERE trip_id=%s",(first["trip_id"],))).fetchone())["n"]==0
    run(count())


@pytest.mark.parametrize("fault", ["statement","commit"])
def test_transaction_failure_rolls_back_http_command_and_intake(m101db,fault):
    @asynccontextmanager
    async def broken(owner):
        async with unit_of_work(m101db[0])(owner) as repo:
            class Failing:
                def __getattr__(self,name): return getattr(repo,name)
                async def save_command(self,*args):
                    await repo.save_command(*args)
                    if fault=="statement": await repo.conn.execute("SELECT 1/0")
                    else:
                        await repo.conn.execute("SET CONSTRAINTS ALL DEFERRED")
                        await repo.conn.execute("UPDATE easytravel.trip_intakes SET revision=2 WHERE trip_id=%s",(args[2].trip_id,))
            yield Failing()
    before=run(db_fingerprint(m101db[0])); key=str(uuid4())
    with client_for(IntakeService(broken)) as client:
        r=client.post("/api/v1/trips",json=input_data(),headers=headers(key))
        assert r.status_code==500 and r.json()["error"]["code"]=="internal_error"
        assert TOKEN_A not in r.text and "Traceback" not in r.text
    assert run(db_fingerprint(m101db[0]))==before
    with client_for(IntakeService(unit_of_work(m101db[0]))) as client:
        assert client.post("/api/v1/trips",json=input_data(),headers=headers(key)).status_code==201


def test_independent_process_reads_and_replays(m101db,http):
    key=str(uuid4()); body=input_data(True)
    r=http.post("/api/v1/trips",json=body,headers=headers(key)); assert r.status_code==201
    # Credentials only travel via stdin/environment, never process arguments or stdout.
    script='''
import asyncio,json,os,sys
from easytravel.backend.application.intakes import IntakeService
from easytravel.backend.domain.intake import CreateTripRequest
from easytravel.backend.infrastructure.database import DatabaseSettings
from easytravel.backend.infrastructure.intakes import unit_of_work
async def main():
    args=json.load(sys.stdin)
    service=IntakeService(unit_of_work(DatabaseSettings(url=os.environ['EASYTRAVEL_M101_TEST_DATABASE_URL'])))
    value=await service.get(args['trip_id'],actor=args['actor'])
    replay=await service.create(CreateTripRequest.model_validate(args['request']),key=args['key'],actor=args['actor'])
    assert value==replay
    print(value.model_dump_json())
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
    runner.run(main())
'''
    result=subprocess.run([sys.executable,"-c",script],input=json.dumps({"trip_id":r.json()["trip_id"],"actor":ACTOR_A,"key":key,"request":body}),
                          capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==r.json()


def test_legacy_trip_has_no_fabricated_input(http,m101db):
    before=run(db_fingerprint(m101db[0],trip_id=m101db[1]))
    r=http.get("/api/v1/trips/"+m101db[1],headers=headers())
    assert r.status_code==404 and r.json()["error"]["code"]=="resource_not_found"
    assert run(db_fingerprint(m101db[0],trip_id=m101db[1]))==before


def test_intake_and_command_database_guards(http,m101db):
    r=http.post("/api/v1/trips",json=input_data(),headers=headers()); trip_id=r.json()["trip_id"]
    async def scenario():
        async with connection(m101db[0]) as conn:
            statements=["UPDATE easytravel.trip_intake_versions SET input=input WHERE trip_id=%s",
                "DELETE FROM easytravel.trip_intake_versions WHERE trip_id=%s",
                "UPDATE easytravel.api_commands SET result=result WHERE trip_id=%s",
                "DELETE FROM easytravel.api_commands WHERE trip_id=%s",
                "UPDATE easytravel.trip_intakes SET revision=revision WHERE trip_id=%s"]
            for statement in statements:
                with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                    async with conn.transaction(): await conn.execute(statement,(trip_id,))
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with conn.transaction():
                    await conn.execute("INSERT INTO easytravel.trip_intake_versions(trip_id,owner_id,revision,input) VALUES (%s,%s,2,'{}')",(trip_id,ACTOR_B))
    run(scenario())


def test_command_lock_timeout_is_retryable_409(m101db):
    import httpx
    key=str(uuid4())
    scope=digest(dict(actor=ACTOR_A,method="POST",path="/api/v1/trips",key=key))
    app=create_app(CONFIG); app.dependency_overrides[intake_service]=lambda:IntakeService(unit_of_work(m101db[0]))
    async def scenario():
        async with connection(m101db[0]) as conn,conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,10101))",(scope,))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=("127.0.0.1",50000)),base_url=BASE) as client:
                r=await client.post("/api/v1/trips",json=input_data(),headers=headers(key))
                assert r.status_code==409 and r.json()["error"]["code"]=="command_in_progress"
                assert r.headers["retry-after"]=="1"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=("127.0.0.1",50000)),base_url=BASE) as client:
            assert (await client.post("/api/v1/trips",json=input_data(),headers=headers(key))).status_code==201
    run(scenario())


def test_unavailable_database_and_health(monkeypatch):
    secret="never-print-this-password"
    monkeypatch.setenv("EASYTRAVEL_DATABASE_URL",f"postgresql://fake:{secret}@127.0.0.1:1/dedicated")
    with client_for() as client:
        r=client.post("/api/v1/trips",json=input_data(),headers=headers())
        assert r.status_code==503 and r.json()["error"]["code"]=="business_dependency_unavailable"
        assert secret not in r.text and TOKEN_A not in r.text
        assert client.get("/api/v1/health").json()["dependencies"]["database"]=="not_checked"
