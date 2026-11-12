"""Actual HTTP, PostgreSQL transaction IDs, restart and browser compatibility evidence."""
import asyncio
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
    env=os.environ|dict(line.split('=',1) for line in (ROOT/'.local/m104-http.env').read_text(encoding='utf-8').splitlines() if line)
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    subprocess.run([sys.executable,'-m','easytravel.backend.infrastructure.migrate'],env=env,check=True)
    token=secrets.token_urlsafe(32)
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test',EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([dict(actor='m104_http',token=token)]),
        EASYTRAVEL_APP_LOCAL_ALLOWED_HOSTS='["127.0.0.1:18040"]',
        EASYTRAVEL_APP_LOCAL_ALLOWED_ORIGINS='["http://127.0.0.1:5174"]',
        EASYTRAVEL_API_PROXY_TARGET='http://127.0.0.1:18040')
    processes=[];logs=[];records=[]
    def launch(name,command,cwd=ROOT):
        log=(ROOT/f'.local/m104-{name}.log').open('w',encoding='utf-8');logs.append(log)
        proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT);processes.append(proc);return proc
    try:
        server=launch('http', [sys.executable,'-m','easytravel.backend.local_server','--port','18040'])
        worker=launch('worker',[sys.executable,'-m','easytravel.backend.generation_worker'])
        launch('vite',['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5174'],ROOT/'frontend')
        with httpx.Client(base_url='http://127.0.0.1:18040',trust_env=False,timeout=10) as client:
            for _ in range(100):
                try:
                    if client.get('/api/v1/health').status_code==200 and 'executor ready' in (ROOT/'.local/m104-worker.log').read_text(encoding='utf-8'):break
                except httpx.TransportError:pass
                assert all(p.poll() is None for p in processes),'verification process exited'
                time.sleep(.1)
            else:raise RuntimeError('HTTP/worker startup timed out')
            def request(method,path,body=None,key=None,status=200):
                h={'Authorization':'Bearer '+token}
                if method!='GET':h['Idempotency-Key']=key or str(uuid4())
                r=client.request(method,path,json=body,headers=h)
                assert r.status_code==status,r.text
                records.append(dict(method=method,path=path,key=h.get('Idempotency-Key'),request=body,status=r.status_code,response=r.json(),location=r.headers.get('location')))
                return r.json()
            given=lambda value:dict(state='provided',value=value)
            data=dict(origin=given('上海'),destination=given('杭州'),dates=given(dict(mode='fixed',start_date='2026-12-04',end_date='2026-12-06')),
                days=given(3),travelers=given(2),target_budget_cents=given(200000),hard_limit_cents=given(230000),transport=given('公共交通'),
                hotel=given('安静，一间双人房'),restrictions=given('不爬山'),raw_text=None)
            t=request('POST','/api/v1/trips',dict(requirements=data),status=201);path='/api/v1/trips/'+t['trip_id']
            body=dict(expected_intake_revision=1,expected_working_revision=None,retry_of=None);key=str(uuid4())
            a=request('POST',path+'/generations',body,key,status=202)
            assert a['status']=='ACCEPTED'
            url=path+'/generations/'+a['run_id']
            for _ in range(100):
                r=request('GET',url)
                if r['status'] in ('SUCCEEDED','FAILED'):break
                time.sleep(.05)
            assert r['status']=='SUCCEEDED'
            trip=request('GET',path)
            assert trip['synchronization']=='CURRENT' and trip['working']['revision']==1
            worker.terminate();worker.wait(timeout=5)
            result=subprocess.run([sys.executable,'-m','easytravel.backend.generation_worker','--once'],env=env,capture_output=True,text=True,check=True)
            assert request('POST',path+'/generations',body,key,status=202)==a
            assert request('GET',url)==r
            assert request('GET',path)==trip
            with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'],row_factory=dict_row) as conn:
                db=conn.execute('SELECT version() version,current_database() database,current_user owner').fetchone()
                ledger=conn.execute('SELECT version,checksum,applied_at FROM easytravel.schema_migrations ORDER BY version').fetchall()
                transactions={table:conn.execute(f'SELECT xmin::text transaction_id FROM easytravel.{table} WHERE trip_id=%s',(t['trip_id'],)).fetchone()['transaction_id'] for table in ('draft_versions','working_drafts','draft_contexts','generation_runs')}
                assert len(set(transactions.values()))==1
                context=conn.execute('SELECT intake_revision,working_revision,run_id,context FROM easytravel.draft_contexts WHERE trip_id=%s',(t['trip_id'],)).fetchone()
                assert context['context']==trip['working']['context']
                receipts=conn.execute('SELECT method,path,response_status,result FROM easytravel.api_commands WHERE trip_id=%s ORDER BY created_at',(t['trip_id'],)).fetchall()
                assert receipts[-1]['result']==a and receipts[-1]['response_status']==202
            failure_worker=launch('worker-failure',[sys.executable,'-m','easytravel.backend.generation_worker'])
            for _ in range(100):
                if 'executor ready' in (ROOT/'.local/m104-worker-failure.log').read_text(encoding='utf-8'):break
                assert failure_worker.poll() is None
                time.sleep(.05)
            else:raise RuntimeError('Failure worker startup timed out')
            limited=data|dict(hard_limit_cents=given(1),target_budget_cents=given(0))
            failed_trip=request('POST','/api/v1/trips',dict(requirements=limited),status=201)
            failed_path='/api/v1/trips/'+failed_trip['trip_id']
            failed_run=request('POST',failed_path+'/generations',body,status=202)
            for _ in range(100):
                failed=request('GET',failed_path+'/generations/'+failed_run['run_id'])
                if failed['status']=='FAILED':break
                time.sleep(.05)
            assert failed['status']=='FAILED' and failed['error']['code']=='known_costs_exceed_hard_limit'
            assert request('GET',failed_path)['working'] is None
            actual=client.get('/openapi.json').json()
            (ROOT/'Docs/M1-04a_openapi.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
            (ROOT/'Docs/M1-04a_HTTP证据.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            (ROOT/'Docs/M1-04a_PostgreSQL证据.json').write_text(json.dumps(dict(**db,ledger=ledger,atomic_transaction_ids=transactions,context=context,receipts=receipts,restart_stdout=result.stdout),default=str,ensure_ascii=False,indent=2),encoding='utf-8')
            env.update(EASYTRAVEL_BROWSER_TOKEN_A=token,EASYTRAVEL_BROWSER_TRIP=t['trip_id'],PLAYWRIGHT_BROWSERS_PATH=str(ROOT/'.cache/ms-playwright'))
            subprocess.run(['node','scripts/verify_m104_browser.mjs'],env=env,check=True)
            for file in (ROOT/'Docs').glob('M1-04a*'):
                if file.suffix in ('.json','.txt'):assert token not in file.read_text(encoding='utf-8-sig')
            print('Actual 202/poll/SUCCEEDED, restart replay, PostgreSQL shared xmin, browser CURRENT compatibility passed.')
    finally:
        for p in reversed(processes):
            if p.poll() is None:p.terminate();p.wait(timeout=10)
        for log in logs:log.close()

if __name__=='__main__':main()
