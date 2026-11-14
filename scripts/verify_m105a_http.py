"""Real loopback HTTP, PostgreSQL reads, API process restart and durable replay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4
import httpx
import psycopg
from psycopg.rows import dict_row

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--database-env',required=True); args=p.parse_args()
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT')=='55434'
    env=os.environ.copy() | dict(l.split('=',1) for l in Path(args.database_env).read_text().splitlines() if l)
    token=secrets.token_urlsafe(32)
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test', EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([dict(actor='m105_http_owner',token=token)]))
    subprocess.run([sys.executable,'-m','easytravel.backend.infrastructure.migrate'],env=env,check=True,capture_output=True)
    log=(ROOT/'.local/m105a-http-server.log').open('w',encoding='utf-8')
    proc=None
    def start():
        server=subprocess.Popen([sys.executable,'-m','easytravel.backend.local_server'],env=env,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(100):
            if server.poll() is not None: raise RuntimeError('Isolated HTTP server failed')
            try:
                if httpx.get('http://127.0.0.1:8000/api/v1/health',trust_env=False).status_code==200:return server
            except httpx.TransportError: pass
            time.sleep(.1)
        server.terminate();server.wait();raise RuntimeError('HTTP readiness timeout')
    try:
        proc=start()
        with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False,headers={'Authorization':'Bearer '+token}) as c:
            records=[]
            def call(method,path,body=None,key=None):
                r=c.request(method,path,json=body,headers={'Idempotency-Key':key or str(uuid4())})
                records.append(dict(method=method,path=path,request=body,status=r.status_code,response=r.json()))
                return r
            data={k:dict(state='provided',value=v) for k,v in dict(origin='上海',destination='杭州',
                dates=dict(mode='fixed',start_date='2026-12-04',end_date='2026-12-06'),days=3,travelers=2,
                target_budget_cents=200000,hard_limit_cents=230000,transport='公共交通',hotel='安静',restrictions='不爬山').items()}
            r=call('POST','/api/v1/trips',dict(requirements=data|dict(raw_text=None)))
            assert r.status_code==201
            path='/api/v1/trips/'+r.json()['trip_id']
            accepted=call('POST',path+'/generations',dict(expected_intake_revision=1,expected_working_revision=None,retry_of=None))
            assert accepted.status_code==202
            # The daemon's startup recovery deliberately fails preexisting ACCEPTED runs.
            # Exercise a separate live executor step without invoking startup recovery.
            worker_code='''import asyncio
from easytravel.backend.infrastructure.database import load_database_settings,connection
from easytravel.backend.generation_worker import GenerationWorker
async def main():
 s=load_database_settings()
 async with connection(s) as c:assert await GenerationWorker(s).step(c)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
'''
            subprocess.run([sys.executable,'-c',worker_code],env=env,check=True,capture_output=True)
            assert call('GET',path+'/generations/'+accepted.json()['run_id']).json()['status']=='SUCCEEDED'
            r=call('GET',path+'/confirmation-preview');assert r.status_code==200
            preview=r.json();b={k:preview[v] for k,v in dict(expected_intake_revision='intake_revision',expected_working_revision='working_revision',seen_disclosure_hash='disclosure_hash',seen_risk_hash='risk_hash').items()}|dict(explicit_confirmation=True)
            key=str(uuid4());r=call('POST',path+'/confirmations',b,key);assert r.status_code==201,r.text
            result=r.json()
            trip=call('GET',path).json();assert trip['plan_confirmation']=='CONFIRMED'
            actual=c.get('/openapi.json').json()
            Path('Docs/M1-05a_openapi.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
            proc.terminate();proc.wait(timeout=10);proc=start()
            replay=call('POST',path+'/confirmations',b,key)
            assert replay.status_code==201 and replay.json()==result
            assert call('GET',path).json()==trip
            with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'],row_factory=dict_row) as conn:
                tid=trip['trip_id']
                rows={t:conn.execute(f'SELECT * FROM easytravel.{t} WHERE trip_id=%s',(tid,)).fetchall() for t in
                    ['confirmed_snapshots','confirmation_disclosures','working_drafts','operation_receipts','api_commands','draft_versions','draft_contexts','specific_authorizations']}
                assert len(rows['confirmed_snapshots'])==len(rows['confirmation_disclosures'])==len(rows['operation_receipts'])==1
                assert not rows['specific_authorizations']
                assert rows['working_drafts'][0]['latest_confirmed_version_id']==result['snapshot']['snapshot']['snapshot_id']
                assert rows['confirmation_disclosures'][0]['disclosure']==result['snapshot']['risk_disclosure']
            Path('Docs/M1-05a_HTTP证据.json').write_text(json.dumps(dict(transport='real loopback TCP HTTP',restartReplayIdentical=True,records=records,database=rows),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print('Real HTTP preview 200, confirmation 201, process restart 201 replay; PostgreSQL facts match')
    finally:
        if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=10)
        log.close()

if __name__=='__main__':main()
