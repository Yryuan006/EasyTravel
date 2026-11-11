"""Real loopback HTTP evidence; use a dedicated database env, no data reset."""
import argparse
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
    parser=argparse.ArgumentParser();parser.add_argument('--database-env',required=True);args=parser.parse_args()
    env=os.environ.copy()|dict(line.split('=',1) for line in (ROOT/args.database_env).read_text(encoding='utf-8').splitlines() if line)
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    subprocess.run([sys.executable,'-m','easytravel.backend.infrastructure.migrate'],env=env,check=True)
    token=secrets.token_urlsafe(32)
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test',EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
      EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([dict(actor='m103_http',token=token)]),
      EASYTRAVEL_APP_LOCAL_ALLOWED_HOSTS='["127.0.0.1:18030"]')
    records=[];base='http://127.0.0.1:18030'
    with (ROOT/'.local/m103-http-server.log').open('w',encoding='utf-8') as log:
        server=subprocess.Popen([sys.executable,'-m','easytravel.backend.local_server','--port','18030'],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=base,trust_env=False,timeout=15) as c:
                for _ in range(100):
                    try:
                        if c.get('/api/v1/health').status_code==200:break
                    except httpx.TransportError:pass
                    if server.poll() is not None:raise RuntimeError('Loopback server exited')
                    time.sleep(.1)
                else:raise RuntimeError('Loopback server readiness failed')
                def request(method,path,body=None,key=None,expect=200):
                    h={'Authorization':'Bearer '+token}
                    if method!='GET':h['Idempotency-Key']=key or str(uuid4())
                    r=c.request(method,path,json=body,headers=h)
                    assert r.status_code==expect,r.text
                    records.append(dict(method=method,path=path,key=h.get('Idempotency-Key'),request=body,status=r.status_code,response=r.json()))
                    return r.json()
                fields=('origin','destination','dates','days','travelers','target_budget_cents','hard_limit_cents','transport','hotel','restrictions')
                req={f:dict(state='unanswered',value=None) for f in fields};req['raw_text']='用户原文只保存，不提取为事实'
                def given(v):return dict(state='provided',value=v)
                req.update(origin=given('上海'),destination=given('杭州'),dates=given(dict(mode='fixed',start_date='2026-12-04',end_date='2026-12-06')),
                           days=given(3),travelers=given(2),target_budget_cents=given(200000),transport=dict(state='no_preference',value=None))
                trip=request('POST','/api/v1/trips',dict(requirements=req),expect=201);path='/api/v1/trips/'+trip['trip_id']
                q=request('GET',path+'/clarifications/next')['question'];assert q['field_key']=='hard_limit_cents'
                body=dict(expected_intake_revision=1,expected_working_revision=None,field_key='hard_limit_cents',answer=given(230000));key=str(uuid4())
                route=path+'/clarifications/'+q['question_id']+'/answers'
                answered=request('POST',route,body,key);assert answered['intake']['intake_revision']==2
                assert request('POST',route,body,key)==answered
                request('POST',route,body|dict(answer=given(240000)),key,expect=409)
                req2=answered['intake']['input']|dict(days=given(2))
                body2=dict(expected_intake_revision=2,expected_working_revision=None,requirements=req2)
                changed=request('PUT',path+'/requirements',body2);assert changed['intake']['intake_revision']==3
                request('POST',route,body,expect=409)
                assert request('GET',path+'/clarifications/next')['question']['field_key']=='dates'
                assert request('GET',path)==changed
                def fingerprint(conn):
                    return {table:conn.execute(f"SELECT count(*) n,md5(COALESCE(string_agg(md5(row_to_json(t)::text),'' ORDER BY md5(row_to_json(t)::text)),'')) hash FROM easytravel.{table} t").fetchone()
                            for table in ('trip_intake_versions','trip_intakes','clarification_questions','clarification_answers','api_commands','draft_versions','confirmed_snapshots')}
                with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'],row_factory=dict_row) as conn:
                    before=fingerprint(conn)
                    request('GET',path+'/clarifications/next')
                    assert fingerprint(conn)==before
                    versions=conn.execute('SELECT revision,input FROM easytravel.trip_intake_versions WHERE trip_id=%s ORDER BY revision',(trip['trip_id'],)).fetchall()
                    assert [v['revision'] for v in versions]==[1,2,3] and versions[0]['input']==req
                    answers=conn.execute('SELECT question_id::text,intake_revision,resulting_revision,field_key FROM easytravel.clarification_answers WHERE trip_id=%s',(trip['trip_id'],)).fetchall()
                    receipts=conn.execute('SELECT method,path,response_status,result FROM easytravel.api_commands WHERE trip_id=%s ORDER BY created_at',(trip['trip_id'],)).fetchall()
                    assert len(answers)==1 and len(receipts)==3
                    evidence=dict(http=records,database=dict(versions=versions,answers=answers,receipts=receipts,get_before=before,get_after=fingerprint(conn)))
                (ROOT/'Docs/M1-03a_HTTP与数据库证据.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                live=c.get('/openapi.json').json()
                (ROOT/'Docs/M1-03a_openapi.json').write_text(json.dumps(live,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print('PASS: real TCP HTTP create/answer/update/read/replay/conflicts; revisions 1,2,3; one answer; three receipts; GET hashes unchanged')
        finally:
            server.terminate();server.wait(timeout=15)

if __name__=='__main__':main()
